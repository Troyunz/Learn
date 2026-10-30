# Lesson 80 — Quorums

*Phase 4, Topic 17*

## 1. Simple definition

A **quorum** is the minimum number of participants that must agree before an operation counts as having happened.

For replicated data, with **N** replicas you choose two numbers:

- **W** — how many replicas must acknowledge a **write** before it is confirmed to the client.
- **R** — how many replicas must respond to a **read** before it is returned to the client.

The rule that makes the whole thing work is the **intersection property**:

> **W + R > N**

If every write is on at least `W` replicas and every read consults at least `R`, then those two sets must overlap in at least one replica — so a read always sees at least one copy of the most recent acknowledged write. With `N = 3`, `W = 2`, `R = 2`: `2 + 2 = 4 > 3`, and any two replicas intersect any other two. ✓

The same idea governs **agreement systems**: a leader is elected by a **majority**, and a change is committed when a **majority** has it. Majorities always intersect, which is why `⌊N/2⌋ + 1` is the number that appears everywhere in this phase.

Terms defined before use:

- **Replica**: one copy of the data, on one node.
- **Quorum intersection**: the guarantee that any read quorum and any write quorum share at least one member.
- **Read repair**: when a read sees replicas disagree, it returns the newest value and writes it back to the stale ones (§4.5).
- **Sloppy quorum**: writing to `W` *healthy* nodes even if they are not the *home* nodes for the key — which breaks intersection (§4.6).
- **Hinted handoff**: the mechanism that makes sloppy quorums "work" — the stand-in node remembers the write and forwards it later.
- **Session guarantees**: read-your-writes, monotonic reads — properties a client needs that quorums alone do not provide (§4.7).
- **Flexible quorum**: read and write quorums that need not both be majorities, as long as they intersect (used in Raft/Paxos variants).

The one sentence to carry out of this lesson: **quorums trade availability and latency for consistency by arithmetic — and every one of those trades disappears the moment you allow a "sloppy" quorum.**

## 2. Why it exists

Because replication creates a choice you cannot avoid: **do you require all replicas to agree, or not?**

**Require all (N of N).** BookNook writes a session to 3 replicas. With `W = 3`, any single node being slow or down **fails the write**. Availability becomes the product of three nodes' availabilities — `0.999³ = 99.7 %` — and the latency becomes the **slowest** of three nodes, so one 400 ms straggler makes every write take 400 ms. Unusable.

**Require one (W = 1).** Now writes are fast and always available — but a read of `R = 1` from a replica that missed the last write returns stale data, and there is no arithmetic that makes it correct. On BookNook's inventory path this produced the §7.2 incident: 40 oversold copies of a signed edition.

**The quorum is the middle.** `W = 2, R = 2` means:

- Writes tolerate **one** node being down or slow (still 2 acks available).
- Reads tolerate **one** node being down.
- Any read overlaps every acknowledged write, so **no stale read, no lost update** from the reader's perspective.

Arithmetically: availability `1 − P(≥2 of 3 down)` and consistency guaranteed by intersection. That is the entire content of the topic, and it is why `N = 3, W = 2, R = 2` is the most common configuration in production leadered-less stores.

The second reason quorums exist is more subtle, and it is the reason this lesson sits next to leader election and consensus: **a majority is what makes agreement possible at all.** Two disjoint groups of equal size cannot both be "the decision" — but any two majorities share a member, and that shared member is the physical reason a system can recover a decision (L81). Every consensus protocol is, underneath, a machine for exploiting quorum intersection.

## 3. Real-world analogy

**The bank safe-deposit box with three keys.**

The box needs **two of three** keyholders present to open. This is a quorum, and every property follows from it:

- **Any two keyholders can open it** — `W = 2` of `N = 3`. That is why the arrangement survives one keyholder being ill, on holiday, or having lost their key. The bank is not closed when one person is unavailable.
- **Any two keyholders overlap with any other two** — whoever deposits something (two keys) and whoever inspects the box later (any other two) always include **at least one person who was present at the deposit**. This is why they cannot be told the box is empty when it is not. That is the intersection property, and it holds *because* 2 + 2 > 3.
- **One keyholder is not enough** — no single person can deposit or inspect alone. That is `W = 1` being disallowed, and it is a decision about **trust**, not about speed.
- **All three are not required** — requiring three keys means the box is inaccessible whenever anyone is away. That is `W = N`, the availability mistake.

Now the failure mode, which is the most valuable part of the analogy. Suppose the bank, during a flood, says: "the box is unreachable, so we will open **a temporary box in the next building** with two of the three keyholders, and move the contents back later." Deposits and inspections now happen in a *different* place, with a *different* set of possible witnesses. A customer who inspects the original box sees it empty — even though a deposit was confirmed. **That is a sloppy quorum** (§4.6): the quorum was formed, the write was acknowledged, and the intersection guarantee was silently destroyed. The bank still "worked", which is exactly why the problem is discovered late.

The final piece: the box's **logbook**. Each opening is written down by those present. When someone opens the box and finds the contents do not match the logbook, the newest entry wins and the earlier keyholders are told to update their records. That is **read repair** (§4.5) — the mechanism that turns an occasional overlapping-but-disagreeing read into convergence.

## 4. Detailed technical explanation

### 4.1 The arithmetic, and what each configuration means

For `N = 3`:

| W | R | W+R | Reads see latest write? | Write latency | Read latency | Availability | Verdict |
|---|---|---|---|---|---|---|---|
| 1 | 1 | 2 | **No** | fastest (1 node) | fastest | highest | No consistency |
| 1 | 3 | 4 | Yes | fastest | **slowest of 3** | reads need all nodes | Read-all, write-one |
| 2 | 2 | 4 | **Yes** | 2 of 3 | 2 of 3 | balanced | **The standard choice** |
| 3 | 1 | 4 | Yes | **all 3 must ack** | fastest | writes need all nodes | Write-all, read-one |
| 3 | 3 | 6 | Yes | worst | worst | worst | Never |

Two things to note:

- `W + R > N` is the **intersection** condition, not the "latest write" condition — seeing the latest requires that the read quorum contains at least one node with the latest write, and for *reading* the newest among disagreements you need the values to be comparable (a version or timestamp, L66).
- **Higher W costs availability and latency on writes; higher R costs them on reads.** Choose based on which side you can afford to lose. BookNook's inventory write is `W = 3` (must never be lost); its product-page read is `R = 1` (stale by 50 ms is fine, and a cache sits in front anyway).

### 4.2 Why majority and not "any W"

Any `W + R > N` works arithmetically, so why does `⌊N/2⌋ + 1` dominate? Because a majority is the **only** quorum size that guarantees intersection with **itself**:

```
  N = 5, W = 3, R = 3:  3 + 3 = 6 > 5 ✓  every quorum intersects every other
  N = 5, W = 2, R = 4:  2 + 4 = 6 > 5 ✓  but TWO writes (2 + 2 = 4 < 5) may
                                          be disjoint → a later write can
                                          clobber an earlier one without
                                          seeing it (lost update between two
                                          concurrent writers)
  N = 5, W = 3, R = 3:  any two writer sets intersect → write-write conflicts
                        are DETECTED, not silently merged
```

So `W > N/2` is required when **writes must be serialised against each other** (which for a lock, a lease, or a leader election they must, L78/L79), and `W + R > N` is required when **reads must see writes**. Consensus systems need the first; Dynamo-style stores need only the second, which is why they can offer `R = 1` for fast reads.

### 4.3 Quorums in leader-based replication (Raft)

Raft uses quorums differently, and more strictly:

```
  WRITE (a log append):  the leader sends to all followers, but commits as
                         soon as ⌊N/2⌋ + 1 nodes have it (including itself).
                         ⇒ one slow follower does not slow the cluster; it is
                           the (N+1)/2-th fastest that determines latency.

  ELECT (a leader):      a candidate needs votes from ⌊N/2⌋ + 1 nodes.
                         ⇒ any two majorities share a voter, so a new leader
                           always contains the latest committed entry (L81).

  READ (default):        the leader serves it after confirming it is still
                         leader by a round trip to a majority — otherwise an
                           isolated old leader would serve stale reads (L79).
```

The consequence for **latency** is asymmetric, and worth remembering: commit latency is the latency of the **median** replica, not the maximum. With 5 replicas where 4 are in one region and 1 is remote, commits need 3 of 5 — so the remote replica can be arbitrarily slow without affecting you. This is why cluster sizing is a latency-layout decision as much as a fault-tolerance decision (§4.8).

### 4.4 Even-sized clusters are usually a mistake

```
  N = 4, majority = 3:  tolerates 1 failure (same as N = 3), costs 33 % more.
  N = 2, majority = 2:  tolerates 0 failures — strictly worse than N = 1.
  N = 6, majority = 4:  tolerates 2 failures (same as N = 5), costs more.
  ⇒ odd sizes, always: 3 for most, 5 across three availability zones.
```

This is not a superstition: a quorum of `N/2 + 1` for even `N` buys nothing over `N − 1`, while adding a node to the failure domain.

### 4.5 Read repair, and how divergence heals

Leadered-less systems converge lazily:

```
  client reads key K with R = 2
  replica A → value v7 (version 7)
  replica B → value v5 (version 5, missed an update)
  → return v7 (newest), and asynchronously write v7 to B
  ⇒ the next read is consistent; no coordinator needed.
```

Read repair only heals keys that are **read**. That is why real systems run **anti-entropy** in the background — Merkle-tree comparison between replicas (L58 §4.9) — to find and repair keys nobody reads. Without it, a key that is written once and never read stays divergent forever, which is exactly the failure that makes a later read return garbage.

### 4.6 Sloppy quorums: the trade that breaks the guarantee

When `W` home replicas are unreachable, a **sloppy quorum** writes to any `W` *reachable* nodes and relies on **hinted handoff** to deliver later.

```
  N = 3, W = 2, R = 2.  Home replicas for K: {A, B, C}.  Partition: {A, B} × {C}.
      writer in {A, B}: writes A and B               → 2 acks ✓
      reader in {C}: requires 2 of 3, only C reachable → UNAVAILABLE (correct!)
  Now with sloppy quorums and two extra standby nodes D, E in the second partition:
      writer in {A, B}: writes A, B                   → ✓
      reader in {C}: C is stale, but reads C and D — D has an *older* value
                     from a previous hint → returns STALE DATA as if current.
  ⇒ the quorum was arithmetic; the intersection was geometry. W + R > N
    guarantees nothing if the sets are not drawn from the same N.
```

Sloppy quorums buy **availability during partitions** and pay with **consistency**. That is a legitimate choice for a shopping cart (which famously chose availability) and a catastrophic one for inventory or a lock. **Decide it explicitly, per data set** — and if you choose sloppy, you must accept that `W + R > N` is no longer a correctness argument.

### 4.7 Session guarantees quorums do not provide

`W + R > N` guarantees that a read sees the latest **acknowledged** write. It does not guarantee what a *particular client* expects:

| Guarantee | Statement | How to get it |
|---|---|---|
| **Read-your-writes** | I see my own write | Sticky routing to the replica that holds it, or carry the write's version and require `R` nodes ≥ it |
| **Monotonic reads** | I never go backwards in time | Sticky routing per session, or pin to the highest version seen |
| **Monotonic writes** | My writes apply in order | Session-pinned coordinator, or version-based conditional writes |
| **Writes-follow-reads** | My write is based on what I read | Version checks on write (`WHERE version = n`) |

BookNook hit the read-your-writes case in the classic way: a customer placed an order, was redirected to `/orders`, and the read hit a replica **600 ms behind** — so the order was "missing", and roughly **1 in 900 customers clicked "Place order" again**. The fix was 20 lines: on the redirect, route the session to the primary replica for 2 seconds (a *read-your-writes window*), then fall back to replicas.

### 4.8 Quorum size and latency: the actual numbers

Larger `N` improves fault tolerance and *hurts* latency, because a quorum must wait for the `k`-th fastest of `N`:

```
  Model: per-node write latency p50 = 2 ms, p99 = 12 ms, p99.9 = 90 ms
         (independent, same region)

  N=3, W=2 → latency = 2nd of 3 →  p50 ≈ 2.6 ms,  p99.9 ≈ 45 ms
  N=5, W=3 → latency = 3rd of 5 →  p50 ≈ 3.0 ms,  p99.9 ≈ 20 ms  ← BETTER tail!
  N=5, W=5 → latency = max of 5  →  p99.9 ≈ 400 ms              ← much worse
```

The counter-intuitive result is real: **requiring a majority of a larger cluster often improves the tail**, because you are waiting for the 3rd of 5 rather than the 2nd of 3 — one slow node is tolerated rather than waited on. What destroys tails is `W = N`, where you wait for the maximum.

Cross-region changes everything, because latency becomes bimodal (2 ms local, 60 ms cross-AZ):

```
  3 replicas in 3 AZs, W = 2:  write must reach exactly one remote AZ → +60 ms
  5 replicas, 3 in AZ-a, 1 in AZ-b, 1 in AZ-c, W = 3:  can commit entirely
      within AZ-a → +0 ms, but you have lost the ability to survive AZ-a's loss
  ⇒ the layout of replicas is a quorum design decision, not an infrastructure
    detail.
```

## 5. Request / data flow

BookNook's inventory counter with `N = 3, W = 2, R = 2`, including a node failure and a repair.

```
 ── WRITE with one replica down ─────────────────────────────────────────────
  client → coordinator (any node, or the app)
  write K=SKU-4471, value=498, version=1201
    → replica A  ack ✓ (0.8 ms)
    → replica B  ack ✓ (1.1 ms)
    → replica C  TIMEOUT (node down)
    ⇒ W = 2 reached → ACK THE CLIENT at 1.1 ms. Success.
      (With W = 3 this write FAILS. That is the availability trade.)

 ── READ during the same failure ────────────────────────────────────────────
  read K=SKU-4471 with R = 2
    → replica A  v1201 ✓
    → replica B  v1201 ✓      → return 498, version 1201
    (C not consulted; it is down. Reads still work: R = 2 of 3.)

 ── WRITE-WRITE CONFLICT, and why W = 2 is enough ──────────────────────────
  two clients write K concurrently, one to {A, B} and one to {B, C}
    ⇒ the sets intersect at B.
    ⇒ B sees both writes and applies them in version order; one of the writers
      sees the conflict (compare-and-set fails) rather than both silently
      succeeding. With W = 1 and disjoint sets, one write would simply
      VANISH with no error at all.

 ── READ REPAIR ────────────────────────────────────────────────────────────
  C returns. It holds v1199.
  a read with R = 2 hits A (v1201) and C (v1199)
    → return v1201, and asynchronously write v1201 to C
    ⇒ healed on read. Keys nobody reads are healed by anti-entropy (L58).

 ── THE SESSION-GUARANTEE EDGE ─────────────────────────────────────────────
  a customer places order 90210 (written with W = 2 to {A,B}), then is
  redirected to /orders which reads with R = 1 from C (still v-old)
    ⇒ "your order is missing" → support ticket, or a duplicate click
  fix: after a write, pin the session to a replica known to be ≥ that version
       for 2 s (§4.7).
```

## 6. ASCII diagram

`N = 3, W = 2, R = 2` — the intersection that makes it work:

```
             ┌───────────────────────────────┐
             │        N = 3 replicas         │
             │                               │
             │     ┌─────┐  ┌─────┐          │
             │     │  A  │  │  B  │  ┌─────┐ │
             │     └──▲──┘  └──▲──┘  │  C  │ │
             │        │        │     └──▲──┘ │
             │   write quorum {A,B}     │    │
             │        │        │        │    │
             │        ▼        ▼        │    │
             │   ┌────────────────┐     │    │
             │   │ INTERSECTION ▲ │◄────┘    │
             │   │  at least one  │          │
             │   │  replica in    │          │
             │   │  BOTH sets     │          │
             │   └────────────────┘          │
             │        read quorum {B,C}      │
             └───────────────────────────────┘
        W + R = 4 > N = 3  ⇒  the sets CANNOT be disjoint  ⇒  no stale read

  the four configurations, visually:

   W=1,R=1  {A}   {B}    DISJOINT  ✗ stale reads possible
   W=1,R=3  {A}   {A,B,C}  overlap ✓  but reads need every node
   W=2,R=2  {A,B} {B,C}    overlap ✓  balanced  ← standard
   W=3,R=1  {A,B,C} {C}    overlap ✓  but writes need every node
```

The sloppy-quorum failure, drawn:

```
   home N = {A, B, C}          standbys {D, E}

   partition:   {A, B, C}          |          {D, E}
                    │              |              │
   write to A, B    │ ✓✓           |              │   A and B are HOME nodes
   (2 acks, W=2 ok) │              |              │
                    │              |   read with R=2: C (stale) + D (old hint)
                    │              |   ⇒ returns STALE value, faithfully,
                    │              |     believing it satisfied the quorum
                    ▼              |              ▼
              ┌──────────────────────────────────────┐
              │  W + R > N was satisfied BY COUNT,    │
              │  but the sets were NOT drawn from the │
              │  same N  ⇒  intersection is an        │
              │  illusion. Availability bought with   │
              │  correctness.                         │
              └──────────────────────────────────────┘
```

## 7. Detailed example

### 7.1 Choosing the quorum for three different data sets

BookNook runs one 5-node cluster (3 AZs) and configures different consistency per data set — which is the correct professional answer, and the opposite of "we picked one and applied it to everything".

| Data set | N | W | R | Why |
|---|---|---|---|---|
| **Session tokens** | 5 | 2 | 1 | A stale session read is harmless (the token is valid or not); losing a write logs someone out |
| **Product catalogue** | 5 | 2 | 2 | Stale prices are a support ticket; balance matters more than speed |
| **Inventory counters** | 5 | **3** | **3** | Overselling costs real money; must serialise concurrent writers, so `W > N/2` |
| **Order timeline** | 5 | 3 | 1 | Writes must never be lost; reads are per-order and rare, so `R = 1` is enough with read repair |

```
  latency measured for the inventory path (W=3, R=3, 3 AZs):
      p50 4.2 ms   p99 61 ms   p99.9 240 ms
  versus session path (W=2, R=1):
      p50 0.9 ms   p99 14 ms   p99.9 95 ms
  ⇒ 4.6× the p50 cost for the guarantee that stops overselling. BookNook
    measured this and accepted it for inventory, and only for inventory.
```

The general method: **write down, per data set, the cost of a stale read and the cost of a lost write**, then pick W and R so that the more expensive failure is the one the arithmetic prevents.

### 7.2 The sloppy quorum that oversold a signed edition

BookNook's inventory used a 3-node Dynamo-style store with `W = 2, R = 2` — correctly. Then, during a network partition that isolated AZ-c, an engineer "temporarily" enabled sloppy quorums with hinted handoff to keep checkout working. It did keep checkout working. It also:

```
  t+0     partition: AZ-c isolated. AZ-a and AZ-b hold 500 copies.
  t+2 min 400 orders committed in AZ-a/AZ-b (as {"A","B"} quorums) → 100 left
  t+3 min a coordinator in AZ-c (serving 12 % of traffic) reads
          R = 2 from {C, D}: C has 500 (its own stale copy), D has a hint
          from 40 minutes ago → returns "≥ 500 available" for 90 seconds
  t+3 min 212 further orders accepted against AZ-c's view
  t+5 min partition heals; 132 orders cannot be fulfilled
  ⇒ 132 oversold copies, 132 apology emails, £6,400 in goodwill +
    marketplace resale at 3× face value to make good
```

Three conclusions, and they generalise:

1. **Sloppy quorums convert a consistency guarantee into a hope.** The setting was a one-word flag; the failure was silent; the discovery was 5 minutes late.
2. **`R = 2` from `{C, D}` returned confident, plausible, wrong data.** The system had no way to know it was lying — the hint was older than the read quorum's tolerance.
3. **If availability during a partition is genuinely required, do not relax the quorum — change the business model.** BookNook's eventual fix was a **reservation** model: during a partition, orders are accepted as *provisional* and confirmed later, which is honest to the customer instead of silently overselling.

## 8. Code or configuration example

```python
# ============================================================================
# BookNook: a minimal quorum client over N replicas, with read repair.
# Illustrative only — real deployments use Cassandra/DynamoDB/etcd.
# ============================================================================

async def quorum_write(replicas, key, value, version, W: int, timeout_ms=200):
    """Succeed when W acks arrive. Do NOT wait for the slowest replica."""
    tasks = [asyncio.create_task(
                 r.put(key, value, version, timeout=timeout_ms/1000))
             for r in replicas]
    acks, errors = 0, []
    for fut in asyncio.as_completed(tasks):        # return on the W-th ack
        try:
            await fut
            acks += 1
            if acks >= W:
                for t in tasks:                    # let the stragglers finish
                    if not t.done():              #   in the background:
                        t.add_done_callback(_swallow)   # a cancelled slow
                return WriteResult(acks=acks, ok=True)  # write is a failed write
        except Exception as e:
            errors.append(e)
    return WriteResult(acks=acks, ok=False, errors=errors)


async def quorum_read(replicas, key, R: int, repair=True):
    """Succeed when R responses arrive; return the NEWEST by version (L66)."""
    tasks = [asyncio.create_task(r.get(key)) for r in replicas]
    responses = []
    for fut in asyncio.as_completed(tasks):
        try:
            responses.append((await fut))
            if len(responses) >= R:
                break
        except Exception:
            pass
    if len(responses) < R:
        raise QuorumUnavailable(f"{len(responses)} < R={R}")

    newest = max(responses, key=lambda x: x.version)   # versions, not clocks
    if repair:
        stale = [r for r in responses if r.version < newest.version]
        for r in stale:
            asyncio.create_task(r.put(key, newest.value, newest.version))
            metrics.incr("quorum.read_repair")
    return newest
```

```python
# --- Cassandra: the consistency level IS the quorum choice ----------------
# per-statement, which is the point: different data sets, different trades.
session.execute(SimpleStatement(
    "UPDATE inventory SET qty = 498 WHERE sku='SKU-4471'",
    consistency_level=ConsistencyLevel.QUORUM))       # W = majority of RF

session.execute(SimpleStatement(
    "SELECT qty FROM inventory WHERE sku='SKU-4471'",
    consistency_level=ConsistencyLevel.QUORUM))       # R = majority ⇒ R+W>N

#   replication factor 3 → QUORUM = 2
#   ⚠ ConsistencyLevel.ONE for the read would break W+R>N and allow a
#     stale read of an acknowledged write.
#   ⚠ ConsistencyLevel.ANY on a write is a SLOPPY quorum: it counts a
#     hinted-handoff write as success even with all replicas down.
```

```hcl
# --- Sizing: odd clusters, replicas spread across failure domains ---------
cluster:
  size: 5                     # tolerates 2 node failures
  failure_domains: [az-a, az-b, az-c]     # never 2 replicas in one AZ
  replication_factor: 3       # QUORUM = 2; LOCAL_QUORUM for single-region reads
  # For a 2-region deployment, do NOT use a global quorum: cross-region RTT
  # (60 ms) would dominate every write. Use LOCAL_QUORUM per region and let
  # the regions reconcile asynchronously (§4.8).
```

```yaml
# --- The alarms that catch a quorum problem before it is an outage --------
alerts:
  - expr: quorum_unavailable_total > 0
    severity: page          # you are refusing operations: capacity or partition

  - expr: rate(quorum_read_repair_total[5m]) > 0.01 * rate(quorum_read_total[5m])
    severity: warn          # sustained divergence → a replica is behind

  - expr: replica_lag_seconds{cluster="booknook-main"} > 30
    severity: page          # a read quorum may be about to see stale data

  - expr: (up{job="replica"} == 0) >= 2      # for N=3; >= 3 for N=5
    severity: page          # one more failure removes the quorum entirely
```

## 9. Numerical example

BookNook's 5-node inventory cluster, `W = 3, R = 3`.

**Step 1 — verify the intersection, then check it against `W > N/2`.**

```
  W + R = 6 > N = 5 ✓                        reads see acknowledged writes
  W = 3 > N/2 = 2.5 ✓                        two writers always intersect
  ⇒ because W > N/2, ANY two write quorums overlap, so concurrent writers
    detect each other instead of silently clobbering. This is the property
    that matters for inventory and for locks (L78), and it is STRONGER than
    W + R > N. Cost: writes need 3 of 5 nodes, so 2 nodes may be down.
```

**Step 2 — availability.**

```
  per-node monthly availability 99.9 % (8.76 h/year downtime)
  P(a given node is up) = 0.999
  need 3 of 5 up:  P(quorum) = Σ_{k=3..5} C(5,k) 0.999^k 0.001^(5-k)
      = 1 − [P(3 down) + P(4 down) + P(5 down)]
      = 1 − [10 × 1e-9 + 5 × 1e-12 + 1e-15] ≈ 0.99999  (≈ 5 min/year)
  ⇒ the cluster is ~100× more available than a single node, for reads AND
    writes, which is the entire point of replication.
  For N=3, W=2: P = 1 − [3 × 1e-6 + 1e-9] ≈ 0.999997 — similar, cheaper.
  ⇒ the jump from 3 to 5 buys SLA headroom against CORRELATED failures (AZ
    outage), not against independent ones. Plan around the AZ, not the disk.
```

**Step 3 — latency, and why `W = 3` of 5 beats `W = 2` of 3 at the tail.**

```
  per-node write latency: p50 2 ms, p99 12 ms, p99.9 90 ms (independent)
  latency of a quorum = the W-th order statistic.

  N=3, W=2 → P(max of 2 nodes > 90 ms) = 1 − 0.999² = 0.002  → 0.2 %
             ⇒ p99.8 ≈ 90 ms
  N=5, W=3 → P(3rd of 5 > 90 ms) = P(≥3 nodes > 90 ms)
             = C(5,3)(0.001)³… ≈ 1e-8         → utterly negligible
             ⇒ p99.9 ≈ 12–20 ms
  N=5, W=5 → max of 5 → p99.9 ≈ 90 × (5)^(1/…) ≈ 250–400 ms  ⇒ worst tail

  ⇒ COMMIT LATENCY IS THE MEDIAN REPLICA'S LATENCY, not the maximum, which
    is why "wait for a majority" is both safe and fast.
```

**Step 4 — the cost of cross-AZ quorums, and the LOCAL_QUORUM escape.**

```
  RTT: same AZ 0.2 ms, cross-AZ 1.5 ms (same region), cross-region 60 ms.
  5 replicas: 2 in AZ-a, 2 in AZ-b, 1 in AZ-c. W = 3.
      writer in AZ-a: needs 3 acks. Its own 2 + 1 cross-AZ = 1.5 ms per hop,
      so p50 ≈ 3.5 ms including fsync.
  Now a 2-REGION deployment with a global quorum of 3 of 5 across regions:
      every commit needs at least one 60 ms hop → p50 ≈ 63 ms.
      10,000 writes/sec × 63 ms of held resources ⇒ a latency catastrophe
      for something that was 3.5 ms.
  ⇒ for multi-region, use per-region LOCAL_QUORUM (3 of 3 locally, or 2 of 3)
    and accept asynchronous cross-region replication. That is a CONSISTENCY
    decision (L91), and it cannot be avoided by tuning numbers.
```

**Step 5 — sizing the read-repair window that stopped duplicate clicks.**

```
  replica lag p99 = 600 ms; 1.4 % of sessions read immediately after writing.
  With R = 1 on a random replica: P(stale) ≈ P(lag > time since write) ≈ 0.11 %
  duplicate clicks observed: 1 in 900 = 0.111 % → matches the model exactly.
  at 40,000 orders/day: 45 duplicate submissions/day, ~9 of them reaching
      payment authorisation before dedupe (L74) caught them.
  fix: pin the session to a replica with version ≥ mine for 2 s
      ⇒ duplicates → 0, and the cost is that 1.4 % of sessions read from one
        named replica for 2 s (~0.3 % extra load on it).
  ⇒ session guarantees are cheap; they are just not implied by W + R > N.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **W=1, R=1** | Lowest latency, highest availability | Stale reads, lost writes | Ephemeral data where loss is fine (metrics samples, presence) |
| **W=2, R=2 (N=3)** | Balanced; no stale reads | No read/write serialisation guarantee | General-purpose default |
| **W > N/2** | Concurrent writers detect each other | Needs a majority up for writes | Inventory, locks, leases, ledgers |
| **W=N** | Strongest write durability | One slow node stalls every write | Almost never — it is the availability anti-pattern |
| **R=N** | Strongest read freshness | One slow node stalls every read | Almost never |
| **Sloppy quorum** | Availability during a partition | **Silently breaks intersection** (§4.6, §7.2) | Data where stale is harmless — decide per data set |
| **Read repair** | Lazy convergence | Only heals what is read | Default; pair with anti-entropy |
| **Lower consistency for one data set** | Speed where it is safe | Per-path complexity | Almost always worth it — quantify both failures |
| **LOCAL_QUORUM in multi-region** | ~3.5 ms instead of ~63 ms | Cross-region staleness window | Any multi-region active-active design (L91) |
| **Even cluster size** | Nothing | One extra node in the failure domain | Never |

## 11. Failure scenarios

**11.1 — Sloppy quorum oversell** (§7.2). `W=2, R=2` satisfied by `{C, D}` during a partition; 132 orders accepted with no stock. **Symptom:** confident, stale reads that satisfy the configured consistency level. **Fix:** never enable sloppy quorums on data where stale is expensive; model provisional orders instead.

**11.2 — `R = 1` against `W = 1` and a "mystery" lost update.** Two writers hit disjoint replicas; the later write was invisible to the earlier one; last-writer-wins by timestamp **lost** a real update, and wall-clock skew (L66) chose the wrong winner. **Symptom:** a field reverts; no error anywhere. **Fix:** `W > N/2` for anything where writes conflict, and version vectors or compare-and-set rather than timestamps.

**11.3 — The read-your-writes duplicate** (§4.7, step 5). 45 duplicate submissions a day from 600 ms replica lag. **Symptom:** duplicate orders that dedupe catches *sometimes*. **Fix:** pin the session, or carry the version.

**11.4 — `W = N` during a rolling restart.** A 3-node cluster with `W = 3` restarted one node at a time; every restart caused **100 % write failure for 40 seconds**, three times. **Symptom:** outages that exactly match deploy timings. **Fix:** never configure `W = N`; use a majority, and use rolling restarts with `W = 2`.

**11.5 — Quorum lost with only a minority down.** A 4-node cluster (even size) with majority 3: losing **two** nodes stopped all writes, i.e. it tolerated one failure — the same as a 3-node cluster with one more node to pay for and one more possible failure. **Symptom:** worse availability than the maths suggested. **Fix:** odd sizes.

**11.6 — Divergence nobody read.** A key written once during a partition and read only by a monthly report stayed divergent on one replica for 27 days. **Symptom:** a report value that changes depending on which replica answered. **Fix:** anti-entropy with Merkle trees (L58), not read repair alone.

**11.7 — Cross-region global quorum.** A 5-node cluster spread over two regions used a global `QUORUM`; every write paid a 60 ms cross-region round trip, and the write path saturated at 14 % of capacity. **Symptom:** uniformly terrible p50, no failures. **Fix:** `LOCAL_QUORUM`, plus an explicit decision about cross-region consistency.

**11.8 — The hinted handoff that arrived late and wrong.** Hints were retained for 3 hours by default; a node rejoined after 4 hours, the hints had expired, and the writes were silently dropped — while the write had been acknowledged to the client. **Symptom:** data loss for writes that "succeeded" during a partition. **Fix:** bound hint retention to the outage duration you are willing to lose, alarm on hint backlog, and treat hints as best-effort, not as durability.

## 12. Alternatives

| Instead of quorum reads/writes | Consider | When |
|---|---|---|
| Quorum | **Leader + synchronous replicas** (Raft, L81) | You want a single write order and simpler client logic |
| Quorum | **Single primary with async replicas** | Loss on failover is acceptable (RPO > 0, L92) |
| Quorum | **Consensus (Paxos/Raft)** | You need agreement, not just intersection (L81) |
| Sloppy quorum | **Reject the write, queue it, retry** (L68) | Availability without lying: be honest that it failed |
| `R = 1` with repair | **`R = 2` for user-visible reads** | Staleness is a support ticket |
| Global quorum | **LOCAL_QUORUM + async cross-region** | Multi-region latency matters (L91) |
| Cassandra-style tuning | **Consensus store (etcd) for metadata** | The data must be linearizable, not merely fresh |

## 13. When to use it

- **Any replicated data store where you must reason about staleness** — quorums give you the arithmetic to do it, instead of hope.
- **Whenever you need writes to serialise against each other** — `W > N/2`, and this is mandatory for locks, leases and ledgers.
- **When reads must not miss acknowledged writes**, including read-your-writes with session pinning.
- **As the sizing decision for every consensus cluster** you operate — majority, odd size, spread across failure domains.
- **Per data set, not per system** — the professional move is different W/R for session tokens, catalogue and inventory (§7.1).

## 14. When *not* to use it

- **When a single leader is simpler.** A quorum of 3 with read repair has more failure modes than one primary that fails over; if availability is adequate, take the simpler system.
- **When `W = N` or `R = N` is what you are proposing.** You have chosen the availability anti-pattern; use a majority instead.
- **When sloppy quorums are satisfying your consistency claim.** They are not (§4.6).
- **When the real requirement is linearizability.** Quorums do not give you a real-time order or a decision; only consensus does (L81).
- **When cross-region latency dominates.** A global quorum across 60 ms links is a design mistake; use locality plus an explicit consistency decision.

## 15. Common mistakes

1. **`W + R ≤ N`** — the arithmetic simply does not hold, and nothing warns you.
2. **Sloppy quorums counted as quorums** (§11.1).
3. **`W = N`** (and the rolling-restart outage it causes, §11.4).
4. **Even-sized clusters**, or 2-node clusters (§11.5).
5. **`R = 1` for user-visible reads** right after a write (§11.3).
6. **Wall-clock timestamps to resolve conflicts** instead of versions (L66).
7. **Assuming quorums give linearizability** — they give freshness, not a single order.
8. **Global quorums across regions** (§11.7).
9. **No anti-entropy**, so unread keys diverge forever (§11.6).
10. **Hints treated as durability** (§11.8).
11. **One consistency level for all data sets**, so either your sessions are slow or your inventory is wrong.
12. **Not alarming on remaining quorum margin** — one more failure removes the quorum and nobody knows.
13. **Forgotten replicas**: an old node rejoining with stale data and being counted in a quorum without a repair.
14. **Trusting per-node latency averages** when quorum latency is an order statistic of the tail (§9 step 3).

## 16. Interview perspective

**What is actually being tested.** Whether you can reason about **replication trade-offs with arithmetic** — `W + R > N`, majority for write serialisation, odd cluster sizes, and the difference between "fresh" and "linearizable". Strong candidates pick *different* consistency per data set and can justify each numerically. Weak candidates say "we use a majority" without being able to say what breaks at `R = 1` or why sloppy quorums are different.

**Answers that land well:**

- *"How do you configure replication for the inventory counter?"* — "N=5 across three AZs, W=3, R=3. W > N/2 because two concurrent decrements must intersect and detect each other — that is what stops overselling. It survives two node failures, commit latency is the 3rd-of-5 order statistic so the tail is actually better than a 3-node cluster with W=2, and the cost is about 4 ms p50 versus 0.9 ms for the session store, where stale reads are harmless."
- *"What does `W + R > N` actually guarantee?"* — "That any read quorum intersects any write quorum, so a read sees at least one copy of the latest acknowledged write. It does not guarantee linearizability, it does not give read-your-writes for a specific client, and it says nothing if you use sloppy quorums, because then the two sets are not drawn from the same N."
- *"Why odd clusters?"* — "Because a majority of an even N tolerates the same number of failures as N−1 while adding a node to the failure domain. A 2-node cluster tolerates zero failures — strictly worse than one node."
- *"Why does a bigger cluster sometimes have a better tail?"* — "Because a quorum waits for the k-th fastest, not the slowest. Waiting for 3 of 5 tolerates one pathological straggler; waiting for 2 of 3 does not. The tail collapses only at W = N, where you are waiting for the maximum."

**Common interview tasks:** choose N/W/R for a stated data set; compute availability for a quorum; explain sloppy quorums and hinted handoff; fix a read-your-writes bug; size a cluster across AZs; explain why quorums are not consensus.

**Red flags interviewers watch for:**

- `W + R > N` quoted without knowing when it is insufficient (`W > N/2` for writers).
- Sloppy quorums assumed to be safe.
- `W = N` proposed for "safety".
- No awareness of read-your-writes as a separate problem.
- Even-sized clusters, or 2-node clusters, chosen for "fault tolerance".
- Confusing quorums with consensus/linearizability.
- No numbers: no availability figure, no latency comparison, no failure tolerance.

## 17. Knowledge check

**Conceptual**

1. State the intersection condition and derive, for `N = 5`, two valid `(W, R)` pairs and one invalid pair, explaining what specifically goes wrong with the invalid one. Then explain why `W > N/2` is a *stronger* requirement than `W + R > N`, and which class of data needs the stronger one.
2. Explain sloppy quorums and hinted handoff, then explain precisely why a write acknowledged under a sloppy quorum can be invisible to a later read that satisfies `W + R > N`. State which data sets in a bookstore you would and would not allow this for, and why.
3. Explain why commit latency in a Raft cluster is the latency of the median replica rather than the maximum, and why this means a 5-node cluster can have a better p99.9 than a 3-node cluster. Then explain when that advantage disappears.

**Practical scenario**

4. BookNook runs a 5-node replicated store across three AZs holding: session tokens (write-heavy, read-heavy, staleness harmless), product catalogue (read-heavy, price must be fresh), and inventory counters (write-contended, overselling is a £50/incident business cost). For each data set, choose N, W, R and justify with failure costs. Then specify: the session guarantee you need after checkout and how you implement it, what happens during a 90-second partition isolating AZ-c under your choices, the alarms you would put on quorum margin, and the exact configuration change you would refuse to make under pressure during a flash sale, with the argument you would give.

**Calculation**

5. A 3-node cluster has per-node availability 99.95 % and `W = 2`. (a) Compute the probability the cluster can serve writes. (b) Recompute for `W = 3`. (c) Compute the monthly downtime difference and state which you would choose for a checkout path. (d) If per-node write latency is p50 3 ms and p99 20 ms, compute the approximate p99 of the quorum write for `W = 2` and for `W = 3`, and explain the difference in terms of order statistics.

6. A 5-node cluster has `W = 3` and `R = 2`. (a) Verify whether the intersection condition holds. (b) Verify whether concurrent writers are guaranteed to intersect, and state the consequence. (c) Determine the maximum number of node failures tolerated for writes and for reads. (d) Recompute for `N = 4` with `W = 3, R = 2` and explain why the even size is worse despite a larger quorum fraction.

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
  - [x] 14. Circuit breakers, bulkheads and graceful degradation (L77)
  - [x] 15. Distributed locks and leases (L78)
  - [x] 16. Leader election (L79)
  - [x] **17. Quorums (L80) ← you are here**
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

**NEXT LESSON: Phase 4, Topic 18 — Consensus and Raft**

---

---

# Lesson 81 — Consensus and Raft

*Phase 4, Topic 18*

## 1. Simple definition

**Consensus** is the problem of getting a group of machines to agree on **one value** — or, more usefully, on **one order of operations** — even though some of them crash, some are slow, and the network may split them into groups that cannot hear each other.

**Raft** is a consensus protocol designed to be understandable. It solves the problem the same way every consensus protocol does, with three ingredients:

1. **A leader**, elected by a majority, that decides the order of operations.
2. **A replicated log**, where the leader appends operations and copies them to followers, and an entry is **committed** once a majority has stored it.
3. **Terms** — monotonically increasing numbers — so that any two nodes can tell whose information is newer, and so a stale leader cannot pretend to still be in charge (L79 §4.2).

The result is **state machine replication**: if every node applies the same operations in the same order to the same deterministic state machine, every node ends in the same state. Consensus is therefore the foundation under everything that needs a single truth: leader election (L79), distributed locks (L78), configuration, service discovery, cluster metadata, and the commit decision in distributed databases.

Terms defined before use:

- **Log**: an ordered sequence of entries, each with a term and an index.
- **Commit index**: the highest log index known to be replicated on a majority. Entries at or below it will never be lost.
- **Apply**: handing a committed entry to the state machine (in order).
- **Log matching property**: if two logs contain an entry with the same index and term, all preceding entries are identical. This is the invariant that makes replication checkable in one step.
- **Election restriction**: a candidate may only win if its log is at least as up to date as a majority — which is what makes committed entries unlosable.
- **Linearizability**: the system behaves as if there were one copy and every operation took effect at a single instant between its invocation and response.
- **FLP impossibility**: in a fully asynchronous system with even one crash fault, no deterministic protocol can guarantee both safety and liveness. Practical systems escape it with **timeouts** (partial synchrony) and accept that liveness can fail during a partition — which is exactly what a majority requirement is.

The one sentence to carry out of this lesson: **consensus turns "everyone agrees" into "a majority has it, and the terms say which majority is current" — and that single idea is why distributed systems can have any truth at all.**

## 2. Why it exists

Every attempt to build correct coordination without consensus fails in the same way: **two nodes disagree, both are confident, and nothing in the system can decide**.

Recall the failures from the last three lessons, all of which are consensus problems in disguise:

| Problem | Without consensus | What consensus provides |
|---|---|---|
| Leader election (L79) | Two leaders, split-brain (§7.2 of L79) | A majority vote with terms |
| Distributed lock (L78) | Two holders after a pause | A linearizable compare-and-set as the arbiter |
| Quorum writes (L80) | Readers may see stale data; conflicting writes need manual resolution | A single agreed order, so no conflict exists |
| Cluster configuration | Node A thinks the cluster has 3 members, node B thinks 4 | A committed configuration change |

And the deepest one: **a database's commit decision.** When you write to a replicated database, "committed" must mean something that cannot later be undone. Only a majority-stored entry has that property, because any future leader must contain a majority too, and two majorities intersect (L80 §4.2). That intersection — not speed, not cleverness — is the whole mechanism.

The reason Raft matters specifically is its **explicit safety argument**. It is a protocol you can read in an afternoon and verify against four rules (§4.5), unlike Paxos, whose published description was famously hard to implement correctly. Raft's contribution was not a new theory but an implementable structure, and that is why etcd, Consul, CockroachDB, TiKV, Nomad and dozens of others use it.

The cost, stated plainly so the rest of the lesson is calibrated:

> Consensus gives you **linearizability at the speed of a disk fsync plus a majority round trip** — roughly 1–10 ms in one region, 60+ ms across regions. That is fast enough for metadata, configuration and coordination, and hopelessly too slow for a system's main data path at high volume (§4.9).

## 3. Real-world analogy

**The numbered minutes book, signed by a majority.**

A committee must maintain one authoritative record of decisions. The rules they adopt:

**One chairperson at a time.** The chair decides what goes in the book and in what order. Anyone can be chair, but the committee votes, and a **majority** must agree — so there is exactly one chair at any time (a minority cannot elect a rival, because it lacks the votes).

**Pages are numbered, and each page records who was chair.** Page 41, written by chair #7. The numbering matters because it gives an unambiguous order; the chair number matters because it settles "whose version of page 41 is real" without comparing content.

**A page becomes official when a majority has signed it.** Before that, it is a draft. This is the **commit rule**, and it is why the record cannot later be contradicted: any future chair needs a majority to be elected, and any two majorities share at least one member who signed the page — so a future chair cannot be ignorant of what was already official (§4.5).

**A new chair must accept all official pages.** The voters enforce this by refusing to elect anyone whose copy of the book is missing an official page — the **election restriction**. A candidate who has been out of the room is simply not electable until it catches up. This single rule is why committed decisions survive changes of chair.

**The chair must keep checking it is still chair.** Before answering a question about the book, a careful chair asks a majority "am I still chair?" because it may be a chair that has been replaced while it was out of the room (L79 §7.2's stale leader). Answering from memory alone is how a deposed chair gives a stale answer with total confidence — the **stale-read** bug (§4.8).

**The book grows forever, so it is periodically summarised.** A signed summary of pages 1–10,000, plus the ability to certify the summary's contents, lets latecomers catch up without reading every page — the **snapshot** (§4.7).

And the failure the rules cannot prevent: **if the committee cannot assemble a majority, nothing can be decided.** Two factions of three in a six-member committee can both hold meetings and neither can pass anything. That is not a bug to be fixed; it is the price of the guarantee, and it is why clusters are odd-sized and why a minority partition correctly stops accepting writes.

## 4. Detailed technical explanation

### 4.1 State machine replication: why a log is enough

```
  deterministic state machine M
  log: [e1, e2, e3, e4, ...]
  state(n) = M applied to e1..en  (in order)

  ⇒ if every replica applies the SAME sequence, every replica has the SAME state.
  ⇒ the entire problem collapses to agreeing on ONE thing: the sequence.
```

Two requirements make this work: the state machine must be **deterministic** (no `now()`, no random, no un-ordered concurrency inside it), and the log must be **totally ordered**. Everything a consensus protocol does — election, replication, commit — exists to protect the second requirement.

### 4.2 Terms, roles and the election

```
  state: FOLLOWER ──timeout──► CANDIDATE ──majority votes──► LEADER
              ▲                    │                            │
              └── discovers a higher term ─────────────────────┘

  term: monotonic. Every message carries it.
        a node receiving a lower term REFUSES; a higher term DEMOTES itself.
```

The election, step by step:

```
  1. A follower's election timeout fires (e.g. 150–300 ms, RANDOMISED).
  2. It becomes a candidate, increments term → T, votes for itself,
     and sends RequestVote{T, lastLogIndex, lastLogTerm} to everyone.
  3. A voter grants its vote if:
       a. it has not already voted in term T, AND
       b. the candidate's log is AT LEAST AS UP TO DATE:
          (lastLogTerm > mine) OR (lastLogTerm == mine AND lastLogIndex >= mine)
  4. A candidate with ⌊N/2⌋ + 1 votes becomes leader → sends heartbeats.
  5. Others receive a heartbeat with term ≥ theirs → become followers.
```

Rule 3b is the **election restriction**, and it is the reason committed data is never lost: a candidate missing a committed entry cannot be as up-to-date as a majority, so a majority will not vote for it.

**Randomised timeouts** matter more than they look: if all followers time out together, they split the vote, nobody wins, everyone waits, and repeats — a **split-vote livelock**. Randomisation makes repeated ties improbable, and a fresh random timeout per round makes it self-correcting.

### 4.3 Log replication and the log matching property

```
  leader appends entry {term: 8, index: 41, command: "set x=5"}
  sends AppendEntries{term: 8, prevLogIndex: 40, prevLogTerm: 8, entries: [...], leaderCommit: 39}

  follower accepts ONLY IF it has an entry at index 40 with term 8.
      ✓ accepts, appends, acks
      ✗ rejects → the leader decrements nextIndex for that follower and retries
        with an earlier prevLogIndex, rewinding until it finds the agreement point
        (which then truncates the follower's divergent suffix)
```

This one check gives the **log matching property**: if two logs share an entry at the same index and term, they are identical up to that index. You never need to compare logs end-to-end; one `(prevLogIndex, prevLogTerm)` comparison certifies the prefix.

**Commit**: the leader advances `commitIndex` to the highest `N` such that a majority of nodes have entry `N`, **and** entry `N` is from the leader's current term. Followers learn the commit index from the next `AppendEntries`. Only then do nodes **apply** the entry to the state machine.

### 4.4 What a client sees

```
  WRITE:  client → leader (or any node, which redirects/forwards)
          leader appends → replicates → fsync on a majority → commit → apply
          → respond to client.
          p50 in one region: ~1–3 ms (one fsync + one RTT).
          A leader does NOT wait for all followers — only a majority (L80 §4.3).

  READ:   three options, with different guarantees:
    1. Read from the leader with no check      → FAST, but a deposed leader
                                                 serves STALE data. Wrong.
    2. ReadIndex: leader confirms with a majority round trip that it is still
       leader, then serves locally            → LINEARIZABLE, costs one RTT.
    3. Leader lease: the leader trusts that no election can have happened
       within its lease window                → LINEARIZABLE, costs 0 RTT,
                                                 but depends on clock bounds.
  ⇒ option 2 or 3, never option 1, for anything that matters.
```

That third column is the most commonly missed detail in interviews: **a Raft cluster does not give linearizable reads for free.** You must ask for them, and pay for them.

### 4.5 The four safety rules, stated precisely

| Rule | Statement | Failure if violated |
|---|---|---|
| **Election safety** | At most one leader per term | Split-brain (two leaders appending) |
| **Leader append-only** | A leader never overwrites or deletes its own entries | Log divergence |
| **Log matching** | Same index + term ⇒ identical prefixes | Unrecoverable divergence |
| **Leader completeness** | A committed entry is present in the log of every future leader | **Committed data loss** — the worst outcome, and the reason for the election restriction and for "commit only current-term entries" |

The last row hides a classic implementation bug: a leader that commits an entry **from an older term** merely because it is on a majority can, after another election, have that entry overwritten. Raft's fix is subtle and worth memorising: a new leader commits an entry from its own term first (a **no-op** if there is nothing to write), and the older entries become committed indirectly by the log matching property.

### 4.6 Membership changes and snapshots

**Changing cluster membership** (3 → 5 nodes) is itself a consensus problem: two different configurations must never both have a majority simultaneously. Raft's answers:

- **Single-server changes** — add or remove one node at a time, so the old and new majorities always overlap.
- **Joint consensus** — a transitional state requiring majorities in *both* the old and new configurations.

**Log growth** is bounded by **snapshots**: a node takes a point-in-time image of its state machine plus the last included index/term, discards the log prefix, and sends the snapshot to lagging followers. The costs are real: snapshotting a large state machine is expensive (BookNook's etcd snapshot of 6 GB of metadata took 40 s and caused a visible write-latency spike), and a follower that falls too far behind must transfer the whole snapshot rather than a few entries.

### 4.7 What consensus is not

| Misconception | Reality |
|---|---|
| "Raft is a distributed database" | It is a replicated log. Every byte goes through one leader's disk and the network |
| "I can use it as a message queue" | You can, at 1–10 k ops/sec, with every message replicated and fsynced. Use Kafka (L71) |
| "It scales with more nodes" | More nodes = **larger** quorums = slower commits. Throughput is bounded by the leader |
| "Reads are linearizable" | Only with ReadIndex or a leader lease (§4.4) |
| "It survives any partition" | A minority correctly **stops** committing. Availability requires a majority |
| "3 nodes across 3 regions is fine" | Every commit pays a cross-region RTT: 60–200 ms, not 2 ms (§9 step 3) |

### 4.8 What it is genuinely for

The pattern to internalise: **consensus is for the small, critical, low-volume truth; everything else reads from something cheaper.**

```
  ✓ configuration, feature flags, cluster membership
  ✓ leader election / coordinator ownership (L79)
  ✓ distributed locks and leases (L78)
  ✓ service discovery registries (L87)
  ✓ shard/partition assignment maps (L72)
  ✓ "is this job already running?" state
  ✗ order events at 1,000/sec        → Kafka / outbox+Cdc (L85)
  ✗ session data                     → Redis, replicated
  ✗ product catalogue                → the application database
  ✗ large blobs                      → object storage
```

## 5. Request / data flow

A single write through a 3-node Raft cluster (`n1` leader, term 8), with a leader crash mid-replication.

```
 ── NORMAL ─────────────────────────────────────────────────────────────────
 t=0.0  client → n1: "set inventory:SKU-4471 = 498"
 t=0.1  n1 appends entry{term 8, index 41} to its log, fsyncs          (1.2 ms)
 t=1.3  n1 → n2, n3: AppendEntries{prev: (40, 8), entries: [41], leaderCommit: 40}
 t=1.9  n2 appends + fsync → ACK
 t=2.1  n3 appends + fsync → ACK
 t=2.1  n1 has 3 of 3 → commitIndex = 41 → applies to state machine
 t=2.2  n1 responds 200 OK to the client                    ← p50 ≈ 2.2 ms
 t=2.3  next AppendEntries tells n2, n3 leaderCommit = 41 → they apply.
        ⇒ the client's response means "durably on a majority", which is the
          only meaning of committed that survives a leader crash.

 ── LEADER CRASH BEFORE COMMIT ─────────────────────────────────────────────
 t=1.9  n2 acked index 41; n3 has not
 t=2.0  n1 crashes. commitIndex was never advanced. The client got NO response.
 t~2.2  n2 or n3's election timeout fires (150–300 ms randomised)
 t~2.4  n3 becomes candidate, term 9, requests votes
        n2's log has index 41 (uncommitted); n3's does not.
        ⇒ n2 votes NO (n3's log is not as up to date) — but n2 alone cannot win.
        ⇒ if n2 becomes candidate, it wins with n3's vote and index 41
          survives; if n3 becomes leader, index 41 is TRUNCATED.
        Both outcomes are CORRECT, because the client was never told "yes".
        ⇒ this is exactly why the client must be told the truth at commit time,
          and why "acknowledged" must never precede a majority.

 ── READ WITH READINDEX ────────────────────────────────────────────────────
 t=0.0  client → n1: "get inventory:SKU-4471"
 t=0.1  n1 sends heartbeats to a majority (a "read index" round trip)
 t=0.5  majority responds; n1 confirms it is still leader
 t=0.6  n1 serves the value locally                ← one extra RTT, guaranteed fresh
        ⇒ without the check, a deposed n1 would answer with a value it has not
          updated since it lost leadership (L79 §7.2).
```

## 6. ASCII diagram

A Raft log with terms, commit index, and the two-things-that-matter:

```
   index:      38    39    40    41    42    43
   term:        7     7     8     8     8     9
           ┌─────┬─────┬─────┬─────┬─────┬─────┐
   n1      │ a=1 │ b=2 │ c=3 │ d=4 │ e=5 │     │   ← leader (term 9)
           └─────┴─────┴─────┴─────┴─────┴─────┘
   n2      │ a=1 │ b=2 │ c=3 │ d=4 │     │     │   ← 1 behind
   n3      │ a=1 │ b=2 │ c=3 │     │     │     │   ← 2 behind (slow disk)
                    ▲
                    └── commitIndex = 40 : a majority (n1+n2) have index 40.
                        Everything ≤ 40 is PERMANENT. Index 41+ may be lost.

   LOG MATCHING: an AppendEntries for index 42 carries prevLogIndex=41,
   prevLogTerm=8. n3 has nothing at 41 → REJECT → leader rewinds to 40 → n3
   accepts 41..42 in one batch. One comparison certifies the whole prefix.
```

The election, and why the up-to-date check matters:

```
  term 8 leader dies. Both n2 (has 41) and n3 (has 40) time out.

   RANDOMISED TIMEOUTS: n3 fires first (its 180 ms beat n2's 240 ms)

   n3: "vote for me, term 9, my last entry is (40, term 8)"
   n2: "my last entry is (41, term 8) — yours is NOT as up to date → NO"
   n1: "granted"                       ⇒ n3 has 2 of 3 votes → LEADER
   ⇒ n3 truncates the uncommitted index 41. Correct: nobody was told it committed.

   IF n3 had had a committed entry that n2 lacked, the voters would have
   refused it — which is precisely the mechanism that makes committed
   entries unlosable. Without this check, an out-of-date node could become
   leader and erase acknowledged writes.
```

## 7. Detailed example

### 7.1 What BookNook actually puts in etcd — and what it must not

BookNook runs a 3-node etcd cluster (one per AZ) and uses it as the consensus substrate for:

| Data | Volume | Why consensus | Why not a database |
|---|---|---|---|
| Partition assignment map | 40 keys, ~12 writes/min | Every consumer must see the same owner | Needs linearizable reads at failover |
| Leader/coordinator leases | 3 leases, ~20 writes/min | Split-brain prevention (L79) | Latency of a lease matters |
| Feature flags | 60 keys, ~5 writes/min | Every service must agree | A stale flag is a real incident |
| Service registry | 120 keys, ~400 writes/min | Discovery must be consistent (L87) | Wrong endpoint = failed request |
| **Total** | **~450 writes/min ≈ 7.5/sec** | | **vs etcd's ~1–10 k/s capacity** |

The number to sit with: **BookNook uses 0.1 % of its consensus cluster's capacity.** That is the correct ratio. Consensus is a scarce, expensive resource that should carry the *pointers* to the truth, not the truth itself.

The counter-example from the same company's history: an earlier design put **order status events** into etcd (`/orders/90210/status`) so that "every service sees the same status". At 1,000 order events/sec peak it worked for three weeks, then the cluster hit its 8 GB quota during a flash sale, went read-only, and **every service discovery lookup failed**, which took down the entire site — from a data path that had nothing to do with discovery.

```
  measured: etcd with 3 nodes on SSD, single-key writes, no batching
      p50 2.8 ms   p99 12 ms   p99.9 40 ms   ≈ 1,400 writes/sec sustained
  with batching (the client's default), ~10,000 writes/sec on the same kit.
  BookNook's order path needed 1,000/sec CONTINUOUSLY: 71 % of the sustained
  un-batched ceiling, with no headroom, and every write replicated 3× and
  fsynced 3×.
  ⇒ the fix was Kafka for order events (replayable, partitioned, cheap) and
    etcd for the assignment map that says WHO consumes which partition.
```

### 7.2 The stale read that shipped a wrong price

BookNook's pricing service read a price multiplier from etcd and served it to the checkout path. During a leader election (a 3.5-second window), the old leader — which had been partitioned but not crashed — continued answering reads from local state, which was **4 minutes stale**. During those 4 minutes, 1,900 checkouts used a multiplier of `1.00` instead of `0.85` (a sale that had been committed).

```
  root cause: the service used a plain read (Raft option 1 of §4.4) instead of
              ReadIndex or a leader-lease read.
  cost: 1,900 orders × 15 % overcharge = £11,400 refunded, plus the support load.
  fix:  client config `--consistency=strong` (a ReadIndex read), measured at
        +1.3 ms p50; or a local cache with a 5 s TTL and a lease-based read.
```

The lesson generalises beyond etcd: **"it came from the consistent cluster" is not the same as "the read was consistent."** Consistency is a property of the *operation you issued*, not of the system's name.

## 8. Code or configuration example

```python
# ============================================================================
# Raft, reduced to the two messages that matter. Illustrative only —
# never implement Raft to run production coordination; use etcd/Consul.
# ============================================================================

def request_vote(cand_term, cand_last_index, cand_last_term) -> bool:
    if cand_term < self.term:
        return False                                # stale candidate
    if cand_term > self.term:
        self.term, self.voted_for = cand_term, None # adopt the newer term
    if self.voted_for not in (None, cand_id):
        return False                                # already voted this term
    # THE ELECTION RESTRICTION (§4.2 rule 3b): protects committed entries.
    up_to_date = (cand_last_term > self.last_log_term() or
                  (cand_last_term == self.last_log_term() and
                   cand_last_index >= self.last_log_index()))
    if not up_to_date:
        return False
    self.voted_for = cand_id
    self.reset_election_timer()
    return True


def append_entries(term, prev_idx, prev_term, entries, leader_commit) -> bool:
    if term < self.term:
        return False                                # reject stale leader
    self.reset_election_timer()                     # leader is alive
    # THE LOG MATCHING CHECK (§4.3): one comparison certifies the prefix.
    if self.log_index < prev_idx or self.term_at(prev_idx) != prev_term:
        return False                                # leader will rewind and retry
    # Truncate any divergent suffix, then append.
    del self.log[prev_idx + 1:]
    self.log.extend(entries)
    self.commit_index = min(leader_commit, self.last_log_index())
    self.apply_committed()                          # state machine, IN ORDER
    return True


# --- On the leader, after a majority ack ---------------------------------
def maybe_advance_commit(self):
    for n in range(self.last_log_index(), self.commit_index, -1):
        if self.count_replicated(n) >= self.quorum():
            # SAFETY (§4.5): only commit an entry from the CURRENT term.
            # Committing an old-term entry on a bare majority can later be
            # undone by a new leader. Old entries commit indirectly.
            if self.term_at(n) == self.term:
                self.commit_index = n
            break
```

```bash
# --- etcd: the operations you actually use for coordination --------------
etcdctl put /booknook/assign/p3 "consumer-C"            # a linearizable write
etcdctl get /booknook/assign/p3 --consistency=s         # ReadIndex read ✓
etcdctl get /booknook/assign/p3 --consistency=l         # local, may be STALE ✗

# Compare-and-set = a lock, a lease, a leader election, all at once:
etcdctl txn <<'EOF'
value("/booknook/lease/reindex") = ""
put /booknook/lease/reindex "worker-3"
EOF
# ⇒ succeeds only if the key was absent: this IS the distributed lock (L78).

# A lease is the TTL mechanism behind leader election (L79):
etcdctl lease grant 15                 # lease 694d8b... , 15 s TTL
etcdctl put /booknook/lead "worker-3" --lease=694d8b...
etcdctl lease keep-alive 694d8b...     # renew every ~5 s
```

```yaml
# --- Cluster layout: the decisions that are hard to change later ---------
etcd:
  nodes: 3                    # tolerates 1 failure; 5 for 2 failures
  placement: one per AZ       # never two in one failure domain
  # 3 nodes across 3 AZs in one region: commit p50 ~3 ms, p99 ~12 ms
  # 3 nodes across 3 REGIONS: every commit pays a cross-region RTT
  #   → p50 60–200 ms. Do NOT do this for a latency-sensitive control plane.
  disk: local SSD             # fsync latency IS commit latency
  snapshot_count: 10000       # wal entries between snapshots
  quota_backend_bytes: 8589934592      # 8 GiB default: hit it and the
                                       # cluster goes READ-ONLY, which breaks
                                       # discovery and therefore everything
```

```yaml
# --- Alarms: consensus failures look like "everything is slow" -----------
alerts:
  - expr: etcd_server_has_leader == 0 for 1m
    severity: page        # no leader: writes blocked, elections failing

  - expr: rate(etcd_server_leader_changes_seen_total[15m]) > 3
    severity: page        # churn: flapping leadership (§11.1)

  - expr: histogram_quantile(0.99, etcd_disk_wal_fsync_duration_seconds) > 0.025
    severity: page        # the disk is the commit path; 25 ms fsync = slow quorum

  - expr: etcd_mvcc_db_total_size_in_bytes / etcd_server_quota_backend_bytes > 0.8
    severity: page        # approaching READ-ONLY: this took a site down (§7.1)

  - expr: etcd_network_peer_round_trip_time_seconds{quantile="0.99"} > 0.05
    severity: warn        # cross-AZ/region latency inflates every commit
```

## 9. Numerical example

BookNook's 3-node etcd cluster, one node per AZ, local SSD.

**Step 1 — what a commit costs, in the only three terms that matter.**

```
  commit latency ≈ fsync + (majority round trip)
      fsync (SSD, default)        p50 1.2 ms, p99 8 ms
      intra-AZ RTT                p50 0.3 ms, p99 1.5 ms
  ⇒ unbatched single-key write    p50 ≈ 2.8 ms, p99 ≈ 12 ms
     (measured; consistent with the model)

  THE SAME CLUSTER ACROSS REGIONS (RTT 60 ms):
      p50 ≈ 1.2 + 60 = 61 ms  ⇒ 22× worse, for identical hardware.
  ⇒ consensus latency is a PLACEMENT decision first and a code decision second.
```

**Step 2 — throughput, and why batching is the whole game.**

```
  ceiling without batching = 1 / commit_latency = 1 / 0.0028 ≈ 357 writes/sec
      (each write waits for its own fsync and RTT)
  with batching (the client pipelines, the server fsyncs once per batch):
      100 entries per fsync → 100 × 357 ≈ 35,700 writes/sec theoretically;
      measured ~10,000/sec on this kit.
  ⇒ a factor of 28 comes from BATCHING alone, not from more nodes.
  ⇒ and note the direction: adding nodes makes this SLOWER (bigger quorums),
    which is why "scale out the consensus cluster" is not a thing.
```

**Step 3 — how much of it BookNook uses, and the safety margin that matters.**

```
  BookNook's coordination load:     7.5 writes/sec
  measured sustained capacity:      ~10,000 writes/sec (batched)
  ⇒ utilisation 0.075 %. The cluster is idle 99.9 % of the time.
  ⇒ That is CORRECT. The cluster exists to be fast and correct at tiny volume,
    not to be busy.

  The dangerous version (the §7.1 incident):
      order events at 1,000/sec = 10 % of capacity sustained, 3× fsync per
      event, 2 KB per entry ⇒ 1,000 × 2 KB = 2 MB/sec, and
      8 GB quota / 2 MB/sec = 4,000 s ≈ 67 minutes to fill the database.
      At peak (1,000/sec) with no compaction keeping up → READ-ONLY cluster
      → discovery fails → site down. The numbers were knowable in advance.
```

**Step 4 — failover: what it costs and where the time goes.**

```
  election timeout 150–300 ms randomised
  detection: the leader's death is noticed after one timeout   ~225 ms mean
  election:  one round trip for votes                          ~1 ms intra-AZ
  ⇒ writes pause ~150–300 ms during a leader change.
  Measured at BookNook: p99 write latency 12 ms normally, 340 ms in the
  minute containing an election; 3 elections in a 24 h window (one deploy,
  one disk, one injected fault).
  ⇒ cost per day ≈ 3 × 0.3 s = 0.9 s of write unavailability = 0.001 %.
  ⇒ consensus failover is CHEAP compared with warm-up-dominated systems
    (L79 §4.5) — which is a genuine advantage of Raft-based control planes.
```

**Step 5 — why 5 nodes can be slower than 3, and when to use each.**

```
  N=3, quorum 2:  commit = the 2nd fastest of 3.
  N=5, quorum 3:  commit = the 3rd fastest of 5 — more slow nodes to wait on,
                  and 5× the replication traffic from the leader.
  measured on identical hardware, same 3 AZs (5 nodes = 2,2,1 per AZ):
      N=3: p50 2.8 ms, p99 12 ms, 1 failure tolerated
      N=5: p50 3.4 ms, p99 15 ms, 2 failures tolerated
  ⇒ 5 nodes buy fault tolerance, not speed. Choose on failure tolerance
    (and on correlated AZ failure, which 5-across-3-AZ does not actually
    fix — losing an AZ with 2 replicas in it leaves 3 of 5, still a quorum ✓).
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **Consensus for coordination state** | Linearizability; a single truth | 1–10 ms per write; operational complexity | Configuration, locks, leases, discovery — always |
| **Consensus for bulk data** | Nothing you cannot get elsewhere | Quota risk, leader bottleneck, 3× writes | Never (§7.1) |
| **Raft (etcd/Consul)** | Understandable, battle-tested, good tooling | Another cluster to run | The default choice |
| **Paxos-family (ZooKeeper)** | Mature, strong ecosystem | Harder to reason about; more moving parts | Existing ZooKeeper estate |
| **3 nodes, one region** | ~3 ms commits | Tolerates 1 failure | Most control planes |
| **5 nodes, one region** | Tolerates 2 failures | ~20 % slower, 5× replication | Larger blast-radius tolerance |
| **3 nodes across regions** | Region-failure tolerance | 60–200 ms commits | Only if the control plane can be slow |
| **ReadIndex reads** | Linearizable reads | +1 RTT | Any read that affects money or correctness |
| **Leader-lease reads** | Linearizable reads at 0 RTT | Depends on clock bounds | High read rates (L66 skew caveat) |
| **Local/stale reads** | Fastest | A deposed leader lies (§7.2) | Caches with bounded staleness and a lease |
| **Snapshots on** | Bounded log | Latency spikes while snapshotting | Always; schedule them |

## 11. Failure scenarios

**11.1 — Leadership churn from a slow disk.** etcd's fsync p99 rose to 40 ms; heartbeats missed their deadlines; leaders changed 11 times in 15 minutes; writes were unavailable for ~40 % of that window. **Symptom:** `leader_changes_seen_total` climbing, with high `wal_fsync_duration`. **Fix:** local SSD (never network storage), alert on fsync p99, and remember that **disk latency is commit latency** — a consensus cluster on a slow volume is a consensus cluster that flaps.

**11.2 — The read-only cluster.** The etcd DB hit its 8 GB quota during a flash sale (§7.1) and the cluster went read-only. Service discovery failed, so every request failed — a total outage from a metadata store. **Symptom:** `etcd_mvcc_db_total_size` at the quota, `alarm NOSPACE`. **Fix:** alarm at 80 % of quota; raise the quota deliberately; never put high-volume data in the cluster; compact and defragment on a schedule.

**11.3 — Stale reads from a deposed leader** (§7.2). 1,900 checkouts with a stale price multiplier. **Symptom:** money wrong during an election window, perfect consistency otherwise. **Fix:** ReadIndex (`--consistency=s`) or a leader lease for any correctness-relevant read.

**11.4 — Split vote livelock.** Four candidates with identical timeouts repeatedly split the vote; no leader was elected for 90 seconds. **Symptom:** repeated elections with no winner, healing spontaneously. **Fix:** randomised timeouts (Raft's design already does this — a reimplementation that "simplified" them caused this), and cluster sizes where a majority is achievable.

**11.5 — The four-node cluster.** A 4-node etcd cluster: quorum 3, tolerating one failure — and any *two* failures (which a single AZ outage can cause) stopped all writes. **Symptom:** an AZ blip takes down the control plane. **Fix:** odd sizes, one node per failure domain, 3 or 5.

**11.6 — Snapshot bloat.** A 6 GB state machine snapshot took 40 s and generated a write-latency spike; the follower that needed it took 6 minutes to catch up and, meanwhile, WAL grew, risking the quota. **Symptom:** periodic latency spikes correlated with `snapshot_count` cycles. **Fix:** smaller state machines (the whole lesson), tuned `snapshot_catch_up_entries`, and defragmentation.

**11.7 — Cross-region consensus.** A 3-region etcd cluster used for a latency-sensitive lock: every acquisition cost 65 ms, and the lock-heavy path capped at ~15 ops/sec per client. **Symptom:** uniformly high latency with no errors. **Fix:** keep consensus regional; replicate cross-region asynchronously and treat the remote copy as a read model (L91).

**11.8 — The client that bypassed the leader.** A service wrote directly to a **follower** with a plain HTTP PUT to its local IP; the follower rejected or forwarded inconsistently, and some writes silently went to a node that was later removed. **Symptom:** writes that "succeeded" but never appear. **Fix:** always use the cluster client (which finds the leader) — never address consensus members directly for writes.

## 12. Alternatives

| Instead of Raft | Consider | When |
|---|---|---|
| Raft / etcd | **ZooKeeper (Zab)** | Existing ZK estate; you need its watch/ephemeral model |
| Raft | **A single primary + async replicas** | You can tolerate a small RPO (L92) and want simplicity |
| Raft | **Quorum reads/writes in a Dynamo store** (L80) | You need freshness, not a single order |
| Raft | **Database with its own replication** | Your state is already in one database; use its failover |
| Raft | **Kubernetes Lease objects** | You only need leader election inside a cluster (L79) |
| Consensus log | **Kafka** (L71) | The volume is high, ordering is per-key, and replay matters |
| Consensus log | **Outbox + CDC** (L85) | You need reliable event publication from a database |
| Consensus | **CRDTs** | Concurrent updates can be merged without a single order |

## 13. When to use it

- **Cluster configuration and membership** — the canonical use.
- **Leader election and leases** (L79), including partition/shard ownership (L72).
- **Distributed locks where correctness matters** (L78) — etcd's CAS is the arbiter.
- **Service discovery** (L87) — a small, consistent registry.
- **Feature flags and dynamic config** whose staleness causes incidents.
- **The commit decision of a replicated database** — where consensus is inside the product rather than something you operate.
- **Small, critical, low-volume state — the "pointers to the truth"** (§4.8).

## 14. When *not* to use it

- **As a message queue or event bus.** Use Kafka (L71); the numbers are two to three orders of magnitude apart (§9 step 3).
- **As an application database.** No queries, no indexes, 8 GB default quota, everything through one leader.
- **For large values or blobs.** etcd's default request limit is 1.5 MiB — store a pointer, not the object.
- **Across regions for a latency-sensitive path** (§11.7).
- **When a single primary with failover suffices** — consensus adds a cluster to operate for a guarantee you may not need.
- **When you only need freshness, not order.** Quorums (L80) are cheaper and simpler.

## 15. Common mistakes

1. **Using etcd/Consul as a data store** — the quota incident (§11.2, §7.1).
2. **Plain reads from a leader** without ReadIndex or a lease, so a deposed leader lies (§11.3).
3. **Slow or networked disks**, so fsync latency causes churn (§11.1).
4. **Even-sized or 2-node clusters** (§11.5).
5. **Consensus across regions** for a latency-sensitive control plane (§11.7).
6. **Committing an old-term entry on a bare majority** — the classic Raft implementation bug (§4.5).
7. **Removing randomised election timeouts** as "simplification", causing split votes (§11.4).
8. **No quota/size alarm**, so the cluster goes read-only at the worst moment.
9. **Addressing consensus members directly** instead of through the client (§11.8).
10. **Ignoring log growth and snapshots**, so catch-up times explode (§11.6).
11. **Assuming more nodes means more throughput.** The leader is the bottleneck.
12. **Assuming a consensus cluster makes every read linearizable.** It does not (§4.4).
13. **Storing per-request state in the cluster** — sessions, counters, caches.
14. **No alarm on `leader_changes_seen_total`**, so flapping looks like "it's just slow today".

## 16. Interview perspective

**What is actually being tested.** Whether you can say **what consensus buys and what it costs**, and whether you know the two facts most candidates miss: committed reads are not free, and consensus is for metadata rather than volume. Strong candidates describe state machine replication, the majority commit rule, terms, and the election restriction — then place it correctly in an architecture. Weak candidates treat "use Raft/etcd" as a universal answer.

**Answers that land well:**

- *"Where would you use consensus in this design?"* — "For the small things that must not disagree: the partition-assignment map, leases, feature flags, the service registry. About 7 writes/sec against a capacity of thousands, so the cluster is always fast. Order events and catalogue data never go near it — they go through Kafka and the application database, because consensus pays a fsync plus a majority round trip per write and replicates everything three times."
- *"How does Raft decide an entry is committed?"* — "When a majority has it stored, and — the subtle part — only if the entry is from the leader's current term; older entries become committed indirectly through the log matching property. That rule plus the election restriction is what makes a committed entry impossible to lose, because any future leader must contain a majority and two majorities intersect."
- *"Are reads linearizable?"* — "Only if you ask. A plain read from the leader can be served by a deposed leader that is 4 minutes stale — we lost £11,400 that way. ReadIndex costs one round trip; a leader lease costs nothing but depends on bounded clock skew."
- *"Why not more nodes for throughput?"* — "Throughput comes from batching, not from nodes. More nodes means bigger quorums, more replication traffic, and slightly slower commits. The leader's disk and its batch size are the ceiling."

**Common interview tasks:** explain how Raft commits; design a control plane and choose which data belongs in it; fix a stale-read bug; pick cluster size and placement with latency numbers; explain why a minority partition must stop accepting writes; compare etcd, ZooKeeper and a quorum store for a given requirement.

**Red flags interviewers watch for:**

- "Use Kafka for this — it's like Raft with partitions" (or the reverse: "just put it in etcd").
- No mention of the majority commit rule or terms.
- Assuming reads are linearizable by default.
- Proposing a consensus cluster on network storage or across regions for a hot path.
- Even or 2-node clusters.
- No numbers: no commit latency, no capacity, no cluster size justification.
- Not knowing that a minority cannot serve writes — and calling that a bug.

## 17. Knowledge check

**Conceptual**

1. Explain state machine replication and why it reduces consensus to "agree on one sequence". Then explain the majority commit rule and why an entry stored on a majority can never be lost by a future leader — naming the rule that guarantees it.
2. Explain why a plain read served by a Raft leader may be stale, and describe the two mechanisms that make reads linearizable, including the cost of each. Relate this to the stale-price incident in §7.2.
3. Explain why adding nodes to a consensus cluster does not increase throughput, and describe what does. Give the numbers from §9 step 2 that show where the capacity actually comes from.

**Practical scenario**

4. BookNook is designing the control plane for its event platform: 40 partitions to assign, 3 coordinator leases, 60 feature flags, 120 service-registry entries, plus a requirement to store "the last 24 hours of order status events" (1,000/sec, 2 KB each). Choose the consensus cluster's size and placement with justification, specify exactly which data sets go in it and which do not, explain what you tell the engineer who wants the order status events included (with arithmetic), design the read path for the feature flags given that a stale flag caused an incident, and list the five alarms you would install with thresholds.

**Calculation**

5. A 3-node consensus cluster on local SSD has fsync p50 1.2 ms, p99 8 ms, and intra-AZ RTT 0.3 ms. (a) Compute unbatched commit p50 and p99. (b) Compute the unbatched write ceiling. (c) Compute the ceiling with 80 entries per batch, and state the measured order of magnitude you would expect. (d) Recompute (a) if the cluster is spread across three regions with 60 ms RTT, and state the practical consequence for a lock used on every request.

6. An etcd cluster has an 8 GiB quota and is receiving 1,000 writes/sec of 2 KB entries. (a) Compute the ingest rate in MB/sec. (b) Compute how long until the quota is reached, ignoring compaction. (c) With compaction deleting everything older than 5 minutes, compute the steady-state size and state whether it is safe. (d) Recompute (b) with a 500-byte average entry and explain why the conclusion about using consensus for this data does not change.

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
  - [x] 14. Circuit breakers, bulkheads and graceful degradation (L77)
  - [x] 15. Distributed locks and leases (L78)
  - [x] 16. Leader election (L79)
  - [x] 17. Quorums (L80)
  - [x] **18. Consensus and Raft (L81) ← you are here**
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

**NEXT LESSON: Phase 4, Topic 19 — Distributed transactions**

---

---

# Lesson 82 — Distributed Transactions

*Phase 4, Topic 19*

## 1. Simple definition

A **distributed transaction** is a unit of work that must be **atomic** — all of it happens or none of it — across **more than one independent resource**: two databases, a database and a message broker, or two services with private storage.

Within one database, atomicity is the database's job (L41). Across resources there is no shared engine, no shared lock manager and no shared log, so atomicity must be *arranged*. There are only four honest ways to do it, and this lesson is mostly about choosing between them:

| Approach | Atomicity mechanism | Cost |
|---|---|---|
| **Avoid it** — one service owns the data | Local ACID | Requires design discipline |
| **Atomic commit protocol** (2PC, L83) | A coordinator + a prepare phase | Blocking, locks held across the network, in-doubt states |
| **Compensation** (saga, L84) | Undo steps in reverse | Visible intermediate states; not isolated |
| **Outbox + idempotency** (L74, L85) | Local atomic write + at-least-once delivery | Eventual consistency only |

Terms defined before use:

- **Resource manager (RM)**: something that can commit or roll back — a database, a broker, a queue.
- **Coordinator**: the component that decides commit or abort across RMs (L83).
- **Atomic commit**: the guarantee that all RMs commit, or all abort, even if some crash.
- **Isolation**: the property that concurrent transactions do not observe each other's partial work. Distributed across services, **this is the property you almost always lose**.
- **Compensating action**: a business-level undo (refund, release, cancel) — *not* a rollback; it is a new forward action.
- **In-doubt transaction**: a transaction whose coordinator died after some RMs prepared but before the decision — those RMs hold locks until someone resolves it.

The one sentence to carry out of this lesson: **distributed atomicity is available, distributed isolation is not — and most systems that claim to offer "distributed transactions" are quietly offering you atomicity with compensation, so you must design for the intermediate states either way.**

## 2. Why it exists

Because a business operation that means one thing is usually implemented by several services that each mean something smaller.

BookNook's checkout touches four places:

```
  1. orders-db      INSERT the order
  2. inventory-db   DECREMENT stock for each line
  3. payment        AUTHORISE the card (a third party, no XA, no transaction)
  4. event bus      PUBLISH OrderPlaced
```

If step 1 succeeds and step 2 fails, the customer has an order for stock that does not exist. If 2 succeeds and 3 fails, the stock is reserved for an order that will never be paid. If 3 succeeds and 4 fails, the customer is charged and the warehouse never ships — that is the **ghost order** of L85 §2.

Every one of these is a real incident BookNook has had, and each one argues for atomicity. But look at what atomicity would cost here:

- The payment provider is a **third party**. It offers an authorise/capture/void API and nothing else. There is no XA transaction to join, and there never will be. **So the fully atomic version of this operation is not available at any price.**
- Steps 1 and 2 are in **separate databases** owned by separate teams. Joining them in one transaction means a coordinator that both teams' deploys can break, plus locks held across a network boundary — the exact thing L83 warns about.

That is the real shape of the problem in almost every system: **the operations that most need atomicity are precisely the ones that span a third party or an organisational boundary, where atomicity is least available.**

So the practical version of the topic is not "how do I do distributed transactions" but "**which correctness mechanism is the cheapest one that satisfies the business requirement**". BookNook's answer, for checkout, is: local transaction for (1) + outbox for (4), a *reservation* for (2) that can be released, an authorisation for (3) that can be voided, and a saga (L84) that ties them together with compensations. Not atomic — *correct*, which is a different and more useful property.

## 3. Real-world analogy

**Buying a house, versus buying a coffee.**

The **escrow** process is an atomic commit protocol, and it is used precisely because the two parties cannot trust each other and cannot act simultaneously:

1. The buyer's money goes to a neutral third party (**prepare**: funds committed but not transferred).
2. The seller's deed goes to the same third party (**prepare** again).
3. The escrow agent, once both are in place, transfers both (**commit**).
4. If either party fails to deliver, the agent returns what it holds and nothing changes (**abort**).

Every property of 2PC is visible: a **coordinator** (the agent), **prepared state** (money in escrow — real resources are locked and unavailable for other uses), a **blocking** window (your money is in escrow and you cannot spend it while the agent waits), and the **in-doubt** case (the agent has a heart attack; the money is in escrow and nobody can release it until a court appoints a replacement).

Now the contrast. A **coffee shop** does not use escrow. You pay, then you receive. If the machine breaks after you have paid, the shop **refunds** you — a compensating action. That is a **saga**: forward steps, with compensating steps in reverse if something fails. It has visible intermediate states (you are, briefly, a person who has paid and has no coffee), and that is fine because the amounts are small, the duration short, and "refund" is a legitimate business operation that already exists.

The design principle the two share: **use escrow when a partial state is unacceptable and the amount or risk justifies blocking; use compensation when a partial state is tolerable for seconds and the undo is a real business verb.** Nobody escrows a coffee; nobody refunds a house.

And the analogy captures the lesson's hardest constraint: **there is no escrow agent for a card payment.** The card network offers "authorise now, capture later, void if needed" — which is exactly a saga's forward and compensating steps, and exactly not an atomic commit. The mechanism available to you is set by the counterparties, not by your preferences.

## 4. Detailed technical explanation

### 4.1 What "atomic across resources" actually requires

Three distinct problems, often conflated:

```
  (a) ATOMIC COMMIT — all RMs commit or none do, despite crashes.
      ⇒ needs a protocol (2PC/3PC) or consensus (L81). Achievable.

  (b) ISOLATION — no observer sees a partial state.
      ⇒ needs a global lock manager or serializable scheduling across RMs.
        NOT achievable in practice across independent services.

  (c) DURABILITY — the decision survives loss of any node.
      ⇒ needs the decision written to replicated, fsynced storage. Achievable.
```

Most real "distributed transactions" give you (a) and (c) and **not** (b). That is why a saga's intermediate state is visible to other readers even though each step is atomic: a customer watching the orders page sees "payment pending" because the isolation was never there.

### 4.2 Why (b) is the one that cannot be bought

Within one database, isolation is enforced by a lock manager and MVCC over **one** set of pages. Across services there is no shared page set, so there is nothing to lock. You can approximate it with:

- **Distributed locks** (L78) — correct but slow, and unsafe without fencing.
- **Single-partition routing** (L72) — put everything one transaction touches in one ownership domain.
- **Optimistic version checks** — detect conflicts at write time rather than preventing them.

None of these is general. This is the single most useful thing to know before designing a cross-service write: **you are choosing between an atomic-but-not-isolated outcome (saga) and a slow, fragile, isolated one (2PC + locks)** — and the third option, *restructure so it is one service's data*, is almost always the best.

### 4.3 The four mechanisms, with their costs made explicit

| Mechanism | Atomicity | Isolation | Availability during partition | Latency | Where it exists |
|---|---|---|---|---|---|
| **Single resource** (one DB) | ✓ local | ✓ local | Depends on the DB | 1 commit | Always — the target |
| **2PC / XA** (L83) | ✓ | ✗ (partial) | **Poor**: participants block | 2 RTT + 2 fsync | XA, Postgres prepared txns, some MQ integrations |
| **Consensus transaction** (Spanner, CockroachDB) | ✓ | ✓ (serializable) | Requires a majority | 1–2 consensus rounds (~7–100 ms) | Spanner, CockroachDB, TiKV, YugabyteDB |
| **Saga** (L84) | ✗ (compensated) | ✗ | Good (local steps) | N sequential steps | Any microservice system |
| **Outbox** (L85) | ✗ for the cross-resource effect | ✗ | Good | eventual | Any service with a DB + broker |

The row that most surprises people is **2PC's availability**: while a prepared transaction is unresolved, every participant holds locks on the affected rows. If the coordinator dies, those locks persist until an operator resolves them — which is why 2PC has a reputation for taking systems down in a way that is difficult to reason about (L83 §11).

### 4.4 Long transactions, and the throughput cliff

Locks held for the duration of a distributed transaction mean throughput is bounded by `1 / lock_hold_time` for the contended rows:

```
  lock hold time = local work + 2PC coordination + (any slow participant)
      optimistic (same DB, 2 ms):        500 ops/sec on a hot row
      local 2PC, 2 RTT intra-AZ (6 ms):  166 ops/sec
      2PC with an external participant (150 ms — e.g. a payment API):
                                         6.6 ops/sec   ← a business outage
```

BookNook measured exactly this while prototyping 2PC between `orders-db` and a payment gateway that supported XA: at **1,200 checkouts/min (20/sec)**, the hot-row lock queue produced a p99 of **9.4 seconds** and the prototype was abandoned. Not because 2PC is wrong, but because the participant's latency was 150 ms and the lock was on the row every checkout needed.

### 4.5 Where true distributed transactions do exist

Worth knowing, because "it is impossible" is the wrong lesson:

| System | Mechanism | Guarantee | Cost |
|---|---|---|---|
| **Spanner** | Paxos replication + TrueTime commit-wait | Externally consistent (linearizable), serializable | Commit-wait ~7 ms; needs atomic clocks/GPS |
| **CockroachDB / TiKV** | Raft + a Percolator-style commit protocol | Serializable across shards | Multiple Raft rounds per transaction |
| **Postgres `PREPARE TRANSACTION`** | 2PC within one installation (multi-database / FDW) | Atomic across the prepared branches | Blocks; in-doubt if the coordinator dies |
| **XA (JTA)** | 2PC across heterogeneous RMs | Atomic | The full 2PC cost profile |
| **Kafka transactions** | Transactional producer + consumer offsets | Atomic **within Kafka** | Does not span your database |
| **MongoDB multi-document** | Snapshot isolation inside a replica set | Multi-document atomicity | Single replica-set scope; you pay for cross-shard versions |

The pattern: **the systems that genuinely offer cross-shard serializability either control the clock (Spanner) or run consensus on every commit (CockroachDB) — and they charge consensus latency per transaction.** You can buy the guarantee; you cannot buy it cheaply.

### 4.6 The decision framework

```
  Does the operation span more than one resource?
    │
    ├─ NO  → use the database's local transaction. Done. (Target this.)
    │
    └─ YES
        │
        ├─ Can the resources be merged into one ownership domain?
        │     → do that. One service owning the data is not a compromise;
        │       it is the correct architecture for a transactional invariant.
        │
        ├─ Is a partial state unacceptable AND the operation short AND all
        │  participants XA-capable AND low volume?
        │     → 2PC / consensus transaction (L83)
        │
        ├─ Is there a real business verb that undoes each step
        │  (refund, release, cancel)?
        │     → SAGA (L84). This is the common case.
        │
        └─ Is the requirement only "the event is published iff the write
           happened"?
              → OUTBOX (L85)
```

And the override rule: **if any participant is a third party, the answer is always saga + idempotency.** You cannot join an external system's transaction, so it will be compensated, and the compensation must be idempotent (L74) because you will retry it.

## 5. Request / data flow

The same checkout, through three different mechanisms, so the shapes are comparable.

```
 ── (a) ONE DATABASE (the target design) ───────────────────────────────────
 BEGIN;
   INSERT orders ...;  UPDATE inventory SET qty=qty-1 WHERE sku=... AND qty>0;
   INSERT outbox(OrderPlaced) ...;
 COMMIT;                       ← atomic, isolated, 3.1 ms. One round trip.
 THEN: payment authorisation is a SAGA STEP AFTER this (see (c)), because
       the payment provider cannot be in the transaction.

 ── (b) 2PC ACROSS TWO DATABASES ───────────────────────────────────────────
 coordinator
   ├─ PREPARE on orders-db    → "ready"  (rows LOCKED, decision unknown)
   ├─ PREPARE on inventory-db → "ready"  (rows LOCKED)
   │   ← if the coordinator dies HERE, both databases hold locks, and every
   │     checkout touching those rows BLOCKS until an operator resolves it.
   └─ COMMIT PREPARED on both → done
 cost: 2 × RTT + 2 × fsync ≈ 7 ms;  availability: both RMs must be up at
 prepare time, and the coordinator must survive.

 ── (c) SAGA (what BookNook actually does) ─────────────────────────────────
 T1 orders-db:     INSERT order (status=pending) + outbox    ✓ committed
 T2 inventory:     RESERVE stock (reservation row, TTL 15 min) ✓
 T3 payment:       AUTHORISE card                            ✓ (id 8fa2)
 T4 orders-db:     UPDATE status=confirmed                   ✓
 T5 fulfilment:    request shipment                          ✗ FAILS
   ⇒ compensate in reverse: T3 VOID the authorisation,
     T2 release the reservation, T1 status=cancelled.
   ⇒ intermediate states were VISIBLE for ~2 s ("payment pending"), which is
     acceptable for an order and NOT acceptable for a bank transfer.
   ⇒ every step and every compensation must be idempotent (L74), because a
     crash in the middle means the compensation is retried.
```

## 6. ASCII diagram

The four mechanisms and what each guarantees:

```
                        spans >1 resource?
                                │
              ┌─────────────────┴─────────────────┐
             NO                                  YES
              │                                    │
   ┌──────────▼─────────┐            ┌─────────────▼──────────────┐
   │ LOCAL TRANSACTION  │            │ can you collapse it into   │
   │ atomic + isolated  │            │ ONE owner?                 │
   │ 1 commit, ~3 ms    │            └──────┬─────────────┬───────┘
   └────────────────────┘                  YES            NO
                                            │              │
                              ┌─────────────▼───┐   ┌──────▼─────────┐
                              │ MERGE THE DATA  │   │ short + XA +   │
                              │ (best outcome)  │   │ low volume +   │
                              └─────────────────┘   │ no 3rd party?  │
                                                     └───┬───────┬────┘
                                                        YES       NO
                                                         │         │
                                            ┌────────────▼──┐  ┌───▼──────────┐
                                            │ 2PC / CONSENSUS│  │ SAGA         │
                                            │ atomic, blocked│  │ compensated, │
                                            │ not isolated   │  │ not atomic   │
                                            └───────────────┘  └──────────────┘
```

The throughput cliff that decides most of these choices:

```
  ops/sec on a contended row
    500 │█                          local transaction (2 ms)
        │█
    166 │█   █                      2PC, intra-AZ (6 ms)
        │█   █
     20 │█   █   █                  required rate (BookNook checkout)
        │█   █   █
    6.6 │█   █   █   █              2PC with a 150 ms external participant
        │█   █   █   █
      1 └┴───┴───┴───┴───────────
        local  2PC  2PC+remote  ← the lock is the constraint, not the CPU

  ⇒ a distributed transaction does not fail loudly under load. It QUEUES,
    and the queue appears as a p99 of 9.4 s at 20 checkouts/sec.
```

## 7. Detailed example

### 7.1 BookNook's three attempts at checkout

**Attempt 1 — 2PC with the payment gateway's XA interface.** Prototyped, measured, abandoned (§4.4): p99 9.4 s at 20 checkouts/sec, because the lock was held across a 150 ms external call. Also: the gateway's XA recovery was undocumented, so an in-doubt transaction would have needed vendor support to resolve.

**Attempt 2 — choreographed saga with no coordination.** Each service listened for events and did its part. It worked until a payment failure left 340 orders in `pending` forever, because no component was responsible for the *whole* outcome. **Choreography without an owner is not a saga; it is an unowned distributed state machine.**

**Attempt 3 — the current design: an orchestrated saga** (L84) with:

```
  - explicit state machine per order: pending → reserved → authorised →
    confirmed → shipped | cancelled | failed
  - a durable state store (the orders table), so a crash resumes
  - compensations: void_authorisation, release_reservation, cancel_order
  - every step and compensation idempotent, keyed on (order_id, step)
  - a reconciler that scans for orders stuck > 10 min in a transient state
  - the outbox for every event that leaves the service (L85)
```

Measured outcome vs attempt 1:

```
                     attempt 1 (2PC)   attempt 3 (saga)
  checkout p50           4.1 s              310 ms
  checkout p99           9.4 s              780 ms
  max throughput         6.6/sec            1,400/sec
  stuck orders            0                  0.004 % (reconciler-cleaned)
  visible intermediate    none (blocked)     ~2 s ("processing")
  incidents (3 months)    2 lock-related     1 (a badly-written compensation)
```

The trade-off, stated honestly: BookNook accepted **a 2-second window in which an order is visibly "processing"**, in exchange for **200× the throughput and a system that degrades instead of deadlocking**. Nobody escrows a coffee.

### 7.2 The one place they did merge the databases

The inventory and orders data started in two databases owned by two teams. The transactional invariant — "never sell stock you do not have *for an order you accepted*" — crossed that boundary, and every cross-boundary design was either slow (2PC) or eventually consistent in a way customers could see (saga).

The decision, made after a year of incidents: **merge them.** One database, one schema, one owner, with `orders` and `inventory` tables in the same installation so that the reservation is a local transaction.

```
  cost: 5 weeks of migration, a shared on-call rota, one deploy pipeline
  benefit: the checkout invariant became a 40-character SQL WHERE clause
           (UPDATE inventory SET qty=qty-1 WHERE sku=$1 AND qty>0 → 0 rows
            means sold out — L78 §7.1), instead of a saga step with a
           compensation and a reconciler.
  incidents after: 0 oversells in 14 months, versus 3 before.
```

The lesson that generalises: **the cheapest distributed transaction is the one you removed by changing a boundary.** "Merge the services" is unfashionable advice and frequently the correct engineering answer to a distributed-transaction problem, because it converts a hard problem into a solved one.

## 8. Code or configuration example

```sql
-- ── 2PC within one PostgreSQL installation (PREPARE TRANSACTION) ──────────
-- Only valid across databases/FDW in ONE cluster, and it is BLOCKING:
-- prepared transactions hold locks until resolved, and survive a crash.
BEGIN;
  UPDATE inventory SET qty = qty - 1 WHERE sku = 'SKU-4471' AND qty > 0;
PREPARE TRANSACTION 'txn-order-90210';       -- ← locks held, decision unknown

-- later, from the coordinator (which must durably record the decision FIRST):
COMMIT PREPARED 'txn-order-90210';
-- or: ROLLBACK PREPARED 'txn-order-90210';

-- ⚠ Operational essentials:
--   max_prepared_transactions must be > 0 (default is 0 = disabled!)
--   monitor pg_prepared_xacts — any row older than a few minutes is an
--   INCIDENT: it holds locks and blocks vacuum (L58 §11.4).
SELECT gid, prepared, owner, database FROM pg_prepared_xacts ORDER BY prepared;
```

```python
# ── An orchestrated saga step: idempotent, resumable, compensable ──────────
# This is the mechanism to use when a participant cannot join a transaction.

@dataclass
class OrderSaga:
    order_id: str
    state: str           # pending|reserved|authorised|confirmed|shipped|failed
    steps_done: list

def advance(order_id):
    saga = store.load(order_id)          # durable state: the saga IS the table
    for step in STEPS[saga.state:]:
        try:
            # IDEMPOTENCY KEY = (order_id, step) — L74. A crash re-runs the
            # step; the participant recognises the key and returns the prior
            # result instead of acting twice.
            result = step.execute(order_id, idem_key=f"{order_id}:{step.name}")
        except Retryable as e:
            return schedule_retry(order_id, e)      # L75's ladder
        except Permanent as e:
            compensate(saga)                        # undo in reverse (L84)
            return
        store.record_step(order_id, step.name, result)   # commit, then next

def compensate(saga):
    for step in reversed(saga.steps_done):
        # NOT a rollback: a NEW forward action that undoes the effect.
        # Must itself be idempotent — it will be retried.
        step.compensate(saga.order_id, idem_key=f"{saga.order_id}:{step.name}:undo")
    store.set_state(saga.order_id, "failed")
```

```python
# ── The reconciliation that every saga needs (and nobody builds first) ─────
# A saga can get stuck: a crash after the effect and before the record, or a
# compensation that keeps failing. Something must sweep.
def reconcile():
    for s in store.find_stuck(older_than_minutes=10):
        actual = query_each_participant(s.order_id)   # ask, do not guess (L65)
        if all_done(actual) and not s.state_is_terminal():
            log.warning("saga stuck after success; advancing", extra=s.__dict__)
            store.force_advance(s, actual)
        elif none_done(actual):
            store.set_state(s.order_id, "failed")
        else:
            alert("saga_needs_human", s)             # partial, ambiguous, rare
```

```yaml
# ── The alarms. Distributed transactions fail by BLOCKING, not erroring. ──
alerts:
  - expr: pg_prepared_xacts_age_seconds > 300        # 2PC in doubt
    severity: page
  - expr: saga_stuck_total{older_than="10m"} > 0
    severity: page
  - expr: saga_compensation_failures_total > 0
    severity: page        # effects that cannot be undone are the worst state
  - expr: checkout_p99 > 2 * baseline
    severity: page        # a saga step is slow: it serialises the whole flow
```

## 9. Numerical example

BookNook checkout: 20 checkouts/sec peak, 1,200/min.

**Step 1 — why 2PC with an external participant is not merely slower, but disqualifying.**

```
  external participant (payment gateway) p50 150 ms, p99 900 ms.
  2PC holds the PREPARED state — and therefore the locks — for the whole
  prepare→commit window, which includes the external call:
      lock hold ≈ 150 ms (p50) / 900 ms (p99)
  throughput on the contended inventory row = 1 / lock_hold
      p50:  1 / 0.150 =  6.6 ops/sec
      p99:  1 / 0.900 =  1.1 ops/sec
  required: 20/sec
  ⇒ 3× over the p50 ceiling and 18× over the p99 ceiling. The design cannot
    meet its own requirement by construction. No amount of extra hardware
    fixes a lock held across a 150 ms network call.
```

**Step 2 — the local-transaction comparison, and the cost of the invariant.**

```
  single-DB conditional UPDATE (L78 §7.1):
      lock hold = 2 ms (row lock, no network)
      ceiling   = 500 ops/sec on that row; sharded 10 ways = 5,000 ops/sec
  measured p99 for the whole checkout: 780 ms (dominated by the payment
      gateway's own latency, which is unavoidable and NOT held under a lock)
  ⇒ the correct pattern: keep the invariant inside one database, and let the
    slow external call happen OUTSIDE any transaction (with a reservation
    that holds the stock, and an authorisation that can be voided).
```

**Step 3 — compounding failure rates: why sagas need retries and compensations, not optimism.**

```
  5 steps, each 99.5 % successful:
      P(all succeed first time) = 0.995^5 = 97.5 %
      ⇒ 2.5 % of checkouts enter a compensation path.
  with 1 retry per step (L75 ladder, 90 % of failures recover):
      effective per-step success = 0.995 + 0.005 × 0.9 = 0.9995
      P(all succeed) = 0.9995^5 = 99.75 %
      ⇒ compensation path drops to 0.25 % = 3,000/day at 1.2 M/month.
  ⇒ compensations are a ROUTINE path, not an exception. At 3,000/day they
    must be fast, idempotent, tested, and monitored — treating them as
    "error handling" is how they become the incident.
```

**Step 4 — what the saga's visibility costs, in support load.**

```
  intermediate state visible for p50 1.4 s, p99 3.2 s.
  customers who see "processing" and contact support: 0.02 % of orders
      → 240/month at 40,000 orders/month.
  cost: 240 × £2.10 (avg handling) = £504/month — versus 2PC's £0 in
      support but a p99 of 9.4 s and a hard throughput ceiling of 6.6/sec.
  ⇒ £504/month to run 200× the throughput. This is the trade, and it is
    worth stating in exactly these terms in a design review.
```

**Step 5 — the in-doubt window, which is the reason 2PC has its reputation.**

```
  coordinator prepares A and B, then crashes.
  locks held on both; every checkout touching those rows blocks.
  recovery requires: the coordinator's decision log to survive, or an
      operator to decide manually (usually by inspecting both RMs).
  realistic resolution time: 10–40 minutes (paged engineer, runbook,
      policy decision). During it, the affected rows are UNUSABLE.
  ⇒ if those rows are hot (an inventory row for the top-selling SKU), the
    outage is site-wide for that product for 10–40 minutes.
  ⇒ mitigation: raise the coordinator's durability (a decision log on
    replicated storage), shorten the prepare window, and NEVER prepare a
    transaction that includes a third-party call.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **Merge into one resource** | Real ACID, 1 commit, no compensation | Bigger blast radius, shared deploy, team coupling | A transactional invariant spans the data — usually the right answer (§7.2) |
| **2PC (internal, all XA)** | Atomicity, no visible partial state | Locks held across the network; blocking; in-doubt | Short operations, low volume, all participants reliable |
| **Consensus DB (Spanner/Cockroach)** | Serializable **and** atomic across shards | Commit latency per transaction; vendor/complexity | You can pay consensus latency per transaction |
| **Saga with orchestration** | High throughput, degrades gracefully, resumable | Visible intermediate states; compensation code; reconciler needed | The common case (§7.1) |
| **Saga with choreography** | No central coordinator | No owner of the whole outcome; unwinding is hard to reason about | Rarely; and only with an explicit state machine per aggregate |
| **Outbox** (L85) | Reliable event publication with the write | Eventual consistency only | The requirement is "publish iff committed" |
| **Do nothing (best effort)** | Simplest | Silent partial states, found by customers | Never for money, stock or shipping |

## 11. Failure scenarios

**11.1 — The prepared transaction nobody resolved.** A coordinator pod was OOM-killed between `PREPARE` and `COMMIT PREPARED`. The prepared transaction held a row lock on the top-selling SKU; every checkout for it blocked; **p99 for that product was 60 s** until an engineer found `pg_prepared_xacts` 25 minutes later. **Symptom:** one hot row blocking, no errors. **Fix:** alarm on prepared-transaction age, keep a decision log, and never prepare across a slow call.

**11.2 — The saga with no owner.** A choreographed flow left 340 orders permanently `pending` after a payment failure, because no service tracked the whole outcome (§7.1 attempt 2). **Symptom:** orders that never progress and never fail — silently stuck. **Fix:** an orchestrator with a durable state machine, plus a reconciler that sweeps stuck sagas.

**11.3 — The non-idempotent compensation.** A `void_authorisation` call timed out, was retried, and voided a *second* authorisation that belonged to the customer's retried checkout — refunding a valid order. **Symptom:** customers receiving money back for orders that shipped. **Fix:** idempotency keys on every step and compensation (L74), scoped per order.

**11.4 — Partial application through a missing step.** A new "apply loyalty points" step was added to the flow *after* the order was confirmed; when it failed, the order stayed confirmed and the points were lost with only a log line. **Symptom:** a silent business inconsistency with no error. **Fix:** the saga's steps and compensations are a contract — adding one means defining its failure semantics and its reconciler coverage.

**11.5 — The 2PC that took out the whole site.** A XA transaction between the orders database and the message broker deadlocked under load; the broker's prepared branch held; the orders database's connection pool filled with sessions waiting on prepared transactions; **the entire application** ran out of connections. **Symptom:** total outage traced to one XA flow. **Fix:** separate connection pools per workload, a timeout on the whole distributed transaction, and (better) don't 2PC to a broker — use the outbox (L85).

**11.6 — The "distributed transaction" that was a lock.** A team implemented cross-service atomicity by taking a distributed lock (L78) around all the calls. During a pause the lock was held for 40 s; every checkout queued behind it; p99 hit 40 s. **Symptom:** identical to a lock-contention incident. **Fix:** if you have to serialise anyway, do it in one database; a lock across services is the worst of both mechanisms.

**11.7 — Isolation assumed.** A reporting query read the `orders` table mid-saga and counted 1,400 "pending" orders as revenue, then published a weekly figure that was 4 % high. **Symptom:** numbers that change when re-run. **Fix:** expose only terminal states to analytics (a read model, L85's CDC), and document that intermediate states are visible by design.

**11.8 — The reconciler nobody wrote.** A saga compensation failed permanently (the payment provider had already voided the authorisation out-of-band). The saga sat in `compensating` forever; the stock reservation expired but the order stayed open. **Symptom:** a growing count of orders in a non-terminal state. **Fix:** every transient state needs a sweeper and an alarm on its count.

## 12. Alternatives

| Instead of a distributed transaction | Consider | When |
|---|---|---|
| 2PC | **Merge the data into one owner** (§7.2) | A transactional invariant spans them |
| 2PC | **Saga with compensation** (L84) | A third party is involved, or volume is high |
| Saga | **Outbox + idempotent consumers** (L85, L74) | The requirement is event publication, not multi-step undo |
| Saga | **Single-partition routing** (L72) | Everything one transaction touches can be keyed together |
| 2PC across DB and broker | **Outbox** (L85) | Always — 2PC to a broker is the classic 11.5 outage |
| Atomic multi-step | **Reserve → confirm → release** | The work is a resource hold (stock, seats, credit) |
| Distributed lock | **Conditional UPDATE in one DB** (L78 §7.1) | The contention is on a row |

## 13. When to use it

- **2PC** when: every participant supports it, the operation is short (milliseconds), volume is low, and a visible partial state is unacceptable — e.g. an internal transfer between two schemas in the same installation.
- **A consensus database** when you need serializable cross-shard transactions and can pay ~10–100 ms per commit.
- **A saga** when a third party is involved, or volume is high, or the steps are minutes long.
- **Reserve/confirm** when the operation is fundamentally a hold on a resource.
- **An outbox** whenever the requirement is "publish the event iff the write committed".

## 14. When *not* to use it

- **2PC across a network call to a third party** — the lock is held for the third party's latency; disqualifying (§9 step 1).
- **2PC to a message broker** — use the outbox; the failure mode is a total connection-pool exhaustion (§11.5).
- **As a substitute for a boundary decision.** If a transactional invariant spans two services, the answer is usually to merge them, not to add a coordinator.
- **Choreographed sagas with no owner** (§11.2).
- **When the business actually tolerates eventual consistency.** A "recently viewed" list does not need a saga; it needs a queue.
- **As a performance mechanism.** Every distributed transaction is slower than a local one; they exist for correctness, not speed.

## 15. Common mistakes

1. **Using 2PC with an external participant**, holding locks across a network call (§9 step 1).
2. **2PC to a broker** instead of an outbox (§11.5).
3. **No decision log**, so a coordinator crash leaves in-doubt transactions (§11.1).
4. **No alarm on prepared-transaction age**, so a hot row blocks silently.
5. **Sagas without an owner** — choreography with no state machine (§11.2).
6. **Non-idempotent compensations** (§11.3).
7. **No reconciler**, so stuck sagas accumulate (§11.8).
8. **Assuming isolation** across services, then letting analytics read intermediate states (§11.7).
9. **Treating compensations as error handling** rather than as a routine path (§9 step 3: 3,000/day).
10. **Adding a step without defining its failure and compensation semantics** (§11.4).
11. **Implementing atomicity with a distributed lock** across services (§11.6).
12. **Holding a transaction open across a human decision or a batch** — locks for minutes.
13. **Not documenting which states are visible** to which readers.
14. **Believing "we use a distributed database, so we have distributed transactions"** — check whether your writes actually span shards, and at what commit cost.

## 16. Interview perspective

**What is actually being tested.** Whether you know that **atomicity and isolation are different problems**, that isolation is the one you lose across services, and that the cheapest distributed transaction is the one eliminated by a boundary decision. Strong candidates enumerate the four mechanisms and pick by requirement, with numbers. Weak candidates say "use 2PC" or "use a saga" as a matter of taste.

**Answers that land well:**

- *"How do you make this checkout atomic across four services?"* — "I'd first ask whether it needs to be. The invariant that matters — never sell stock we don't have — I'd put in one database with a conditional UPDATE, and merge those two services if that is what it takes. The payment authorisation cannot be in any transaction, so it is a reservation plus a voidable authorisation, orchestrated as a saga with idempotent steps and compensations, plus a reconciler for stuck orders. 2PC is off the table because it would hold a row lock across a 150 ms external call: that caps throughput at 6.6/sec against a requirement of 20."
- *"What is the difference between 2PC and a saga?"* — "2PC gives atomicity with blocking and locks held across the network, and it is not isolated either — the intermediate state is just invisible instead of absent. A saga gives no atomicity at all: it gives compensations, which are new forward actions, and a visible intermediate state. 2PC fails by blocking; a saga fails by needing reconciliation."
- *"Which is more correct?"* — "Neither; they fail differently. 2PC is correct when partial states are unacceptable and the operation is short. A saga is correct when a real business verb undoes each step and a few seconds of 'processing' is acceptable. The professional answer is that this is a requirements question, not a preference."
- *"What does Spanner give you that a saga doesn't?"* — "Serializable atomicity across shards, by paying consensus latency per commit and using TrueTime commit-wait — about 7 ms of deliberate waiting — to make commit order match real time. You can buy it, but you are buying consensus in the write path."

**Common interview tasks:** decide between 2PC, saga and service merge; design compensations and a reconciler; explain why 2PC holds locks; identify the isolation you lose; handle a third party that supports only authorise/capture/void; compute the throughput ceiling imposed by lock hold time.

**Red flags interviewers watch for:**

- "Use 2PC" with no mention of blocking or in-doubt transactions.
- "Use a saga" with no compensations, no idempotency, no reconciler.
- Not distinguishing atomicity from isolation.
- Proposing 2PC across a third-party API call.
- No awareness that a partial state is *visible* during a saga.
- No numbers: no lock hold time, no throughput ceiling, no compensation rate.
- Missing the option of restructuring the boundaries entirely.

## 17. Knowledge check

**Conceptual**

1. Distinguish atomic commit, isolation and durability, and state which of the three cannot be provided across independent services at any reasonable cost. Then explain why a saga has visible intermediate states even though each individual step is atomic.
2. Compare 2PC and a saga on availability, failure mode, and the kind of correctness each provides. Explain why 2PC's failure mode (blocking on locks) can be worse than a saga's (needing reconciliation), using the throughput arithmetic from §9 step 1.
3. Explain why a saga's compensations must be idempotent, and why a compensation is not a rollback. Then explain why a reconciler is mandatory rather than optional.

**Practical scenario**

4. BookNook's checkout must: create an order, reserve stock, authorise a card with a third-party gateway (p50 150 ms, p99 900 ms, supports authorise/capture/void only), confirm the order, and publish `OrderPlaced`. Peak is 20 checkouts/sec. Design the complete solution: which parts live in one database and why, the saga's states and transitions, every step and its compensation with idempotency key, the failure handling for each step including the ambiguous timeout case, the reconciler's rules, the metrics and alarms with thresholds, what the customer sees during each intermediate state, and the arithmetic that rules out 2PC.

**Calculation**

5. A 2PC transaction participates in two databases, each with fsync 1.5 ms, and an intra-AZ RTT of 0.4 ms, and it holds a lock on one contended row for the entire operation. (a) Compute the coordination overhead per transaction. (b) Compute the maximum throughput on that row. (c) Recompute if one participant is an external API with p50 120 ms. (d) State the maximum throughput, and what fraction of a 25 ops/sec requirement it can serve.

6. A 5-step saga has per-step success rates of 99.5 %, 99.0 %, 99.5 %, 98.5 % and 99.9 %, and each step has a retry that recovers 85 % of failures. (a) Compute the probability all steps succeed on the first attempt. (b) Compute it with the retries applied. (c) Compute the number of compensation runs per day at 50,000 checkouts/day. (d) If each compensation takes 400 ms of engineering-tested code and the failure of one leaves an order in a non-terminal state, compute the expected number of stuck orders per day, and state the alarm threshold you would set.

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
  - [x] 14. Circuit breakers, bulkheads and graceful degradation (L77)
  - [x] 15. Distributed locks and leases (L78)
  - [x] 16. Leader election (L79)
  - [x] 17. Quorums (L80)
  - [x] 18. Consensus and Raft (L81)
  - [x] **19. Distributed transactions (L82) ← you are here**
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

**NEXT LESSON: Phase 4, Topic 20 — Two-phase commit**

---

---

# Lesson 83 — Two-Phase Commit

*Phase 4, Topic 20*

## 1. Simple definition

**Two-phase commit (2PC)** is an **atomic commit protocol**: a coordinator drives a set of participants through two phases so that either all of them commit or all of them abort, even if some of them — or the coordinator — crash.

```
  PHASE 1 — PREPARE (the vote)
      coordinator → each participant: "can you commit?"
      participant: does all the work, writes a PREPARED record to its log,
                   takes and HOLDS all its locks, replies "yes" or "no".
      ⇒ after voting YES, a participant may NOT change its mind on its own.

  PHASE 2 — COMMIT / ABORT (the decision)
      if ALL voted yes:  coordinator writes COMMIT to its log, then tells
                         everyone to commit; they release locks and ack.
      if ANY voted no:   coordinator writes ABORT to its log, then tells
                         everyone to abort; they undo and release locks.
```

The single most important property, and the source of everything good and bad about 2PC:

> **After a participant votes YES, it is prepared: it has made an irrevocable promise to obey the coordinator's decision, whatever that decision is. It cannot unilaterally abort — because another participant may already have been told to commit.**

That promise is why 2PC is *correct*, and it is also why 2PC is *blocking*: if the coordinator dies after collecting the votes but before recording and delivering the decision, every participant must sit with its locks held and its fate unknown until the coordinator returns or an operator decides.

Terms defined before use:

- **Coordinator** (also *transaction manager*): the component that collects votes and records the decision.
- **Participant / resource manager (RM)**: a database or broker that can commit or abort.
- **Prepared state**: the participant has done all its work, holds its locks, and has durably promised to obey.
- **In-doubt transaction**: a prepared transaction whose decision has not yet been received.
- **Presumed abort**: the convention that if a coordinator's log has no commit record, the transaction aborted — which lets a recovering coordinator clean up cheaply.
- **Heuristic decision**: a participant that resolves an in-doubt transaction *by guessing* (because the coordinator is presumed lost) — a violation of atomicity, reported as an error, e.g. `XA_HEURHAZ`.
- **Termination protocol**: the procedure by which participants determine the decision after a coordinator failure.

The one sentence to carry out of this lesson: **2PC trades availability for atomicity by making participants hold locks and promise obedience — so it is correct, and it blocks, and the blocking is not a bug to be tuned away but the price of the guarantee.**

## 2. Why it exists

Consider the naive alternative: each participant commits independently as soon as it is ready.

```
  orders-db      commits at t=10 ms   ✓ (order exists)
  inventory-db   fails at t=12 ms     ✗ (stock never decremented)
  ⇒ an order for stock that was never taken. No error reached the client.
```

Or the opposite ordering, which is worse: inventory commits, the order does not, and BookNook has decremented stock for 1,400 orders that nobody placed.

The reason 2PC exists is that **a decision made after seeing all the votes requires a moment of durability**: each participant must be able to promise "I will commit if you say so" *before* knowing whether anyone else failed. Without that promise, the coordinator cannot make a decision that everyone will honour, because some participant might have independently rolled back after seeing a local problem.

The precise problem it solves:

```
  Without a prepare phase, a participant can fail at any moment until it
  commits, so the coordinator cannot know whether a "yes" from everyone is
  still true when it sends the commit. The window between "I am ready" and
  "I have committed" is where inconsistency lives.

  ⇒ 2PC closes that window by making "ready" a DURABLE, IRREVOCABLE state:
    once prepared, the participant has given up the right to abort, so the
    coordinator's decision is guaranteed to be executable.
```

And here is why this lesson spends as much time on the costs as the mechanism: **the price is a lock held from the prepare until the decision — across a network, across a crash, across an operator's investigation.** In L82 §9 step 1 the arithmetic showed that this caps throughput at `1 / (external_call_latency)`, which is 6.6 ops/sec for a 150 ms third party. That is not a tuning problem; it is the definition of the protocol.

## 3. Real-world analogy

**The wedding ceremony.**

Phase 1 is the officiant asking, "if anyone knows why these two should not be joined, speak now." Both parties have already made their arrangements — the guests are seated, the rings are in place, the licence is signed (the **prepared** state). Everyone is committed in the sense that matters: **nobody can leave without an announcement**.

Phase 2 is "I now pronounce you married." Once spoken, it is done: there is no rollback, and the rings stay on.

Now the failure that makes 2PC what it is. Suppose the officiant **faints** between the vows and the pronouncement. What can the couple do?

- They cannot simply **walk away and call it off**, because the officiant may have already signed the register in the vestry — which is precisely the state a prepared participant is in. Acting unilaterally risks being married *and* having denied it. (In protocol terms: a participant cannot abort after voting yes.)
- Everyone must **wait** until the officiant recovers, or until someone with authority checks the register and decides. That wait, with the guests standing there, is **blocking**, and it is exactly the in-doubt state.
- If the officiant's register is **lost forever**, the wedding party must make a **heuristic** decision — and one of them will later be wrong. That is `XA_HEURHAZ`, and it is why the register (the coordinator's decision log) must be durable and replicated.

Compare the **3PC** version of the ceremony: you announce "we are now *about* to pronounce you, does anyone object to pronouncing?" — one extra round so that a fainting officiant at any single point still leaves a definite answer. It works beautifully until the guests are split between two rooms and cannot hear each other, at which point the extra round buys nothing. That is why 3PC exists on paper and rarely in production (§4.5).

The analogy's final lesson is the useful one: **the ceremony is expensive and slow compared with simply signing a document together.** You hold a ceremony when the stakes justify the ritual — a marriage, a transfer between banks — and you sign a receipt when they do not. Choosing 2PC is exactly that judgement.

## 4. Detailed technical explanation

### 4.1 The protocol, with every log write shown

```
  COORDINATOR                          P1                P2
  ───────────                          ──                ──
  BEGIN; do local work
  write BEGIN to log (optional)
  send PREPARE ───────────────────────►│                 │
                                       ├─ do all work
                                       ├─ write PREPARED
                                       │  + locks held    │
                                       │  (fsync)         │
                                       │◄─── VOTE YES ────┤
                          send PREPARE ──────────────────►│
                                                          ├─ write PREPARED (fsync)
                                                          │◄── VOTE YES ────
  ┌───────────────────────────────┐
  │ DECISION POINT                │
  │ write COMMIT to log (fsync),  │  ← ★ the instant the outcome is decided
  │ typically REPLICATED          │
  └───────────────────────────────┘
  send COMMIT ────────────────────────►│
                                       ├─ commit, release locks
                                       │── ACK ──────────►│
                          send COMMIT ──────────────────►│
                                                          ├─ commit, release locks
                                                          │── ACK ────────────
  write COMPLETE to log, forget the transaction
```

Three details that carry the whole correctness argument:

1. **The decision is written to the coordinator's log *before* any commit message is sent.** If the coordinator crashes after that write, it can recover the decision and re-deliver it. If it crashes *before* the write, presumed abort lets everyone abort safely (no participant can have committed, because no commit message was sent).
2. **A participant's PREPARED record is fsynced before it votes yes.** A vote that could be lost in a crash is not a promise.
3. **Locks are held from the vote until the decision.** That is what makes the intermediate state invisible to other transactions — and it is the entire cost.

### 4.2 Why a participant cannot abort unilaterally (the crux)

Suppose P1 votes yes and then times out waiting for the decision, and decides "I will just abort to be safe":

```
  P1 aborts and releases its locks.
  Meanwhile the coordinator (alive, just slow) had already told P2 to COMMIT.
  ⇒ P2 committed, P1 aborted. ATOMICITY VIOLATED, permanently, with no
    mechanism to repair it — one database has the order, the other does not.
```

So the prepared state must be genuinely irrevocable, and a participant that is in doubt must **block** rather than guess. The only way out is for the coordinator's decision to be discovered:

```
  participant in doubt  → asks other participants
                          ├─ any participant is COMMITTED → commit (a commit
                          │   decision implies everyone was told to commit)
                          ├─ all are PREPARED → nobody knows; keep waiting
                          └─ any ABORTED      → abort
  ⇒ the termination protocol. Note that it CANNOT resolve the case where
    everyone is merely prepared: the decision exists only in the lost log.
```

That last case is the residual blocking window, and it is irreducible in 2PC: **the decision lives in exactly one place, and if that place is lost, a human (or a heuristic) must decide.**

### 4.3 Presumed abort, and why it reduces cost

With **presumed abort**, participants do not need to durably record their own vote in the common case:

```
  if a recovering coordinator finds no COMMIT record → the transaction was
  never decided → ABORT is safe (nobody was ever told to commit).
  ⇒ fewer forced writes in the happy path; a cleaner recovery.
  The alternative, "presumed commit", requires recording the intent to commit
  in phase 1 — more writes, but fewer messages on abort-heavy workloads.
```

This is a *performance* choice, not a correctness one; both are atomic.

### 4.4 Optimisations that are safe, and one that is not

| Optimisation | What it saves | Safe? |
|---|---|---|
| **Read-only vote** | A participant that only read can vote "read-only" and forget the transaction entirely | ✓ Safe — standard in XA |
| **Last-agent optimisation** | With one participant, skip phase 1 and commit directly | ✓ Safe |
| **Parallel prepare** | Send all PREPAREs at once instead of sequentially | ✓ Safe — turns `N × RTT` into `1 × RTT` |
| **Batching** | Many transactions per coordinator flush | ✓ Safe, and essential for throughput |
| **Presumed abort** | One fewer forced write | ✓ Safe |
| **Early lock release** | Release locks before the decision is delivered | **✗ Unsafe** — allows another transaction to read uncommitted state and then the commit fails |
| **Heuristic resolution on timeout** | Unblock by guessing | ✗ Breaks atomicity (it is a *reported* violation, not a fix) |

The early-lock-release row is worth internalising because it is tempting: it recovers most of 2PC's throughput cost by giving up the very isolation that made the locks worth holding.

### 4.5 3PC, and why nobody uses it

**Three-phase commit** adds a **pre-commit** phase so that a participant can distinguish "I may still abort" from "everyone certainly will commit":

```
  1. CAN_COMMIT   → participants report whether they *could* commit
  2. PRE_COMMIT   → coordinator announces the intention (non-blocking state)
  3. DO_COMMIT    → everyone commits
```

It removes 2PC's coordinator-failure blocking **provided the network is synchronous** (bounded message delays) and nodes fail by stopping. Under a **network partition**, 3PC can elect to commit in one partition and abort in the other, so it is not atomic either. Since real networks are asynchronous and partitions happen (L65), 3PC buys complexity without buying the guarantee — which is why the systems that needed better-than-2PC went to consensus instead.

**The modern answer is consensus-based atomic commit** (Paxos Commit, or Spanner's design): instead of one coordinator with a log, the *decision itself* is replicated to a majority (L81). Then:

```
  2PC:           decision survives the COORDINATOR being lost  ✗ (if its log dies)
  Paxos Commit:  decision survives a MINORITY being lost       ✓
                 cost: one consensus round per transaction (L81 §9)
```

That is the honest upgrade path: **you cannot make 2PC non-blocking; you can replace its single log with a replicated one.**

### 4.6 XA, and why it has a reputation

XA is the standard interface for 2PC between a transaction manager and resource managers (`xa_start`, `xa_end`, `xa_prepare`, `xa_commit`, `xa_rollback`, `xa_recover`). Its operational problems are not protocol flaws — they are consequences of running 2PC in systems that were never designed for it:

```
  ✗ Locks held across application logic, external calls, and human decisions
    → the throughput cliff of L82 §9.
  ✗ Heuristic outcomes when a participant gives up waiting → data divergence
    that surfaces as "XA_HEURHAZ" in a log nobody reads.
  ✗ Recovery requires an XA-aware transaction manager plus xa_recover loops
    in every participant; most application servers implement this poorly.
  ✗ Connection-pool coupling: a prepared transaction pins a connection
    (L82 §11.5's total-outage pattern).
  ⇒ XA is not wrong; it is simply a guarantee whose cost must be paid with
    discipline that most teams do not have.
```

## 5. Request / data flow

A transfer between two databases, with a coordinator crash after the decision point.

```
 ── HAPPY PATH ─────────────────────────────────────────────────────────────
 t=0.0  coordinator: BEGIN; debit A in db-A
 t=1.0  coordinator → db-A: PREPARE
 t=1.6  db-A: writes PREPARED (fsync), holds row locks → VOTE YES
 t=1.7  coordinator → db-B: PREPARE
 t=2.3  db-B: writes PREPARED (fsync), holds row locks → VOTE YES
 t=2.4  coordinator: ★ writes COMMIT to its decision log (fsync, replicated)
 t=3.0  coordinator → db-A: COMMIT  → db-A commits, releases locks, ACK
 t=3.4  coordinator → db-B: COMMIT  → db-B commits, releases locks, ACK
 t=3.5  coordinator: writes COMPLETE, forgets the transaction
        ⇒ latency ≈ 2 × RTT + 2 × fsync ≈ 3.5 ms intra-AZ. Both or neither.

 ── CRASH AFTER THE DECISION POINT (safe) ──────────────────────────────────
 t=2.4  COMMIT written to the log; coordinator dies before sending anything.
 t=2.4+ db-A and db-B are PREPARED, locks held, IN DOUBT. Clients block.
 t=2.4+ a new coordinator instance reads the log, sees COMMIT for txn 90210,
        re-sends COMMIT to both, both release locks.
        ⇒ window of unavailability = failover time of the log (~seconds if
          replicated; minutes if someone must find the log).

 ── CRASH BEFORE THE DECISION POINT (also safe) ────────────────────────────
 t=2.0  coordinator dies after both voted YES, before writing COMMIT.
        Presumed abort: recovery finds no COMMIT → ABORT to both.
        ⇒ safe, because no commit message was ever sent.

 ── THE IRRECOVERABLE CASE ─────────────────────────────────────────────────
 The coordinator's log is destroyed (single disk, no backup, no replica).
        db-A and db-B remain prepared forever, holding locks.
        ⇒ an operator must choose: commit both or abort both — a HEURISTIC
          decision with no protocol support. If they get it wrong, the two
          databases are permanently divergent.
        ⇒ this is why the decision log must be REPLICATED (a consensus store,
          L81) — and why "2PC on a single un-replicated coordinator" is not
          an atomic commit protocol at all.
```

## 6. ASCII diagram

The participant's state machine — the only three states that matter:

```
                    ┌──────────────┐
       BEGIN ──────►│   ACTIVE     │  doing work, holds locks
                    └──────┬───────┘
                           │ PREPARE received
                           ▼
                    ┌──────────────┐    local problem
                    │  PREPARED    │──────────────► vote NO ──► ABORTED
                    │ (irrevocable)│                  (safe: nothing done)
                    └──────┬───────┘
                           │ vote YES  ★ locks held, fsync'd promise
                           ▼
                    ┌──────────────┐
                    │  IN DOUBT    │◄──── coordinator silent
                    │  (BLOCKING)  │      ⇒ ask others; if all merely
                    └──────┬───────┘        PREPARED, WAIT (no way to know)
                           │ COMMIT / ABORT received
              ┌────────────┴────────────┐
              ▼                         ▼
        ┌───────────┐             ┌───────────┐
        │ COMMITTED │             │  ABORTED  │
        │ (release) │             │ (release) │
        └───────────┘             └───────────┘
```

The blocking window, in context — where the guarantee is bought:

```
  participant            ┌──── ACTIVE ────┬── PREPARED / IN DOUBT ──┬─ done ─┐
  holds locks            │       yes      │         YES             │   no   │
  can abort alone        │       yes      │         NO              │   —    │
  other txns can read    │       no       │         no              │  yes   │
                         └────────────────┴─────────────────────────┴────────┘
                                          ▲
                          this is the availability you paid for atomicity:
                          if the coordinator is gone, this region BLOCKS and
                          cannot be resolved by any participant.
```

## 7. Detailed example

### 7.1 The prototype BookNook abandoned, with numbers

BookNook prototyped 2PC between `orders-db` (PostgreSQL, XA-capable) and the payment gateway (XA-capable, but with `p50 150 ms`).

```
  setup: XA transaction spanning orders-db and the gateway's XA endpoint
  measured at 20 checkouts/sec (the peak requirement):
      p50 lock hold             158 ms      (dominated by the gateway)
      p99 lock hold             940 ms
      p50 checkout latency      4.1 s       (queued on the hot inventory row)
      p99 checkout latency      9.4 s
      effective max throughput  6.6/sec
      prepared transactions older than 60 s during a chaos test: 14
        (an induced gateway timeout left 14 in-doubt transactions holding
         locks on the top 3 SKUs for the duration of the test)
  ⇒ the protocol did exactly what it promises: atomic. It was also unusable
    at this latency profile, and the in-doubt count during a *test* was enough
    to black out the best-selling products.
```

The team's conclusion, recorded in the design doc, generalises: **2PC is viable when all participants are (a) fast, (b) inside your failure domain, and (c) reliable enough that `PREPARE` rarely hangs. A third party satisfies none of the three.**

### 7.2 The one place 2PC was kept

BookNook's **loyalty ledger** and **orders** lived in two databases (two teams, historical split). A nightly settlement process needed to move points and record the corresponding order adjustments atomically. Volume: **40 settlements/minute**, all internal, all on local SSDs.

```
  measured with 2PC (both Postgres, same AZ):
      p50 3.2 ms, p99 11 ms, prepared-transaction backlog always 0
      throughput ceiling on the contended account row: 1 / 0.0032 = 312/sec
      requirement: 0.67/sec
  ⇒ 470× headroom. 2PC is exactly right here: low volume, short operations,
    both participants internal and fast, and a partial state (points moved
    with no order adjustment) that would be a real accounting error.

  The operational controls they added, and every 2PC deployment needs:
      1. decision log on a replicated volume (so §5's irrecoverable case
         becomes "the log survived")
      2. alarm on pg_prepared_xacts age > 60 s
      3. a runbook: for an in-doubt txn, query both participants, decide,
         and record the decision in the incident ticket
      4. no external calls, ever, inside the prepared window
      5. a hard timeout that ABORTS BEFORE PREPARE, never after it
```

The pairing of §7.1 and §7.2 is the practical lesson: **the same protocol, correct in both cases, is right at 0.67 ops/sec and catastrophic at 20 ops/sec with a 150 ms participant.** The decision is quantitative, not doctrinal.

## 8. Code or configuration example

```sql
-- ── PostgreSQL: a participant in an XA-style 2PC ─────────────────────────
-- Requires max_prepared_transactions > 0 (the default, 0, DISABLES this).
SHOW max_prepared_transactions;      -- must be > 0; size it > peak in-doubt

BEGIN;
  UPDATE loyalty SET points = points - 500 WHERE customer_id = 'C-4417';
PREPARE TRANSACTION 'settle-2026-10-30-C-4417';
-- ⇒ the row is LOCKED and the outcome is UNDECIDED until someone resolves it.

-- The coordinator, having durably recorded its decision:
COMMIT PREPARED 'settle-2026-10-30-C-4417';
-- or
ROLLBACK PREPARED 'settle-2026-10-30-C-4417';

-- ── The operational query. If this returns anything older than a minute,
--    you have an incident: locks held, vacuum blocked (L58 §11.4). ────────
SELECT gid, prepared, owner, database FROM pg_prepared_xacts
 ORDER BY prepared ASC;
```

```python
# ── A coordinator, with the two mandatory properties made explicit ────────
class Coordinator:
    def commit_all(self, txn_id, participants) -> bool:
        # PHASE 1 — parallel PREPARE (§4.4 optimisation)
        votes = parallel_map(lambda p: p.prepare(txn_id), participants,
                             timeout_ms=3000)          # timeout BEFORE prepare
        if not all(v is True for v in votes):
            # A NO or a prepare timeout → abort is always safe: no commit
            # message has been sent, so nobody can have committed.
            self.log.write(txn_id, "ABORT")
            parallel_map(lambda p: p.rollback(txn_id), participants)
            return False

        # ★ DECISION POINT — must be DURABLE and REPLICATED before anything
        #   is sent, or a coordinator crash here loses the only copy of the
        #   decision and leaves every participant in doubt (§5, §4.2).
        self.log.write(txn_id, "COMMIT")        # fsync + replicated (L81)
        self.log.await_quorum()                 # e.g. an etcd/Raft write

        # PHASE 2 — retry FOREVER. There is no timeout on delivering a
        # decision: a participant that never hears will block, but a
        # participant told to abort after being told to commit diverges.
        for p in participants:
            retry_until_acked(p.commit, txn_id, max_delay_s=60)
        self.log.write(txn_id, "COMPLETE")
        return True

    def recover(self):
        """Runs on startup. This is the part implementations get wrong."""
        for txn_id, decision in self.log.unfinished():
            if decision == "COMMIT":
                for p in self.participants_for(txn_id):
                    retry_until_acked(p.commit, txn_id)     # re-deliver
            else:                                    # presumed abort (§4.3)
                for p in self.participants_for(txn_id):
                    retry_until_acked(p.rollback, txn_id)
```

```ini
# ── XA configuration essentials (Java/JTA style), and the traps ──────────
spring.datasource.hikari.maximum-pool-size=20
# ⚠ a PREPARED transaction PINS a connection until resolved. If your pool is
#   20 and 20 transactions go in-doubt, the application has NO connections —
#   that is the total-outage pattern of L82 §11.5. Size the pool for
#   peak_in_doubt + normal concurrency, and alarm on pool exhaustion.
spring.jta.enabled=true
spring.jta.timeout=3000          # must expire BEFORE prepare, not after
```

```yaml
# ── Alarms. 2PC fails by blocking, so every alarm is about AGE, not errors ─
alerts:
  - expr: pg_prepared_xacts_max_age_seconds > 60
    severity: page          # in-doubt transaction holding locks
  - expr: pg_prepared_xacts_count > 10
    severity: page          # a coordinator is failing to deliver decisions
  - expr: xa_heuristic_outcomes_total > 0
    severity: page          # atomicity was VIOLATED; needs manual repair
  - expr: hikari_connections_active / hikari_connections_max > 0.9 for 2m
    severity: page          # prepared transactions may be pinning the pool
```

## 9. Numerical example

BookNook's two databases, same AZ.

**Step 1 — the latency floor, in parts.**

```
  per transaction, all participants fast and local:
      phase 1: 1 RTT (parallel prepare) + participant fsync
      decision: 1 fsync (coordinator log, replicated to a majority)
      phase 2: 1 RTT
  with RTT 0.4 ms and fsync 1.2 ms:
      ≈ 0.4 + 1.2 + 1.2 + 0.4 = 3.2 ms   ⇒ matches the measured p50 of 3.2 ms
  ⇒ the floor is TWO fsyncs and TWO round trips. Nothing can make it cheaper
    without giving up durability of the promise or of the decision.
```

**Step 2 — the throughput ceiling that the prepared state imposes.**

```
  contended row, lock held from prepare to decision = 3.2 ms:
      ceiling = 1 / 0.0032 = 312 ops/sec on that row
  with one external participant at p50 150 ms:
      lock hold ≈ 158 ms → ceiling = 6.3 ops/sec
  ⇒ the ceiling is set by the SLOWEST participant, because the promise must
    be held until the slowest vote arrives.
```

**Step 3 — the probability of being in doubt, and the pool sizing it implies.**

```
  assume: coordinator failover takes 20 s; coordinator crash rate 1/week;
  transaction rate 40/min = 0.67/sec.
  transactions in doubt during a 20 s failover = 0.67 × 20 = 13.3
  ⇒ size max_prepared_transactions ≥ 32 (2× for jitter), and the connection
    pool for peak_in_doubt + steady concurrency (L82 §11.5).
  at 20 checkouts/sec (the abandoned prototype) the same 20 s failover puts
      ~400 transactions in doubt — which is why that design needed a pool of
      400+ or it exhausted connections, and why the prototype could not have
      been made safe by tuning.
```

**Step 4 — why the decision log must be replicated, quantified.**

```
  single-disk coordinator log, annual disk failure rate 2 %:
      expected loss of the decision log = 0.02/year
      consequence: every in-doubt transaction at that moment is unresolvable
      by protocol; repair needs human reconciliation across two databases.
      cost per event: ~40 engineer-hours + a customer-visible data error.
      ⇒ expected annual cost ≈ 0.02 × 40 h = 0.8 h, plus a tail event that
        WILL happen eventually and be expensive.
  with the log on a 3-node consensus store (L81):
      loss requires a majority failure: ~1e-6/year. Effectively eliminated.
      cost: one extra consensus write per transaction (~2.8 ms, L81 §9) —
      which TRIPLES the 3.2 ms floor to ~6 ms.
  ⇒ this is the real 2PC design decision: pay ~2× latency for a durable
    decision, or accept a small probability of an unresolvable divergence.
    For a settlement process at 0.67/sec, pay it. For 20/sec with a 150 ms
    participant, do not use 2PC at all.
```

**Step 5 — the cost of a heuristic decision.**

```
  A participant gives up after 10 minutes and aborts unilaterally while the
  coordinator had decided COMMIT.
      db-A committed (points moved), db-B aborted (no order adjustment).
      reconciliation: identify affected accounts (312 possible), recompute
      from source, apply corrections, prove the ledger balances.
      measured in the industry: 1–3 days of senior engineer time per event,
      plus audit exposure.
  ⇒ heuristics convert a 10-minute blocking problem into a multi-day
    correctness problem. Alarm at 60 s and page a human; never let the
    participant decide on its own.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **2PC, all participants internal and fast** | Atomicity, ~3 ms, no visible partial state | Locks held; blocking on coordinator loss | Low volume, short operations (§7.2) |
| **2PC with an external participant** | Atomicity including the third party | Throughput capped at `1/external_latency` (6.3/sec) | Never (§7.1) |
| **2PC with an un-replicated coordinator log** | Cheaper | A small chance of unresolvable divergence | Never for money |
| **Consensus-based atomic commit** (Paxos Commit, Spanner) | Atomicity that survives a minority loss | One consensus round per transaction | You must not block |
| **3PC** | Non-blocking under *synchronous* assumptions | Still not atomic under partitions; much more complexity | Never in practice |
| **Presumed abort** | One fewer forced write; simpler recovery | None material | Default |
| **Read-only vote** | Read-only participants exit early | None | Default |
| **Parallel prepare** | `N × RTT` → `1 × RTT` | None | Default |
| **Early lock release** | Recovers throughput | **Breakable isolation** | Never |
| **Saga instead** (L84) | No blocking; scales | Compensations, visible states | The common case |

## 11. Failure scenarios

**11.1 — In-doubt transactions blocking a hot row.** A coordinator pod was killed between the decision write and delivery; 14 prepared transactions held locks on the top 3 SKUs; checkouts for those products hung until an operator resolved them 25 minutes later. **Symptom:** specific rows blocking, no errors, `pg_prepared_xacts` non-empty. **Fix:** replicated decision log so recovery re-delivers automatically, plus an alarm at 60 s.

**11.2 — Connection-pool exhaustion from prepared transactions.** 20 in-doubt transactions pinned all 20 pooled connections; the application could not serve *any* request, including unrelated ones (L82 §11.5). **Symptom:** total outage with healthy databases. **Fix:** separate pools per workload, size for peak in-doubt, alarm on pool utilisation.

**11.3 — The heuristic that diverged the data.** A participant timed out after 10 minutes and aborted while the coordinator had committed; two databases disagreed permanently (§9 step 5). **Symptom:** `XA_HEURHAZ` in a log, discovered by an audit. **Fix:** never let participants time out of the prepared state; alarm and page instead.

**11.4 — Timeout applied in the wrong phase.** A 3-second transaction timeout fired **after** prepare in one participant, which rolled back locally while others committed. **Symptom:** rare, unexplainable divergence under load. **Fix:** timeouts must abort **before** `PREPARE`; after voting yes there is no timeout, only retry.

**11.5 — The coordinator's log on ephemeral storage.** A container-based coordinator wrote its log to the container filesystem; a reschedule lost the decisions for 6 in-flight transactions. **Symptom:** prepared transactions that no coordinator would ever resolve. **Fix:** the decision log is the only copy of the truth — put it on replicated durable storage, never on ephemeral disk.

**11.6 — 2PC to a message broker.** An XA transaction spanning the database and a broker deadlocked under load; prepared branches accumulated; the connection pool drained (§11.2). **Symptom:** site-wide outage from one flow. **Fix:** the outbox pattern (L85) — there is no reason to 2PC to a broker.

**11.7 — Prepared transactions blocking vacuum.** Long-lived prepared transactions held back the oldest transaction ID, so autovacuum could not reclaim dead rows; the table bloated to 3× its size over two weeks (L58 §11.4). **Symptom:** disk growth with normal write volume. **Fix:** alarm on `pg_prepared_xacts` age, and treat any prepared transaction older than minutes as an incident.

**11.8 — Sequential prepare.** A coordinator prepared 8 participants sequentially: 8 × (RTT + fsync) ≈ 13 ms per transaction instead of 3.2 ms. **Symptom:** uniformly 4× the expected latency with no contention. **Fix:** parallel prepare — it is free.

## 12. Alternatives

| Instead of 2PC | Consider | When |
|---|---|---|
| 2PC | **One database** (merge the resources) | The invariant is genuinely transactional (L82 §7.2) |
| 2PC | **Saga with compensations** (L84) | A third party, high volume, or long durations |
| 2PC | **Outbox + CDC** (L85) | The second resource is a broker |
| 2PC | **Consensus-based atomic commit** | You need non-blocking atomicity |
| 2PC | **Reserve/confirm/release** | The operation is a hold on a resource |
| 3PC | **Nothing** — use consensus instead | You thought 3PC solved blocking; it does not (§4.5) |

## 13. When to use it

- **Low-volume internal operations** across two or three fast, reliable participants (§7.2: 0.67 ops/sec, 3.2 ms).
- **Financial or inventory invariants** where a visible partial state is unacceptable and the operation is short.
- **When all participants are inside your failure domain** and support the protocol properly.
- **With a replicated decision log**, an alarm on prepared-transaction age, and a documented runbook — all three, or you do not have an atomic commit protocol.

## 14. When *not* to use it

- **With any third-party participant** whose latency you do not control (§7.1).
- **To a message broker** — use the outbox (L85, §11.6).
- **On a hot row at high volume** — the lock caps throughput at `1/lock_hold` (§9 step 2).
- **With a single un-replicated coordinator log** — that is 2PC minus its guarantee (§9 step 4).
- **For long-running business processes** (minutes, hours) — that is a saga.
- **When 3PC is proposed as a remedy** — it does not survive partitions (§4.5).

## 15. Common mistakes

1. **Preparing across a call to an external service** (§7.1).
2. **A non-durable, non-replicated decision log** (§11.5, §9 step 4).
3. **A timeout that fires after prepare**, causing unilateral abort (§11.4).
4. **Heuristic resolution** instead of paging a human (§11.3).
5. **Sequential prepares** (§11.8).
6. **No alarm on prepared-transaction age**, so blocking is invisible (§11.1).
7. **Connection pools not sized for in-doubt transactions** (§11.2).
8. **2PC to a broker** (§11.6).
9. **Conflating 2PC with isolation** — it is atomicity with locks, not serializability across services (L82 §4.1).
10. **Assuming "2PC = safe"** without asking what the participants' latencies and failure rates are.
11. **Early lock release** to claw back throughput (§4.4).
12. **Forgetting `max_prepared_transactions`** is `0` by default in PostgreSQL, so 2PC silently does not work.
13. **No runbook** for resolving in-doubt transactions, so the first incident is improvised.
14. **Monitoring 2PC by error rate** — it fails by blocking, so errors stay at zero.

## 16. Interview perspective

**What is actually being tested.** Whether you know **why** 2PC blocks — the irrevocable vote — and whether you can decide, with numbers, when it is appropriate. Strong candidates describe prepare/commit, the decision point, presumed abort, and then say "here is the lock hold time, here is the throughput ceiling, so 2PC is right at 0.67 ops/sec and wrong at 20 with a 150 ms participant". Weak candidates either treat 2PC as the correct answer to every atomicity problem, or dismiss it without knowing why it exists.

**Answers that land well:**

- *"Explain 2PC."* — "A coordinator asks every participant to prepare. Preparing means doing the work, durably promising to obey, and holding locks; after voting yes a participant cannot unilaterally abort, because another may already have been told to commit. Once all votes are in, the coordinator writes the decision to a durable replicated log — that write is the decision point — then delivers it and retries until acknowledged. It is atomic, and it blocks: if the coordinator is lost, participants hold locks until the decision is recovered. That blocking is the price of the guarantee, not a bug."
- *"When would you use it?"* — "Low volume, short operations, all participants internal and fast, with a replicated decision log. We keep it for a settlement process: 40 per minute, 3.2 ms p50, 312 ops/sec ceiling on the contended row. We rejected it for checkout because the payment gateway is a 150 ms third party, which caps the lock to 6.3 ops/sec against a requirement of 20."
- *"Why is 3PC not the answer?"* — "It makes blocking-free termination possible only under synchrony; under a partition it can commit in one partition and abort in the other, so it is not atomic. The real fix is to replicate the decision with consensus — accept one consensus round per transaction and you get non-blocking atomic commit."
- *"How do you operate it?"* — "Alarm on prepared-transaction age, never on error rate because it fails by blocking; size the connection pool for peak in-doubt; replicate the decision log; and keep a runbook for resolving an in-doubt transaction by inspecting both participants and recording the decision."

**Common interview tasks:** trace 2PC through a coordinator crash at a specified point; decide 2PC vs saga for a described workload with latencies; explain the decision point and presumed abort; explain why participants cannot time out of the prepared state; explain what makes XA operationally painful.

**Red flags interviewers watch for:**

- "2PC is non-blocking" or no awareness of in-doubt transactions.
- Letting a participant time out and abort unilaterally.
- 2PC across a third-party API call as a serious proposal.
- No mention of a durable decision log.
- Confusing 2PC with isolation or with serializability.
- "Use 3PC to fix blocking."
- No numbers: no lock hold, no throughput ceiling, no in-doubt sizing.

## 17. Knowledge check

**Conceptual**

1. Explain why a participant cannot abort unilaterally after voting yes, and construct the exact sequence of messages that produces permanent divergence if it does. Then explain why the prepared state must be both durable and irrevocable.
2. Explain the decision point and describe what happens after a coordinator crash (a) after it but before delivery, (b) before it, and (c) with the decision log destroyed. State which of the three requires a human and why no protocol can help.
3. Explain why 3PC does not solve the blocking problem in practice, and describe the mechanism that does. State the cost of that mechanism in latency terms.

**Practical scenario**

4. BookNook must settle loyalty points across two PostgreSQL databases (both internal, same AZ) at 40 settlements/minute, and separately must charge cards (third-party gateway, p50 150 ms) as part of checkout at 20/sec. For each: decide whether 2PC is appropriate, with the arithmetic. For the one where it is, design the complete operational setup — decision-log durability, timeouts and where they apply, prepared-transaction alarms and thresholds, pool sizing for a 20-second coordinator failover, recovery procedure, and the runbook for a human-resolved in-doubt transaction. For the other, state what you would build instead and why.

**Calculation**

5. A 2PC transaction spans 3 participants in the same AZ with RTT 0.5 ms and per-participant fsync 1.0 ms; the coordinator's decision log is a 3-node consensus write at 2.8 ms. (a) Compute the latency with sequential prepares. (b) Compute it with parallel prepares. (c) Compute the throughput ceiling on a single contended row. (d) Recompute (c) if one participant is an external API with p50 200 ms, and state whether a 30 ops/sec requirement can be met.

6. A coordinator fails over in 25 seconds. Transaction rate is 2/sec. (a) Compute the expected number of transactions in doubt during a failover. (b) Compute the connection pool size you would provision, assuming steady concurrency of 10 and 2× headroom on in-doubt. (c) Recompute both for a rate of 40/sec, and state what the result implies about using 2PC at that rate. (d) If the decision log is on a single disk with a 2 % annual failure rate, compute the annual expected number of unresolvable-divergence events, and the effect of moving it to a 3-node consensus store.

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
  - [x] 14. Circuit breakers, bulkheads and graceful degradation (L77)
  - [x] 15. Distributed locks and leases (L78)
  - [x] 16. Leader election (L79)
  - [x] 17. Quorums (L80)
  - [x] 18. Consensus and Raft (L81)
  - [x] 19. Distributed transactions (L82)
  - [x] **20. Two-phase commit (L83) ← you are here**
  - [ ] 21. Saga pattern (L84)
  - [ ] 22. Transactional outbox and change data capture (L85)
  - [ ] 23. Monoliths, modular monoliths, microservices (L86)
  - [ ] 24. Service discovery and configuration (L87)
  - [ ] 25. Observability: logs, metrics, traces (L88)
  - [ ] 26. SLIs, SLOs and error budgets (L89)
  - [ ] 27. High availability and disaster recovery (L90)
  - [ ] 28. Active-passive versus active-active (L91)
  - [ ] 29. RTO and RPO (L92)

**NEXT LESSON: Phase 4, Topic 21 — Saga pattern**

---

---

# Lesson 84 — Saga Pattern

*Phase 4, Topic 21*

## 1. Simple definition

A **saga** is a way to keep a multi-service business operation correct **without a distributed transaction**: it is a sequence of **local** transactions, each in one service, and for each step that can be undone there is a **compensating action** — a new forward action that reverses the effect — which runs if a later step fails.

```
  T1  →  T2  →  T3  →  T4  →  T5          forward path
         ↓      ↓      ↓      ↓
        C4?   C3     C2     C1             compensations (reverse order)
```

Crucially: **a saga is not a transaction.** It gives up **atomicity** — intermediate states are visible to the rest of the world — and it does not give **isolation**. What it preserves is **consistency**, by ensuring every completed step is eventually either matched by its successors or reversed by its compensation. The standard framing is that a saga exchanges ACID for **ACD**: atomicity is replaced by compensation, and the visible intermediates are the price.

Terms defined before use:

- **Compensating transaction**: a business-level undo (`refund`, `release`, `cancel`). It is not a rollback, it may charge fees, and it may itself fail.
- **Pivot transaction**: the point of no return. Before it, everything is **compensable**; after it, everything is **retryable** (must eventually succeed). It is usually where money moves or goods are committed.
- **Semantic lock**: a flag on the affected record (`status = 'pending'`, `reserved_until`) that makes the partial state visible and discourages conflicting actions — the saga's weak substitute for isolation.
- **Orchestration**: a central coordinator that calls each step and decides what to do.
- **Choreography**: each service reacts to events, with no central coordinator.
- **Saga log / state store**: the durable record of which steps have run, which is what lets a crash resume rather than restart.
- **Forward recovery**: retry the failing step until it succeeds (used after the pivot).
- **Backward recovery**: run compensations in reverse (used before the pivot).

The one sentence to carry out of this lesson: **a saga is a state machine with undo verbs, and its correctness depends entirely on three unglamorous things — the pivot is chosen deliberately, every step and compensation is idempotent, and something sweeps for sagas that get stuck.**

## 2. Why it exists

Lesson 82 established that distributed transactions are unavailable or unusable for the interesting cases: a third party that only offers `authorise/capture/void`, or a 150 ms participant that would cap a hot row at 6.6 ops/sec. Lesson 83 established that 2PC blocks, and that its blocking is deliberate rather than fixable.

But the business requirement does not go away: **a checkout must produce either a paid order with reserved stock, or nothing at all.** So the mechanism has to change from *preventing* a partial state to *repairing* one.

That is the whole idea, and it is worth being precise about what "repair" means, because it is where implementations go wrong:

| | 2PC | Saga |
|---|---|---|
| Partial state | Invisible (locks) | **Visible** |
| Undo | Rollback (as if it never happened) | A **new action** with its own side effects |
| Failure of undo | Impossible by construction | **Possible**, and it must be handled |
| Duration | Milliseconds | Seconds to days |
| Blocking | Yes, on coordinator loss | No |
| Throughput | `1 / lock_hold` | `1 / step_latency`, parallelisable per order |

BookNook's numbers make the choice easy: at 20 checkouts/sec, with a payment gateway at `p50 150 ms`, 2PC caps the system at 6.6/sec and produced a `p99` of 9.4 s in the prototype (L82 §9). An orchestrated saga on the same hardware supports **1,400 checkouts/sec** with a `p99` of 780 ms — because nothing is locked across the slow call, and the cost is a **2-second window in which an order reads "processing"**.

The requirement that makes this acceptable is a product decision, not an engineering one: **is a visible "processing" state tolerable?** For an order, yes. For a bank transfer, no — which is why banks still use clearing windows and settlement batches rather than sagas.

## 3. Real-world analogy

**Booking a holiday: flight, then hotel, then car hire.**

You want all three or none. They are sold by three companies with no shared transaction, so the travel agent does this:

1. **Book the flight** — fully refundable for 24 hours (compensable).
2. **Book the hotel** — cancellable up to 48 hours (compensable).
3. **Book the car** — **non-refundable** once confirmed.

Between steps 2 and 3 sits the **pivot**: after the car is booked, there is no going back — the money is spent. The agent therefore orders the steps so that the *irreversible* one comes **last**, and everything before it can be undone:

- If the hotel fails, cancel the flight. (Compensate in reverse.)
- If the car fails, cancel the hotel and the flight — annoying, but no money lost.
- If the car succeeds and then the *confirmation email* fails, you **retry the email forever** — you do not cancel the holiday because a notification failed. This is **forward recovery after the pivot**, and it is the single most important structural insight in the pattern: **put irreversible steps last, and put notification steps after those.**

And the failure everyone forgets: **the hotel's cancellation policy changes.** The compensation rate was 100 %, then a non-refundable rate appeared, and "cancel" started returning a cancellation fee. The agent's plan assumed compensation was free; it was merely *possible*. Real compensations have costs, and the design must know what they are.

The other visible property: **while the agent is working, your account says "booking in progress."** That semantic lock is how the partial state is made tolerable — nobody else tries to book the same seats, and you are not surprised when the holiday is not yet confirmed.

## 4. Detailed technical explanation

### 4.1 The three zones, which decide everything

```
  ┌─────────────── COMPENSABLE ───────────────┬── PIVOT ──┬──── RETRYABLE ────┐
  │  can be undone by a compensation          │ the point │  must eventually  │
  │  → on failure, run compensations backward │ of no     │  succeed; retry   │
  │                                           │ return    │  forward forever  │
  └───────────────────────────────────────────┴───────────┴───────────────────┘
        reserve stock, hold credit,           capture     send email, publish
        authorise payment, create draft       payment     event, create shipment
```

The pivot should be:

- **Late**, so that most failures happen while compensation is still possible.
- **Cheap to retry**, because everything after it must be.
- **Placed immediately before the first irreversible effect** — usually money or a physical action.

Getting the pivot wrong is the most expensive mistake in the pattern. BookNook originally captured payment *first* (pivot at step 1) and then reserved stock; when stock ran out, it had to refund — **1,900 refunds in one flash sale**, each costing a payment fee and a customer's trust. Moving the capture after the reservation removed the entire class of failure.

### 4.2 Orchestration vs choreography

| | Orchestration | Choreography |
|---|---|---|
| Control | A central orchestrator calls each step | Each service reacts to events |
| Visibility | The whole state machine is in one place | Distributed across services and topics |
| Coupling | Orchestrator depends on every service | Services depend only on events |
| Adding a step | One file changes | New subscriber; existing services must not break |
| Failure handling | Centralised, testable | Emergent, often nobody's job |
| Debugging | One log, one state per order | Correlate across many topics |
| Risk | Orchestrator becomes a god-service | **No owner of the whole outcome** |

The rule of thumb from production experience: **choreograph the notifications, orchestrate the transaction.** Simple, single-consequence reactions ("stock reserved → notify warehouse") are ideal for events. The *outcome* of a multi-step business process needs an owner, or you will eventually have the BookNook incident of L82 §11.2: **340 orders permanently `pending`, because no service was responsible for the whole.**

### 4.3 Semantic locks: the weak substitute for isolation

Because there is no isolation, the partial state must be *marked*:

```sql
-- The reservation IS the semantic lock: a row that says "this stock is held
-- for order 90210 until 14:17, after which it may be reclaimed".
INSERT INTO stock_reservation (sku, order_id, qty, expires_at)
VALUES ('SKU-4471', '90210', 2, now() + interval '15 minutes');

-- Every other checkout sees it and cannot take the same stock:
UPDATE stock SET available = available - 2
 WHERE sku = 'SKU-4471' AND available >= 2;      -- 0 rows ⇒ sold out

-- Anyone reading the order sees the truth, not a pretend-confirmed order:
--   status = 'reserved' / 'authorising' / 'confirmed'
```

A TTL on the semantic lock is essential, because **a crashed saga leaves a lock behind**: the `expires_at` guarantees the resource returns to circulation without human intervention. Choosing the TTL is arithmetic (§9 step 3): it must exceed the `p99.9` saga duration, but not by so much that a stuck order holds stock for hours.

### 4.4 Idempotency, without which nothing else works

Every step and every compensation runs **at least once** (L73), because delivery is retried and crashes are resumed from the saga log. Therefore:

- Each step is keyed by `(saga_id, step_name)` as an **idempotency key**, and the participant records it (L74 §4).
- Each compensation is keyed by `(saga_id, step_name, "undo")`.
- The saga log write and the step's effect must be **atomic together** — otherwise a crash between them produces the worst case: an effect applied with no record of it. The standard solution is the **outbox** (L85) or keeping the effect and the log in the same database.

That third point is the one that separates a working saga from a demo. BookNook's first orchestrator wrote the step result to its own DB *after* calling the service; a pod eviction in between left 11 orders where the stock was reserved and the saga believed it was not — and the compensations then released stock that had never been reserved, corrupting the available count. The fix was to make the participant's idempotency record authoritative and the orchestrator's state derived from it (with a reconciler, §4.6).

### 4.5 The isolation anomalies a saga permits

Since there is no isolation, know exactly what can be observed:

| Anomaly | Example | Mitigation |
|---|---|---|
| **Dirty read** | Another request sees `status='reserved'` and treats the stock as sold | Semantic lock + read models that only expose terminal states |
| **Lost update** | Two sagas read a balance, both apply, one overwrites | Conditional updates with version checks (`WHERE version = n`) |
| **Fuzzy read** | A report sums `reserved` and `confirmed` in one pass, double-counting | Aggregate only terminal states; take reports from a read model (CDC, L85) |
| **Non-repeatable read across steps** | Step 3 re-reads data that step 1 changed elsewhere | Pass values by copy in the saga context; don't re-read for decisions |

The report anomaly is the one that bites in practice: L82 §11.7's 4 %-high revenue figure came from counting `pending` orders. **Decide which states are visible to which readers, and document it** — that is the saga's isolation contract, and it is a documentation artefact, not a database property.

### 4.6 Stuck sagas, and the reconciler that is not optional

A saga can stop advancing in four ways:

```
  1. A step keeps failing (the participant is down) → retries eventually exhaust
  2. A compensation keeps failing (e.g. the refund API rejects)
  3. A crash after the effect, before the log write (if not transactional)
  4. A bug: a state with no outgoing transition
```

Each leaves an order in a **non-terminal state forever**. The reconciler is the component that:

- finds sagas older than a threshold in a non-terminal state,
- **asks each participant what actually happened** (L65 §4.2: query, do not guess),
- advances or compensates accordingly, and
- **escalates to a human** for the genuinely ambiguous remainder.

Without it, the count of stuck orders grows monotonically and silently, which is exactly how "we have a saga" turns into "we have 4,000 orders in `authorising` and nobody noticed since March".

## 5. Request / data flow

BookNook checkout as an orchestrated saga, with a compensation and a stuck case.

```
 ── HAPPY PATH ─────────────────────────────────────────────────────────────
 t=0.00  ORCHESTRATOR creates saga 90210 (state=PENDING) in the saga table
 t=0.01  T1 orders-db:  INSERT order(status='pending') + outbox    ✓  (40 ms)
 t=0.05  T2 inventory:  reserve 2 × SKU-4471, expires_at = +15 min ✓  (60 ms)
                        ← SEMANTIC LOCK: stock is held, visible, expiring
 t=0.11  T3 payment:    AUTHORISE card (id 8fa2)                   ✓ (150 ms)
         ★ PIVOT: nothing before it is irreversible; nothing after it may fail
 t=0.26  T4 payment:    CAPTURE the authorisation                   ✓  (90 ms)
 t=0.35  T5 orders-db:  status='confirmed'                          ✓  (30 ms)
 t=0.38  T6 fulfilment: create shipment request                     ✓  (80 ms)
 t=0.46  T7 email:      queue confirmation email (via outbox)       ✓  (10 ms)
 t=0.47  state=COMPLETED; total 470 ms p50, 780 ms p99
         ⇒ intermediate states were visible for ≤ 470 ms ("processing")

 ── FAILURE BEFORE THE PIVOT (backward recovery) ───────────────────────────
 t=0.11  T3 payment returns CARD_DECLINED (permanent, not retryable)
         → compensate in reverse: C2 release reservation, C1 cancel order
         → state=FAILED. Customer sees "payment declined", stock is free.
         ⇒ correct, fast, no money moved.

 ── FAILURE AFTER THE PIVOT (forward recovery ONLY) ────────────────────────
 t=0.26  T4 capture succeeds. T5 fails (orders-db unavailable).
         → DO NOT refund. Retry T5 with a ladder (L75) until it succeeds.
         ⇒ refunding here would be the worst possible action: the customer's
           money was taken and the order cancelled for a transient DB blip.
         ⇒ this is the rule: after the pivot, only forward.

 ── A COMPENSATION THAT FAILS (the genuinely hard case) ────────────────────
 t=0.12  C2 release reservation fails: inventory returns 500 for 20 minutes.
         → retry on the ladder; after exhaustion, state=COMPENSATION_STUCK
         → reserved stock expires naturally at +15 min (the TTL saves us)
         → reconciler flags the saga; an operator confirms the stock is free
         ⇒ the TTL on the semantic lock is what makes an un-compensable step
           survivable rather than permanent.
```

## 6. ASCII diagram

The zones, and why the pivot's position is the design decision:

```
   ┌──────────────── COMPENSABLE ────────────────┐ ┌── PIVOT ──┐ ┌─ RETRYABLE ─┐
   │ T1 create order                             │ │ T4 CAPTURE│ │ T5 confirm  │
   │ T2 reserve stock    ← semantic lock         │ │  payment  │ │ T6 shipment │
   │ T3 authorise card                           │ └───────────┘ │ T7 email    │
   └─────────────────────────────────────────────┘               └─────────────┘
              │ failure here: COMPENSATE                     failure here: RETRY
              ▼                                              ▼
        C3 void authorisation                          retry forever, page a
        C2 release reservation                         human if it never works
        C1 cancel order
        (reverse order, each idempotent)

   ⚠ Every step left of the pivot that can fail must have a compensation.
   ⚠ Every step right of the pivot must be idempotent AND retryable forever.
   ⚠ The pivot should be as LATE as possible (BookNook learned this the
     expensive way — L84 §4.1: capture first cost 1,900 refunds).
```

Orchestration, drawn with the state store as the source of truth:

```
   client ──► ORCHESTRATOR ──────────────► saga table (state machine, durable)
                   │                              ▲
                   │ 1. load state                │ every transition is
                   │ 2. execute next step         │ committed BEFORE the
                   │ 3. record transition         │ next step runs
                   ▼
        ┌──────────┼──────────┬───────────┬──────────────┐
        ▼          ▼          ▼           ▼              ▼
    orders-db  inventory   payment    fulfilment      email
        │          │          │           │              │
    (idempotency key = (saga_id, step) on EVERY participant)
        │          │          │           │              │
        └──────────┴──────────┴───────────┴──────────────┘
                   │
                   ▼
            RECONCILER (every 60 s)
              finds non-terminal sagas older than 10 min
              → asks participants what happened
              → advances / compensates / escalates to a human
```

## 7. Detailed example

### 7.1 BookNook's checkout saga, measured

```
  Steps: 7 (create, reserve, authorise, capture, confirm, ship, email)
  Pivot: after CAPTURE (step 4) — money moved

  Volume:               40,000 orders/month ≈ 1.5/min, peak 20/sec
  Per-step success:     99.5 % typical, 98.5 % worst (payment)
  Compensation rate:    2.4 % of sagas (matches 1 − 0.995^7 ≈ 3.4 % minus
                        retry recovery of 85 % → 0.5 %… measured 2.4 % because
                        the payment step is genuinely flakier than modelled)
  Compensation success: 99.6 % first attempt, 99.97 % within the ladder
  COMPENSATION_STUCK:   0.004 % = 1.6 orders/month
      → each handled by the reconciler automatically (TTL expiry) or a
        5-minute operator action
  Stuck in a non-terminal state > 10 min: 0.9 orders/day pre-reconciler,
      0.02/day post-reconciler
  p50 saga duration:    470 ms;  p99: 780 ms;  p99.9: 3.1 s
  Reservation TTL:      15 min (see §9 step 3 for why 15 and not 2)

  Cost of the intermediate state, measured: 240 support contacts/month from
      customers who saw "processing" for 1–3 s and asked about it
      = £504/month. That is the entire price of running 200× the throughput
      of the 2PC prototype (L82 §9 step 4).
```

### 7.2 The step that could not be compensated

The confirmation **email** was originally step 4, before capture. When capture failed, the saga compensated everything — but the email could not be un-sent. Customers received "Your order is confirmed!" for orders that were then cancelled.

```
  the fix was structural, not technical: move the email to step 7, AFTER the
  pivot, and make it a RETRYABLE action.
  ⇒ a non-compensable step must sit to the RIGHT of the pivot, and everything
    to its right must be retryable. That is the whole rule, and it is why
    "send the notification last" appears in every saga design guide.
  ⇒ the same applies to physical actions: you cannot un-ship a parcel, so
    "create shipment" belongs after the pivot, with a cancellation path that
    is a BUSINESS process (recall the courier), not a compensation.
```

The general test to apply to every step: **"if this succeeds and everything after it fails, what do I do?"** If the answer is "we announce it and manage the consequences", the step belongs after the pivot. If the answer is "we undo it", it belongs before.

## 8. Code or configuration example

```sql
-- ── The saga log: the state machine, and the source of truth ──────────────
CREATE TABLE saga (
  id            text PRIMARY KEY,          -- 'order-90210'
  type          text NOT NULL,             -- 'checkout'
  state         text NOT NULL,             -- see the transitions below
  current_step  int  NOT NULL DEFAULT 0,
  context       jsonb NOT NULL,            -- values passed between steps
  pivot_passed  boolean NOT NULL DEFAULT false,
  attempts      jsonb NOT NULL DEFAULT '{}',
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  -- non-terminal states must be findable fast by the reconciler
  CONSTRAINT terminal CHECK (state IN
    ('COMPLETED','FAILED','CANCELLED','COMPENSATION_STUCK') OR true)
);
CREATE INDEX saga_active_idx ON saga (updated_at)
  WHERE state NOT IN ('COMPLETED','FAILED','CANCELLED');

-- Every transition is a conditional UPDATE: the optimistic lock that stops
-- two orchestrator replicas from both advancing the same saga.
UPDATE saga SET state = $2, current_step = $3, updated_at = now()
 WHERE id = $1 AND state = $4;          -- 0 rows ⇒ someone else moved it
```

```python
# ============================================================================
# An orchestrated saga. The three properties that make it correct are marked.
# ============================================================================

STEPS = [                                    # order matters: pivot is step 3
    Step("create_order",   forward=T1, compensate=C1),
    Step("reserve_stock",  forward=T2, compensate=C2),   # semantic lock + TTL
    Step("authorise",      forward=T3, compensate=C3),
    Step("capture",        forward=T4, compensate=None),  # ★ PIVOT: irreversible
    Step("confirm",        forward=T5, compensate=None),  # retryable forever
    Step("ship",           forward=T6, compensate=None),  # retryable forever
    Step("email",          forward=T7, compensate=None),  # retryable forever
]

def advance(saga_id):
    saga = store.load_for_update(saga_id)     # optimistic: state must match
    if saga.state in TERMINAL:
        return                                 # idempotent re-entry

    if saga.compensating:
        return _run_compensations(saga)

    while saga.current_step < len(STEPS):
        step = STEPS[saga.current_step]
        try:
            # IDEMPOTENCY (§4.4): the key makes a repeated call a no-op.
            # Without it, every retry after a crash duplicates the effect.
            result = step.forward(saga, idem_key=f"{saga_id}:{step.name}")
        except Retryable as e:
            if saga.pivot_passed:
                # AFTER the pivot: FORWARD ONLY. Retrying is the only correct
                # action; compensating would undo a committed payment (§5).
                return schedule_retry(saga_id, e, ladder=BACKOFF_LADDER)
            if saga.attempts[step.name] >= MAX_ATTEMPTS:
                return _begin_compensation(saga, e)     # before pivot: undo
            return schedule_retry(saga_id, e, ladder=BACKOFF_LADDER)
        except Permanent as e:
            if saga.pivot_passed:
                # A permanent failure after the pivot is an INCIDENT, not a
                # flow: a human must finish the order. Page, do not compensate.
                return escalate(saga_id, "post_pivot_permanent", e)
            return _begin_compensation(saga, e)

        saga.context.update(result or {})
        saga.current_step += 1
        if step.name == "capture":
            saga.pivot_passed = True
        store.transition(saga)                 # commit state BEFORE the next
                                               # step runs (§4.4: else a crash
                                               # loses the record of an effect)
    store.set_state(saga_id, "COMPLETED")


def _begin_compensation(saga, cause):
    store.set_state(saga.id, "COMPENSATING", reason=str(cause))
    _run_compensations(saga)


def _run_compensations(saga):
    for step in reversed(STEPS[:saga.current_step]):
        if step.compensate is None:
            continue
        try:
            step.compensate(saga, idem_key=f"{saga.id}:{step.name}:undo")
        except Retryable as e:
            # keep trying: an un-compensated effect is the worst state there is
            return schedule_retry_compensation(saga.id, step.name, e)
        except Permanent as e:
            # §4.6 case 2: the compensation itself is broken.
            store.set_state(saga.id, "COMPENSATION_STUCK", reason=str(e))
            metrics.incr("saga.compensation_stuck")
            page_oncall(saga.id, step.name, e)
            return
    store.set_state(saga.id, "FAILED")
```

```python
# ── The reconciler. Not optional (§4.6). ──────────────────────────────────
def reconcile():
    for saga in store.find_active(older_than_minutes=10):
        # ASK, do not guess (L65 §4.2): every participant reports whether it
        # has a record for this saga's idempotency key.
        truth = {s.name: s.probe(saga.id) for s in STEPS}
        if all_done(truth) and saga.state != "COMPLETED":
            log.warning("saga completed but not recorded; advancing")
            store.force_complete(saga)         # participants already agree
        elif none_done(truth):
            store.force_fail(saga)             # nothing happened; safe to stop
        elif saga.pivot_passed:
            alert("post_pivot_stuck", saga)    # needs a human, urgently
        else:
            _begin_compensation(saga, "reconciled")   # undo what did happen
```

```yaml
# ── Alarms: sagas fail by being STUCK, not by erroring ───────────────────
alerts:
  - expr: saga_active_older_than_10m > 5            # page
  - expr: rate(saga_compensation_total[5m]) > 0.05 * rate(saga_started_total[5m])
    severity: page        # >5 % compensating = a dependency is failing
  - expr: saga_compensation_stuck_total > 0         # page: effects cannot be undone
  - expr: saga_post_pivot_permanent_total > 0       # page: money moved, flow broke
  - expr: rate(saga_completed_total[5m]) == 0 and rate(saga_started_total[5m]) > 0
    severity: page        # nothing is finishing
```

## 9. Numerical example

BookNook checkout saga: 7 steps, 20 checkouts/sec peak.

**Step 1 — why the pivot must be late, in money.**

```
  ORIGINAL order (pivot at step 1, capture first):
      failures after capture → refund required
      measured: during a flash sale with stock contention, 1,900 captures
      were followed by a failed reservation → 1,900 refunds
      cost per refund: payment fee £0.20 non-refunded + £0.35 handling
                       + 1 refund-mandated support contact at £2.10
                     ≈ £2.65
      total: 1,900 × £2.65 = £5,035, plus a visible "we cancelled your order"
      moment for 1,900 customers during the biggest sale of the year.

  FIXED order (pivot at capture, after reservation):
      stock is reserved BEFORE money moves, so the stock-out failure happens
      in the compensable zone and costs only a released reservation: £0.
      the remaining post-pivot failures (confirm/ship/email) are retryable
      and do not need refunds.
  ⇒ same protocol, same code shape, £5,035 and a reputational event avoided
    purely by moving one boundary.
```

**Step 2 — the compensation rate is a routine load, not an exception.**

```
  per-step success 99.5 %, 7 steps, one retry recovering 85 % of transient
  failures:
      P(success first try) = 0.995^7 = 96.6 %
      effective per-step = 0.995 + 0.005 × 0.85 = 0.99925
      P(success with retry) = 0.99925^7 = 99.48 %
      ⇒ 0.52 % of sagas compensate — at BookNook's measured 2.4 % because
        the payment step is flakier than the model; use 2.4 %.
  at 40,000 orders/month: 960 compensations/month = 32/day.
  ⇒ the compensation path runs every day, in production, at a rate that
    means it MUST be tested and monitored like the happy path. Teams that
    treat it as "error handling" discover, during an incident, that it has
    never run successfully.
```

**Step 3 — sizing the reservation TTL, which is pure arithmetic.**

```
  saga duration: p50 470 ms, p99 780 ms, p99.9 3.1 s, max observed 22 s
      (one incident where a participant hung)
  too short: reservations expire while the saga is still running → the stock
      is resold → oversell. If TTL = 2 s, then 0.1 % of sagas (those above
      p99.9) lose their stock mid-flight → 40 oversells/month. Unacceptable.
  too long: a crashed saga holds stock for the whole TTL. If TTL = 4 h, a
      crashed order holds 2 units of a popular SKU for 4 hours.
  chosen: 15 min.
      ⇒ covers max observed (22 s) with 40× margin;
      ⇒ bounds the damage of a crashed saga to 15 minutes of one order's stock;
      ⇒ and the reconciler notices stuck sagas at 10 min, i.e. BEFORE the
        reservation expires — so a human sees it while the stock is still held.
  ⇒ the TTL and the reconciler threshold should be chosen together: TTL >
    reconcile_threshold, so that the sweep happens first.
```

**Step 4 — throughput of the orchestrator, and why it is not the bottleneck.**

```
  orchestrator work per step: 1 state read + 1 state write + 1 RPC forward
      ≈ 3 ms of its own time, plus the participant's latency
  per saga: 7 steps × 3 ms = 21 ms of orchestrator time, spread over 470 ms
  at 20 sagas/sec: 20 × 21 ms = 420 ms/sec = 0.42 cores busy.
  ⇒ one orchestrator pod handles thousands of sagas/sec; the bottleneck is
    always the participants (payment at 150 ms), never the orchestrator.
  ⇒ so do not build a distributed orchestrator. Build a stateless one with a
    durable state store, and scale it horizontally (it is safe: every
    transition is a conditional UPDATE on the saga row).
```

**Step 5 — what stuck sagas cost, and the alarm threshold.**

```
  pre-reconciler: 0.9 stuck orders/day, discovered by customers (mean 2.4 days)
      0.9 × 2.4 = 2.2 open stuck orders at any time, each costing
      ~25 min of support + a goodwill gesture (£15) ≈ £28
      → £25/month, plus trust.
  post-reconciler: 0.02/day, handled automatically.
  alarm threshold: > 5 active sagas older than 10 minutes.
      justification: normal p99.9 is 3.1 s and max observed 22 s, so ANY
      saga older than 10 minutes is pathological; 5 of them is a systemic
      fault (a participant down), not a blip.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **Orchestration** | One owner, one state machine, testable failure paths | A service that knows about all participants | The outcome matters and spans >2 services — usual |
| **Choreography** | Loose coupling, no central service | No owner of the outcome; hard to unwind | Simple single-consequence reactions |
| **Late pivot** | Most failures stay compensable | Longer compensable zone = longer visible intermediates | Always (§9 step 1) |
| **Early pivot** | Short flow | Compensation in the money path (refunds) | Never, if avoidable |
| **Semantic lock + TTL** | Visible partial state; self-healing from crashes | Readers must understand the states; TTL tuning | Always |
| **Forward recovery after the pivot** | Never undoes a committed effect | A failing step can block forever → needs paging | Always |
| **Compensate after the pivot** | Simplifies the code path | **Refunds money for a transient blip** — the worst outcome | Never |
| **Long TTL** | Fewer mid-flight expiries | Crashed sagas hold resources longer | Long/slow sagas |
| **Reconciler** | Bounded stuck sagas | More code and a sweeper to own | Always |
| **Saga for a real invariant** | — | Not atomic, not isolated: the invariant is not guaranteed | Never — merge the data instead (L82 §7.2) |

## 11. Failure scenarios

**11.1 — The early pivot** (step 1). Capture before reservation cost 1,900 refunds and £5,035 in one sale. **Symptom:** refunds spike exactly when stock runs out. **Fix:** move the pivot after the reservation.

**11.2 — The un-compensable step placed early** (§7.2). The confirmation email preceded capture, so customers were told an order was confirmed and then it was cancelled. **Symptom:** support contacts about orders the customer believes exist. **Fix:** non-compensable steps go after the pivot.

**11.3 — Compensation on a post-pivot failure.** A transient database blip after the payment was captured triggered a full compensation, refunding 340 customers and cancelling 340 orders that had been paid for. **Symptom:** a burst of refunds during an unrelated DB incident. **Fix:** after the pivot, forward recovery only; a permanent post-pivot failure pages a human.

**11.4 — No reconciler** (L82 §11.2, §7.1 attempt 2). 340 orders stuck `pending` forever. **Symptom:** a slowly growing count of non-terminal rows. **Fix:** reconcile on a schedule, alarm on the active count.

**11.5 — The non-idempotent step.** A `reserve_stock` call timed out (but succeeded), was retried, and reserved twice — double-holding stock so an order that could have been fulfilled appeared sold out. **Symptom:** phantom stock-outs correlated with timeouts. **Fix:** idempotency keys recorded at the participant (L74), not just retried blindly.

**11.6 — The state write that was not atomic with the effect** (§4.4). 11 orders where stock was reserved but the saga did not know; compensations then released unreserved stock and corrupted the count. **Symptom:** available stock drifting upward, discovered by inventory audit. **Fix:** participant-recorded idempotency as the truth, or an outbox so effect and record commit together.

**11.7 — The reservation TTL shorter than the saga.** A 30-second TTL with a `p99.9` of 3.1 s was fine until one participant slowed to 40 s; those orders lost their stock mid-flight and oversold. **Symptom:** oversells correlated with a slow dependency. **Fix:** TTL ≫ max observed duration, chosen with §9 step 3's arithmetic.

**11.8 — Two orchestrators advancing the same saga.** A rolling deploy left two orchestrator instances processing the same partition; both executed step 4 (capture) — two captures for one order. **Symptom:** double charges, rare and unexplained. **Fix:** every transition is a conditional update (`WHERE state = expected`), so only one instance can advance; and the participant's idempotency key makes even a duplicated call harmless.

## 12. Alternatives

| Instead of a saga | Consider | When |
|---|---|---|
| Saga | **One database, one local transaction** | The invariant is real and the data can be co-located (L82 §7.2) |
| Saga | **2PC** (L83) | Low volume, short operations, all internal (§7.2 of L83) |
| Saga | **Outbox + idempotent consumers** (L85) | The requirement is reliable event publication, not multi-step undo |
| Saga | **Reserve → confirm → release** | The operation is a hold on a resource |
| Orchestration | **Choreography** | One event, one consequence, no unwinding |
| Saga | **Batch reconciliation** | The domain already has a nightly settlement (banks) |
| Compensations | **Idempotency only** | Every step is naturally idempotent and re-running the whole thing is safe |

## 13. When to use it

- **Any multi-service business process with a third party in it** — the canonical case.
- **When the operation takes longer than a transaction can hold locks** (seconds to days).
- **When partial states are tolerable if visible** — orders, bookings, approvals, subscriptions.
- **When every step has a real business undo**, or can be placed after the pivot where undo is not needed.
- **Always together with**: idempotent steps, a durable state store, semantic locks with TTLs, and a reconciler.

## 14. When *not* to use it

- **When the invariant is genuinely transactional** — money conservation, stock counts, ledger balances. Merge the data (L82 §7.2); compensation is not a substitute for atomicity.
- **When a step cannot be undone and cannot be deferred past the pivot** — you have no correct design, only a decision about which failure to accept.
- **When compensations are untestable** — if you cannot run the compensation path in a test, it will not work in production.
- **For latency-critical single-step operations.** A saga is for processes, not for one call.
- **As a way to avoid thinking about isolation.** The visible intermediates are a product decision that someone must make.

## 15. Common mistakes

1. **Pivoting too early**, putting refunds in the money path (§11.1).
2. **Placing a non-compensable step before the pivot** (§11.2).
3. **Compensating after the pivot** (§11.3).
4. **No reconciler**, so stuck sagas accumulate silently (§11.4).
5. **Non-idempotent steps or compensations** (§11.5).
6. **A state write that is not atomic with the effect** (§11.6).
7. **A reservation TTL shorter than real saga durations** (§11.7), or so long that crashes hold stock for hours.
8. **Two orchestrators advancing the same saga** (§11.8) — no conditional transition.
9. **Choreography with no owner of the outcome** (L82 §11.2).
10. **No semantic lock**, so other requests cannot tell a pending order from a real one.
11. **Exposing intermediate states to analytics** (L82 §11.7).
12. **Compensation code that has never run in production** — untested, unmonitored, broken.
13. **Forgetting that a compensation may charge a fee** or be refused — it is a business action, not a rollback.
14. **No alarm on compensation rate**, so a degrading dependency looks normal until it is an outage.

## 16. Interview perspective

**What is actually being tested.** Whether you can design a **state machine with undo verbs**, and whether you know the three things that decide if it works: pivot placement, idempotency, and reconciliation. Strong candidates say "compensable zone, pivot, retryable zone" and place the irreversible steps last. Weak candidates say "use a saga" and cannot say what happens when a compensation fails.

**Answers that land well:**

- *"How do you keep this checkout consistent without a distributed transaction?"* — "A saga with an explicit state machine. Create the order and reserve stock with a 15-minute TTL, authorise the card, then capture — that capture is the pivot. Everything before it is compensable, so a failure releases the reservation and voids the authorisation; everything after it is retryable forever, so a database blip after capture never becomes a refund. Steps and compensations are idempotent on `(saga_id, step)`, the state transitions are conditional updates so two orchestrators cannot both advance, and a reconciler sweeps sagas older than ten minutes and asks each participant what actually happened."
- *"What happens if a compensation fails?"* — "It retries, and if it keeps failing the saga goes to `COMPENSATION_STUCK` and pages. The reservation's TTL means the resource frees itself anyway, which is why the TTL matters. What I never do is let a post-pivot step compensate — that would refund a customer because a notification failed."
- *"Orchestration or choreography?"* — "Orchestrate the transaction, choreograph the notifications. Choreography is fine for one event with one consequence; for a multi-step outcome with unwinding, someone has to own the whole state machine, or you get orders stuck in `pending` because no service was responsible for the end state."
- *"When is a saga the wrong answer?"* — "When there is a real invariant, like never selling stock you do not have. Compensation does not restore an invariant, it repairs it after the fact and visibly. If the data can be co-located, merge it and use one local transaction — I have done that migration and it removed an entire class of incidents."

**Common interview tasks:** design a saga for a described flow; decide the pivot; write the compensations; handle a compensation failure; choose orchestration vs choreography; size a reservation TTL; explain which anomalies are visible.

**Red flags interviewers watch for:**

- "Compensate everything" with no pivot concept.
- Non-idempotent steps or compensations.
- No mention of stuck sagas or reconciliation.
- Compensating after an irreversible step.
- Not knowing that intermediate states are visible.
- Choreography for a multi-step flow with no owner.
- No numbers: no compensation rate, no TTL, no stuck-saga rate.
- Using a saga where a local transaction would do.

## 17. Knowledge check

**Conceptual**

1. Explain the three zones of a saga and what each requires of a step. Then explain why the pivot should be as late as possible, using the refund arithmetic from §9 step 1, and why a non-compensable step must be placed after it.
2. Explain why every saga step and compensation must be idempotent, and why the step's effect and the saga's state write must be atomic together. Describe what goes wrong when they are not, using §11.6.
3. Explain why a reconciler is mandatory, what it does that ordinary retries cannot, and what rule you would use to distinguish "advance it" from "compensate it" from "page a human".

**Practical scenario**

4. BookNook's checkout has 7 steps: create order, reserve stock, authorise card, capture payment, confirm order, request shipment, send confirmation email. The payment gateway supports authorise/capture/void; shipment cannot be un-shipped; email cannot be unsent. Peak is 20 checkouts/sec, per-step success is 99.5 %, the `p99.9` saga duration is 3.1 s and the maximum observed is 22 s. Design the complete saga: the ordering with the pivot marked and justified, every compensation with its idempotency key, the semantic locks and their TTLs with arithmetic, the state machine with all terminal and non-terminal states, the handling for each of: failure before the pivot, failure after the pivot, permanent post-pivot failure, and compensation failure; the reconciler's decision rules; the metrics and alarms with thresholds; and what the customer sees at each stage.

**Calculation**

5. A 6-step saga has per-step success rates 99.8 %, 99.8 %, 98.5 %, 99.9 %, 99.9 % and 99.9 %, with one retry per step recovering 80 % of failures. (a) Compute the first-attempt success probability. (b) Compute it with retries. (c) Compute the compensation rate per 100,000 sagas. (d) If each compensation costs £0.40 in fees and handling, and 3 % of compensations require a support contact at £2.10, compute the monthly cost at 40,000 sagas/month.

6. A saga has `p50` 400 ms, `p99` 900 ms, `p99.9` 3 s, and a maximum observed of 25 s. Reservations hold one unit of stock each, and the top SKU sells 400 units/day at 2 units per order. (a) Compute a defensible TTL and justify it against the observed distribution. (b) Compute how many units are locked by crashed sagas at any time if 0.05 % of sagas crash and the TTL is your answer. (c) Recompute with a 2-second TTL and state the oversell risk. (d) State the relationship you would enforce between the TTL and the reconciler threshold, and why.

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
  - [x] 14. Circuit breakers, bulkheads and graceful degradation (L77)
  - [x] 15. Distributed locks and leases (L78)
  - [x] 16. Leader election (L79)
  - [x] 17. Quorums (L80)
  - [x] 18. Consensus and Raft (L81)
  - [x] 19. Distributed transactions (L82)
  - [x] 20. Two-phase commit (L83)
  - [x] **21. Saga pattern (L84) ← you are here**
  - [ ] 22. Transactional outbox and change data capture (L85)
  - [ ] 23. Monoliths, modular monoliths, microservices (L86)
  - [ ] 24. Service discovery and configuration (L87)
  - [ ] 25. Observability: logs, metrics, traces (L88)
  - [ ] 26. SLIs, SLOs and error budgets (L89)
  - [ ] 27. High availability and disaster recovery (L90)
  - [ ] 28. Active-passive versus active-active (L91)
  - [ ] 29. RTO and RPO (L92)

**NEXT LESSON: Phase 4, Topic 22 — Transactional outbox and change data capture**

---

---

# Lesson 85 — Transactional Outbox and Change Data Capture

*Phase 4, Topic 22*

## 1. Simple definition

The **transactional outbox** is a pattern that makes "change the database *and* publish a message" reliable, by refusing to do two writes at all.

Instead of writing to the database and then publishing to a broker (two systems, no shared transaction — the dual-write problem of Lesson 82), you write **both** the business row and a message row into **the same database, in the same local transaction**. A separate process — the **message relay** — later reads those message rows and publishes them to the broker.

**Change Data Capture (CDC)** is the technique of producing a stream of row-level changes by reading the database's own replication log, rather than by querying tables. CDC is the most efficient way to implement the relay, and is also a general-purpose integration tool in its own right.

Terms defined before use:
- **Outbox table**: an ordinary table in the service's own database that holds messages waiting to be published. "Outbox" because it is the outgoing-mail tray of the service.
- **Relay** (also *message relay*, *publisher*, *forwarder*): the component that moves rows from the outbox to the broker and marks them done.
- **Aggregate**: a domain-driven-design term for a cluster of data treated as one consistency unit — for BookNook, one `Order` with its order lines is one aggregate. The **aggregate ID** is its primary identifier (e.g. `order-90210`).
- **WAL** (write-ahead log, PostgreSQL) / **binlog** (binary log, MySQL): the sequential file the database writes *before* it modifies data pages, introduced in Lesson 40. It is the authoritative record of every committed change.
- **Logical decoding / logical replication**: translating those physical log entries into understandable row-level events (`INSERT into orders, values …`).
- **Replication slot**: a PostgreSQL server-side bookmark that remembers how far a consumer has read, and prevents the server from deleting WAL the consumer has not read yet.

The one sentence to retain:

> The outbox converts an **impossible atomic write across two systems** into a **guaranteed-eventual single write to one system**.

## 2. Why it exists

Lesson 82 established the dual-write problem. Recall the shape of it. BookNook's order service must do two things when a customer checks out:

```
1. INSERT INTO orders (...)             -- PostgreSQL
2. publish OrderPlaced to Kafka          -- broker
```

There is no transaction spanning both. So four orderings exist, and two of them are wrong:

| Order of operations | Failure point | Result |
|---|---|---|
| DB commit, then publish | process dies after commit | Order exists; **no event**. Warehouse never ships. Silent, permanent. |
| DB commit, then publish | broker unavailable | Same as above unless you retry — and retries can themselves fail |
| Publish, then DB commit | DB rolls back | Event exists; **no order**. Warehouse ships a phantom. Money not taken. |
| Publish, then DB commit | process dies after publish | Same phantom |

Lesson 83 shows that two-phase commit (2PC) can in principle make this atomic. In practice 2PC across a relational database and Kafka is not available (Kafka has no XA resource manager in normal deployments), is slow, and introduces a blocking coordinator — the exact thing Lesson 83 warns about. So 2PC is not the answer here.

The retry-with-idempotency answer is also insufficient on its own: if the **process itself dies** between commit and publish, there is nobody left to retry. The retry state must be **durable**, and the only durable store that is already in the transaction is the database itself.

That observation *is* the outbox. Put the intent-to-publish in the transaction. Then crash recovery is automatic: the row is still sitting there when anything comes back up.

So the outbox exists to give you a property that looks impossible:

> **If the order exists, the event will eventually be published. If the order does not exist, the event will never be published.** Guaranteed, with no distributed transaction.

The cost of this guarantee is that "eventually" is not "immediately", and that the event may be published **more than once**. Both are acceptable in exchange for never losing or inventing one.

## 3. Real-world analogy

**The office outgoing-mail tray.**

You are a clerk recording a sale. The rule is: you must update the ledger *and* post a dispatch note to the warehouse. If you walk to the postbox yourself, you might update the ledger and then be hit by a bus on the way to the postbox — the sale is recorded and the goods never ship.

So the office changes the procedure. At your desk, in one motion, you write the ledger entry **and** drop a sealed dispatch note into the outgoing-mail tray on your desk. Both are on your desk; both happen or neither does (if you are interrupted mid-motion, you tear up the half-finished page and start again).

A separate person — the post clerk — empties the tray every few minutes, walks to the postbox, posts everything, and ticks each note off a list. If the post clerk is hit by a bus, the notes are still in the tray; tomorrow's clerk posts them. If the post clerk posts a note and dies before ticking it off, tomorrow's clerk posts it **again** — so the warehouse must be able to recognise a duplicate dispatch note (Lesson 74).

CDC is a different post clerk. Rather than reading the tray, this clerk reads the *carbon copy book* — the bound, numbered, append-only book in which every ledger entry is automatically duplicated as it is written. The clerk never has to interrupt you, never has to search the tray, and cannot miss an entry because the book is written by the same pen stroke as the ledger.

**Where the analogy breaks down:**

| Office mail tray | Transactional outbox / CDC |
|---|---|
| The tray is physically separate from the ledger | The outbox table is in the *same database*, which is the entire point — separateness would reintroduce the dual write |
| A note posted twice is obviously a mistake | A message delivered twice is **expected and normal**; consumers must be idempotent by design (Lesson 74) |
| The clerk empties the tray in whatever order they grab notes | Ordering matters: all events for one order must be published in the order they were written, or the warehouse sees `OrderCancelled` before `OrderPlaced` |
| An old tray just gets emptied | An outbox table that is never pruned grows without bound and eventually degrades the *business* tables sharing its database (index bloat, vacuum pressure, backup size) |
| The carbon-copy book is a convenience | The WAL is **mandatory and already written** for crash recovery (Lesson 40). CDC is free-riding on work the database must do anyway — that is why its overhead is so low. |
| The clerk reading the book cannot slow down the ledger | A CDC consumer that stops reading **does** harm the database: PostgreSQL retains WAL for an unread replication slot until the disk fills. A stalled CDC connector can take a production database down. |

That last row is the single most dangerous operational fact in this lesson, and §11 returns to it.

## 4. Detailed technical explanation

### 4.1 The core mechanism

```
BEGIN;
  INSERT INTO orders ...;          -- the business change
  INSERT INTO outbox ...;          -- the intent to publish
COMMIT;                            -- ONE atomic commit, ONE system
```

Atomicity here is ordinary local ACID atomicity (Lesson 41). Nothing distributed is involved. If the commit succeeds, both rows exist. If it fails, neither does. There is no window.

The relay then performs an **at-least-once** publish:

```
read unpublished outbox rows
  → publish to broker
    → broker acknowledges
      → mark rows processed
```

The gap between "broker acknowledges" and "mark rows processed" is the duplicate window. A crash there republishes on restart. You **cannot** close this window — closing it would require an atomic write across the broker and the database, which is the original problem. So you stop trying, and you make consumers idempotent instead. This is the standard trade in distributed systems (Lesson 73): you do not get exactly-once delivery; you get at-least-once delivery plus idempotent processing, which together produce exactly-once *effect*.

### 4.2 The outbox table schema

Every column earns its place:

| Column | Purpose |
|---|---|
| `id` (monotonic) | Publication order within the table; also the relay's cursor |
| `aggregate_type` | `order`, `payment`, `shipment` — usually maps to a topic |
| `aggregate_id` | **The partition key.** Guarantees per-aggregate ordering (§4.5) |
| `event_type` | `OrderPlaced`, `OrderCancelled` — lets consumers filter |
| `payload` (JSONB) | The event body. Self-contained; see §4.7 |
| `headers` (JSONB) | Trace context, schema version, tenant |
| `created_at` | Measuring relay lag; debugging |
| `processed_at` | NULL = pending. Non-NULL = published |

A **partial index** on `(id) WHERE processed_at IS NULL` is essential: it indexes only the pending rows, so the relay's query stays fast even when the table holds millions of processed rows awaiting cleanup.

### 4.3 Relay implementation A — polling publisher

The relay runs a loop: query for pending rows, publish, mark done, sleep, repeat.

Characteristics:
- **Simple.** It is a `SELECT`, a `for` loop and an `UPDATE`. No new infrastructure. You can build it in an afternoon.
- **Latency is bounded below by the poll interval.** Mean added latency ≈ interval / 2; worst case ≈ interval. A 1-second poll means events are, on average, half a second stale before they even leave the service.
- **Load is proportional to poll frequency, not to traffic.** At 3 a.m. with zero orders, a 200 ms poller still issues 432,000 queries a day finding nothing. This is the fundamental inefficiency: you pay for *looking*, not for *finding*.
- **Concurrency needs care.** Multiple relay instances must not publish the same row twice *unnecessarily*. `SELECT … FOR UPDATE SKIP LOCKED` is the standard mechanism: each instance locks a disjoint batch. (`SKIP LOCKED` tells the database "do not wait for rows another transaction has locked; just ignore them and return the rest.")
- **It cannot miss a row**, because committed rows are visible to the next query. That is a real guarantee and it is why polling is a perfectly respectable production choice.

Polling is the correct starting point. Do not skip it in pursuit of elegance; a polling relay that works beats a CDC pipeline nobody can operate.

### 4.4 Relay implementation B — log tailing (CDC)

Instead of querying, connect to the database as a **replication client** and receive every committed change as it happens.

- PostgreSQL: a logical replication slot with an output plugin (`pgoutput`, `wal2json`) streams decoded row changes.
- MySQL: the connector registers as a replica and reads the **binlog** in `ROW` format.
- SQL Server: the built-in CDC/change-tracking feature. Oracle: LogMiner or XStream.

Why this is strictly better on the two axes that matter:

1. **Latency.** The change is pushed the moment the commit record hits the log. Typical end-to-end lag is **5–20 ms** rather than hundreds of milliseconds, and it does not degrade when the table is large.
2. **Load.** There are no extra queries at all. The database writes the WAL regardless — that is its crash-recovery mechanism from Lesson 40. Logical decoding adds CPU (typically **1–3%** of one core at moderate write rates, approximate and workload-dependent) and zero extra I/O against your business tables.

And two correctness advantages:

3. **It cannot miss a change.** The log *is* the set of committed changes. There is no query predicate to get wrong, no clock skew, no "rows committed out of ID order" hazard that bites naive `WHERE id > cursor` pollers under concurrent transactions.
4. **It requires no application modification.** The application writes its tables normally; the stream appears.

Point 4 is why CDC is useful far beyond the outbox pattern, and §4.8 develops that.

### 4.5 Ordering guarantees

The outbox gives you a total order (`id`). Brokers usually do not preserve a global total order — Kafka guarantees order only **within a partition** (Lesson 72).

The rule:

> **Publish with `aggregate_id` as the message key.** All events for order `90210` hash to one partition, so they are consumed in the order they were written. Events for different orders may interleave freely, and that is fine, because they are independent.

Two things break this if you are careless:
- **Parallel relay workers.** If two workers each grab a batch and publish concurrently, two events for the *same* aggregate can be published out of order. Fix: partition the relay's work **by aggregate**, not by arbitrary row ranges — e.g. `WHERE hashtext(aggregate_id) % 4 = worker_id`.
- **Retry on a failed send with in-flight successors.** If message 1 fails and is retried while message 2 has already gone, order is lost. Fix: with Kafka, set `max.in.flight.requests.per.connection=1` plus `enable.idempotence=true`, or use a per-key send queue.

CDC inherits ordering from the log, which is commit order — a genuine total order — then the same keying rule applies at publish time.

### 4.6 Cleanup and table growth

An outbox is a queue, not an archive. If you never delete, three things degrade:
- The partial index stays small (good), but the **heap** grows, inflating backups, restores and storage cost.
- In PostgreSQL, `UPDATE … SET processed_at` creates a dead tuple per row; autovacuum must keep up or the table bloats.
- Long-running cleanup `DELETE`s take locks and generate WAL — which the CDC connector then has to decode.

Three standard strategies:

| Strategy | How | Notes |
|---|---|---|
| Batched delete | `DELETE … WHERE processed_at < now() - interval '7 days' LIMIT 10000`, in a loop, off-peak | Simplest. Keep batches small to avoid long locks. |
| Partitioning | Daily/weekly partitions; `DROP TABLE` the old partition | Near-instant reclaim, no vacuum pressure. Best at scale. |
| Delete-on-publish | Relay `DELETE`s instead of `UPDATE`s the row | Smallest table, but destroys the audit trail and makes "did we publish X?" unanswerable |

Keep **at least** long enough to debug an incident — 3 to 7 days is typical. If you want the permanent record, that is event sourcing, not an outbox.

### 4.7 What goes in the payload

Two styles, and the choice is a real architectural decision:

- **Event-carried state transfer**: the payload contains everything a consumer needs (`order_id`, `customer_id`, lines, totals). Consumers never call back. Loosely coupled, but payloads are large and may contain data you later regret exposing.
- **Event notification (thin event)**: the payload is just `{order_id}`; consumers call `GET /orders/{id}`. Small messages, but every consumer now depends on your API being up — and may read a *newer* state than the event described, which is a subtle and nasty bug class.

Default to event-carried state transfer with an explicit `schema_version` field. Include a trace ID in headers so the published message joins the same distributed trace as the HTTP request that caused it (forward reference: **Observability: logs, metrics, and distributed traces**, Phase 4 Topic 25).

### 4.8 Change Data Capture as an integration tool

Once a stream of row changes exists, many long-standing integration problems collapse into "subscribe to the stream":

| Use case | How CDC solves it |
|---|---|
| **Cache invalidation** (Lesson 29) | Subscribe to `books`; on each change, evict or refresh that key. Removes the classic "someone updated the row via a script and the cache went stale for 24 hours" failure. |
| **Search index synchronisation** (Lesson 47) | Subscribe to `books`, project into the Elasticsearch document, index it. No dual write between the database and the index. |
| **Data warehouse / analytics ingestion** | Stream changes into the warehouse continuously instead of a nightly full-table export. Minutes-fresh dashboards instead of day-old ones, with no nightly load spike. |
| **Service decoupling** | A new service needs order data; it subscribes rather than querying your database or adding an endpoint. |
| **Strangler-fig migration** (Lesson 86, §4.6) | Keep the legacy monolith's database and a new service's database in sync *during* the migration, so you can move traffic gradually and roll back. |
| **Audit trails** | Every change, with before/after images, captured outside application code — so it cannot be bypassed by a direct SQL fix. |

### 4.9 Debezium and the standard CDC architecture

**Debezium** is the de-facto open-source CDC platform: a set of connectors (PostgreSQL, MySQL, MongoDB, SQL Server, Oracle, Db2, Cassandra) that read database logs and emit change events to Kafka.

```
Database → WAL/binlog → Debezium connector → Kafka topic per table → consumers
```

Operating details worth knowing:
- Each table typically gets its own topic: `booknook.public.orders`.
- The event envelope carries `before`, `after`, `op` (`c`reate / `u`pdate / `d`elete / `r`ead-snapshot), `source` (LSN, transaction ID, timestamp) and `ts_ms`.
- Debezium stores its read position (LSN/binlog offset) in Kafka, so a restart resumes exactly where it stopped.
- On first start it performs a **consistent snapshot** of existing rows (emitted as `op=r`), then switches to streaming. Snapshots of large tables can take hours and historically took locks; modern **incremental snapshotting** chunks the work and runs concurrently with streaming.
- It is itself at-least-once: after a crash it may re-emit events already written. Again — **consumers must be idempotent** (Lesson 74).

### 4.10 The listen-to-yourself pattern

A variant worth knowing. Rather than updating its own tables and then publishing, the service:

1. Writes **only** the event (to the outbox or directly to the log).
2. Publishes it.
3. **Subscribes to its own topic** and applies the change to its own read tables when the event comes back.

The benefit: the service's own state and every consumer's state are derived from the *same* event stream, so they cannot diverge in the way they can when the write path and the publish path are separate. The cost: your own API becomes read-your-writes-inconsistent — the customer who just clicked "Place order" may not see it on the orders page for 50 ms. You must then either pin that reader to a synchronous path or show an optimistic UI. This is a close cousin of the CQRS pattern; use it when consumer-state convergence matters more than immediate self-consistency.

### 4.11 CDC pitfalls

1. **Schema changes.** `ALTER TABLE ADD COLUMN` changes the shape of every downstream event. Use a schema registry with compatibility enforcement; add nullable columns rather than renaming; never reuse a column name for a different meaning.
2. **Snapshots.** The initial snapshot of a 500 GB table is a sustained read that competes with production traffic. Snapshot from a replica where possible; use incremental snapshots; schedule off-peak.
3. **Replication slot disk growth.** The headline operational risk. If the connector stops (crash, bad deploy, Kafka outage) the slot stops advancing and PostgreSQL **retains every WAL segment since that point**. Disk fills; the database stops accepting writes; you have caused a full outage from a monitoring component. Mitigations: alert on `pg_replication_slots.restart_lsn` lag in bytes; set `max_slot_wal_keep_size` so the server drops the slot rather than dying (you then must re-snapshot — a deliberate choice of recoverable pain over catastrophic pain); never leave an orphaned slot behind after decommissioning a connector.
4. **Internal schema becomes a public contract.** This is the deepest problem. Raw CDC publishes your *table structure*. The moment three teams consume `booknook.public.orders`, you can no longer rename a column. You have accidentally given away your private data model. Mitigations, in increasing order of discipline: a transformation layer that maps raw change topics to curated domain events; or — better — use CDC **on the outbox table only**, so what you publish is an event you deliberately designed, and your business tables stay private. This hybrid (outbox pattern + CDC relay) is the recommended default and is what §8 implements.

## 5. Request / data flow

End-to-end, for BookNook's `POST /orders`, using outbox + CDC relay:

1. The client sends `POST /orders` with an `Idempotency-Key` header (Lesson 74) through the API gateway (Lesson 25) to an order-service instance chosen by the load balancer (Lesson 23).
2. The order service validates the basket and reserves payment authorisation.
3. The service opens a database transaction: `BEGIN`.
4. It inserts the `orders` row and the `order_lines` rows.
5. **In the same transaction**, it inserts one `outbox` row: `aggregate_type='order'`, `aggregate_id='90210'`, `event_type='OrderPlaced'`, `payload={…}`.
6. `COMMIT`. PostgreSQL writes the commit record to the WAL and fsyncs it. Both rows are now durable, atomically.
7. The service returns `201 Created` to the client. **The customer's request is finished.** Nothing downstream is on the critical path.
8. PostgreSQL's logical decoder reads the new WAL records and converts them into row-change events.
9. The Debezium connector receives the `outbox` INSERT, applies the outbox-event routing transformation (which extracts `payload` as the message body, `aggregate_id` as the key, and `aggregate_type` as the topic), and produces to Kafka topic `booknook.orders`.
10. Kafka acknowledges the write with `acks=all` (all in-sync replicas have it). Debezium records its new WAL position (LSN) in its offsets topic.
11. Consumers — warehouse service, email service, analytics loader — each read from their own consumer group at their own pace (Lesson 72).
12. Each consumer checks a dedupe store keyed on `event_id`. If seen, acknowledge and skip. If not, perform the work, record the ID, acknowledge (Lesson 74).
13. A nightly cleanup job deletes outbox rows older than 7 days in 10,000-row batches.

The critical property: **step 7 happens before steps 8–13 and does not depend on them.** Kafka can be down for an hour; orders keep being accepted and every event is published when it returns.

## 6. ASCII diagram

```
                       ORDER SERVICE
  POST /orders  ┌────────────────────────────────┐
  ─────────────►│  validate → reserve payment    │
                │                                │
                │   BEGIN ───────────────────┐   │
                │     INSERT orders          │   │  ONE local
                │     INSERT order_lines     │   │  transaction
                │     INSERT outbox  ◄───────┤   │  (atomic)
                │   COMMIT ──────────────────┘   │
                └───────────────┬────────────────┘
  201 Created ◄─────────────────┘
  (customer done — nothing below is on the critical path)
                                │
                     ┌──────────▼───────────┐
                     │   PostgreSQL         │
                     │  ┌────────────────┐  │
                     │  │ orders         │  │
                     │  │ order_lines    │  │
                     │  │ outbox         │  │
                     │  └────────────────┘  │
                     │  ┌────────────────┐  │
                     │  │ WAL (Lesson 40)│──┼──┐ written anyway
                     │  └────────────────┘  │  │ for crash recovery
                     └──────────────────────┘  │
                                               │
        RELAY OPTION A                RELAY OPTION B
        polling publisher             log tailing (CDC)
   ┌──────────────────────┐      ┌────────────────────────┐
   │ every 200 ms:        │      │ replication slot       │
   │  SELECT ... WHERE    │      │  + logical decoding    │
   │   processed_at IS    │      │  Debezium connector    │
   │   NULL FOR UPDATE    │      │                        │
   │   SKIP LOCKED        │      │ push-based, ~10 ms lag │
   │  publish             │      │ no extra queries       │
   │  UPDATE processed_at │      │ cannot miss a change   │
   └──────────┬───────────┘      └───────────┬────────────┘
              │                              │
              └──────────────┬───────────────┘
                             ▼
              ┌──────────────────────────────┐
              │  Kafka  topic booknook.orders│
              │  key = aggregate_id  ────────┼── per-order ordering
              │  partitions 0..5             │
              └───┬──────────┬──────────┬────┘
                  │          │          │
             ┌────▼───┐ ┌────▼────┐ ┌───▼─────────┐
             │Warehouse│ │ Email   │ │ Analytics   │
             │ svc     │ │ svc     │ │ loader      │
             │ dedupe  │ │ dedupe  │ │ dedupe      │
             │(L74)    │ │ (L74)   │ │ (L74)       │
             └─────────┘ └─────────┘ └─────────────┘

  GUARANTEE:  order row exists  ⟺  event eventually published
  DELIVERY :  at-least-once  →  consumers MUST be idempotent
```

## 7. Detailed example

### 7.1 BookNook — publishing `OrderPlaced` reliably

Before. BookNook's checkout did this:

```
order = db.save(order)                 # commit
kafka.send("orders", OrderPlaced(order))   # separate, unprotected
```

Over one quarter the team observed 31 "ghost orders" — paid orders with no shipment — each found only when the customer emailed support, on average 2.4 days later. Root cause analysis showed three causes: pod evictions during deploys (19), a 90-second Kafka partition leader election during which sends timed out and the catch block only logged (9), and an OOM kill (3). Every one was a dual write failing between commit and publish.

After. The checkout handler inserts the outbox row inside the same transaction. The team chose the **hybrid**: outbox table for a designed event contract, CDC (Debezium) as the relay for low latency and low load.

Observable results after the change (typical of this migration):
- Ghost orders: **0** across the following two quarters.
- Checkout p99 latency fell by 34 ms, because the synchronous Kafka `send()` left the request path entirely.
- When Kafka was deliberately stopped for 22 minutes during a game day, checkout kept working; 1,140 outbox rows accumulated and drained in 41 seconds on recovery, oldest-first, with per-order ordering intact.
- Duplicates did occur: during one connector restart, 83 events were re-emitted. All consumers absorbed them silently via their dedupe tables. Nobody noticed, which is the correct outcome.

The warehouse team also noticed a second-order benefit. Because `OrderPlaced` is now a designed event in `payload` rather than the raw `orders` row, BookNook was able to rename the internal column `cust_id` to `customer_id` without any downstream coordination — the event contract was unaffected. That is §4.11 pitfall 4 being avoided on purpose.

### 7.2 Contrasting example — a bank's regulatory reporting feed

A retail bank must report every account balance change to a regulator within 15 minutes. The core banking system is a 30-year-old COBOL/Db2 application. Nobody is permitted to modify it — change control alone would take 9 months.

The outbox pattern is **unavailable**: it requires the application to insert the outbox row, and the application cannot be changed. So the bank uses **pure CDC**: a connector reads the Db2 transaction log and streams `ACCOUNT_BALANCE` changes into a processing pipeline that enriches, formats and submits them.

This illustrates the genuine division of labour:
- **Outbox** when you control the application and want to publish **designed domain events**. The event is a deliberate public contract.
- **Pure CDC on business tables** when you do **not** control the application, or when the consumer genuinely wants raw row changes (replication, warehouse ingestion, cache invalidation). You accept that your table schema becomes a contract, and you manage that with a transformation layer.

The bank also hit pitfall 3 in production: a connector crashed on a Friday evening and was not restarted until Monday. The retained log grew by 410 GB and the database's log filesystem reached 94% before an on-call engineer intervened. They now alert at 10 GB of retained log and page at 25 GB.
## 8. Code or configuration example

```sql
-- ── The outbox table. Every column earns its place (§4.2) ──────────────────
CREATE TABLE outbox (
  id             bigserial PRIMARY KEY,
  aggregate_type text        NOT NULL,      -- 'order' → topic
  aggregate_id   text        NOT NULL,      -- THE PARTITION KEY (§4.5)
  event_type     text        NOT NULL,      -- 'OrderPlaced'
  payload        jsonb       NOT NULL,      -- self-contained body (§4.7)
  headers        jsonb       NOT NULL DEFAULT '{}',
  created_at     timestamptz NOT NULL DEFAULT now(),
  processed_at   timestamptz                -- NULL = pending
);

-- The relay's query must stay fast even with millions of processed rows.
CREATE INDEX outbox_pending_idx ON outbox (id) WHERE processed_at IS NULL;
-- Ordering helper for partitioned relays.
CREATE INDEX outbox_agg_idx ON outbox (aggregate_id, id);
```

```sql
-- ── Write path: ONE transaction, ONE system (§4.1) ────────────────────────
BEGIN;
  INSERT INTO orders (id, customer_id, total_pence, status)
       VALUES ('90210', 'C-4417', 4194, 'placed');
  INSERT INTO order_lines (order_id, sku, qty) VALUES ('90210','HP-4471',2);
  INSERT INTO outbox (aggregate_type, aggregate_id, event_type, payload, headers)
       VALUES ('order', '90210', 'OrderPlaced',
               '{"order_id":"90210","customer_id":"C-4417","total_pence":4194,
                 "lines":[{"sku":"HP-4471","qty":2}],"schema_version":2}'::jsonb,
               '{"trace_id":"trace-9f2c4a10","tenant":"uk"}'::jsonb);
COMMIT;                    -- both rows durable, or neither. No window.
```

```python
# ── Relay A: polling publisher (§4.3). Correct, boring, shippable in a day. ─
async def relay_loop(db, producer, *, batch=200, poll_s=0.2, worker_id=0, workers=4):
    while True:
        rows = await db.fetch("""
            WITH claimed AS (
              SELECT id FROM outbox
               WHERE processed_at IS NULL
                 -- partition by AGGREGATE, not by row range, so two workers
                 -- never publish two events of the same order out of order (§4.5)
                 AND hashtext(aggregate_id) % $2 = $3
               ORDER BY id
               LIMIT $1
               FOR UPDATE SKIP LOCKED        -- disjoint batches, no waiting
            )
            SELECT * FROM outbox WHERE id IN (SELECT id FROM claimed) ORDER BY id
        """, batch, workers, worker_id)

        if not rows:
            await asyncio.sleep(poll_s)          # costs a query even when idle
            continue

        for r in rows:
            await producer.send(
                topic=f"booknook.{r['aggregate_type']}s",
                key=r["aggregate_id"].encode(),   # ← per-order ordering
                value=r["payload"],
                headers=r["headers"],
                acks="all",
            )
        # Duplicate window: a crash between send and this UPDATE republishes
        # the batch. That is why consumers dedupe (L74). Closing the window
        # would require an atomic write across broker + DB — the original
        # problem, reintroduced.
        await db.execute(
            "UPDATE outbox SET processed_at = now() WHERE id = ANY($1)",
            [r["id"] for r in rows])
```

```json
// ── Relay B: Debezium with the outbox event router (§4.4, §4.9) ────────────
// Only the OUTBOX table is captured, so the published events are the ones
// you designed and your business tables stay private (§4.11 pitfall 4).
{
  "name": "booknook-outbox",
  "config": {
    "connector.class": "io.debezium.connector.postgresql.PostgresConnector",
    "database.hostname": "orders-db.internal",
    "database.dbname": "booknook_orders",
    "plugin.name": "pgoutput",
    "slot.name": "booknook_outbox",
    "publication.autocreate.mode": "filtered",
    "table.include.list": "public.outbox",
    "transforms": "outbox",
    "transforms.outbox.type": "io.debezium.transforms.outbox.EventRouter",
    "transforms.outbox.table.field.event.id": "id",
    "transforms.outbox.table.field.event.key": "aggregate_id",
    "transforms.outbox.table.field.event.type": "event_type",
    "transforms.outbox.table.field.event.payload": "payload",
    "transforms.outbox.route.by.field": "aggregate_type",
    "transforms.outbox.route.topic.replacement": "booknook.${routedByValue}s",
    "transforms.outbox.table.expand.json.payload": "true",
    "key.converter": "org.apache.kafka.connect.storage.StringConverter",
    "value.converter": "io.confluent.connect.avro.AvroConverter",
    "value.converter.schema.registry.url": "http://schema-registry:8081",
    "producer.override.acks": "all",
    "producer.override.enable.idempotence": "true",
    "heartbeat.interval.ms": "10000",
    "slot.max.retries": "6"
  }
}
```

```sql
-- ── Cleanup: partitioning beats DELETE at scale (§4.6) ────────────────────
CREATE TABLE outbox (LIKE outbox_template) PARTITION BY RANGE (created_at);
CREATE TABLE outbox_2026_10_30 PARTITION OF outbox
  FOR VALUES FROM ('2026-10-30') TO ('2026-10-31');

-- Daily job: create tomorrow's partition, drop the one older than 7 days.
-- DROP TABLE is a metadata operation: no dead tuples, no vacuum, no WAL flood.
-- A batched DELETE of 900,000 rows instead generates ~1.1 GB of WAL that the
-- CDC connector must then decode — cleanup causing its own load spike.
```

```yaml
# ── The alarms that matter (§11.1, §11.2) ─────────────────────────────────
alerts:
  - expr: pg_replication_slots_wal_retained_bytes{slot="booknook_outbox"} > 10e9
    for: 10m
    severity: page          # the disk-filling failure, §11.1

  - expr: max(now() - outbox.created_at) FILTER (WHERE processed_at IS NULL) > 30
    severity: page          # relay lag: events are not leaving the building

  - expr: rate(outbox_rows_total[5m]) > 0 and rate(outbox_published_total[5m]) == 0
    for: 5m
    severity: page          # rows arriving, nothing published: relay is stuck

  - expr: count(outbox_relays_running) != 2
    severity: page          # a dead relay is silent otherwise

  - expr: pg_table_size('outbox') > 50e9
    severity: ticket        # cleanup has stopped working (§4.6)
```

## 9. Numerical example

BookNook's order service: **10 orders/sec peak**, 60/sec baseline across all event types (L71 §7), 2 KB average outbox row.

**Step 1 — what the outbox costs, in rows and bytes.**

```
  outbox rows:  10/sec peak × 86,400 = 864,000/day peak-rate; realistically
                ~2.2 M/day across all event types at 60/sec baseline... but the
                outbox only carries events the ORDER SERVICE writes:
                ~1.4 M rows/day × 2 KB              = 2.8 GB/day
  partial index (pending only) ≈ 3 % of rows at any time
      42,000 × ~120 B                              = 5 MB   ← stays small
  retention 7 days: 9.8 M rows                     = 19.6 GB heap + index
  ⇒ trivial on a 1.2 TB database (L61), and WHY the partial index matters:
    without it, the relay's query plans against 9.8 M rows instead of 42,000.
```

**Step 2 — polling cost, which is the pattern's honest weakness.**

```
  200 ms poll, 2 relay instances, one query each:
      2 × (86,400 / 0.2) = 864,000 queries/day   — 10 queries/sec, forever
  at 3 a.m. with zero orders, that is still 10 queries/sec finding nothing.
  Mean added publish latency = interval/2 = 100 ms; worst case 200 ms.
  ⇒ acceptable for a warehouse/email path (humans measure these in minutes).
  ⇒ NOT acceptable for a user-visible path with a 300 ms budget.

  CDC instead: 0 extra queries, ~10 ms lag, 1–3 % of one core.
  ⇒ the reason to move to CDC is LATENCY and IDLE LOAD, not correctness.
```

**Step 3 — what "eventually" means end to end, and why the sum matters.**

```
  path                                        p50      p99
  ──────────────────────────────────────────────────────────
  commit → WAL fsync                         1 ms     4 ms
  logical decoding + Debezium                8 ms     25 ms
  Kafka produce (acks=all, 3 replicas)       6 ms     30 ms
  consumer poll interval                     50 ms    100 ms
  ──────────────────────────────────────────────────────────
  total publish→consume                     65 ms    159 ms
  + consumer processing (email render)      120 ms   400 ms
  ⇒ customer-visible lag for a confirmation email: ~200 ms p50.
    With the POLLING relay instead of CDC: +100 ms mean, so ~300 ms p50.

  But the guarantee is about the TAIL, not the mean:
    Kafka down 22 minutes (the §7.1 game day) → 1,140 rows accumulate,
    drain in 41 s on recovery, oldest-first, per-order order intact.
  ⇒ the design's promise is: p99.9 of publish latency is bounded by OUTAGE
    DURATION, not by load. That is the property worth having.
```

**Step 4 — the duplicate rate, and why nobody notices.**

```
  relay crash/restart rate: ~2 per week per service (deploys, evictions)
  rows republished per restart: ≤ 1 batch = 200
  ⇒ duplicates ≈ 400/week = 57/day out of 1.4 M events = 0.004 %
  observed in §7.1: 83 events from one connector restart.
  cost with idempotent consumers: ~0 (a dedupe-table hit, 0.3 ms each)
  cost WITHOUT idempotent consumers: 83 duplicate emails — or, on a payment
      path, 83 duplicate charges = £3,700 of refunds and 83 trust incidents.
  ⇒ the outbox does not remove the need for idempotency; it makes duplicates
    RARE enough that idempotency is a cheap safety net instead of the
    primary defence. Both are required.
```

**Step 5 — the ghost-order arithmetic that funded the project.**

```
  before: 31 ghost orders/quarter (§7.1)
      discoverable only by customer contact, 2.4 days later on average
      handling: 1 agent × 25 min + refund/re-ship + goodwill £15
      = ~£28 fully loaded each, plus an estimated 3× silent escalation
        (customers who never wrote in and simply did not return)
  ⇒ direct cost ≈ 31 × £28 × 4 = £3,472/year; churn cost ≈ 3× that
  after: 0 ghost orders across two quarters
  engineering cost: 6 days (outbox + relay + consumer dedupe audits)
  ⇒ the case closed in the first quarter. The generalisable point: dual-write
    bugs are rare, silent, unbounded in age, and expensive per instance —
    which is exactly the profile that justifies structural prevention.
```

**Step 6 — the disk arithmetic of an abandoned replication slot (§11.1).**

```
  BookNook's busiest DB: ~4 GB/day of WAL (L61's write volume)
  A connector stops on Friday 18:00; nobody notices until Monday 09:00 = 63 h
      retained WAL = 63 h × (4 GB/24 h) = 10.5 GB
  ⇒ survivable. But the §7.2 bank case retained 410 GB in the same window
    because its write volume was ~110 GB/day.
  ⇒ the risk is proportional to WRITE VOLUME, not to table size, and it is
    the one CDC failure that takes down the PRIMARY for writers. Alert on
    RETAINED BYTES (§8), and set max_slot_wal_keep_size so the slot is
    dropped (recoverable: re-snapshot) instead of the disk filling
    (catastrophic: outage).
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **Outbox + polling relay** | No new infrastructure; correct; shippable in a day | 100 ms mean latency; idle query load | Starting out; non-user-facing paths |
| **Outbox + CDC relay** | ~10 ms latency; zero query load; cannot miss a change | A connector to operate; slot-disk risk | Latency matters, or the outbox table is large |
| **Pure CDC on business tables** | Works without touching the app; useful for cache/search/warehouse | Your schema becomes a public contract (§4.11) | You do not control the app (the bank in §7.2) |
| **Direct publish with retry** | Simplest code | Ghost events on crash — the dual write (L82) | Never for anything durable |
| **2PC / XA** | True atomicity | Blocking coordinator, slow, often unavailable (L83) | Rarely; see L83 §14 |
| **Event sourcing** | The event log *is* the state; full audit | Large rewrite; eventual consistency everywhere | The domain is genuinely event-shaped (L84) |
| **Delete-on-publish** | Smallest table | No audit trail; "did we publish X?" unanswerable | High-volume, low-value events |
| **Partitioned cleanup** | Instant reclaim, no vacuum pressure | More DDL to manage | > ~1 M rows/day (§8) |
| **Event-carried state (§4.7)** | Consumers need no callback | Larger payloads; data exposure | The default |
| **Thin events** | Small messages | Consumer reads newer state than the event described | Only with versioned APIs and care |
| **Listen-to-yourself (§4.10)** | Self-state and consumer-state cannot diverge | Read-your-writes breaks for the user who just acted | Convergence matters more than immediacy |

## 11. Failure scenarios

**11.1 — The replication slot that filled the disk.** A Debezium connector crashed on a Friday; the slot stopped advancing; PostgreSQL retained every WAL segment; by Monday the filesystem was at 94 % on the bank's system and an on-call engineer intervened (§7.2). **Symptom:** rising WAL retention, then write failures on the primary — an outage caused by a *read-side* component. **Fix:** alert on retained bytes (§8), set `max_slot_wal_keep_size`, never leave orphaned slots, and monitor the connector's liveness like a primary dependency.

**11.2 — Two relays, double the events.** A polling relay was left running after CDC was introduced. Both published, so every event was emitted twice — with a 100 ms gap, which defeated consumers whose dedupe window was 30 s *per event id* but whose ordering assumption broke (the two copies interleaved differently). **Symptom:** duplicate rate jumped from 0.004 % to exactly 100 %, with ordering anomalies. **Fix:** exactly one relay per service; assert `count(relays) == 1`; make the switch a deploy, not a manual step.

**11.3 — Out-of-order publishes from parallel workers.** Four relay workers took row ranges `id BETWEEN`, so events for order `90210` were split across workers and published as `OrderCancelled` (id 1041, worker 2) before `OrderPlaced` (id 1040, worker 1). The warehouse shipped a cancelled order. **Symptom:** impossible state transitions for a small set of aggregates, correlated with relay concurrency. **Fix:** partition the relay by `hashtext(aggregate_id)` (§4.5, §8), never by row range.

**11.4 — The schema change that broke every consumer.** A developer ran `ALTER TABLE orders RENAME COLUMN cust_id TO customer_id` against a pure-CDC pipeline; three downstream services read `cust_id` and began writing nulls. **Symptom:** silent data loss in consumers, discovered from a report. **Fix:** CDC on the **outbox only** (§4.11 pitfall 4), so the schema is private; plus a schema registry with backward-compatibility enforcement.

**11.5 — The outbox grew to 400 GB.** `processed_at` was set but the cleanup job had been disabled during an incident and never re-enabled. The partial index stayed small, so nothing *looked* wrong, but backups grew from 40 minutes to 9 hours and restores failed their RTO. **Symptom:** backup duration and restore drills, months later. **Fix:** alarm on `pg_table_size('outbox')`, and partition by day so cleanup is a `DROP` you can automate safely.

**11.6 — The snapshot that took production down.** A first-time Debezium snapshot of a 500 GB table ran during peak, competing for I/O; p99 on the primary tripled for 90 minutes. **Symptom:** primary I/O saturation correlated with a *new* connector. **Fix:** snapshot from a replica, use incremental snapshotting with chunked watermarks, and schedule the initial snapshot off-peak.

**11.7 — Thin events plus read-after-write.** An order event carried only `{order_id}`; the consumer called `GET /orders/90210` and read a state *newer* than the event — including a subsequent cancellation — so it shipped and then un-shipped 4,000 orders in one day. **Symptom:** consumers acting on state that does not match the event they were handling. **Fix:** event-carried state transfer (§4.7), or versioned reads (`GET /orders/{id}?version=7`).

**11.8 — The outbox transaction that was too big.** A bulk import wrote 50,000 orders and 50,000 outbox rows in **one** transaction: a 90-second commit, a lock on the outbox sequence, and a relay that could not see any of it until the end. **Symptom:** publish latency of 90 s for a bulk batch; lock contention on `outbox`. **Fix:** batch the transaction (500–1,000 rows), which also keeps the WAL burst decodable.

## 12. Alternatives

| Instead of the outbox | Consider | When |
|---|---|---|
| Outbox | **Kafka transactions** (produce + offset commit atomically) | The whole write is already Kafka-side; still does not span your DB |
| Outbox | **2PC / XA** (L83) | Rarely; both participants support XA and the coordinator is acceptable |
| Outbox | **Saga with compensations** (L84) | The side effects are business-visible and reversible |
| Outbox | **Direct publish + retry with a durable retry store** | You accept a hand-rolled outbox with more bugs |
| Outbox + CDC | **Polling relay** | Simplicity outweighs latency (start here) |
| CDC on outbox | **CDC on business tables** | You cannot change the application (§7.2) |
| Outbox | **Event sourcing** (L84) | The log *is* the source of truth for the domain |
| Outbox | **Listen-to-yourself** (§4.10) | Convergence matters more than read-your-writes |

## 13. When to use it

- **Whenever a database write must cause a message** — the canonical dual-write case (L82). This is not an edge case; it is the default shape of every event-driven service.
- **When the publish must survive a process crash** between commit and send.
- **When a broker outage must not fail user-facing writes** (§7.1: 22 minutes of Kafka downtime, 1,140 rows queued, zero checkout failures).
- **When you need an in-database audit of what was published and when** — for free, from the outbox table itself.
- **CDC additionally, when**: you need cache/search/warehouse sync without dual writes (§4.8), you must integrate with an application you cannot modify, or sub-10-ms publish latency matters.

## 14. When *not* to use it

- **When the write and the publish are genuinely independent** — e.g. analytics that may be lossy. A simple fire-and-forget publish with a log is honest and cheaper.
- **When the consumer can reconstruct the event from the table** — a nightly batch that reads `orders WHERE updated_at > ...` needs no outbox (though it does need a reliable watermark; `updated_at` under concurrent transactions is the hazard §4.4 avoids).
- **When you would have to add a distributed transaction to implement it** — that defeats the purpose; use a saga instead (L84).
- **For high-frequency telemetry** where loss is acceptable — the outbox makes every write a two-row write.
- **As a substitute for idempotent consumers.** The outbox *guarantees duplicates are possible*; without dedupe you have simply converted lost events into double effects (§9 step 4).

## 15. Common mistakes

1. Writing the outbox row in a **separate transaction** from the business row — this is the dual write, wearing the pattern's clothes.
2. **No idempotent consumers**, so the guaranteed duplicates become double effects (§9 step 4).
3. **Partitioning the relay by row range** instead of by aggregate, breaking per-order ordering (§11.3).
4. **Two relays** (polling + CDC) publishing every event twice (§11.2).
5. **CDC on business tables** by default, giving away the internal schema (§11.4).
6. **No cleanup**, so the outbox grows until backups and restores break (§11.5).
7. **Ignoring replication-slot disk growth**, the one CDC failure that takes the primary down (§11.1).
8. **Snapshotting a large table at peak** (§11.6).
9. **Thin events without versioned reads**, producing state-mismatch bugs (§11.7).
10. **One enormous transaction** for bulk writes (§11.8).
11. **Not keying by `aggregate_id`**, so per-aggregate ordering is lost at the broker even though the outbox had it.
12. **No relay-lag alarm**, so "events stopped flowing" is invisible until a consumer's data is hours stale.
13. **Treating the outbox as an archive** — keep 3–7 days, not forever; event sourcing is the pattern for forever (§4.6).
14. **Assuming Kafka's transactional producer removes the need for an outbox** — it removes duplicate *produce* within Kafka, not the DB/broker dual write.

## 16. Interview perspective

**What is actually being tested.** Whether you can recognise the **dual-write problem** and articulate why the outbox is a *structural* fix rather than a retry policy. Strong candidates say "one local transaction, both rows, relay publishes at-least-once, consumers dedupe", and then get specific about per-aggregate ordering, the duplicate window, and the replication-slot disk risk. Weak candidates say "publish after commit and retry" — which is exactly the design that produced the 31 ghost orders.

**Answers that land well:**

- *"How do you publish an event when an order is created?"* — "In the same transaction as the order: one insert to `orders`, one to `outbox`. Then a relay publishes from the outbox — polling at first, CDC once latency matters — keyed by `aggregate_id` so per-order order is preserved. Consumers are idempotent because the relay guarantees at-least-once: a crash between the broker ack and marking the row republishes it. Result: if the order exists, the event is eventually published; if it doesn't, it never is."
- *"Why not just publish and retry?"* — "Because the failure I care about is the process dying between the commit and the publish — there is nobody left to retry. The retry state has to be durable, and the only durable store already inside the transaction is the database. That is the entire argument for the outbox."
- *"Polling or CDC?"* — "Start with polling: 200 ms interval, `FOR UPDATE SKIP LOCKED`, partitioned by aggregate. It costs about 10 queries/sec forever and adds ~100 ms of latency. Move to CDC when you need ~10 ms and zero idle load, accepting the operational cost — a connector to run, and the risk that a stalled slot fills the disk and takes down the primary, so I alert on retained WAL bytes."
- *"What is the difference between the outbox and CDC?"* — "The outbox is a reliability pattern — where you write the intent. CDC is a relay technique — how you read it out. The recommended hybrid is an outbox table that CDC reads: guaranteed atomicity on the write side, low latency and no query load on the read side, and the published events are ones I designed rather than my raw table schema."

**Common interview tasks:** design reliable event publication from a relational service; choose polling vs CDC with numbers; fix an out-of-order publish; size outbox retention; explain the duplicate window; decide whether to CDC the outbox or the business tables; handle a schema change with live consumers.

**Red flags interviewers watch for:**

- "Commit, then publish, and if the publish fails, retry" as a complete answer.
- No idempotency story for the guaranteed duplicates.
- Not knowing that a partial index on pending rows is what keeps the relay fast.
- Keying messages by something other than the aggregate ID.
- CDC on business tables with no mention of the schema-contract risk.
- No awareness of replication-slot disk growth.
- No cleanup story for the outbox table.
- No numbers: no poll interval, no latency budget, no retention, no row volume.

## 17. Knowledge check

**Conceptual**

1. Explain the dual-write problem in terms of the four possible orderings of "commit the row" and "publish the message", and state which two are unsafe. Then explain why a retry loop with idempotency does not fix the unsafe two, and what property the outbox adds.
2. Explain why the outbox is necessarily at-least-once, using the duplicate window between the broker acknowledgement and marking the row processed. Then explain why attempting to close that window reintroduces the original problem, and what the correct division of labour is between the relay and the consumer.
3. Compare the polling relay and the CDC relay on latency, idle load, failure modes, and operational cost. Then explain why CDC on the **outbox table** is preferred over CDC on the **business tables** for a service you fully control.

**Practical scenario**

4. BookNook's checkout writes `orders`, `order_lines` and `payments` in one transaction and must publish `OrderPlaced` to a broker. Traffic is 10 orders/sec peak, the database is 1.2 TB, WAL volume is 4 GB/day, and the broker has had two 20-minute outages in the last year. Design the complete solution: the outbox schema with justification for each column and index, the relay choice with the arithmetic that justifies it, the message key and why, the ordering guarantee you can and cannot make, the consumer-side dedupe design, retention and cleanup, the four alarms, the failure procedure when the relay is down for 2 hours, and the exact behaviour during a 20-minute broker outage.

**Calculation**

5. An outbox carries 1.2 M rows/day at 2.5 KB each, retained 7 days, with a partial index on pending rows (2 % of the table at any moment). (a) Compute daily and total heap size. (b) Compute the approximate size of the partial index and compare it with an index on all rows. (c) Compute the relay's query count per day at a 200 ms poll with 3 instances, and the mean added publish latency. (d) If the connector stalls for 48 hours at 4 GB/day of WAL, compute the retained WAL and state the threshold you would alarm at.

6. A relay publishes in batches of 250 and crashes 3 times a week, always after the broker ack and before the `processed_at` update. (a) Compute the expected duplicates per week and per day. (b) Compute the duplicate rate against 1.2 M events/day. (c) If 4 % of events are payment captures worth £45 each and consumers are **not** idempotent, compute the weekly over-charge exposure. (d) State the cheapest change that removes that exposure and why it is not "close the duplicate window".

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
  - [x] 14. Circuit breakers, bulkheads and graceful degradation (L77)
  - [x] 15. Distributed locks and leases (L78)
  - [x] 16. Leader election (L79)
  - [x] 17. Quorums (L80)
  - [x] 18. Consensus and Raft (L81)
  - [x] 19. Distributed transactions (L82)
  - [x] 20. Two-phase commit (L83)
  - [x] 21. Saga pattern (L84)
  - [x] **22. Transactional outbox and change data capture (L85) ← you are here**
  - [ ] 23. Monoliths, modular monoliths, microservices (L86)
  - [ ] 24. Service discovery and configuration (L87)
  - [ ] 25. Observability: logs, metrics, traces (L88)
  - [ ] 26. SLIs, SLOs and error budgets (L89)
  - [ ] 27. High availability and disaster recovery (L90)
  - [ ] 28. Active-passive versus active-active (L91)
  - [ ] 29. RTO and RPO (L92)

**NEXT LESSON: Phase 4, Topic 23 — Monoliths, modular monoliths and microservices**

---
