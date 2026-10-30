# Phase 2 — Scaling

Phase 1 answered one question: *what exactly happens when one computer asks another computer for something?* You can now trace a request from a keystroke, through DNS, TCP, TLS and HTTP, into an application server, out to a database, and back — and you can put numbers on every hop. BookNook, our online bookstore, runs on a single virtual machine with PostgreSQL beside it, serves 50,000 books to 10,000 daily active users, and peaks at roughly 14 requests per second. It works. It is boring. That is the correct state for a system this size.

Phase 2 is about what happens when that stops being true. Scaling is not a single technique; it is a sequence of **bottleneck removals**, each one done because a measurement forced it, each one adding a failure mode you did not previously have. The discipline of this phase is therefore not "learn 22 technologies" — it is "learn which number tells you to reach for which technology, and what that technology costs you." Nearly every production outage you will ever debug lives in the gap between "we added a component to go faster" and "we never worked out how that component fails."

The phase has a deliberate shape. Topics 1–8 scale the **compute tier**: make one machine bigger, then make many machines, then make those machines interchangeable, then put something in front of them to spread the work and route around the dead ones. Topics 9–14 attack **read load** by not doing the work at all — caching, from the browser all the way out to a CDN point of presence 20 ms from the user. Topics 15–21 attack the hardest tier, **data**: replication for read scale and durability, sharding for write scale and capacity, and the consistency problems both create. Topic 22 ties it together with autoscaling and multi-region.

One warning that governs the whole phase. The order above is also the order of difficulty, and it is not a coincidence. Scaling stateless compute is close to a solved problem — you add machines and a load balancer, and it mostly works. Scaling state is where real engineering lives, because state cannot simply be copied without someone deciding what happens when two copies disagree. Every time this phase lets you avoid touching the data tier — by caching, by offloading to a CDN, by adding a read replica instead of a shard — take the escape route. You will spend Phase 4 learning why.

**Phase 2 topics**

| # | Topic | Lesson |
|---|---|---|
| 1 | Vertical scaling | Lesson 18 |
| 2 | Horizontal scaling | Lesson 19 |
| 3 | Stateless application servers | Lesson 20 |
| 4 | Load balancers | Lesson 21 |
| 5 | Layer 4 versus Layer 7 load balancing | Lesson 22 |
| 6 | Load-balancing strategies | Lesson 23 |
| 7 | Health checks and failover | Lesson 24 |
| 8 | Reverse proxies and API gateways | Lesson 25 |
| 9 | Caching fundamentals | Lesson 26 |
| 10 | Browser, application, database, distributed caches | Lesson 27 |
| 11 | Cache-aside, read-through, write-through, write-behind | Lesson 28 |
| 12 | Cache invalidation and TTL | Lesson 29 |
| 13 | Cache stampedes, hot keys, penetration, eviction | Lesson 30 |
| 14 | CDNs and edge caching | Lesson 31 |
| 15 | Database replication | Lesson 32 |
| 16 | Synchronous versus asynchronous replication | Lesson 33 |
| 17 | Replication lag and read-after-write consistency | Lesson 34 |
| 18 | Database partitioning and sharding | Lesson 35 |
| 19 | Range, hash, directory, geographic sharding | Lesson 36 |
| 20 | Resharding, hot partitions, cross-shard queries | Lesson 37 |
| 21 | Consistent hashing and virtual nodes | Lesson 38 |
| 22 | Autoscaling and multi-region architecture | Lesson 39 |

---

# Lesson 18 — Vertical Scaling

*Phase 2, Topic 1*

## 1. Simple definition

**Vertical scaling** (also called **scaling up**) means making a single machine more powerful so it can do more work, while keeping the number of machines at one.

You do not change the architecture. You do not change the code. You change the hardware underneath the same process: more CPU cores, more memory, faster disks, more network bandwidth. The program wakes up on a bigger box and immediately does more.

Its opposite, **horizontal scaling** (**scaling out**, Lesson 19), means adding *more* machines and splitting the work across them.

Two terms to pin down before we use them twenty times:

- **vCPU** — a *virtual CPU*, the unit cloud providers sell. On most x86 instance families one vCPU is one **hardware thread** (half a physical core, because of simultaneous multithreading), not a whole core. On ARM families (AWS Graviton, Google Axion, Ampere) one vCPU is usually one whole physical core, which is why an ARM instance with the same vCPU count often does noticeably more work.
- **IOPS** — *input/output operations per second*, the number of discrete reads or writes a storage device can complete each second. A database that commits 2,000 transactions per second with one disk flush each needs at least 2,000 write IOPS, and no amount of CPU will substitute.

A useful one-sentence test: **if your fix is "buy a bigger instance type" and you ship zero lines of code, you are scaling vertically.**

## 2. Why it exists

Vertical scaling exists because **it is the only form of scaling that requires no change to your program's assumptions**, and those assumptions are usually load-bearing in ways you have not catalogued.

Consider BookNook v0 from Lesson 1: one VM, one Python process pool, one PostgreSQL instance on the same box. Traffic triples after a press mention. CPU sits at 85 %, p99 latency (Lesson 16) has gone from 180 ms to 1.4 s. You have two options.

Option A — scale out. But first you must answer: where do logged-in sessions live? (In a dictionary in process memory — Lesson 2 — so two servers means random logouts.) Where do uploaded cover images go? (The local filesystem, so server B cannot serve what server A received.) Which process runs the nightly "expire abandoned carts" job? (All of them, now, simultaneously, three times.) How do two app servers share one database connection limit? Scaling out is not one change; it is a project.

Option B — scale up. Stop the VM, change the instance type from 4 vCPU/16 GB to 16 vCPU/64 GB, start it. Twelve minutes of downtime, zero code changes, four times the CPU and memory. p99 goes back to 200 ms. You go to lunch.

That asymmetry is the entire reason vertical scaling exists and the entire reason it remains correct far more often than the internet implies. **The dominant cost of horizontal scaling is not hardware, it is the engineering required to make the software tolerate being multiple.** Vertical scaling buys you time to do that engineering properly rather than at 2 a.m. during an incident.

There is a second, harder reason. Some workloads genuinely **cannot** be split. A single PostgreSQL primary must serialise writes through one write-ahead log; a `JOIN` across two tables is dramatically cheaper when both tables are on one machine's memory bus than when they are on two machines connected by a 0.3 ms network hop. For the data tier, vertical scaling is frequently not the lazy option — it is the *correct* option, and it remains correct for far longer than people expect (Phase 2, Topics 15–21 cover what to do when it finally stops being correct).

## 3. Real-world analogy

A **one-person bakery** that cannot keep up with morning orders.

Vertical scaling is: buy a bigger oven (more CPU), install a larger refrigerator so more dough is ready to hand (more RAM), replace the hand mixer with an industrial one (faster disk), and widen the service counter so customers can be handed bread faster (network bandwidth). Still one baker, one shop, one set of keys. The bakery's *recipes and workflow do not change at all* — that is exactly the appeal.

Horizontal scaling would be: open a second branch. Now you need a way to tell customers which branch to go to, both branches need the same recipes, and "we're out of sourdough" has to be true or false in a way both shops agree on.

**Where the analogy breaks down**

| Bakery | Vertical scaling of a server |
|---|---|
| A bigger oven bakes proportionally more bread | Doubling vCPUs almost never doubles throughput — lock contention, memory bandwidth and single-threaded code paths eat the gain (quantified in §9 and formalised in Lesson 19) |
| You can upgrade the oven overnight while closed | A cloud resize requires a **stop/start**, which is visible downtime in the middle of whatever hour you do it; there is no "while closed" for a website |
| Ovens come in a continuous range of sizes | Instances come in a **discrete ladder** (2, 4, 8, 16, 32, 48, 64, 96, 192 vCPU). You cannot buy 11 vCPUs; you buy 16 and pay for 16 |
| A bigger oven makes the bakery more robust | A bigger instance makes the system **no more available at all**. One machine, one power supply, one kernel, one failure domain. This is the single most important row in this table |
| There is always a bigger oven | There is a **largest instance in the region**, and when you reach it the ladder simply stops |
| One baker can work faster indefinitely | One operating system kernel has fixed limits: file descriptors, ephemeral ports, scheduler behaviour above ~64 cores, NUMA memory locality |

The fourth row is where beginners lose marks in interviews. A customer asks "how do we improve uptime?" and the candidate says "bigger instance". A bigger instance improves *latency under load*. It improves availability by exactly zero.

## 4. Detailed technical explanation

### 4.1 What you can actually add

"Make it bigger" is four or five independent dials, and choosing the wrong one wastes money without moving the number you care about.

| Resource | What it fixes | Symptom that says "this is the bottleneck" | Typical ceiling on one cloud machine (approximate, 2026) |
|---|---|---|---|
| **vCPU** | CPU-bound work: JSON serialisation, template rendering, compression, TLS handshakes, query planning, sorting | CPU utilisation > 70–80 % sustained; run-queue length > number of vCPUs; latency rises with load but disk and network are idle | 192–448 vCPU |
| **RAM** | Working-set size: database page cache, in-process caches, concurrent request buffers, JVM heap | Swapping (any swap activity is an emergency), OOM kills, database cache hit ratio falling below ~95 %, GC pauses rising | 768 GB on mainstream families; 3–24 TB on high-memory families |
| **Storage IOPS** | Random read/write throughput: database commits, index lookups that miss cache, write-ahead log flushes | High `iowait`, disk queue depth consistently > 1 per device, `fsync` latency in the milliseconds | 256,000 IOPS on provisioned network storage (AWS io2 Block Express); 2–8 million IOPS on local NVMe |
| **Storage latency / NVMe** | Tail latency on every uncached read | p99 request latency dominated by a handful of disk reads; `fsync` p99 ≫ p50 | Network block storage ~0.5–1 ms; local NVMe ~70–150 µs — a 5–10× difference that no CPU upgrade fixes |
| **Network bandwidth** | Byte-heavy responses: images, file downloads, replication streams, backups | Interface near its cap; TCP retransmits; throughput plateaus while CPU is idle | 12.5 Gbps on mid-size instances, 50–200 Gbps on the largest; often *burstable* on small ones, which is a trap (see §15) |

Two subtleties that separate a confident answer from a vague one:

**Network bandwidth on small instances is usually burstable, not guaranteed.** An instance advertised as "up to 12.5 Gbps" sustains perhaps 0.5–5 Gbps and earns credits when idle. A nightly backup or a sudden image-heavy hour will exhaust the credit balance and then throttle, and the symptom looks exactly like an application slowdown. Only instances at roughly 8xlarge and above typically give you a guaranteed floor.

**Storage comes in two fundamentally different shapes.** *Network-attached* block storage (AWS EBS, GCP Persistent Disk, Azure Managed Disks) survives the instance dying, can be snapshotted, and can be re-attached elsewhere — but every I/O crosses a network, so ~0.5–1 ms is the floor. *Local NVMe* (instance store) is physically in the machine at ~100 µs, often 10× the IOPS, and is **erased when the instance stops** — including the stop/start you need in order to resize. That last clause is a genuine trap: vertically scaling an instance with local NVMe data destroys the data.

### 4.2 The cloud instance ladder

Vertical scaling in the cloud is not continuous. You pick a rung. Approximate AWS on-demand Linux prices, us-east-1, 2026 — *treat these as order-of-magnitude, not quotes; prices drift and regions differ by 10–30 %*:

| Instance | vCPU | RAM (GB) | Network | ~$/hour | ~$/month (730 h) |
|---|---|---|---|---|---|
| `t3.medium` (burstable) | 2 | 4 | up to 5 Gbps burst | 0.042 | 30 |
| `m7i.large` | 2 | 8 | up to 12.5 Gbps burst | 0.101 | 74 |
| `m7i.xlarge` | 4 | 16 | up to 12.5 Gbps burst | 0.202 | 147 |
| `m7i.2xlarge` | 8 | 32 | up to 12.5 Gbps burst | 0.403 | 294 |
| `m7i.4xlarge` | 16 | 64 | up to 12.5 Gbps burst | 0.806 | 589 |
| `m7i.8xlarge` | 32 | 128 | 12.5 Gbps guaranteed | 1.613 | 1,177 |
| `m7i.16xlarge` | 64 | 256 | 25 Gbps guaranteed | 3.226 | 2,355 |
| `m7i.24xlarge` | 96 | 384 | 37.5 Gbps guaranteed | 4.838 | 3,532 |
| `m7i.48xlarge` | 192 | 768 | 50 Gbps guaranteed | 9.677 | 7,064 |
| `x2idn.32xlarge` (memory-optimised) | 128 | 2,048 | 100 Gbps | 13.34 | 9,738 |
| `u-24tb1.112xlarge` (high-memory) | 448 | 24,576 | 100 Gbps | ~218 | ~159,400 |

Look carefully at the general-purpose rungs: `m7i.large` costs $74/month for 2 vCPU and `m7i.48xlarge` costs $7,064/month for 192 vCPU. That is $37 per vCPU-month at both ends — **the list price is essentially linear inside a family.** This is the single most common factual error in system-design blog posts, which assert that big instances cost disproportionately more per core. On modern general-purpose families they do not.

The super-linear cost is real, but it lives in three other places:

1. **Delivered throughput per vCPU falls as the machine gets bigger.** You pay linearly for cores and receive sub-linearly more requests served. Cost per *useful RPS* is therefore super-linear, and §9 works this out exactly. This is the dominant effect.
2. **Crossing out of the general-purpose family.** When you need 2 TB of RAM you leave `m7i` and enter `x2idn` or `u-`; the `u-24tb1` rung is 22× the price of `m7i.48xlarge` for 32× the RAM but only 2.3× the vCPUs. Buying RAM alone becomes very expensive at the extremes.
3. **Managed-service multipliers.** A managed database (RDS, Cloud SQL) charges roughly 1.3–1.6× the raw EC2 price for the same hardware, then **doubles it again** for a Multi-AZ standby, then adds storage and I/O charges. A `db.m7i.4xlarge` Multi-AZ can easily be 3× the $589 the raw instance implies.

### 4.3 Why doubling cores does not double throughput

Three mechanisms, all of which you can observe on a laptop:

- **Serial fractions.** Every request has some work that cannot overlap with other requests — acquiring a connection from a pool, incrementing a shared counter, writing to one log file, taking a lock on a hot row. Amdahl's Law (formalised in Lesson 19) says the speedup from n workers is capped at `1/(1-p)` where `p` is the parallel fraction. With 95 % parallel work, infinite cores give you 20×, not infinity.
- **Memory bandwidth and NUMA.** A large instance is physically multiple CPU sockets. Memory attached to socket 0 is ~1.5–2× slower to reach from socket 1 (this is **NUMA** — Non-Uniform Memory Access). Databases and JVMs that do not pin memory and threads to the same socket lose 20–40 % of the theoretical gain. Memory bandwidth per core also *falls* as core count rises, because the memory controllers are shared.
- **Lock and cache-line contention.** More threads hammering the same mutex or the same 64-byte cache line makes things actively *slower*, not merely flat. This is the κ term in the Universal Scalability Law (Lesson 19), and it is why a 96-vCPU box sometimes benchmarks worse than a 48-vCPU box on the same workload.

### 4.4 The hard ceilings

Vertical scaling has an end. Five distinct walls:

1. **The largest instance that exists in your region.** Not every region offers `u-24tb1`; many offer nothing above `m7i.24xlarge` without a capacity reservation. "The big one was out of stock" is a real incident report.
2. **One operating-system kernel.** One scheduler, one network stack, one page cache, one file-descriptor table. Linux handles 192 cores, but kernel lock contention, interrupt handling on a single NIC queue set, and `ksoftirqd` saturation become visible well before the CPU graph does.
3. **One process's own limits.** A single Node.js or Python process uses one core for its event loop no matter how many you buy. A JVM with a 400 GB heap has garbage-collection pauses that no tuning fully removes. Giving a single-threaded program 192 vCPUs is burning $7,000/month to use $37 of it.
4. **Single-socket memory bandwidth.** Roughly 300–500 GB/s per socket on current server CPUs. A workload that streams through memory (analytics scans, large sorts) hits this wall regardless of core count.
5. **Blast radius and availability.** Covered next, because it is the one that matters most.

### 4.5 Vertical scaling does not improve availability. At all.

This deserves its own subsection because it is the most important sentence in the lesson.

Availability (Lesson 15) is the probability the system is serving requests. A single machine has a single power supply, a single NIC, a single kernel, a single hypervisor host, a single rack, and a single set of maintenance events. Moving from `m7i.large` to `m7i.24xlarge` changes none of those. If a cloud provider's single-instance SLA is 99.5 %, it is 99.5 % at both ends of the ladder — about **43.8 hours of expected downtime per year**.

Worse, the blast radius *grows*. With one server, a crash affects 100 % of users. That is true at 2 vCPUs and at 192. But the bigger box is now serving 50× more users, so the same 99.5 % availability destroys 50× more sessions, orders and revenue per incident. **Vertical scaling concentrates risk.** Horizontal scaling (Lesson 19) is the only move that changes the availability arithmetic, and that is the real argument for it — not cost, not elasticity, availability.

### 4.6 Downtime for a resize

How bad is the interruption?

- **Cloud VM resize (stop → change type → start):** typically 2–10 minutes of hard downtime. The instance gets a new physical host; if it has an ephemeral public IP it changes (use an Elastic IP or a DNS record with a short TTL — Lesson 4). Local NVMe data is destroyed.
- **Managed database resize (RDS/Cloud SQL, Multi-AZ):** the provider resizes the standby, fails over, then resizes the old primary. Downtime is the failover window, typically **60–120 seconds**, plus a period of degraded performance while caches re-warm. Single-AZ resize is a full stop/start: 5–15 minutes.
- **Live/hot resize:** some hypervisors support CPU and memory hot-add, and some providers (GCP) allow in-place machine-type changes with a reboot. "No downtime at all" is rare and should not be assumed in a design.
- **The cache cold-start tail.** A database that just restarted has an empty buffer pool. For 10–60 minutes afterwards, queries that normally hit RAM hit disk, and p99 latency can be 5–20× normal. The outage is 90 seconds; the *incident* is half an hour. Plan the maintenance window around the second number.

### 4.7 The heuristic: scale up until it hurts, then scale out

The working rule used by most experienced teams:

> **Scale vertically first, until either (a) the cost per delivered request starts rising noticeably, (b) you reach roughly half the largest available instance, or (c) the availability requirement exceeds what one machine can give. Then scale out.**

Clause (b) exists because you want headroom for an emergency. If you are already on the largest instance in the region, your only remaining lever during a traffic spike is an architecture change, and you cannot ship an architecture change in twenty minutes.

Clause (c) is usually what actually forces the move, and it forces it long before cost does. An SLA of 99.9 % (8.8 hours/year) is not achievable on one machine with a 99.5 % single-instance SLA, no matter how large.

## 5. Request / data flow

What physically happens when you vertically scale BookNook's single VM from `m7i.xlarge` (4 vCPU/16 GB) to `m7i.4xlarge` (16 vCPU/64 GB), start to finish:

1. **Measure first.** You collect a week of metrics and establish that CPU sits at 82 % during the 19:00–21:00 peak, `iowait` is 2 %, memory is 60 % used, and the network is at 3 % of its cap. **The bottleneck is CPU.** Without this step you are guessing.
2. **Confirm the resource actually helps.** You profile one slow request: 120 ms total, of which 95 ms is in JSON serialisation and template rendering (CPU-bound) and 20 ms is PostgreSQL. More vCPU will help. Had it been 95 ms of database wait, more vCPU would have changed nothing.
3. **Choose the rung.** You want peak CPU ≤ 50 % so a 2× spike does not page anyone. 82 % on 4 vCPU → 4 × 0.82 = 3.28 vCPU of real work → to hold that at 50 % you need ≈ 6.6 vCPU → next rung up that comfortably clears it is 16 vCPU (`m7i.4xlarge`), since 8 vCPU would sit at 41 % and leaves only a 2.4× spike margin. (You will often pick 8; the point is that you *computed* the choice.)
4. **Schedule the window.** You pick 04:00 local, the trough from your traffic graph, and you announce it. BookNook has no second machine, so this is a real outage for every user.
5. **Put up a maintenance page.** Change the DNS record for `booknook.com` to a static page on object storage *at least one TTL in advance* (Lesson 4 — if the TTL is 3,600 s, the change must be made an hour early or stale resolvers will keep sending traffic to a dead IP).
6. **Drain and quiesce.** Stop accepting new requests, let in-flight requests finish (connection draining — Lesson 20, §4), stop the application process, then cleanly shut down PostgreSQL so it writes a checkpoint instead of needing crash recovery on boot.
7. **Snapshot the disk.** This is your rollback. Resizes very occasionally fail to start on the new instance family because of a driver or kernel-module mismatch.
8. **Stop the instance.** The VM is now not running anywhere. If it has local NVMe storage, that data is gone.
9. **Change the instance type.** One API call / one console field.
10. **Start the instance.** The hypervisor places it on a host with 16 free vCPUs. This can **fail with an `InsufficientInstanceCapacity` error** if the region is short on that family — which is why step 7 matters and why you should know your fallback family in advance.
11. **Verify at the OS level.** `nproc` returns 16, `free -g` returns 64. If you skip this you will later discover you resized the wrong instance.
12. **Re-tune the software.** PostgreSQL does **not** use more memory just because it exists. `shared_buffers`, `effective_cache_size`, `work_mem` and `max_worker_processes` are all fixed numbers in a config file. Application worker/thread counts are similarly fixed. **Resizing without retuning is the single most common way teams pay for a bigger box and get no improvement.**
13. **Restart the application and database with the new configuration.**
14. **Warm the caches.** Run a script that touches the hottest 10,000 books so the PostgreSQL buffer pool and any in-process cache are populated before real users arrive.
15. **Revert DNS** to the application's IP.
16. **Watch for 30–60 minutes.** Compare p50/p95/p99 and CPU against the pre-change baseline. If p99 is worse, step 7's snapshot is your undo.
17. **Record the new ceiling.** Write down "we are now at 16 of a possible 192 vCPU; at the current growth rate of 15 %/month we have about 18 months of runway." That number is what tells you when to start the horizontal-scaling project — *before* it is urgent.

## 6. ASCII diagram

```
VERTICAL SCALING: the box grows, the count stays at one.

   t = 0                          t = 1 (after resize)
   peak 14 RPS                    peak 400 RPS
 ┌─────────────────┐            ┌───────────────────────────────┐
 │  m7i.xlarge     │            │  m7i.4xlarge                  │
 │  4 vCPU         │            │  16 vCPU                      │
 │  16 GB RAM      │   ====>    │  64 GB RAM                    │
 │  gp3 3k IOPS    │  (stop,    │  gp3 16k IOPS                 │
 │                 │   resize,  │                               │
 │ [app][postgres] │   start)   │ [app] [app] [app] [postgres]  │
 └─────────────────┘            └───────────────────────────────┘
        ▲                                      ▲
        │                                      │
   ONE failure domain                  STILL ONE failure domain
   99.5% availability                  STILL 99.5% availability
   blast radius = 100% of users        blast radius = 100% of users
                                       (…of 30x more users)


THE LADDER AND WHERE IT ENDS

 delivered
   RPS  ▲
        │                                        ┌── hard ceiling:
 24,000 │                               ●········│   largest instance
        │                        ●               │   in the region
 19,200 │                                        │
        │                 ●                      ▼
 11,520 │          ●                  ╔══════════════════════╗
        │     ●                       ║ beyond here the only ║
  6,720 │  ●                          ║ lever is Lesson 19   ║
  3,680 │●                            ╚══════════════════════╝
   ─────┼───────────────────────────────────────────────────▶
        2   8   16    32      64        96          192   vCPU

        Note the curve bends DOWNWARD: 48x the vCPUs
        buys 24x the throughput (see §9).


WHAT EACH DIAL FIXES  (pick by measurement, not by vibes)

  CPU > 75% sustained ............▶ more vCPU
  cache hit ratio < 95% ..........▶ more RAM
  iowait high, queue depth > 1 ...▶ more IOPS / NVMe
  fsync p99 in milliseconds ......▶ faster storage (NVMe)
  bandwidth at cap, CPU idle .....▶ bigger network tier
  availability target missed .....▶ NOT VERTICAL SCALING.
                                    Go to Lesson 19.
```

## 7. Detailed example

### BookNook: the first real growth event

BookNook v0 (Lesson 1): one `m7i.xlarge`, 4 vCPU, 16 GB, running the FastAPI app and PostgreSQL side by side. 10,000 DAU, ~14 RPS at peak, p99 of 180 ms. Comfortable.

A popular newsletter features BookNook. Over six weeks DAU goes from 10,000 to 120,000. Peak RPS goes from 14 to ~170. Here is what the team actually observes, in order:

**Week 1 (≈30 RPS peak).** Nothing. Little's Law (Lesson 14) says concurrency is `170 × 0.020 = 3.4` requests in flight at the eventual peak, and a 4-vCPU box has enormous headroom. The graphs barely move. *This is important: real systems absorb 2× without noticing.*

**Week 3 (≈80 RPS peak).** p99 rises from 180 ms to 420 ms during the evening peak, while p50 stays at 60 ms. Classic tail-latency behaviour (Lesson 16): the mean hides it, the percentile reveals it. Investigation shows PostgreSQL's `shared_buffers` is 4 GB, the working set of hot books plus indexes has grown past it, and the buffer-cache hit ratio has fallen from 99.4 % to 96.1 %. Each 1 % of misses is now a 0.8 ms network-block-storage read.

**The first vertical move is a config change, not a purchase.** Memory is 60 % free because PostgreSQL was told to use 4 GB on a 16 GB box. They raise `shared_buffers` to 6 GB and `effective_cache_size` to 12 GB. Hit ratio returns to 99.3 %, p99 drops to 210 ms. **Cost: $0.** Always check whether you have already bought the resource and failed to use it.

**Week 5 (≈140 RPS peak).** CPU is now the problem: 88 % sustained during peak, `iowait` 1 %. The app and the database are competing for the same four vCPUs — the app's JSON serialisation starves the database's query planner and vice versa. p99 is 900 ms and customers are complaining about checkout.

**The second vertical move is a resize**, following §5 exactly: `m7i.xlarge` → `m7i.4xlarge` (16 vCPU, 64 GB). Cost goes from $147/month to $589/month. Downtime: 9 minutes at 04:12. After retuning (`shared_buffers` 16 GB, Uvicorn workers from 4 to 12, `max_connections` from 100 to 200, `work_mem` 16 MB), peak CPU is 29 % and p99 is 165 ms — *better than the original 180 ms at 10× the traffic*.

**Week 6: the decision that actually matters.** The team writes down three facts:

1. They are at 16 of 192 available vCPUs. At 15 %/month growth, CPU returns to 80 % in roughly 13 months and the ladder runs out in roughly 30 months.
2. The 9-minute outage was visible, and the next one will be too, because *every* vertical move needs one.
3. Availability is unchanged at ~99.5 %, and the business has just started talking about an enterprise SLA of 99.9 %.

Fact 3, not fact 1, is what schedules the horizontal-scaling project. Cost says "you have two years". Availability says "you have one quarter". **The availability requirement almost always arrives before the cost crossover.**

### Contrasting example: Stack Overflow

For roughly a decade, Stack Overflow served one of the 50 busiest sites on the internet — billions of monthly page views — from a famously small fleet: on the order of nine web servers and **two SQL Server machines** (one active, one standby), with around 1.5 TB of RAM in the database tier so that essentially the entire working set lived in memory. They published their numbers specifically to argue that teams scale out prematurely.

The lesson is not "you should run two database servers". It is the shape of their reasoning: they bought enormous machines because their workload was read-heavy, cacheable, and dominated by a working set that *fit in RAM if you bought enough RAM* — and buying 1.5 TB of RAM is far cheaper and far simpler than sharding a relational database across twenty machines (Phase 2, Topic 18 — Database partitioning and sharding). They scaled out only the stateless web tier, where it is easy, and scaled up the stateful tier, where scaling out is hard. That is exactly the heuristic in §4.7, executed at extreme scale.

The contrast with BookNook: Stack Overflow's workload *fit* the vertical strategy (one hot dataset, read-dominated, tolerant of a brief failover). A system with 500 TB of data, or one that must survive the loss of an entire availability zone without a 60-second gap, does not fit, and no amount of vertical scaling will make it fit.

## 8. Code or configuration example

Three artefacts: a resize script, the retuning that must accompany it, and the declarative (Terraform-style) equivalent. **All of this is illustrative teaching code, not production-ready** — there is no error handling, no state locking, no secrets management, no monitoring integration, and the maintenance-page swap is omitted. Treat it as a readable version of what your runbook should contain.

```bash
#!/usr/bin/env bash
# resize.sh -- vertically scale one EC2 instance. ILLUSTRATIVE ONLY.
# Real runbooks need: approval gates, a maintenance page, alert silencing,
# rollback automation, and a human watching dashboards.
set -euo pipefail          # fail fast; an unset variable here could target the wrong box

INSTANCE_ID="i-0abc123def456"
NEW_TYPE="m7i.4xlarge"     # chosen in SS5 step 3 by calculation, not by feel

# 1. SNAPSHOT FIRST. This is the only rollback that works if the new type
#    fails to boot (driver/ENA/NVMe mismatches across families are real).
VOLUME_ID=$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].BlockDeviceMappings[0].Ebs.VolumeId' --output text)
aws ec2 create-snapshot --volume-id "$VOLUME_ID" --description "pre-resize $(date -Is)"

# 2. Graceful application shutdown BEFORE the hard stop, so in-flight requests
#    finish and PostgreSQL writes a clean checkpoint instead of needing crash
#    recovery on boot (which can add minutes to the outage).
ssh "$INSTANCE_ID" 'sudo systemctl stop booknook-api && sudo -u postgres pg_ctl stop -m fast'

# 3. Stop the instance. NOTE: any local NVMe (instance-store) data is destroyed
#    here. Only safe because BookNook keeps its data on EBS.
aws ec2 stop-instances --instance-ids "$INSTANCE_ID"
aws ec2 wait instance-stopped --instance-ids "$INSTANCE_ID"

# 4. The actual resize: one attribute change.
aws ec2 modify-instance-attribute --instance-id "$INSTANCE_ID" \
  --instance-type "{\"Value\": \"$NEW_TYPE\"}"

# 5. Start. This can fail with InsufficientInstanceCapacity -- the region may
#    simply not have a free host of this family. Know your fallback type.
aws ec2 start-instances --instance-ids "$INSTANCE_ID"
aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"

# 6. Verify the OS actually sees the new hardware before declaring victory.
ssh "$INSTANCE_ID" 'nproc && free -g | head -2'
```

```ini
# postgresql.conf -- the retune that MUST follow the resize.
# Target machine: 16 vCPU / 64 GB RAM.  ILLUSTRATIVE; tune against your own
# workload with pg_stat_statements, not against a blog post.

# 25% of RAM is the conventional starting point for shared_buffers. PostgreSQL
# will NOT use more memory just because the machine has it -- this number is
# the whole reason "we resized and nothing improved" happens.
shared_buffers = 16GB

# NOT an allocation. A hint to the query planner about how much data is likely
# cached by PostgreSQL *plus* the OS page cache. Too low => planner wrongly
# prefers sequential scans over index scans.
effective_cache_size = 48GB

# Per-sort, per-hash-node, PER CONNECTION. The danger: a query with 4 sort
# nodes at 200 concurrent connections can allocate 200 x 4 x work_mem.
# 16MB x 200 x 4 = 12.8GB worst case. Raise this one carefully.
work_mem = 16MB

# Used by VACUUM and index builds. Safe to make large; few run concurrently.
maintenance_work_mem = 2GB

# Parallel query: now worth enabling because we actually have cores to spare.
max_worker_processes = 16
max_parallel_workers = 16
max_parallel_workers_per_gather = 4

# Tell the planner that random reads are NOT 4x the cost of sequential reads.
# Default 4.0 assumes spinning disks. On SSD/NVMe, 1.1 is realistic and will
# flip many plans from seq-scan to index-scan.
random_page_cost = 1.1

# How many concurrent I/Os the storage can usefully service. gp3 at 16k IOPS
# handles far more than the default of 1.
effective_io_concurrency = 200

# Connections are expensive in PostgreSQL (one OS process each, ~10MB).
# 200 is near the practical ceiling even on a big box -- beyond this you need
# PgBouncer, not a bigger max_connections. (Phase 2, Topic 8 -- Reverse proxies.)
max_connections = 200
```

```hcl
# main.tf -- the declarative equivalent. ILLUSTRATIVE Terraform-style config.
# The advantage over the shell script: the instance size is version-controlled,
# reviewable in a pull request, and identical across environments.

resource "aws_instance" "booknook_app" {
  ami           = "ami-0abcdef1234567890"
  instance_type = var.instance_size        # <-- the ONLY line a vertical scale changes

  root_block_device {
    volume_type = "gp3"
    size        = 200
    iops        = 16000   # gp3 decouples IOPS from size; 3000 is the free baseline
    throughput  = 500     # MB/s, also independent of size on gp3
  }

  # Changing instance_type forces a stop/start. Terraform will show this as an
  # in-place update, which hides the fact that it is a real outage. Say so in
  # the PR description; the plan output will not.
  lifecycle {
    prevent_destroy = true   # guards against an accidental replace-instead-of-resize
  }

  tags = { Name = "booknook-app", Tier = "app" }
}

variable "instance_size" {
  type    = string
  default = "m7i.4xlarge"   # was m7i.xlarge; see the week-5 capacity review
}
```

## 9. Numerical example

We will answer one question precisely: **at what traffic level does vertical scaling stop being the cheapest option for BookNook's application tier?**

### Assumptions (every one stated, as you would in an interview)

1. BookNook's average request costs **20 ms of server time** (Lesson 1's figure), mostly I/O wait.
2. A fully-utilised vCPU of this instance family delivers **500 RPS** of this workload at the smallest size. (Derived from: 20 ms of wall-clock, of which ~2 ms is CPU, so one vCPU can push ~500 req/s with enough concurrency to hide the I/O.)
3. **Scaling efficiency degrades with core count** because of locks, NUMA and shared memory bandwidth (§4.3). We use measured-style efficiency factors — these are *typical for a Python/Node web tier*, not a law of nature:

| vCPU | Efficiency | Delivered RPS = vCPU × 500 × efficiency |
|---|---|---|
| 2 | 100 % | 1,000 |
| 4 | 96 % | 1,920 |
| 8 | 92 % | 3,680 |
| 16 | 84 % | 6,720 |
| 32 | 72 % | 11,520 |
| 64 | 60 % | 19,200 |
| 96 | 50 % | 24,000 |

4. Prices from §4.2, monthly = hourly × 730.
5. A network load balancer costs ≈ **$25/month fixed + ~$60/month of capacity units** at these volumes ≈ **$85/month** (Lesson 21 explains the pricing model).
6. We size for **peak**, not average, and ignore autoscaling (Phase 2, Topic 22) so the comparison is like-for-like.

### Step 1 — cost per delivered RPS, vertical ladder

```
m7i.large     $74    / 1,000 RPS  = $0.0740 per RPS/month
m7i.xlarge    $147   / 1,920 RPS  = $0.0766 per RPS/month   (+3.5%)
m7i.2xlarge   $294   / 3,680 RPS  = $0.0799 per RPS/month   (+8.0%)
m7i.4xlarge   $589   / 6,720 RPS  = $0.0876 per RPS/month   (+18.4%)
m7i.8xlarge   $1,177 / 11,520 RPS = $0.1022 per RPS/month   (+38.1%)
m7i.16xlarge  $2,355 / 19,200 RPS = $0.1227 per RPS/month   (+65.8%)
m7i.24xlarge  $3,532 / 24,000 RPS = $0.1472 per RPS/month   (+98.9%)
```

**There is the super-linear curve.** The list price per vCPU never changed ($37/vCPU-month at every rung), yet the price of a *useful request* doubled. 48× the vCPUs delivered 24× the throughput at 2× the unit cost.

### Step 2 — cost of the horizontal alternative

Build the fleet from `m7i.xlarge` (4 vCPU, 1,920 RPS each, $147) — small enough to stay near peak efficiency, large enough that per-instance overhead is amortised.

```
Horizontal cost(R) = ceil(R / 1,920) × $147  +  $85 (load balancer)
```

### Step 3 — compare at each rung

| Target peak RPS | Vertical: smallest rung that fits | Vertical $/mo | Horizontal: n × m7i.xlarge | Horizontal $/mo | Winner |
|---|---|---|---|---|---|
| 1,920 | m7i.xlarge (1,920) | 147 | 1 × 147 | 232 | **Vertical** by $85 |
| 3,680 | m7i.2xlarge (3,680) | 294 | 2 × 147 = 294 | 379 | **Vertical** by $85 |
| 5,760 | m7i.4xlarge (6,720) | 589 | 3 × 147 = 441 | 526 | **Horizontal** by $63 |
| 6,720 | m7i.4xlarge (6,720) | 589 | 4 × 147 = 588 | 673 | **Vertical** by $84 |
| 8,000 | m7i.8xlarge (11,520) | 1,177 | 5 × 147 = 735 | 820 | **Horizontal** by $357 |
| 11,520 | m7i.8xlarge (11,520) | 1,177 | 6 × 147 = 882 | 967 | **Horizontal** by $210 |
| 19,200 | m7i.16xlarge (19,200) | 2,355 | 10 × 147 = 1,470 | 1,555 | **Horizontal** by $800 |
| 24,000 | m7i.24xlarge (24,000) | 3,532 | 13 × 147 = 1,911 | 1,996 | **Horizontal** by $1,536 |

### Step 4 — read the crossover correctly

The crossover is **not a smooth curve, it is a sawtooth**. Vertical wins exactly when you happen to be sitting on a rung you have nearly filled, and loses badly the moment you are forced onto the next rung with most of it idle. At 8,000 RPS you must buy an 11,520-RPS machine and waste 30 % of it; that is where horizontal wins by 43 %.

**The durable statement:** for this workload, vertical scaling is cheaper below roughly **4,000 RPS** and reliably more expensive above roughly **7,000 RPS**, with a contested band in between that depends on where in the rung you sit. Above ~7,000 RPS the gap only widens.

### Step 5 — the cost comparison is the weaker argument

Price in the availability (Lesson 15), using a 99.5 % single-instance SLA:

```
One machine:            A = 0.995         → 43.8 hours/year expected downtime
Six machines, any one
of which can serve:     A = 1 - (1-0.995)^6
                          = 1 - (0.005)^6
                          = 1 - 1.5625e-14
                          ≈ 99.9999999999984%   (theoretically ~0.5 microseconds/year)
```

That second number is **not** what you will achieve, and saying it with a straight face is a red flag (§16). It assumes perfectly independent failures, and the six machines share an availability zone, a load balancer, a deployment pipeline, a database and a config file — all correlated. A realistic figure for a six-instance single-AZ fleet behind one LB is **99.9–99.95 %** (4.4–8.8 hours/year), limited by the correlated components, not the machines. Lesson 19 does this arithmetic honestly.

Even the honest number is the decisive one: **8.8 hours/year versus 43.8 hours/year is a 5× improvement that vertical scaling cannot buy at any price.**

### Step 6 — when does BookNook cross over?

Current peak is 14 RPS. Crossover is ~7,000 RPS, i.e. **500× current traffic**. At 15 % month-on-month growth:

```
1.15^n = 500   →   n = ln(500) / ln(1.15) = 6.215 / 0.1398 ≈ 44 months
```

**Nearly four years of pure vertical headroom on cost grounds.** And yet BookNook should scale out within the year, because of the 99.9 % SLA. Write both numbers down; they tell you different things.

## 10. Trade-offs

**Advantages**

- **Near-zero code change.** This is the single biggest advantage and it is worth more than it sounds. No session externalisation, no shared file storage, no distributed locking for cron jobs, no cache-coherency questions, no new network hop to debug. The risk of a resize is operational and bounded; the risk of a re-architecture is unbounded.
- **No new failure modes.** You do not introduce a load balancer, a session store, or a split-brain scenario. The system you are on call for is the system you already understand.
- **Everything gets faster, including the things you forgot about.** The nightly report, the migration script, the ad-hoc query someone runs in `psql` — all benefit. Horizontal scaling helps only the work you explicitly distributed.
- **Latency can improve, not just throughput.** A bigger buffer pool turns disk reads into memory reads; NVMe turns 800 µs into 100 µs. Horizontal scaling never makes a single request faster — it only stops requests queueing behind each other. For a latency-bound (not throughput-bound) problem, scaling up is the *only* answer.
- **Keeps data local.** One machine means `JOIN`s, transactions and foreign keys all still work, cheaply. The moment data spans machines, those become distributed problems (Phase 4).
- **Cheaper below the crossover**, as §9 shows, once you count the load balancer and the engineering time.

**Disadvantages**

- **Zero availability improvement.** One failure domain, before and after.
- **Downtime to apply.** Minutes for a VM, ~60–120 s for a managed-DB failover — plus a long cache-cold tail.
- **A hard ceiling** that arrives suddenly and cannot be engineered around in an emergency.
- **Super-linear cost per delivered request** (§9, step 1).
- **Growing blast radius.** The bigger the box, the more users one crash takes down.
- **You pay for peak, continuously.** A 16-vCPU box bought for a two-hour evening peak is 16 vCPU at 04:00 too. Horizontal + autoscaling (Phase 2, Topic 22) is the only way to stop paying for troughs.

**Operational costs**

Low, but not zero: a maintenance window, a rollback plan, a config retune, and a deliberate re-measurement afterwards. The most expensive operational cost is *forgetting to retune*, which converts a $442/month purchase into a $442/month donation.

**When it becomes unnecessary complexity**

Almost never — it is the *simplest* option. The failure mode is the opposite: vertical scaling used as a substitute for fixing an O(n²) query or a missing index. Buying 16 vCPUs to brute-force a query that would run 400× faster with a `CREATE INDEX` is the most common form of this. **Always profile before you purchase.**

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| **Instance fails to start on the new type** (capacity shortage, or a driver/ENA mismatch across families) | The maintenance window never ends; the site stays down well past the announced time | `start-instances` returns `InsufficientInstanceCapacity`, or the instance reaches "running" but never passes its status checks | Revert the instance type to the old one and start; if the volume is damaged, restore the §8 snapshot. **Always know a fallback family before you begin** |
| **Local NVMe data destroyed by the stop/start** | Permanent data loss — missing orders, an empty cache tier, a corrupted database | Application errors on boot; the data directory is empty | Restore from backup. There is no other recovery. Prevented only by never putting durable data on instance store |
| **Resized but not retuned** | No improvement at all; latency identical, bill 4× higher | CPU now idle while p99 is unchanged; database cache hit ratio still low | Apply the §8 `postgresql.conf` changes and raise application worker counts, then restart. Catch it by diffing config against machine size in CI |
| **Cold cache after restart** | 10–60 minutes of 5–20× worse p99 immediately after the "successful" change; users see spinners and timeouts | p99 spikes while CPU is low and `iowait` is high; buffer-cache hit ratio starts near 0 and climbs | Self-heals as the cache fills. Mitigate with a warm-up script (§5 step 14) and by scheduling the change at traffic trough |
| **Ceiling reached mid-incident** — traffic spikes and you are already on the largest instance | Sustained overload: queueing, timeouts, then 5xx for everyone | Autoscaling/capacity alarms fire with no remediation available; the resize API returns no larger type | **No fast recovery exists.** Emergency load shedding, turn off expensive features, serve a static page. This is why §4.7 says never go past half the ladder |
| **Single machine dies** (host failure, kernel panic, disk failure) | Total outage for 100 % of users until a replacement is built | Health checks fail; the instance disappears from the hypervisor | Restore from snapshot onto a new instance: typically **20–60 minutes**. Unchanged by how big the machine was — the only fix is Lesson 19 |
| **NUMA / memory-bandwidth wall on a very large instance** | Throughput plateaus or *regresses* after the upgrade; p99 gets worse on a bigger box | Benchmark shows a 96-vCPU box underperforming a 48-vCPU one; `numastat` shows cross-node memory access | Pin processes to NUMA nodes, or run two smaller instances instead — i.e. scale out |
| **Burstable instance exhausts CPU or network credits** | Site is fine for 20 minutes, then crawls; recovers overnight; nobody can reproduce it | CPU credit balance graph hits zero just before the slowdown; throughput caps at a suspiciously round number | Move off `t`-family to a fixed-performance family. Do not debug the application — the application is innocent |

## 12. Alternatives

| Approach | How it differs | Good at | Bad at | Example / when you'd pick it |
|---|---|---|---|---|
| **Vertical scaling** | One machine, more resources | Zero code change, data locality, latency, simplicity, cheap below crossover | Availability (no change), downtime, hard ceiling, blast radius | BookNook's database for the next 3 years |
| **Horizontal scaling** (Lesson 19) | More machines, work split | Availability, elasticity, no ceiling, cheap above crossover | Requires statelessness (Lesson 20); the data tier is genuinely hard | BookNook's app tier once the 99.9 % SLA lands |
| **Optimise the code / schema** | Same hardware, less work per request | Free capacity; often 10–1000× for a single missing index; improves latency *and* cost | Needs profiling skill and engineering time; eventually exhausted | A 2 s query becoming 5 ms with one index — try this **before** §8 |
| **Caching** (Phase 2, Topics 9–13) | Avoid the work entirely | Removes 80–95 % of read load for a few $/month | Only helps repeated reads; adds invalidation problems | Caching BookNook's 1,000 most-viewed books |
| **CDN / edge offload** (Phase 2, Topic 14) | Static bytes never reach you | Images and assets — the 180 Mbps from Lesson 1 §9 | Useless for personalised or write traffic | BookNook's cover images, day one |
| **Queue the work** (Lesson 13, Phase 4) | Make slow work asynchronous | Smooths spikes; peak sizing follows *average*, not peak | Users must tolerate "we'll email you"; adds a broker | Order-confirmation emails, invoice PDFs, recommendations |
| **Managed/serverless runtime** | Provider scales for you | Zero capacity planning; pay per request at low volume | Cold starts, per-request cost at high volume, vendor lock-in | BookNook's rarely-used admin endpoints |
| **Read replicas** (Phase 2, Topic 15) | A second machine serving reads only | Scales *reads* without sharding; also a warm failover | Does nothing for writes; adds replication lag (Topic 17) | The first thing to try when the DB is read-bound |
| **Buy physical hardware** | Own the metal | 2–5× cheaper than cloud at steady high scale; larger single boxes exist than any cloud sells | Weeks of lead time, no elasticity, you own the failures | A predictable, large, steady-state workload |

Note the sequencing implied here. Facing "the server is slow", the cheapest correct order is usually: **profile → index/optimise → cache → CDN → scale up → scale out**. Teams that jump from step 1 to step 6 build distributed systems to hide a missing index.

## 13. When to use it

1. **The data tier, almost always, as the first move.** Databases are the hardest thing to scale horizontally (replication brings lag — Phase 2, Topic 17; sharding brings cross-shard queries — Topic 20). A single PostgreSQL primary on a 64-vCPU/256 GB machine with NVMe handles tens of thousands of transactions per second and tens of terabytes. Exhaust that before you shard. This is the strongest, least controversial use of vertical scaling.
2. **Low-to-moderate traffic with a small team.** Below the §9 crossover (~4,000 RPS for this workload) vertical scaling is *both* cheaper and simpler. A three-engineer team running two machines instead of a twelve-node Kubernetes cluster ships features instead of operating infrastructure.
3. **Latency-bound rather than throughput-bound problems.** If one request is slow because it waits on disk or lacks memory, adding servers changes nothing — each server is equally slow. More RAM or NVMe is the only lever. This distinction is worth saying out loud in an interview.
4. **Software that genuinely cannot be distributed.** Single-writer databases, legacy monoliths with in-process state, licensed software priced or keyed per node, workloads needing a huge shared in-memory structure (a 400 GB graph, a large in-memory index).
5. **As an emergency stopgap while you do the real work.** Traffic tripled, the horizontal project is six weeks out. Resizing buys those six weeks in twelve minutes. This is a legitimate, professional use of vertical scaling — provided you actually do the project.
6. **When you have already paid for the resource and are not using it.** The BookNook week-3 story: `shared_buffers` at 4 GB on a 16 GB box. Free capacity is the best capacity.

## 14. When *not* to use it

1. **When the requirement is availability.** An SLA of 99.9 % or better is unreachable on a single machine whose provider only promises 99.5 %. No instance size changes this. If the conversation is about uptime, vertical scaling is not an answer — it is a non sequitur.
2. **When you are already past half the ladder.** On `m7i.24xlarge` of a 192-vCPU maximum, you have one rung left and no emergency lever. Start the horizontal project at 50 % of the ceiling, not at 100 %.
3. **When traffic is spiky and predictable-in-shape.** A 10× evening peak means paying for 10× capacity for 24 hours a day. Horizontal + autoscaling (Phase 2, Topic 22) can cut that bill by 60–80 %. Vertical scaling has no "scale to zero".
4. **When the bottleneck is not a machine resource at all.** If p99 is dominated by a 400 ms third-party payment API, or by a lock on one hot row, or by `GIL`-bound single-threaded code, a bigger machine does nothing. Measure first (§5 step 1) — this is the most common wasted purchase in the industry.
5. **When downtime is unacceptable even once.** Payment processing, healthcare, anything with a contractual uptime penalty. Every vertical scale is a scheduled outage.
6. **When data must survive the loss of an availability zone.** One instance lives in one AZ. An AZ-level event takes you fully offline regardless of instance size. That requires multi-AZ, which is horizontal by definition.

## 15. Common mistakes

**Beginner mistakes**

- **Resizing without measuring.** Adding vCPU to an I/O-bound workload. The CPU graph was already at 20 % and nobody looked. Always identify *which* resource is saturated (§4.1 table) before choosing a dial.
- **Resizing without retuning.** `shared_buffers`, `work_mem`, JVM `-Xmx`, Uvicorn/Gunicorn worker counts, connection-pool sizes, `max_connections` — all are fixed numbers that do not grow with the machine. This single mistake accounts for an enormous share of "we scaled up and it didn't help".
- **Believing a bigger instance improves uptime.** It does not. Not slightly. Not at all.
- **Using burstable (`t`-family) instances for sustained production load.** They work for 20 minutes, then the credit balance hits zero and you get a reproducible-only-at-peak mystery. Use fixed-performance families for anything with a steady floor.
- **Putting durable data on local NVMe** and then discovering that a resize erases it.
- **Forgetting that a resize is a real outage** and announcing it ten minutes beforehand, with a DNS TTL of one hour (Lesson 4).
- **Scaling the application when the database is the bottleneck.** Both live on the same box in BookNook v0, so "the server is at 90 % CPU" does not tell you which process is responsible. `top` does.

**Experienced-engineer mistakes**

- **Refusing to scale vertically on principle** because "real systems scale horizontally", and building a Kubernetes platform to serve 200 RPS. This is the mirror-image error and it is more expensive, because it costs engineer-months rather than dollars.
- **Scaling up past the knee of the efficiency curve.** The §9 table shows 96 vCPU delivering 50 % efficiency. Running two 48-vCPU machines gives more throughput *and* better availability for the same money. Benchmark the actual box; do not assume the curve.
- **Ignoring NUMA on large instances.** Over ~32 vCPU you are on multiple sockets. Unpinned databases and JVMs silently lose 20–40 %. `numactl`, `numastat`, and the database's own NUMA settings exist for this.
- **Treating the managed-database price as the instance price.** Multi-AZ RDS is roughly 2× the single-AZ price, which is itself ~1.4× EC2. A "$589 resize" can be a $1,650 resize.
- **Not writing down the remaining headroom after each resize.** Without "we are at 16 of 192 vCPU, ~13 months to the next move", vertical scaling becomes a series of surprises. Capacity planning is a document, not a reflex.
- **Using a resize to paper over a pathological query or a missing index**, which converts a 10-minute fix into a permanent 4× cost increase — and the query is still pathological when you eventually shard.
- **Assuming linear gains when sizing the purchase.** Buying 4× the cores to handle 4× the traffic, landing at ~3.4×, and being surprised. Use measured efficiency, as in §9.

## 16. Interview perspective

Vertical scaling is rarely the *topic* of an interview question, but how you treat it is a strong seniority signal in both directions.

**How to use it well:**

- **Propose it first, explicitly, with a number.** "Peak is 14 RPS and Little's Law gives 0.28 concurrent requests. One `m7i.xlarge` handles this with 100× headroom. I'll scale vertically and revisit at around 4,000 RPS." This is the single highest-value sentence in the opening five minutes of a system-design interview, and most candidates never say it.
- **Name the specific resource.** Not "we'll get a bigger box" but "the working set has outgrown the buffer pool, so the lever is RAM; CPU is at 20 % and more cores would do nothing."
- **State the ceiling out loud.** "Vertical scaling takes the database to roughly 50,000 writes/sec on a 64-vCPU NVMe instance. Past that I need sharding, and I'd want the shard key chosen long before then." This shows you know where the road ends without driving off it early.
- **Separate the two reasons to scale out.** Cost crossover and availability are different arguments with different timelines (§9 steps 4 and 6). Candidates who say "we scale out for availability, not because the big box is too expensive — those are 44 months apart for this workload" are visibly operating at a higher level.
- **Mention the retune.** "And I'd raise `shared_buffers` and the worker count, otherwise we've bought capacity we can't use." Interviewers who have run production systems react to this.
- **Price the downtime.** "The resize is ~9 minutes of downtime at the traffic trough, plus 30 minutes of degraded p99 while the buffer pool re-warms."

**Red flags interviewers watch for:**

- Reaching for Kubernetes, microservices, Kafka or sharding before any capacity estimate exists — designing Phase 4 for a Phase 1 problem.
- Claiming a bigger instance improves availability or reduces blast radius.
- Quoting `1 - (1-a)^n` availability figures with twelve nines and a straight face, with no mention of correlated failure.
- Saying "just scale vertically" with no stated ceiling, or "just scale horizontally" with no mention of state.
- Not knowing that a cloud resize requires a stop/start, or that a managed-DB failover is roughly 60–120 seconds.
- Being unable to name *which* resource is saturated, or saying "it depends" without naming what it depends on.
- Assuming linear scaling with core count.

## 17. Knowledge check

Answer these before reading on. Write your answers down — even one line each.

**Conceptual**

1. BookNook's p99 latency has doubled. CPU is at 25 %, memory is 50 % used, network is at 4 % of capacity, and `iowait` is 30 % with a disk queue depth of 6. Which vertical dial do you turn, why do the other three not help, and what non-vertical fix should you try *first*?
2. Your manager says: "We've been having outages. Let's move the database to the biggest instance available — that'll make it more reliable." Explain in three sentences exactly what that change will and will not do, and state what *would* improve reliability.
3. The §9 table shows a 96-vCPU instance running at 50 % scaling efficiency. Name the three distinct mechanisms that cause that loss, and for each say whether it is a property of the hardware, the operating system, or your application code.

**Practical scenario**

4. BookNook runs PostgreSQL on an `m7i.4xlarge` with 2 TB of data on local NVMe for speed. You have been asked to resize it to `m7i.8xlarge` this Saturday. Describe (a) the catastrophic outcome of running the §8 script as written, (b) the safe migration path you would use instead, and (c) one thing that will still be degraded for 30 minutes after a *successful* migration, and what you would do about it.

**Calculation**

5. BookNook's app tier runs on an `m7i.2xlarge` (8 vCPU) and peak CPU utilisation is 68 %. Traffic is growing 12 % per month, and company policy requires peak CPU to stay at or below 55 %.
   (a) Using the efficiency table from §9, what peak RPS is the tier currently serving? (Assume CPU utilisation is proportional to delivered RPS against that instance's capacity.)
   (b) How many months until the tier breaches the 55 % policy at its current size?
   (c) Which rung do you move to, and how many further months does that buy you?
   (d) At that new rung, compare the monthly cost against a horizontal fleet of `m7i.xlarge` plus an $85/month load balancer sized for the same peak RPS, and state which you would choose and why. Show every step.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 19.

---

**Progress checklist**

Phase 1 — Internet and System-Design Foundations: all 17 topics `[x]` complete.

Phase 2 — Scaling
- [x] 1. Vertical scaling
- [ ] 2. Horizontal scaling
- [ ] 3. Stateless application servers
- [ ] 4. Load balancers
- [ ] 5. Layer 4 versus Layer 7 load balancing
- [ ] 6. Load-balancing strategies
- [ ] 7. Health checks and failover
- [ ] 8. Reverse proxies and API gateways
- [ ] 9. Caching fundamentals
- [ ] 10. Browser, application, database, distributed caches
- [ ] 11. Cache-aside, read-through, write-through, write-behind
- [ ] 12. Cache invalidation and TTL
- [ ] 13. Cache stampedes, hot keys, penetration, eviction
- [ ] 14. CDNs and edge caching
- [ ] 15. Database replication
- [ ] 16. Synchronous versus asynchronous replication
- [ ] 17. Replication lag and read-after-write consistency
- [ ] 18. Database partitioning and sharding
- [ ] 19. Range, hash, directory, geographic sharding
- [ ] 20. Resharding, hot partitions, cross-shard queries
- [ ] 21. Consistent hashing and virtual nodes
- [ ] 22. Autoscaling and multi-region architecture

Phases 3–5: not started.

**NEXT LESSON: Phase 2, Topic 2 — Horizontal scaling**

---

# Lesson 19 — Horizontal Scaling

*Phase 2, Topic 2*

## 1. Simple definition

**Horizontal scaling** (also called **scaling out**) means handling more load by adding **more machines** and splitting the work across them, rather than making one machine bigger.

The machines are usually **commodity** hardware — ordinary, mid-sized, individually unremarkable, individually disposable. Five `m7i.xlarge` instances, not one `m7i.24xlarge`. The design principle is that **no single machine is special**, so losing one is an event the system absorbs rather than an incident a human responds to.

Three terms we will use throughout:

- **Node / instance / replica** — one machine (or container) in the group. They are interchangeable by construction.
- **Fleet / tier / cluster** — the whole group behaving as one logical service. "The app tier" is four machines pretending to be one server.
- **Shared-nothing architecture** — a design in which nodes share no CPU, no memory, and no disk. They communicate only by messages over a network, and each one can answer a request without consulting its peers. This is the property that makes horizontal scaling work; §4.3 unpacks it.

The test: **can I add a machine, change nothing else, and serve more traffic? Can I remove a machine and have nobody notice?** If yes to both, the tier scales horizontally.

## 2. Why it exists

Three reasons, in increasing order of importance.

**Reason 1 — the ceiling.** Vertical scaling ends (Lesson 18, §4.4). There is a largest instance, a single kernel, a finite memory bus. Google, Netflix, and your bank do not run on one very large computer, because no such computer exists at the required size. Horizontal scaling has no equivalent wall; you can always add another machine, at least until coordination costs eat the gain (§4.5).

**Reason 2 — cost.** Lesson 18 §9 showed cost per delivered request rising from $0.074 to $0.147 as instances grew, because efficiency falls while price stays linear. Above roughly 7,000 RPS for BookNook's workload, a fleet of small machines is 20–45 % cheaper for the same throughput. Commodity machines are the sweet spot of the price/performance curve precisely because everyone buys them.

**Reason 3 — availability, and this is the real one.** A single machine has one failure domain. Ten machines have ten. With one machine, a hardware failure is a total outage for 100 % of users and a 20–60 minute restore. With ten, it is a 10 % capacity reduction that a load balancer (Lesson 21) routes around in under ten seconds, and nobody is paged. This is the only mechanism in this entire phase that changes the fundamental availability arithmetic:

```
Single machine at 99.5%:      43.8 hours/year of expected downtime
Ten machines, N+1 redundancy: ~4-9 hours/year, limited not by the
                              machines but by what they share
```

Horizontal scaling also unlocks two things that are impossible on one machine: **rolling deploys with no downtime** (take one node out, upgrade it, put it back, repeat — Lesson 20), and **elasticity** (add nodes at 19:00, remove them at 02:00, pay for neither at 04:00 — Phase 2, Topic 22).

The catch, and the reason Lesson 20 exists: **horizontal scaling only works if the machines are genuinely interchangeable**, and most software is not interchangeable by default. The entire difficulty of this lesson is not "add a machine"; it is "make the machines not care which one they are."

## 3. Real-world analogy

**Supermarket checkout lanes.**

One lane with a very fast cashier is vertical scaling. Twenty lanes with ordinary cashiers is horizontal scaling. Twenty ordinary cashiers serve far more customers than one superhuman one, cost less in total, and — crucially — if one cashier goes on break, the shop keeps running at 95 % capacity instead of closing.

The analogy carries further than most. The person directing customers to free lanes is the **load balancer** (Lesson 21). The rule "any cashier can serve any customer" is **statelessness** (Lesson 20) — it fails immediately if a cashier has your half-scanned basket behind their counter. The shared stock room that every lane reads from is the **database**, and notice that *it is still one shared thing* — which is exactly why the data tier is the hard part.

**Where the analogy breaks down**

| Supermarket | Horizontal scaling of servers |
|---|---|
| Twenty lanes serve ~20× one lane | Ten servers serve maybe 7–9× one server. Coordination, shared locks and the database take a cut (§4.5, quantified in §9) |
| Opening a new lane takes 30 seconds | A new node needs provisioning, image pull, boot, config, warm-up, and a health check — typically 45 s to 5 minutes, which matters enormously for autoscaling |
| Cashiers never disagree about the price | Nodes can hold **different versions of cached data** and give users contradictory answers. "Refresh and the price changed back" is a cache-coherency bug you did not have with one server |
| Adding lanes never slows the existing lanes | Past a point, adding nodes makes everything *slower* — more connections to the same database, more lock contention, more cache misses. This is the κ term of the Universal Scalability Law (§4.5) and it is genuinely counter-intuitive |
| Each lane has its own till | Nodes share one database. Scaling the lanes does nothing for the stock room (Phase 2, Topics 15–21) |
| A cashier leaving mid-transaction is obviously visible | A node dying mid-request is *ambiguous* to the client — did the order get created or not? (Lesson 17) |

The third and fifth rows are where the real engineering lives.

## 4. Detailed technical explanation

### 4.1 What must be true before you can add the second machine

This is a checklist, and failing any item means the second machine makes things *worse*, not better. Lesson 20 goes through the remediation in detail; here is what must be true:

| Requirement | Why | What breaks if you ignore it |
|---|---|---|
| **No per-client session state in process memory** (Lesson 2) | The LB may send request #2 to a different node | Users randomly logged out; carts empty on alternate page loads |
| **No local file storage of user data** | Node A received the upload; node B serves the next request | Broken images ~50 % of the time |
| **No local-only caches assumed authoritative** | Two nodes cache different versions | Prices and stock counts flip as you refresh |
| **Scheduled jobs run once, not once per node** | Every node has the same cron file | Three duplicate "abandoned cart" emails; triple-charged invoices |
| **Shared external configuration** | Config drift between nodes | A bug that reproduces on 1 node in 5 — the worst kind |
| **Database connection budget accounts for N nodes** | Each node opens its own pool | `FATAL: sorry, too many clients already` the moment you scale |
| **Identity comes from a token or a shared store** | Any node must authenticate any request | Auth failures on the "wrong" node |
| **Idempotent writes** (Lesson 17) | Retries now land on different nodes | Duplicate orders |

The connection-budget item is the one that surprises people, so make it concrete. PostgreSQL costs roughly 5–10 MB per connection (it forks a process per connection) and degrades badly past a few hundred. If each app node holds a pool of 20 connections:

```
 2 nodes × 20 =  40 connections   fine
 5 nodes × 20 = 100 connections   fine
20 nodes × 20 = 400 connections   PostgreSQL is now spending significant
                                  CPU on process scheduling, not queries
50 nodes × 20 = 1,000 connections DEAD
```

**Scaling the app tier can take down the database.** The fix is a connection pooler such as PgBouncer sitting between them (Phase 2, Topic 8 — Reverse proxies and API gateways), multiplexing 1,000 client connections onto 50 real ones.

### 4.2 Linear versus sub-linear scaling

Define the terms precisely:

- **Linear scaling**: n machines deliver n× the throughput of one. Efficiency = 100 %.
- **Sub-linear scaling**: n machines deliver less than n×. Efficiency = `throughput(n) / (n × throughput(1))`. This is what you actually get.
- **Retrograde scaling**: throughput *decreases* as you add machines. This is real, it happens, and it is the single most surprising result in this lesson.

Causes of sub-linearity, in roughly the order you will meet them:

1. **A shared dependency saturates.** Ten app nodes hammering one PostgreSQL primary. The app tier scales; the system does not. Usually the first wall you hit.
2. **Load is not evenly distributed.** Round-robin across nodes with different capabilities, or a hot key that always routes to the same node (Phase 2, Topic 20 — hot partitions).
3. **Per-node fixed overhead.** Each node maintains its own connection pools, its own metrics agent, its own JIT warm-up, its own cache that must be populated independently.
4. **Cache dilution.** One node with 8 GB of cache holding the hot set has a 95 % hit rate. Eight nodes with 8 GB each do **not** have 64 GB of useful cache — each node sees 1/8 of the traffic, so each one's cache is colder and its hit rate drops. Total memory went up 8×; effective hit rate went *down*. This is a genuinely counter-intuitive effect and a major reason for shared caches (Phase 2, Topic 10).
5. **Coordination.** Distributed locks, leader election, consensus, cross-node cache invalidation. Every one of these costs network round trips that grow with n.

### 4.3 Shared-nothing architecture

**Shared-nothing** means each node has its own CPU, its own memory, and its own disk, and shares none of them with peers. Nodes coordinate only by passing messages.

Contrast with the alternatives, which you will see in older or more specialised systems:

| Architecture | What is shared | Scales to | Example |
|---|---|---|---|
| **Shared-memory** | One machine's RAM and CPUs; threads share address space | One machine (this is vertical scaling) | A multi-threaded monolith |
| **Shared-disk** | Separate machines, one shared storage array/volume | Dozens of nodes; the storage layer becomes the bottleneck and the SPOF | Oracle RAC, AWS Aurora (shared storage layer), classic SAN clusters |
| **Shared-nothing** | Nothing | Thousands of nodes | Cassandra, modern web tiers, MapReduce, most cloud-native design |

Shared-nothing wins for scale because the thing that limits the other two — a shared resource that every node must contend for — simply does not exist. The price is that **anything requiring agreement between nodes now requires an explicit protocol**, and protocols over unreliable networks are the subject of all of Phase 4.

A stateless web tier is naturally shared-nothing. A database is naturally *not*, which is why Phase 2, Topics 15–21 are an entire sub-course on forcing shared-nothing onto data.

### 4.4 The coordination tax

Every mechanism that keeps n nodes consistent costs something. Approximate per-operation costs, same data centre:

| Coordination mechanism | Typical cost | Grows with n? |
|---|---|---|
| Nothing (pure stateless request) | 0 | No |
| Reading shared state from Redis | 0.3–1 ms | No (but Redis load does) |
| Acquiring a distributed lock (Redis `SET NX`) | 0.5–2 ms, plus serialisation of everything that takes it | Yes — the lock is a serial section |
| Quorum write to a 3-node consensus group (Raft) | 2–10 ms | Mildly (quorum size), but latency is set by the slowest member |
| Cross-node cache invalidation broadcast | 1–5 ms, **n messages per event** | **Yes, O(n)** |
| Gossip-based membership | background, O(log n) rounds to converge | Mildly |
| Two-phase commit across n nodes | 10–100 ms, blocks on the slowest, unsafe under partition | Badly |

The pattern: **anything that is O(n) or that creates a serial section is what converts linear scaling into sub-linear and eventually retrograde scaling.** The engineering goal of a scalable design is to keep the per-request coordination count at or near zero.

### 4.5 Amdahl's Law and the Universal Scalability Law

**Amdahl's Law** puts a hard ceiling on parallel speedup. If a fraction `p` of the work can be parallelised and `(1−p)` is irreducibly serial, then with n workers:

```
Speedup(n) = 1 / ( (1 − p) + p/n )
```

As n → ∞, speedup → `1/(1−p)`. With 95 % parallelisable work, the ceiling is **20×**, no matter how many machines you buy.

Amdahl is optimistic, though: it says extra workers stop helping, never that they hurt. Neil Gunther's **Universal Scalability Law (USL)** adds the missing term. Relative capacity with n nodes:

```
C(n) = n / ( 1 + σ(n − 1) + κ·n(n − 1) )

  σ (sigma)  = CONTENTION: the serial fraction — queueing for a shared
               resource (the database, a lock, the LB). Amdahl's term.
  κ (kappa)  = COHERENCY: the cost of keeping nodes consistent with each
               other. This term is O(n²) because, in the worst case, every
               node must agree with every other node.
```

The κ term is why scaling can go **retrograde**. The peak is at:

```
n* = sqrt( (1 − σ) / κ )
```

Beyond `n*`, adding machines reduces total throughput. §9 computes both curves for BookNook with real numbers; the headline is that a tier with even small coherency costs peaks around 100 nodes, while a tier with κ = 0 scales nearly linearly for thousands.

**The practical takeaway:** your job as a designer is to drive σ and κ toward zero. Statelessness (Lesson 20) drives κ to ~0. Caching and read replicas (Topics 9–17) drive σ down by removing contention on the database. These are not separate topics from scaling — they *are* scaling.

### 4.6 The stateless tier is easy; the data tier is hard

This asymmetry structures the rest of Phase 2.

**Scaling the stateless app tier** is a solved problem: make nodes interchangeable (Lesson 20), put a load balancer in front (Lessons 21–23), health-check them (Topic 7), and add nodes. Capacity is roughly linear up to the point where a shared dependency saturates. You can do this in an afternoon.

**Scaling the data tier** is not solved, because data is state by definition, and copying state means two copies that can disagree. The options are all compromises:

- **Read replicas** (Topic 15) — copy the data to more machines for reads. Scales reads; does nothing for writes; introduces **replication lag** (Topic 17), so a user can write a review and not see it.
- **Sharding** (Topic 18) — split the data so each machine owns a slice. Scales writes and capacity; breaks `JOIN`s across shards, breaks transactions across shards, and makes "find all orders in the last hour" a scatter-gather across every machine (Topic 20).
- **Caching** (Topics 9–13) — avoid the data tier entirely for hot reads. Cheapest and best first move; introduces invalidation problems.

So the real-world sequence for BookNook is: scale the app tier out (easy, now), cache aggressively (easy, big win), add read replicas (moderate), and shard only when writes or data volume genuinely demand it (hard, and years away).

### 4.7 Blast radius and availability

**Blast radius** is the fraction of users affected by one component failing. With one node it is 100 %. With n nodes it is 1/n — *provided* traffic is redistributed, which requires both spare capacity and working health checks (Topic 7).

The naive availability formula for "the service is up if at least one node is up":

```
A(n) = 1 − (1 − a)^n     where a = per-node availability
```

This is the formula everyone quotes and it is **wrong in two directions** for real systems:

1. **Too optimistic**, because it assumes failures are independent. They are not: nodes share an availability zone, a load balancer, a deployment, a config file, a container image, a certificate expiry, and a database. A bad deploy takes out all n simultaneously — that is a correlated failure with a correlation coefficient of 1.
2. **Too optimistic again**, because "at least one node up" is not "the service is working". If you need 6 nodes to carry peak traffic and only 2 survive, the service is technically up and practically down — it is overloaded, queueing, and timing out.

The honest model uses **binomial availability with a capacity threshold**: you need at least `k` of `n` nodes, where `k = ceil(peak_load / per_node_capacity)`. §9 computes this properly. The rule of thumb that falls out of it: **N+1 redundancy (one spare node) takes you from ~99.5 % to ~99.9 %; N+2 across two availability zones takes you to ~99.95–99.99 %; past that you are limited entirely by correlated failures and deploy quality, not by machine count.**

## 5. Request / data flow

BookNook moves from one node to four. End to end:

1. **Establish the trigger.** Peak RPS has reached 1,600. One `m7i.xlarge` tops out near 1,920 RPS (Lesson 18, §9). Additionally the business has signed a 99.9 % SLA, which one machine cannot deliver. Both the capacity and the availability argument now point the same way.
2. **Audit for statelessness** using the §4.1 checklist. BookNook fails four items: sessions in a process dictionary, cover-image uploads on the local disk, an in-process book cache, and a cron entry for abandoned carts.
3. **Fix each one** (the substance of Lesson 20): sessions move to Redis; uploads move to object storage; the in-process cache is kept but given a short TTL and demoted to a non-authoritative read cache; the cron job moves to a single scheduler that acquires a lock before running.
4. **Externalise configuration** into environment variables injected at deploy time, so all four nodes are provably identical.
5. **Add a connection pooler.** 4 nodes × 20 connections = 80, which PostgreSQL tolerates — but the plan is 12 nodes, so PgBouncer goes in now, in transaction-pooling mode, multiplexing onto 40 real connections.
6. **Build an immutable machine image** containing the app at a fixed version — no "configure on boot" steps that could differ between nodes.
7. **Create the load balancer** with a health-check path (`/healthz`), a readiness path (`/readyz`), and a deregistration delay of 30 s so in-flight requests drain (Lessons 20–21).
8. **Launch nodes 2, 3 and 4** from the image, in at least two availability zones — three nodes in one AZ gives you capacity but not AZ-level availability.
9. **Register all four with the load balancer.** Each passes its health check; the LB begins sending traffic.
10. **Point DNS at the load balancer** instead of at node 1's IP (Lesson 4). Use a low TTL (60 s) during the migration.
11. **Verify the distribution.** Each node should receive ~25 % of requests. If one is receiving 60 %, you have a sticky-session or a keep-alive imbalance (Lesson 21, §4).
12. **Verify interchangeability by force.** Terminate node 3 during business hours. Expected: the LB notices within ~15 s, removes it, the remaining three absorb the load, and no user-visible error occurs. **If you have not tested this, you do not have horizontal scaling; you have four copies of a single point of failure.**
13. **Do a rolling deploy.** Upgrade one node at a time: deregister, drain 30 s, replace, health-check, re-register. Confirm zero errors throughout. This is the capability you just bought.
14. **Re-measure the shared dependency.** The database now receives 4× the connection churn and the same query volume. Check PostgreSQL CPU, connection count and p99. **This is where the next bottleneck will appear**, and you want to see it coming.
15. **Record the new numbers**: capacity 4 × 1,920 = 7,680 RPS; with N+1 headroom, a safe peak of 5,760; measured availability target 99.9 %; blast radius 25 %.

## 6. ASCII diagram

```
BEFORE: vertical only                AFTER: horizontal app tier
                                     (Lessons 20-22 build the LB)

   clients                                   clients
      │                                         │
      ▼                                         ▼
 ┌──────────┐                            ┌─────────────┐
 │  ONE     │                            │Load Balancer│ ◀── new SPOF,
 │  BIG     │                            │  (Lesson 21)│     made redundant
 │  SERVER  │                            └──────┬──────┘     in L21 §4
 │          │                     ┌─────────┬───┴────┬─────────┐
 │ app + db │                     ▼         ▼        ▼         ▼
 └────┬─────┘                  ┌─────┐  ┌─────┐  ┌─────┐  ┌─────┐
      │                        │app 1│  │app 2│  │app 3│  │app 4│  ← commodity,
   100% blast radius           └──┬──┘  └──┬──┘  └──┬──┘  └──┬──┘    identical,
   99.5% availability             └────────┴────┬───┴────────┘       disposable
   ceiling: biggest instance       AZ-a    │    │   AZ-b
                                           ▼    ▼
                                    ┌──────────────────┐
                                    │  PgBouncer pool  │ ← stops N×20 conns
                                    └────────┬─────────┘   killing Postgres
                                             ▼
                                    ┌──────────────────┐
                                    │   PostgreSQL     │ ◀── STILL ONE.
                                    │  (single primary)│     Scaling the app
                                    └──────────────────┘     tier did nothing
                                                             here. Topics 15-21.
      blast radius 25% · target 99.9% · no ceiling in this tier


SCALING CURVES: what you hope for vs what you get

 throughput ▲
  (× one    │                                    ,-- LINEAR (ideal, never happens)
   node)    │                                ,-'
       20   │                            ,-'
            │                        ,-'          ,,,,....----  AMDAHL p=0.95
       15   │                    ,-'      ,,,,''''              (ceiling = 20x)
            │                ,-'   ,,,,'''
       10   │            ,-' ,,,'''        ●●●●●●●●●●●●●●●
            │        ,-',,,''        ●●●''               '''●●●●●●  USL
        5   │    ,-,,''        ●●●'''                                σ=.03
            │ ,,''        ●●●''                                      κ=.0001
        0   └────────────────────────────────────────────────────────▶ nodes
            1    10    20    50       100 (n* peak)      150      200
                                       ▲                   ▲
                                 best you ever get    ADDING NODES
                                                      NOW MAKES IT WORSE


THE ASYMMETRY THAT DEFINES PHASE 2

  stateless app tier  ──▶  add node ──▶ done.           EASY
  cache tier          ──▶  add node ──▶ rebalance keys.  MEDIUM (Topic 21)
  database reads      ──▶  add replica ──▶ now handle
                            replication lag.             HARD   (Topics 15-17)
  database writes     ──▶  shard ──▶ now handle cross-
                            shard joins, txns, resharding. VERY HARD (18-20)
```

## 7. Detailed example

### BookNook: from one node to twelve, and what broke each time

Picking up from Lesson 18: BookNook is on one `m7i.4xlarge`, peak 170 RPS, and has just signed an enterprise contract with a **99.9 % uptime SLA** — a maximum of 43.8 minutes of downtime per month. One machine with a 99.5 % instance SLA cannot deliver that. The decision is forced.

**Move 1 — split the tiers (1 node → 2).** The first horizontal move is not "add an app server"; it is **separating the app from the database**. They currently compete for the same CPU and page cache. The database goes to its own `m7i.4xlarge` (and will scale vertically for years, per Lesson 18 §13); the app goes to `m7i.xlarge`.

*What broke:* every database query now crosses a network. Local Unix-socket queries that took 0.15 ms now take 0.6 ms. A page doing 30 queries gained 13 ms. The fix was not more hardware — it was finding the N+1 query pattern that was issuing 30 queries where 2 would do. **Horizontal moves expose latency that co-location was hiding.**

**Move 2 — three app nodes behind a load balancer.** Following §5. The statelessness audit found all four problems in §5 step 2.

*What broke:* the in-process book cache. Node 1 had the old price of a book cached; nodes 2 and 3 had the new one. A user refreshing saw ₹3,200, ₹2,880, ₹3,200, ₹2,880. Support tickets called it "the flickering price bug". The cause is §4.2 item 4 and §4.5's κ term made visible. The fix was to move the cache to a shared Redis so there is one copy (Phase 2, Topic 10), with a 30-second in-process TTL only for genuinely immutable data.

**Move 3 — six nodes across two availability zones.** Traffic has reached 2,800 RPS.

*What broke:* nothing visible, but the team ran the §5 step 12 test and discovered the load balancer's deregistration delay was 0, so terminating a node produced ~40 failed requests each time. Invisible at three nodes per week; glaring during a six-node rolling deploy. The fix is connection draining (Lesson 20, §4).

**Move 4 — twelve nodes. The database falls over.** Traffic reaches 6,500 RPS. The team adds six more nodes and the *site gets slower*. PostgreSQL is at 95 % CPU with 240 connections, spending more time scheduling processes than executing queries. p99 goes from 210 ms to 2.4 s.

This is σ — contention on a shared resource — made concrete. The app tier scaled perfectly; the system did not scale at all. Three fixes, in order of impact:

1. **PgBouncer** in transaction mode: 240 client connections → 40 server connections. Database CPU drops from 95 % to 61 % with *no other change*. Connection overhead, not query work, was a third of the load.
2. **Redis cache for the hot catalogue** (Topics 9–13): 82 % of reads stop reaching PostgreSQL. CPU drops to 22 %.
3. **Two read replicas** (Topic 15) for analytics and the "customers also bought" queries, which were the heaviest and the most lag-tolerant.

Final state: 12 app nodes (7,680+ RPS capacity, blast radius 8 %), PgBouncer, Redis, one primary and two replicas. Measured availability over the next year: 99.94 %. The SLA is met — and note that the thing that *delivered* the SLA was not the twelve nodes alone, but twelve nodes plus the removal of every correlated single point of failure they sat behind.

### Contrasting example: Google's original design decision

Google's foundational infrastructure bet, made in the late 1990s and described in their published papers, was to build search on large numbers of **cheap commodity PCs** rather than a small number of expensive, reliable servers — at a time when the conventional wisdom was the opposite. Two consequences followed, and they are the two halves of this lesson:

1. **Cost per unit of compute collapsed.** Commodity parts are where the volume is, and therefore where the price/performance optimum sits.
2. **Hardware failure stopped being an exception and became a routine, expected event.** With tens of thousands of cheap machines, something is always broken. This forced the software to handle node failure as a normal code path rather than an incident — which is where GFS (replicate every chunk three times), MapReduce (re-run failed tasks elsewhere), and later Borg/Kubernetes (reschedule the container) all come from.

The transferable insight is the inversion: **horizontal scaling is not primarily a way to get more capacity; it is a way to make failure boring.** Once failure is boring, you can deploy at any hour, patch kernels continuously, and lose a rack without anyone noticing. BookNook gets a small version of the same benefit at twelve nodes.

The contrast with Lesson 18's Stack Overflow example is instructive: both are correct, for different workloads. Stack Overflow had a read-dominated working set that fit in RAM and could tolerate a short failover; Google had a dataset no machine could hold and a uptime requirement no single machine could meet. **Identify which of those two situations you are in before choosing.**

## 8. Code or configuration example

An auto-scaling group of identical nodes behind a load balancer, plus the nginx config that distributes to them, plus a one-line capacity model. **Illustrative only — not production-ready:** no IAM detail, no TLS certificates, no secrets management, no logging/metrics pipeline, no multi-region, and the health checks are simplified.

```hcl
# fleet.tf -- a horizontally scaled, shared-nothing app tier.
# ILLUSTRATIVE Terraform-style configuration.

# A LAUNCH TEMPLATE is the "recipe" for an interchangeable node. Every node is
# built from this identical, immutable image -- nothing is configured by hand
# after boot, so no node can drift from its peers (SS4.1, config drift).
resource "aws_launch_template" "booknook_app" {
  name_prefix   = "booknook-app-"
  image_id      = var.app_ami            # built by CI, tagged with the git SHA
  instance_type = "m7i.xlarge"           # COMMODITY size: near the efficiency
                                         # knee (Lesson 18 SS9), not the biggest

  # Configuration arrives as environment data at boot -- never baked into the
  # image, never edited on the box. 12-factor config (Lesson 20 SS4).
  user_data = base64encode(templatefile("bootstrap.sh", {
    database_url = var.database_url      # points at PgBouncer, not Postgres
    redis_url    = var.redis_url         # shared session + cache store
    app_version  = var.app_ami
  }))
}

resource "aws_autoscaling_group" "booknook_app" {
  name = "booknook-app"

  # MULTI-AZ IS THE POINT. Three nodes in one AZ survive a node failure but not
  # an AZ failure, and AZ failures are the ones that make the news.
  vpc_zone_identifier = [var.subnet_az_a, var.subnet_az_b, var.subnet_az_c]

  # Capacity: min >= the N+1 figure so losing a node never drops you below the
  # peak requirement (SS9 step 4). max is a COST GUARDRAIL -- without it a
  # retry storm can autoscale you into a five-figure bill.
  min_size         = 4
  max_size         = 20
  desired_capacity = 6

  launch_template { id = aws_launch_template.booknook_app.id }

  # The LB's health check -- not just "is the VM running" but "is the app
  # answering". A booted VM with a crashed process must count as unhealthy.
  health_check_type         = "ELB"
  health_check_grace_period = 90   # seconds to boot + warm up before judging

  target_group_arns = [aws_lb_target_group.app.arn]

  # ROLLING REPLACEMENT: the capability horizontal scaling buys you. Replace
  # nodes a few at a time while always keeping 90% of capacity serving.
  instance_refresh {
    strategy = "Rolling"
    preferences { min_healthy_percentage = 90 }
  }
}

resource "aws_lb_target_group" "app" {
  port     = 8000
  protocol = "HTTP"
  vpc_id   = var.vpc_id

  health_check {
    path                = "/readyz"  # readiness, NOT liveness (Lesson 20 SS4)
    interval            = 10
    healthy_threshold   = 2          # 2 passes to add back  -> ~20s
    unhealthy_threshold = 2          # 2 fails to remove     -> ~20s detection
    timeout             = 3
  }

  # CONNECTION DRAINING: on removal, stop sending NEW requests but let in-flight
  # ones finish. Set above your p99.9 request duration. Without this, every
  # scale-in and every deploy produces a burst of user-visible 502s.
  deregistration_delay = 30
}
```

```nginx
# nginx.conf -- the same idea with a self-managed load balancer.
# ILLUSTRATIVE: no TLS block, no rate limiting, no access-log tuning.

upstream booknook_app {
    # Each line is one interchangeable node. SHARED-NOTHING: these four
    # processes never talk to each other.
    server 10.0.1.11:8000 max_fails=2 fail_timeout=10s;
    server 10.0.1.12:8000 max_fails=2 fail_timeout=10s;
    server 10.0.2.11:8000 max_fails=2 fail_timeout=10s;  # different AZ
    server 10.0.2.12:8000 max_fails=2 fail_timeout=10s;

    # KEEP-ALIVE TO BACKENDS: reuse 32 idle upstream connections per worker
    # instead of opening a new TCP connection per request. Without this, a
    # busy LB exhausts ephemeral ports (Lesson 21 SS9) and adds a round trip
    # of latency to every single request.
    keepalive 32;
}

server {
    listen 80;

    location / {
        proxy_pass http://booknook_app;

        # Required for upstream keep-alive to actually engage.
        proxy_http_version 1.1;
        proxy_set_header Connection "";

        # The backend now sees the LB's IP as the client. Pass the real one
        # through, or every rate limit and audit log will be wrong (Lesson 21).
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Bound every stage. An unbounded read here means one slow node can
        # occupy all of nginx's worker connections (Lesson 17, timeouts).
        proxy_connect_timeout 2s;
        proxy_send_timeout    10s;
        proxy_read_timeout    10s;

        # If a node fails mid-request, try the next one -- but ONLY for
        # idempotent conditions. Retrying a POST that may have succeeded
        # creates duplicate orders (Lesson 17). Note 'non_idempotent' is
        # deliberately ABSENT from this list.
        proxy_next_upstream error timeout http_502 http_503;
        proxy_next_upstream_tries 2;
    }
}
```

```python
# capacity.py -- the Universal Scalability Law, so you can find n* before you
# buy n machines. ILLUSTRATIVE teaching code: fit sigma/kappa to your own
# benchmark data (3+ load-test points) rather than guessing them.

def usl_capacity(n, sigma, kappa):
    """Relative throughput of n nodes vs 1 node.
    sigma = contention  (serial fraction: shared DB, locks, the LB itself)
    kappa = coherency   (cost of nodes agreeing with each other; O(n^2))"""
    return n / (1 + sigma * (n - 1) + kappa * n * (n - 1))

def peak_nodes(sigma, kappa):
    """Node count beyond which ADDING machines REDUCES total throughput.
    Undefined when kappa == 0: a perfectly stateless tier has no peak."""
    if kappa <= 0:
        return float("inf")
    return ((1 - sigma) / kappa) ** 0.5

# BookNook's app tier BEFORE the fixes in SS7: shared DB contention plus an
# in-process cache that had to be kept coherent across nodes.
print(peak_nodes(0.03, 0.0001))           # ~98.5 nodes
print(usl_capacity(12, 0.03, 0.0001))     # ~8.5x  (12 nodes, 8.5x throughput)

# AFTER: Redis holds one copy of the cache (kappa ~ 0) and PgBouncer plus
# caching cut database contention (sigma 0.03 -> 0.005).
print(usl_capacity(12, 0.005, 0.0))       # ~11.4x -- nearly linear
print(peak_nodes(0.005, 0.0))             # inf -- no retrograde point
```

## 9. Numerical example

Three questions, each worked fully: **how much throughput do n nodes actually give, where does adding nodes stop helping, and what does each option cost and deliver in availability?**

### Part A — Amdahl versus USL for BookNook

**Assumptions**

1. One `m7i.xlarge` node delivers **1,920 RPS** (Lesson 18, §9).
2. Measured from three load tests at 1, 4 and 8 nodes, the pre-fix tier fits **σ = 0.03** and **κ = 0.0001**. (σ = 0.03 means 3 % of each request is effectively serialised on shared resources — the database, PgBouncer, the LB.)
3. For comparison, Amdahl with the same serial fraction: **p = 0.97**.

**Amdahl's Law — the optimistic model**

```
Speedup(n) = 1 / (0.03 + 0.97/n)

n = 4    →  1 / (0.03 + 0.2425)  = 1 / 0.2725  =  3.67x   (92% efficient)
n = 12   →  1 / (0.03 + 0.0808)  = 1 / 0.1108  =  9.02x   (75% efficient)
n = 50   →  1 / (0.03 + 0.0194)  = 1 / 0.0494  = 20.24x   (40% efficient)
n = 100  →  1 / (0.03 + 0.0097)  = 1 / 0.0397  = 25.19x   (25% efficient)
n = ∞    →  1 / 0.03                            = 33.3x   (the hard ceiling)
```

**Read that last line carefully.** With 3 % serial work, **the maximum throughput of this tier is 33× one node — about 64,000 RPS — no matter how many machines you buy.** Not 1,000 nodes, not 10,000. 33×. Reducing σ is therefore worth more than any number of machines.

**Universal Scalability Law — the realistic model**

```
C(n) = n / (1 + 0.03(n−1) + 0.0001·n(n−1))

n = 4   →  4 / (1 + 0.09 + 0.0012)   = 4 / 1.0912   =  3.67x
n = 12  →  12 / (1 + 0.33 + 0.0132)  = 12 / 1.3432  =  8.93x
n = 50  →  50 / (1 + 1.47 + 0.2450)  = 50 / 2.7150  = 18.42x
n = 98  →  98 / (1 + 2.91 + 0.9506)  = 98 / 4.8606  = 20.16x   ← PEAK
n = 150 →  150 / (1 + 4.47 + 2.2350) = 150 / 7.7050 = 19.47x   ← going DOWN
n = 200 →  200 / (1 + 5.97 + 3.9800) = 200 / 10.950 = 18.26x
```

**Peak node count:**

```
n* = sqrt((1 − σ)/κ) = sqrt(0.97 / 0.0001) = sqrt(9,700) ≈ 98.5 nodes
```

At 98 nodes you get 20.16× — **38,700 RPS from 98 machines that cost $14,406/month**, an effective $0.372 per RPS versus $0.0766 for a single node. And node 150 is actively worse than node 98 while costing 53 % more. This is the number that stops people in interviews, and you can now derive it.

**Part A conclusion:** you cannot buy your way past σ and κ. After the §7 fixes (Redis as the single cache copy → κ ≈ 0; PgBouncer + caching → σ ≈ 0.005):

```
C(12)  = 12 / (1 + 0.005·11 + 0)  = 12 / 1.055  = 11.37x   (95% efficient)
C(100) = 100 / (1 + 0.005·99)     = 100 / 1.495 = 66.9x
C(∞)   = 1/0.005                                = 200x ceiling
```

Same hardware, 6× the ceiling, achieved entirely by removing coordination. **That is what the rest of Phase 2 is for.**

### Part B — cost crossover, vertical versus horizontal

**Assumptions:** prices and delivered-RPS figures from Lesson 18 §9; load balancer $85/month; horizontal fleet built from `m7i.xlarge` at 1,920 RPS and $147/month each; USL efficiency applied to the horizontal fleet at σ = 0.005, κ = 0.

| Target peak RPS | Vertical (single instance) | $/mo | Horizontal (n × m7i.xlarge, USL-adjusted) | $/mo | Cheaper |
|---|---|---|---|---|---|
| 1,920 | m7i.xlarge | 147 | 1 node (1,920) | 232 | Vertical |
| 3,680 | m7i.2xlarge | 294 | 2 nodes (3,821) | 379 | Vertical |
| 6,720 | m7i.4xlarge | 589 | 4 nodes (7,565) | 673 | Vertical |
| 11,520 | m7i.8xlarge | 1,177 | 7 nodes (13,000) | 1,114 | **Horizontal** |
| 19,200 | m7i.16xlarge | 2,355 | 11 nodes (20,246) | 1,702 | **Horizontal** (−28 %) |
| 24,000 | m7i.24xlarge | 3,532 | 14 nodes (25,535) | 2,143 | **Horizontal** (−39 %) |
| 40,000 | **impossible** — past the ladder | — | 24 nodes (42,842) | 3,613 | **Horizontal, by default** |

USL-adjusted node capacity used above, e.g. for n = 11: `C(11) = 11/(1+0.005×10) = 10.48×`, so `10.48 × 1,920 = 20,122` — rounded in the table.

**Crossover: ~7,000–9,000 RPS on cost alone.** Past 24,000 RPS vertical scaling is not expensive, it is *unavailable*.

### Part C — availability, done honestly

**Assumptions:** per-node availability `a = 0.995` (the typical single-instance cloud SLA). Peak load 6,000 RPS. Each node serves 1,920 RPS, so **k = ceil(6,000/1,920) = 4 nodes are required** to carry peak.

**The naive formula everyone quotes:**

```
A = 1 − (1 − a)^n
n = 2  →  1 − 0.005²  = 0.999975      (13 min/year)
n = 4  →  1 − 0.005⁴  = 0.99999999937 (0.02 s/year)
n = 6  →  1 − 0.005⁶  ≈ 0.9999999999999 (~0.5 µs/year)
```

Quoting the n = 6 line in an interview is a red flag (§16). It is wrong because it ignores capacity and correlation.

**The capacity-aware binomial model.** Probability that at least k of n nodes are up:

```
P(exactly j up) = C(n,j) · a^j · (1−a)^(n−j)

n = 4, k = 4 (NO spare capacity — all four needed):
  P(all 4 up) = 0.995⁴ = 0.98015  →  98.0%  (174 hours/year!)
```

**This is the single most important calculation in the lesson.** Four nodes with *no spare capacity* are **less available than one node** (99.5 %), because now any one of four failures causes an overload. Horizontal scaling without headroom makes availability worse.

```
n = 5, k = 4 (N+1):
  P(≥4 up) = P(5 up) + P(exactly 4 up)
           = 0.995⁵ + C(5,4)·0.995⁴·0.005
           = 0.97525 + 5 × 0.98015 × 0.005
           = 0.97525 + 0.02450
           = 0.99975        →  99.975%   (2.2 hours/year)

n = 6, k = 4 (N+2):
  P(≥4) = P(6) + P(5) + P(4)
        = 0.995⁶ + C(6,5)·0.995⁵·0.005 + C(6,4)·0.995⁴·0.005²
        = 0.97037 + 6(0.97525)(0.005) + 15(0.98015)(0.000025)
        = 0.97037 + 0.02926 + 0.00037
        = 0.99999 (approximately)  →  99.999%  (5 minutes/year)
```

**Now subtract the correlated failures**, which the binomial model cannot see. Estimated annual downtime contributions for a real six-node single-region tier:

```
Bad deploy reaching all nodes      2 events/yr × 12 min  = 24 min
Load balancer / control plane      1 event/yr  × 10 min  = 10 min
Database primary failover          2 events/yr × 2 min   =  4 min
AZ-level disruption                0.3 events/yr × 25 min = 7.5 min
Config/certificate error           1 event/yr  × 8 min   =  8 min
Independent node failures (above)                        =  5 min
                                                    TOTAL ≈ 58.5 min/year
Availability = 1 − 58.5/(525,600) = 0.99989  →  ~99.99%
```

**The honest conclusion:** six nodes take you from 43.8 hours/year to roughly **1 hour/year**, and essentially all of the remaining downtime comes from things the nodes *share* — the deploy pipeline, the load balancer, the database, the configuration. Past N+2, buying more nodes changes nothing. Improving deploy safety changes everything. That sentence is worth more in an interview than any formula.

### Part D — blast radius

```
1 node :  1 failure → 100% of users affected
4 nodes:  1 failure →  25% affected for ~15s until the LB removes it,
                       then 0% (if N+1 headroom exists)
12 nodes: 1 failure →   8% affected for ~15s, then 0%
```

With a 10-second health-check interval and an unhealthy threshold of 2, detection takes ~20 s, during which 8 % of requests to that node fail. At 6,000 RPS over 12 nodes, that is `500 RPS × 20 s = 10,000 failed requests` — which is why Topic 7 (health checks) and Lesson 17 (retries) are not optional extras.

## 10. Trade-offs

**Advantages**

- **Availability improves fundamentally** — the only technique in this lesson pair that does. 43.8 h/year → ~1 h/year (§9 Part C).
- **No ceiling within the tier.** Add nodes until σ and κ stop you, and those are software properties you can improve.
- **Cheaper above the crossover** (~7,000–9,000 RPS for this workload), and increasingly so.
- **Elasticity.** Scale to peak at 20:00 and down at 03:00; pay for the area under the curve, not the maximum (Phase 2, Topic 22).
- **Zero-downtime deploys.** Roll one node at a time. This single capability changes release cadence from monthly-at-2 a.m. to several times a day.
- **Blast radius shrinks to 1/n**, and failures become routine rather than incidents.
- **Spot/preemptible instances become usable** — 60–90 % cheaper capacity, viable only because losing a node is a non-event (Lesson 20).
- **Commodity hardware is the price/performance sweet spot.**

**Disadvantages**

- **Requires statelessness**, and retrofitting it into a stateful application is a real project (Lesson 20 is entirely about this).
- **Sub-linear returns, and eventually negative ones** (§9 Part A). Twelve nodes gave 8.9×, not 12×.
- **The data tier does not come along for free.** You have scaled the easy half (§4.6).
- **New failure modes**: cache incoherency between nodes, config drift, split-brain in anything that elects a leader, thundering herds when all nodes retry simultaneously, partial deploys where half the fleet runs the old version.
- **Debugging gets harder.** A bug affecting 1 node in 12 means 8 % of requests fail and no single log file tells the story. Centralised logging, distributed tracing and request IDs stop being nice-to-haves (Phase 4, Topic 27).
- **A load balancer is now a required component**, and a new single point of failure until it is made redundant (Lesson 21, §4).
- **More connections to shared resources**, which can *reduce* total capacity (§7, move 4).

**Operational costs**

Substantially higher than vertical scaling, and this is the honest reason small teams should not rush: you now need infrastructure-as-code (manual node setup guarantees drift), a CI pipeline producing immutable images, centralised logs, per-node and aggregate dashboards, a deployment strategy with automated rollback, and health checks you have actually tested by killing nodes. Budget engineer-weeks, not engineer-hours.

**When it becomes unnecessary complexity**

Below the crossover with no strict availability requirement. Running six nodes, a load balancer, a session store and a connection pooler to serve 200 RPS that one `m7i.large` would handle is the mirror image of Lesson 18's mistake, and it costs engineering time rather than dollars — the more expensive currency.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| **One node dies** (host failure, OOM, kernel panic) | 1/n of requests fail for the detection window (~20 s with a 10 s interval × 2 threshold), then nothing | LB health check fails twice consecutively | LB removes the node; autoscaling group launches a replacement in 60–180 s. **This is the designed-for case** |
| **Node added but not stateless** — sessions still in process memory | Random logouts; carts emptying; "it works if I refresh" | Support tickets, not monitoring. Session-related error rates rise with no infrastructure signal | Externalise sessions (Lesson 20). Band-aid: sticky sessions at the LB, which re-introduces the problem on node failure |
| **Cache incoherency across nodes** | Prices, stock counts or feature flags flicker between page loads | Almost invisible to monitoring; found by users. Detect by logging the serving node ID and correlating with complaints | Move to a shared cache (Topic 10); make in-process caches short-TTL and non-authoritative |
| **Database connection exhaustion from N × pool_size** | Site-wide 500s immediately after a scale-out — *adding capacity caused an outage* | `FATAL: sorry, too many clients already`; DB connection count at max | Add PgBouncer; cap per-node pool size; make pool size a function of fleet size in config |
| **Duplicate scheduled jobs** — cron on every node | Customers receive 12 copies of the same email; counters double-count; reports run 12× | Duplicate-send complaints; 12 identical log lines with different hostnames | Leader election or a distributed lock; better, move to a single scheduler that enqueues work (Lesson 13) |
| **Uneven load distribution** | One node at 95 % CPU with terrible p99 while others idle; a fraction of users have a bad time | Per-node CPU dashboards diverge; per-node latency differs | Usually long-lived keep-alive connections pinned by an L4 LB (Lesson 22), or a hot key. Fix with L7 least-connections balancing (Lesson 23) |
| **Correlated failure: a bad deploy hits every node** | Total outage, exactly as if there were one server. All the redundancy bought nothing | Error rate spikes to 100 % within seconds of a deploy | Automated rollback triggered by error-rate alarm; prevented by canary/rolling deploys with health gates |
| **Retrograde scaling** — added nodes, throughput fell | Site is slower after a capacity increase; everyone assumes it must be something else | Throughput vs node-count graph turns over; lock-wait or DB-contention metrics rise super-linearly | Remove the nodes you just added (genuinely). Then reduce σ/κ: pooling, caching, removing distributed locks |
| **Thundering herd on recovery** | Everything recovers, then immediately falls over again | All nodes reconnect/retry within the same second after a blip | Jittered exponential backoff (Lesson 17); staggered health-check start; LB slow start (Lesson 21) |
| **Scale-in kills in-flight requests** | Sporadic 502s during every deploy and every scale-down, blamed on "the network" | 502 count correlates exactly with deregistration events | Connection draining with `deregistration_delay` > p99.9 request duration; `SIGTERM` handling in the app (Lesson 20) |

## 12. Alternatives

| Approach | How it differs | Good at | Bad at | When you'd pick it |
|---|---|---|---|---|
| **Horizontal scaling** | More commodity nodes | Availability, elasticity, no ceiling, cheap at scale | Needs statelessness; data tier unsolved; operational burden | BookNook's app tier at 99.9 % SLA |
| **Vertical scaling** (Lesson 18) | One bigger node | Simplicity, zero code change, data locality | No availability gain, hard ceiling, downtime | The database, for years |
| **Hybrid (the usual real answer)** | Scale app out, scale DB up | Each tier gets the right tool; minimum total complexity | Requires recognising the tiers differ | What BookNook and Stack Overflow both actually do |
| **Functional decomposition / microservices** | Split by *function*, not by copies — each service scales independently | Teams scale independently; a heavy feature scales alone | Distributed transactions, network hops, operational sprawl (Phase 4, Topic 23) | When *teams*, not CPU, are the bottleneck |
| **Sharding / data partitioning** (Topic 18) | Split the *data*, each node owns a slice | Scales writes and capacity, which replicas cannot | Cross-shard queries/transactions, resharding pain | When one primary can no longer absorb writes |
| **Read replicas** (Topic 15) | Horizontal scaling for reads only | Scales a read-heavy load cheaply; gives a warm standby | No write scaling; replication lag (Topic 17) | BookNook's 100:1 read:write ratio — do this early |
| **Caching / CDN** (Topics 9–14) | Don't do the work at all | Removes 80–95 % of read load for a few dollars | Invalidation; no help for writes | Always try before adding nodes |
| **Serverless / FaaS** | Provider scales to n for you | Zero capacity planning; scales to zero | Cold starts, per-request cost, 15-min limits, lock-in | Spiky, low-average workloads |
| **Queue + worker pool** (Lesson 13) | Scale *consumers* horizontally, asynchronously | Spikes absorbed by the queue; size for average | Users must tolerate deferred results | PDF generation, emails, recommendations |
| **Do nothing — optimise instead** | Same hardware, less work | Often 10–1000× from one index or one N+1 fix | Finite; needs profiling discipline | Always evaluate first (§7, move 1) |

## 13. When to use it

1. **The availability requirement exceeds a single machine's SLA.** Any target of 99.9 % or better. This is usually the *first* binding constraint, arriving long before the cost crossover (Lesson 18, §9 step 6), and it is the strongest single justification.
2. **You are above the cost crossover** — roughly 7,000+ RPS for this workload, or generally once you are past the efficiency knee of the instance ladder.
3. **You are approaching the vertical ceiling.** At 50 % of the largest available instance, start the project. Horizontal scaling takes weeks; you must begin while there is still a bigger box to buy in an emergency.
4. **Traffic is spiky.** A 10× evening peak means vertical scaling bills you for the peak 24 hours a day. Horizontal + autoscaling (Topic 22) typically cuts 50–80 % of that.
5. **You need zero-downtime deploys.** Rolling updates are impossible with one node. If you want to ship daily, you need at least two nodes and connection draining.
6. **The workload is naturally parallel and shared-nothing.** Stateless HTTP request handling, image processing, batch jobs over independent records, queue consumers. These have σ near zero and κ exactly zero — they scale nearly linearly, and you should take that for free.
7. **You want to use spot/preemptible capacity.** 60–90 % discounts, available only to workloads that treat node loss as routine.

## 14. When *not* to use it

1. **The application is not stateless yet.** Adding a node to a stateful app does not scale it — it creates intermittent, hard-to-reproduce bugs (random logouts, missing uploads, flickering data). Do Lesson 20 first. Shipping the load balancer before the statelessness work is the classic ordering mistake.
2. **Below the crossover with a relaxed availability target.** An internal tool at 50 RPS with a 99 % target should be one machine and a good backup. Six nodes there is complexity you pay for daily and benefit from never.
3. **The bottleneck is a shared resource you are not also scaling.** If PostgreSQL is at 95 % CPU, adding app nodes makes it worse (§7, move 4). Find the saturated resource first; horizontal scaling of the wrong tier is a reliable way to cause an outage while "adding capacity".
4. **The workload is irreducibly serial or latency-bound.** A single slow query, a single-threaded hot path, a 400 ms third-party API. n nodes each do the same slow thing. Only vertical scaling or optimisation helps.
5. **You are past `n*`** (§9 Part A). Adding machines beyond the USL peak reduces throughput. If a capacity increase made things slower, the correct action is to remove machines and reduce coordination.
6. **The team cannot operate it.** Without infrastructure-as-code, centralised logging and tested health checks, a 12-node fleet is 12 slightly different servers and a worse on-call rotation than one.
7. **Strong cross-entity transactional consistency is required at every write.** Spreading state across nodes turns local transactions into distributed ones, which are slow and failure-prone (Phase 4). Scale that tier vertically and shard only when forced.

## 15. Common mistakes

**Beginner mistakes**

- **Adding nodes before making the app stateless.** The number one ordering error. It produces bugs that reproduce only sometimes, which are the most expensive kind.
- **Assuming linear scaling.** Buying 12 nodes for 12× capacity and getting 8.9× (§9). Always size with measured efficiency.
- **Forgetting that `n` nodes open `n × pool_size` database connections**, and scaling the app tier into a database outage.
- **Running cron on every node.** Twelve "your cart is waiting" emails is a support incident and, for anything financial, a real one.
- **Treating the load balancer as magic** and never asking what happens when *it* fails (Lesson 21, §4).
- **Quoting `1 − (1−a)^n` with no capacity threshold and no correlation.** §9 Part C shows four nodes with no headroom are *less* available than one.
- **Putting all nodes in one availability zone**, which protects against node failure but not against the AZ event that actually makes the news.
- **Never testing node failure.** If you have not killed a node in production during business hours, you do not know whether your redundancy works.

**Experienced-engineer mistakes**

- **Not measuring σ and κ before scaling out.** Three load tests (1, 4, 8 nodes) and a USL fit tell you the ceiling and the peak node count in an afternoon. Without them you discover `n*` by walking off it.
- **Scaling the tier that is not the bottleneck** because it is the easy one, then being surprised when the system gets slower.
- **Introducing a distributed lock to fix a correctness bug**, thereby converting a parallel system into a serial one and raising σ — scaling regression delivered as a bug fix.
- **Ignoring cache dilution.** Going from 4 to 16 nodes can lower the aggregate in-process cache hit rate and increase database load even though total RAM quadrupled (§4.2, item 4).
- **Believing that redundancy covers correlated failure.** Twelve nodes and one bad deploy is a total outage. The redundancy you actually need is in the *deploy process*, not in the machine count.
- **Deregistration delay shorter than p99.9 request duration**, producing a small permanent background rate of 502s that gets attributed to "the network" for months.
- **Forgetting that scaling out multiplies fixed per-node costs** — agents, sidecars, log shippers, base-image licensing. At 100 nodes a 200 MB-per-node agent is 20 GB of RAM you are paying for and not serving from.
- **Over-fragmenting into too-small nodes.** Fifty `t3.small` instances have more aggregate overhead, more connections, worse cache locality and a far noisier dashboard than ten `m7i.xlarge`. There is a node size that is too small.

## 16. Interview perspective

Horizontal scaling is the single most-discussed topic in system-design interviews and therefore the one where unremarkable answers are most common. The way to stand out is precision about *limits*, not enthusiasm about *scale*.

**How to use it well:**

- **State the precondition before the solution.** "Before I add nodes, the tier must be stateless: sessions in Redis, uploads in object storage, schedulers leader-elected. Otherwise the second node introduces bugs rather than capacity." This answers the question the interviewer was about to ask.
- **Quote sub-linear numbers.** "Twelve nodes gives me roughly 9×, not 12×, because of database contention." Candidates who say "we'll add servers until it's fast" and candidates who say "efficiency will be about 75 % at twelve nodes" are at different levels.
- **Name the ceiling with Amdahl.** "With 3 % serial work, this tier maxes out at 33× one node — about 64,000 RPS — so past that I have to reduce the serial fraction, not add machines."
- **Mention the USL retrograde point.** "There's a node count beyond which adding machines reduces throughput; for a tier with any cross-node coherency cost it's often around 100." Very few candidates know this, and it is directly testable.
- **Do the availability arithmetic honestly.** "Four nodes with no spare capacity is 98 %, worse than one node. Five nodes, N+1, is 99.975 %. Six is nominally 99.999 %, but correlated failures — deploys, the LB, the database — put the real number near 99.99 %." This is the single most impressive thing you can say about availability.
- **Separate the tiers explicitly.** "I'll scale the app tier horizontally and the database vertically, because replication gives me lag and sharding gives me cross-shard queries — I'd rather take neither until I must."
- **Volunteer the new failure modes.** Cache incoherency, duplicate cron, connection exhaustion, draining. Interviewers are listening for whether you have operated a fleet or only read about one.

**Red flags interviewers watch for:**

- Proposing horizontal scaling without ever mentioning state.
- Assuming linear scaling, or being unable to name a single reason it is sub-linear.
- Quoting `1 − (1−a)^n` with many nines, with no capacity threshold and no mention of correlated failure.
- Not knowing that adding nodes can *reduce* throughput.
- Saying "scale horizontally" for a database as though it were the same move as for a web tier.
- Forgetting the load balancer is now a single point of failure.
- Ignoring the database connection-count consequence of n app nodes.
- Reaching for Kubernetes and a service mesh at 14 RPS — the Lesson 18 red flag, in the opposite direction.

## 17. Knowledge check

Answer these before reading on. Write your answers down — even one line each.

**Conceptual**

1. A tier scales from 1 node to 8 and measured throughput goes from 1,920 RPS to 11,500 RPS (6.0×, 75 % efficient). Name three distinct mechanisms that could account for the missing 25 %, and for each, say what measurement would confirm or eliminate it.
2. Explain, in your own words, why the κ (coherency) term of the Universal Scalability Law is `O(n²)` while the σ (contention) term is `O(n)` — and give one concrete piece of BookNook functionality that would contribute to each.
3. Your colleague says "we have six app servers, so our availability is `1 − 0.005⁶`, which is twelve nines." Identify the two independent reasons this is wrong, and state what the realistic figure is dominated by instead.

**Practical scenario**

4. BookNook runs 8 app nodes and one PostgreSQL primary. Traffic doubles. The team adds 8 more nodes and p99 latency gets **worse** — from 240 ms to 1.9 s — while total throughput rises only 12 %. Describe (a) the three most likely causes in the order you would investigate them, (b) the specific metric that would confirm each, and (c) the *immediate* action you would take in the next ten minutes, before any long-term fix.

**Calculation**

5. BookNook's tier has measured σ = 0.02 and κ = 0.00005. One node delivers 1,500 RPS and costs $147/month. The load balancer costs $85/month. Peak load is 18,000 RPS and per-node availability is 99.5 %.
   (a) Using the USL, how many nodes are needed to deliver 18,000 RPS? (Compute `C(n)` and iterate.)
   (b) What is the USL peak node count `n*`, and what is the maximum throughput this tier can ever reach?
   (c) How many nodes must be running to carry 18,000 RPS (the value of `k`), and what is the availability if you run exactly `k` nodes? Is that better or worse than a single machine?
   (d) How many nodes would you actually provision, what does it cost per month, and what availability does the capacity-aware binomial model give? Show every step and state every assumption you add.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 20.

---

**Progress checklist**

Phase 1 — Internet and System-Design Foundations: all 17 topics `[x]` complete.

Phase 2 — Scaling
- [x] 1. Vertical scaling
- [x] 2. Horizontal scaling
- [ ] 3. Stateless application servers
- [ ] 4. Load balancers
- [ ] 5. Layer 4 versus Layer 7 load balancing
- [ ] 6. Load-balancing strategies
- [ ] 7. Health checks and failover
- [ ] 8. Reverse proxies and API gateways
- [ ] 9. Caching fundamentals
- [ ] 10. Browser, application, database, distributed caches
- [ ] 11. Cache-aside, read-through, write-through, write-behind
- [ ] 12. Cache invalidation and TTL
- [ ] 13. Cache stampedes, hot keys, penetration, eviction
- [ ] 14. CDNs and edge caching
- [ ] 15. Database replication
- [ ] 16. Synchronous versus asynchronous replication
- [ ] 17. Replication lag and read-after-write consistency
- [ ] 18. Database partitioning and sharding
- [ ] 19. Range, hash, directory, geographic sharding
- [ ] 20. Resharding, hot partitions, cross-shard queries
- [ ] 21. Consistent hashing and virtual nodes
- [ ] 22. Autoscaling and multi-region architecture

Phases 3–5: not started.

**NEXT LESSON: Phase 2, Topic 3 — Stateless application servers**

---

# Lesson 20 — Stateless Application Servers

*Phase 2, Topic 3*

## 1. Simple definition

A **stateless application server** keeps **no per-client information in its own process memory or on its own local disk between requests**. Every request carries, or points to, everything the server needs to handle it. Any node can serve any request from any user at any time, and killing a node mid-flight costs you at most the requests currently in progress.

Lesson 2 established the idea and the test:

> Kill this server process right now and send the next request to a different machine. Does the user notice?

Lesson 20 is about turning that test into an **engineering checklist** you can actually execute on a real application, plus the operational machinery — graceful shutdown, health versus readiness, immutable deploys — that statelessness makes possible and that it requires in order to be useful.

Two reminders so the term is not misunderstood:

- **Stateless does not mean "stores no data".** BookNook obviously remembers orders. The database holds **persistent business state**, shared by all nodes. That is fine, and it is not what "stateless" refers to.
- **Stateless refers specifically to per-client session state held locally** — the dictionary of logged-in users, the cart in memory, the half-finished upload on `/tmp`, the in-process counter.

A sharper formulation for this lesson: **a stateless server has no information that would be lost, and missed, if the process vanished.**

## 2. Why it exists

Lesson 19 showed what horizontal scaling delivers. Statelessness is the precondition for *all* of it. Each capability below is impossible without it:

| Capability | Why statelessness is required | What happens without it |
|---|---|---|
| **Add a node** | New node must serve existing users immediately | New node has an empty session table; ~1/n of users are logged out |
| **Remove a node** (scale-in) | Removing a node must destroy nothing of value | Every scale-in event logs out a slice of users and loses their carts |
| **Autoscaling** (Topic 22) | Requires both of the above, automatically, many times a day | You can scale up but never down; you pay peak price permanently |
| **Rolling deploys** | Each node is destroyed and rebuilt in turn | Every deploy logs out everyone; so you deploy at 2 a.m.; so you deploy rarely; so each deploy is riskier |
| **Spot / preemptible instances** | The provider may take a node with 2 minutes' notice | Unusable — you cannot accept random user-visible data loss for a discount |
| **Node failure as a non-event** | A crash must cost only in-flight requests | A crash costs every session on that node |
| **Any-node routing** (Lesson 21) | LB must be free to pick any backend | You need sticky sessions, which re-creates the problem on failure |
| **Immutable infrastructure** | Nodes are replaced, never patched | Nodes accumulate local state and drift; "the one that's been up 400 days" |

There is a second, less-discussed reason: **statelessness makes systems comprehensible.** A stateful fleet has `n` divergent memory images, so a bug may depend on which node you hit and how long it has been up. "It only happens sometimes" is the signature. A stateless fleet has one behaviour, reproducible anywhere, because all the state that matters is in a store you can inspect.

The honest cost, stated up front: statelessness **moves** state, it does not eliminate it. Sessions go to Redis, uploads go to object storage, schedulers acquire locks. You have traded "state in one place that fails all at once" for "state in a shared store plus a network hop". That trade is almost always correct — but it is a trade, and §10 prices it.

## 3. Real-world analogy

A **hotel front desk versus a bank teller with a personal filing cabinet.**

At the bank, teller #3 keeps your paperwork in the drawer beside her. You must always return to teller #3. If she is at lunch, you wait. If she leaves the company, your paperwork is in a drawer nobody can open. That is a stateful server with sticky sessions.

At the hotel, every receptionist can serve every guest, because everything they need is in the shared property-management system and you carry a key card that identifies you. Receptionists change shift mid-conversation without you noticing. One calls in sick; the others absorb the work. **The key card is the session token; the shared system is Redis and PostgreSQL; the interchangeability is the whole point.**

**Where the analogy breaks down**

| Hotel front desk | Stateless application server |
|---|---|
| The shared system is assumed always available | The session store is a **new dependency with its own failure mode**. If Redis is down, *every* node is equally unable to authenticate. You have centralised the risk, not removed it |
| Looking something up in the system is instant | Every externalised lookup costs a network round trip — typically 0.3–1 ms, on every request. Statelessness has a latency price |
| A receptionist mid-conversation can simply hand over | A process receiving `SIGTERM` has in-flight requests that must complete; "handing over" requires deliberate **connection draining** (§4.5) |
| The key card cannot be forged | A session token absolutely can be forged or stolen; it must be signed, random, and revocable (Lesson 6) |
| Receptionists have no memory of their own | Servers keep local caches for speed, which are *allowed* — as long as nothing is authoritative and nothing is missed if lost (§4.3) |
| Guests tolerate a 5-second lookup | Moving session data out and back on every request at 10,000 RPS is 10,000 extra network calls per second to one Redis — a capacity question, not a free lunch |

## 4. Detailed technical explanation

### 4.1 The concrete checklist

Here is the audit to run against a real codebase. Each row is a thing that silently breaks when the second node appears.

| # | Local state | How to find it | How to fix it | Trap in the fix |
|---|---|---|---|---|
| 1 | **Sessions in process memory** | Search for a module-level dict/map keyed by session or user ID; any `session[...]` backed by in-memory storage | Signed cookie/JWT for small data; **server-side session in Redis** for anything revocable | JWTs cannot be revoked before expiry. Use short lifetimes (5–15 min) + a refresh token, or keep a deny-list — which re-introduces a lookup |
| 2 | **File uploads on local disk** | Search for writes to `/var/www/uploads`, `./media`, `UPLOAD_DIR` | Upload direct to object storage (S3/GCS) with a **pre-signed URL**, so bytes never touch the app node | Pre-signed URLs need expiry, content-type and size limits, or you have an open file host |
| 3 | **In-process caches treated as authoritative** | Module-level dicts, `@lru_cache`, framework caches with `LocMemCache` | Shared cache (Redis) for anything mutable; keep in-process only as a short-TTL read cache | Cache dilution and incoherency (Lesson 19 §4.2); set TTL ≤ the staleness users tolerate |
| 4 | **Background schedulers / cron on every node** | `cron` entries, `APScheduler`, `@Scheduled`, Celery beat baked into the app | One scheduler that **enqueues** work; workers consume it. Or a distributed lock with a lease | Locks need a TTL and fencing, or a paused process wakes and runs a job twice |
| 5 | **WebSockets / SSE / long-polling** | Any long-lived connection holding per-connection data | The connection is necessarily sticky, but the **subscription registry** must be shared — Redis pub/sub or a dedicated gateway tier | On node death, clients must reconnect *and* replay missed messages; needs a sequence number |
| 6 | **Local temp files across requests** | `tempfile` paths stored in a DB or session and read later | Keep temp files within a single request, or put them in object storage with a lifecycle rule | Multi-step wizards that write a temp file in step 1 and read it in step 3 are a classic silent breakage |
| 7 | **In-memory counters, rate limits, locks** | `self.counter += 1`, `threading.Lock` guarding business state | Redis `INCR` with expiry for counters/limits; `SET NX PX` for locks | A per-node rate limit of 100/min across 10 nodes is really 1,000/min — a security bug, not just an inaccuracy |
| 8 | **Local logs as the only record** | Logging to a file path | Log to stdout; the platform ships it centrally | A node is replaced and its logs vanish with it — exactly when you need them |
| 9 | **Config files edited on the box** | SSH-and-vim history; files not in version control | Environment variables injected at deploy; secrets from a secret manager | Env vars appear in crash dumps and `ps`; secrets need a real manager, not `.env` in the image |
| 10 | **Node identity assumptions** | `if hostname == "app-01"` , "the primary runs the migrations" | Make every node identical; run migrations as a separate job in the pipeline | Migrations run by n nodes simultaneously will race; they belong in the deploy pipeline, not in app startup |

Work down this list in order; items 1, 2 and 4 cause the most user-visible damage.

### 4.2 Which 12-factor principles actually matter here

The *Twelve-Factor App* is a set of conventions for services that are disposable and horizontally scalable. Six of the twelve are directly load-bearing for this lesson:

- **Factor III — Config in the environment.** Everything that differs between environments (database URL, Redis URL, feature flags, log level) comes from environment variables or an injected secret, never from a file in the image. This is what lets one artefact run in staging and production unchanged, and it is what prevents config drift between nodes.
- **Factor IV — Backing services are attached resources.** PostgreSQL, Redis, S3 and the payment provider are all addressed by URL and are swappable without a code change. A local Redis and a managed Redis differ only in a string.
- **Factor VI — Processes are stateless and share-nothing.** The factor this entire lesson expands.
- **Factor IX — Disposability.** Processes start fast (seconds, not minutes) and **shut down gracefully on `SIGTERM`**. Fast start is what makes autoscaling responsive; graceful shutdown is what makes deploys invisible. §4.5 covers this in detail.
- **Factor XI — Logs are event streams.** Write to stdout, unbuffered. Do not manage log files; do not rotate them on the node.
- **Factor XII — Admin tasks as one-off processes.** Migrations, backfills and data fixes run as separate processes against the same release, not as code paths inside the serving application.

The other six are good practice but not preconditions for scaling out.

### 4.3 Local caches are allowed — under two rules

Beginners over-correct and conclude a stateless server may not hold anything in memory. Not true, and giving up in-process caching costs a lot of latency. The two rules:

1. **Nothing in local memory may be authoritative.** It must be a copy of something that exists elsewhere, re-derivable at any moment.
2. **Nothing in local memory may be missed when the process dies.** Losing it costs a cache miss, not a correctness failure.

So: caching the book catalogue for 30 seconds in-process is fine. Caching "this user's cart" in-process is not. Caching a feature-flag snapshot for 10 seconds is fine; caching "how many times this user tried to log in" is not, because that is a security control and 10 nodes would each count to 5.

The practical constraint on rule 1 is **staleness**. With a 30-second in-process TTL and 12 nodes, a price change can take up to 30 seconds to be visible everywhere, and during that window different nodes return different prices (Lesson 19 §7). Choose the TTL by asking "how long may users see the old value?" — not by picking a round number.

### 4.4 Health versus readiness: two different questions

These are routinely conflated and the conflation causes outages.

| | **Liveness** (`/healthz`) | **Readiness** (`/readyz`) |
|---|---|---|
| Question it answers | "Is this process alive and not deadlocked?" | "Should this process receive traffic right now?" |
| Checks | Process responds; event loop not blocked | Dependencies reachable, caches warm, migrations applied, not shutting down |
| Who consumes it | Orchestrator / supervisor (Kubernetes kubelet, systemd) | **Load balancer** |
| Action on failure | **Restart the process** | **Stop sending traffic; do not restart** |
| Must it check the database? | **No** | Usually yes, but carefully — see below |

The critical rule: **liveness must not check external dependencies.** If `/healthz` queries PostgreSQL and PostgreSQL has a 30-second blip, every node fails its liveness check and the orchestrator restarts the entire fleet simultaneously. You have converted a brief database hiccup into a total, self-inflicted outage with cold caches everywhere. This is one of the most common serious misconfigurations in production Kubernetes.

Readiness *may* check dependencies, but should do so with care for the same reason: if Redis is down, marking all nodes unready removes every backend from the load balancer, and the LB has nowhere to send traffic. A good readiness check distinguishes **hard dependencies** (no database, cannot serve anything — fail readiness) from **soft dependencies** (no recommendation service — serve a degraded page, stay ready).

A third endpoint is worth having: **startup** (`/startupz`), which gates the other two during a slow boot (JIT warm-up, cache priming, connection-pool fill). Without it, a node that takes 90 seconds to warm up is killed by a liveness probe at 30 seconds, forever.

### 4.5 Graceful shutdown and connection draining

This is the most under-implemented item on the list and it is the direct cause of the "mysterious 502s during every deploy" that many teams live with for years.

What happens when a node is removed, step by step:

1. The orchestrator or LB decides to remove the node (deploy, scale-in, spot reclamation).
2. The LB begins **deregistration**: it stops sending *new* requests to this node.
3. The process receives **`SIGTERM`**.
4. In-flight requests — possibly hundreds — are still executing.
5. After a grace period, the process receives **`SIGKILL`** and dies unconditionally.

The failure mode: by default, most runtimes treat `SIGTERM` as "exit immediately". Every in-flight request is severed mid-response, producing a 502 at the LB and an error for the user. At 500 RPS per node with a 200 ms average request, there are `500 × 0.2 = 100` requests in flight at any instant — so **every single node replacement destroys ~100 requests**. With 12 nodes, a rolling deploy destroys ~1,200 requests. Deploy five times a day and that is 6,000 user-visible errors daily, attributed to "flakiness".

The correct sequence inside the application:

```
SIGTERM received
  → 1. Immediately start failing /readyz  (LB stops sending new requests)
  → 2. SLEEP for one full LB health-check cycle + propagation (5–15 s)
       ← this step is the one everyone omits, and it is essential, because
         the LB does not learn about the shutdown instantly
  → 3. Stop accepting new connections; finish in-flight requests
  → 4. Wait up to a drain timeout (> p99.9 request duration)
  → 5. Close database/Redis pools, flush logs and metrics
  → 6. Exit 0
```

**Three numbers must be ordered correctly, or draining does nothing:**

```
LB deregistration_delay  >  application drain timeout  >  p99.9 request duration
        30 s             >          25 s              >        2 s
and the orchestrator's termination grace period (e.g. Kubernetes
terminationGracePeriodSeconds) must exceed the application drain timeout,
or SIGKILL arrives mid-drain and you are back where you started.
```

If the grace period is 30 s and your drain timeout is 45 s, the drain is a decoration.

Also note step 2's sleep. The LB discovers unreadiness on its next health-check interval. With `interval=10s` and `unhealthy_threshold=2`, that is up to 20 seconds. Exiting before then means the LB is still sending you traffic when you stop listening.

### 4.6 Immutable deploys

**Immutable infrastructure** means a running node is never modified. To change anything — code, configuration, OS packages, certificates — you build a new artefact and replace the node.

Why it belongs in this lesson: statelessness is what *allows* replacement to be cheap, and immutability is what *guarantees* nodes stay identical. The two together give you a fleet where any node can be destroyed and recreated at any moment without thought — which is the actual operational goal.

| | Mutable (patch in place) | Immutable (replace) |
|---|---|---|
| How you deploy | SSH / Ansible / `git pull` on each node | Build an image, launch new nodes, terminate old |
| Node divergence | Accumulates silently; the 400-day-uptime node is unique | Impossible by construction |
| Rollback | Re-run the old playbook and hope | Re-launch the previous image — fast and certain |
| Reproducing a bug | "Which node, and what's its history?" | The image tag is the whole answer |
| Boot time cost | None | 30 s–3 min per node; must be budgeted for autoscaling |
| Local state | Survives (and that is the problem) | Destroyed every deploy, which forces you to be honest about statelessness |

The last row is the hidden benefit: **immutable deploys make statelessness violations fail loudly and immediately**, instead of lurking until an incident.

### 4.7 What statelessness unlocks, concretely

- **Rolling deploys.** Replace `min_healthy_percentage` of the fleet at a time, with a health gate between batches. Zero downtime, and an automatic rollback trigger if error rate rises.
- **Canary releases.** Send 5 % of traffic to nodes running the new version. Only possible when any node can serve any user — with sticky sessions, your canary cohort is arbitrary and unstable.
- **Autoscaling.** Scale-in is destructive to stateful nodes, so stateful fleets can only ever grow. Statelessness is what makes the downward half of the curve safe (Topic 22).
- **Spot / preemptible capacity.** 60–90 % cheaper, with a 2-minute (AWS) or 30-second (GCP) termination notice. Entirely viable for a stateless tier with working drain logic; entirely unusable without it.
- **Multi-AZ and multi-region.** Nodes in different places are just more nodes. With local session state, "which AZ holds my session" becomes a routing problem.
- **Chaos testing.** You can kill a random node during business hours as a scheduled exercise, because it is a non-event.

## 5. Request / data flow

Two flows matter here: a request on a stateless node, and the lifecycle of a node being replaced.

**Flow A — one authenticated request, no local state anywhere:**

1. Browser sends `GET /cart` with cookie `sid=7f3a...` (an opaque, 128-bit random token — Lesson 6).
2. The load balancer picks **any** healthy node — say node 7. No affinity, no stickiness.
3. Node 7 reads the cookie. **It has never seen this user before and does not need to have.**
4. Node 7 issues `GET session:7f3a...` to Redis. 0.4 ms. Returns `{"user_id": 42, "exp": ...}`.
5. Node 7 validates expiry and, for a privileged action, re-checks the user's current role in the database rather than trusting a cached claim.
6. Node 7 checks its **in-process** catalogue cache for book metadata — allowed, because it is non-authoritative with a 30 s TTL (§4.3). Hit: 0.01 ms.
7. Node 7 reads the authoritative cart from PostgreSQL (or Redis, if carts are modelled as session data). 3 ms.
8. Node 7 renders and returns the response.
9. Node 7 **retains nothing** about user 42. Its memory is identical before and after, apart from cache entries that nobody would miss.
10. The next request from the same user goes to node 3 and behaves identically. The user cannot tell which node served either request — which is the definition of success.

**Flow B — node 7 is replaced during a rolling deploy:**

1. The deploy controller selects node 7 for replacement.
2. The controller calls deregister on the load balancer's target group.
3. The LB marks node 7 "draining" and **stops routing new requests** to it. Existing in-flight requests continue.
4. The controller sends `SIGTERM` to the application process.
5. The application's signal handler sets `SHUTTING_DOWN = True`. `/readyz` now returns 503 — belt and braces, in case the LB learns about removal only via health checks.
6. The application **sleeps 10 seconds** (one full health-check cycle plus propagation). It is still serving in-flight and any straggler requests during this time — deliberately.
7. The application stops accepting new connections and waits for in-flight requests to complete, up to a 25-second drain timeout. In practice they finish in under a second; p99.9 is 2 s.
8. The application closes its PostgreSQL and Redis pools cleanly (so the database does not accumulate dead connections), flushes buffered logs and metrics, and exits 0.
9. The controller terminates the instance. **Any local state dies here, and by design there was none.**
10. A new node boots from the new immutable image. Takes 35 s to boot, 20 s to fill connection pools and warm its cache.
11. The new node's `/startupz` passes, then `/healthz`, then `/readyz`.
12. The LB health-checks it twice (10 s apart), marks it healthy, and begins sending traffic — ideally with **slow start** (Lesson 21), ramping up over 30 s rather than hitting a cold node at full rate.
13. The controller moves to node 8. Total user-visible errors across the entire deploy: **zero**.

Compare step 13 with the same deploy on a stateful fleet: 12 nodes × ~100 in-flight requests = ~1,200 errors, plus every session on every node destroyed.

## 6. ASCII diagram

```
STATEFUL (what you must stop doing)      STATELESS (what you must do)

     ┌───────────────┐                        ┌───────────────┐
     │ Load balancer │                        │ Load balancer │
     │ (sticky by    │                        │ (any node,    │
     │  cookie!)     │                        │  no affinity) │
     └──┬─────┬───┬──┘                        └──┬─────┬───┬──┘
   user A│  B  │ C │                          any │ any │any│
        ▼     ▼   ▼                              ▼     ▼   ▼
   ┌──────┐┌──────┐┌──────┐                 ┌──────┐┌──────┐┌──────┐
   │node 1││node 2││node 3│                 │node 1││node 2││node 3│
   │      ││      ││      │                 │      ││      ││      │
   │ A's  ││ B's  ││ C's  │  ◀── state is   │ 30s  ││ 30s  ││ 30s  │ ◀ caches only:
   │ sess ││ sess ││ sess │      TRAPPED    │cache ││cache ││cache │   non-authoritative,
   │ A's  ││ B's  ││ C's  │      HERE       │      ││      ││      │   unmissed if lost
   │ cart ││ cart ││ cart │                 └───┬──┘└───┬──┘└───┬──┘
   │ /tmp ││ /tmp ││ /tmp │                     └───────┼───────┘
   │ cron ││ cron ││ cron │ ◀── runs 3x              ┌──┴──┐
   └──────┘└──────┘└──────┘                     ┌────▼──┐ ┌▼────────┐  ┌──────────┐
        │       │      │                        │ Redis │ │Postgres │  │ S3 / GCS │
        └───────┴──────┘                        │sessions│ │ orders │  │ uploads  │
               ▼                                │counters│ │ carts  │  │          │
        ┌────────────┐                          │ locks  │ └─────────┘  └──────────┘
        │ PostgreSQL │                          └────────┘   ▲
        └────────────┘                               ▲       │
                                                     └───────┴── ONE scheduler
   kill node 2  ▶ user B logged out,                    enqueues jobs; workers
                  cart lost, 3rd cron                   consume them: runs ONCE
                  still fires


GRACEFUL SHUTDOWN TIMELINE  (the ordering that eliminates deploy 502s)

 t=0s   SIGTERM ───▶ app sets readyz = 503
        │            LB still sending traffic! It doesn't know yet.
        │
 t=0-10s│  ░░░░░░░░ SLEEP ░░░░░░░░   ← THE STEP EVERYONE SKIPS
        │            app keeps serving; LB health-checks fail twice
 t=10s  │  LB removes node from rotation. No NEW requests arrive.
        │
 t=10s  ├──▶ stop accepting connections
        │    ▓▓▓▓ finish in-flight requests ▓▓▓▓  (p99.9 = 2s)
 t=12s  │    all in-flight complete
        ├──▶ close DB + Redis pools, flush logs/metrics
 t=13s  └──▶ exit(0)                         ✔ zero errors

 t=30s       SIGKILL would have fired here.
             REQUIRED: grace_period(30s) > drain_timeout(25s) > p99.9(2s)
             Get this ordering wrong and draining is decoration.


LIVENESS vs READINESS  (conflating these causes fleet-wide outages)

  /healthz  "am I alive?"      → fails → ORCHESTRATOR RESTARTS ME
            MUST NOT check DB ──┐
                                └─▶ if it did: DB blips 30s
                                    → ALL nodes fail liveness
                                    → ALL restart at once
                                    → cold caches everywhere
                                    → self-inflicted total outage

  /readyz   "send me traffic?"  → fails → LB STOPS SENDING, no restart
            MAY check hard deps (DB)  — but if DB is down, ALL nodes go
            unready and the LB has nowhere to route. Prefer: fail readiness
            for HARD deps only; degrade gracefully for SOFT deps.
```

## 7. Detailed example

### BookNook: the statelessness audit, item by item

This is the work hidden inside "step 3" of Lesson 19's §5. BookNook's FastAPI application is audited against §4.1. Six violations are found.

**Violation 1 — sessions in a module-level dict.**

```python
sessions = {}   # session_id -> {"user_id": 42, "cart": [...]}
```

50,000 active sessions × 20 KB of cart data = 1 GB of RAM doing the database's job. Fixed by moving sessions to Redis with a 24-hour TTL and moving carts to PostgreSQL (because a cart is business data a user expects to survive a week, not session data).

*Decision made explicitly:* **signed JWT or server-side session?** BookNook chose server-side sessions in Redis, because support must be able to force-logout a compromised account immediately, and a JWT remains valid until it expires. The cost is one 0.4 ms Redis lookup per request — at 6,000 RPS that is 6,000 Redis GETs/sec, which a single Redis node handles at roughly 5 % of capacity.

**Violation 2 — cover images written to `/var/www/uploads`.** Publishers upload cover art; node A writes the file; nodes B–L return 404 for it. Users saw broken images on roughly 11 of 12 page loads.

Fixed by having the browser upload **directly to S3 using a pre-signed URL** that the app generates. Two benefits beyond correctness: a 4 MB image never passes through the app tier (saving bandwidth and worker time — Lesson 1 §9 showed images were 40× the application's bandwidth), and the app node is never blocked for the duration of a slow mobile upload.

**Violation 3 — `@lru_cache` on `get_book_price()`.** This is the "flickering price" bug from Lesson 19 §7: an unbounded-TTL in-process cache is *authoritative-by-accident*. Rule 1 of §4.3 is violated — nothing refreshes it.

Fixed by replacing `lru_cache` with a 30-second TTL cache *plus* a shared Redis cache behind it. Prices are now at most 30 seconds stale and identical across nodes within that window. The team explicitly decided 30 seconds of price staleness was acceptable for browsing, and that **checkout re-reads the price from the database inside the transaction** — because a stale price at checkout is a financial bug, not a UX blemish.

**Violation 4 — `cron` running `expire_abandoned_carts.py` on every node.** Twelve nodes meant twelve "your cart is waiting!" emails per customer. Fixed with a single scheduler process that enqueues a job; any worker consumes it exactly once (Lesson 13). The interim fix was a Redis lock:

```
SET cron:expire_carts <node_id> NX PX 300000
```

This is correct enough for an email job and *not* correct enough for anything financial, because a node that pauses for garbage collection past the 300-second lease can wake up and continue working while another node holds the lock. The real fix is the single scheduler.

**Violation 5 — per-node login rate limiting.** `failed_logins[ip] += 1` in process memory, limit 5 per 15 minutes. Across 12 nodes this is **60 attempts per 15 minutes**, and an attacker who re-connects gets a fresh node each time. This is a security bug caused by scaling out, and it was invisible to every dashboard. Fixed with Redis `INCR` + `EXPIRE`, giving one global counter.

**Violation 6 — PDF invoice generation writing to `/tmp` across requests.** Step 1 generated the PDF and stored the path in the session; step 3 served it. On 12 nodes it worked 1 time in 12. Fixed by writing to S3 and storing the object key.

**The result.** After the audit, BookNook ran a deliberate test: terminate a random node every 30 minutes during business hours for a week. Zero user-visible errors. That test is what converts "we believe we're stateless" into "we are stateless".

### Contrasting example: a real-time collaborative editor

Now a system where full statelessness is **not** achievable, so you can see the boundary.

A collaborative document editor holds a **WebSocket per connected user** and a live in-memory representation of the document plus pending operations. The connection is inherently sticky: it is one TCP connection to one process, and it lasts an hour.

What such systems do is **not** "give up and be stateful" — it is to make the state *recoverable* rather than *local-only*:

- The connection is sticky, but the **document state is continuously persisted**, and every operation is written to a durable log before it is acknowledged.
- A **routing layer** maps `document_id → owning node` through a shared store, so any node can discover who owns a document rather than guessing.
- On node death, clients reconnect, are routed to a new owner, and **replay from the last sequence number they saw**. The user sees a 1–2 second reconnect, not data loss.
- Deploys are still rolling, but the drain step is longer: the node refuses new document sessions, waits for existing ones to migrate or idle out, and only then exits. Drain times are measured in minutes, not seconds.

The transferable rule: **when a connection must be sticky, make the state behind it replicated and replayable, and make the stickiness a routing decision stored in a shared place rather than an accident of which node answered first.** Note how much more machinery this needs than BookNook's HTTP tier — which is exactly why you should keep the stateless parts of your system stateless.

## 8. Code or configuration example

A FastAPI service showing externalised session state, environment config, the three probe endpoints, and a correct `SIGTERM` drain — plus the Kubernetes manifest whose timings must agree with the application's. **Illustrative only, not production-ready:** no TLS, no CSRF protection, no structured logging, no tracing, no rate-limit tuning, simplified error handling, and secrets are shown as plain environment variables where a real system would use a secret manager with rotation.

```python
# app.py -- a stateless BookNook node. ILLUSTRATIVE TEACHING CODE.
import os, signal, asyncio, time, secrets, json
from fastapi import FastAPI, Request, HTTPException, Response
import redis.asyncio as aioredis
import asyncpg

# --- FACTOR III: ALL config from the environment. Nothing read from a file on
# the box, so every node in the fleet is provably identical and one artefact
# runs unchanged in staging and production.
DATABASE_URL  = os.environ["DATABASE_URL"]       # points at PgBouncer, not PG
REDIS_URL     = os.environ["REDIS_URL"]
APP_VERSION   = os.environ.get("APP_VERSION", "dev")   # the image tag
DRAIN_SLEEP   = float(os.environ.get("DRAIN_SLEEP", "10"))   # SS4.5 step 2
DRAIN_TIMEOUT = float(os.environ.get("DRAIN_TIMEOUT", "25")) # SS4.5 step 4
SESSION_TTL   = 86400

app = FastAPI()
redis = None
pg = None

# The ONLY module-level mutable state permitted: a shutdown flag (not per-user)
# and a cache that is non-authoritative and unmissed if lost (SS4.3).
SHUTTING_DOWN = False
_catalogue_cache: dict[str, tuple[float, dict]] = {}
CATALOGUE_TTL = 30.0   # chosen as "how long may a price be stale while browsing"

@app.on_event("startup")
async def startup():
    global redis, pg
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    # Pool size is PER NODE. 12 nodes x 20 = 240 connections, which is why this
    # URL must point at PgBouncer (Lesson 19 SS4.1), not straight at PostgreSQL.
    pg = await asyncpg.create_pool(DATABASE_URL, min_size=5, max_size=20)

# ---------------------------------------------------------------- PROBES
@app.get("/healthz")
async def healthz():
    """LIVENESS. 'Is this process alive?' Checks NOTHING external.
    If this ever queried PostgreSQL, a 30s DB blip would fail liveness on every
    node at once and the orchestrator would restart the ENTIRE fleet with cold
    caches -- turning a blip into a total outage (SS4.4)."""
    return {"status": "alive", "version": APP_VERSION}

@app.get("/readyz")
async def readyz(response: Response):
    """READINESS. 'Should the load balancer send me traffic?'
    Failing this removes the node from rotation; it does NOT restart it."""
    if SHUTTING_DOWN:
        response.status_code = 503          # drain step 1: refuse new traffic
        return {"status": "draining"}
    try:
        # HARD dependency: without the database this node cannot serve anything.
        # Timeout is short so a slow DB does not make the probe itself hang.
        async with pg.acquire() as c:
            await asyncio.wait_for(c.fetchval("SELECT 1"), timeout=2.0)
    except Exception:
        response.status_code = 503
        return {"status": "db_unreachable"}
    # NOTE a deliberate omission: the recommendations service is a SOFT
    # dependency. If it is down we serve a page without recommendations --
    # we do NOT mark ourselves unready, because that would remove every node.
    return {"status": "ready", "version": APP_VERSION}

# ---------------------------------------------------------------- SESSIONS
async def current_user(request: Request) -> int:
    """No per-client state in this process. The cookie is an opaque random
    token; the authoritative session lives in Redis, visible to ALL nodes."""
    sid = request.cookies.get("sid")
    if not sid:
        raise HTTPException(401, "not authenticated")
    raw = await redis.get(f"session:{sid}")     # ~0.4ms; the price of statelessness
    if raw is None:
        raise HTTPException(401, "session expired")
    # Sliding expiry: refresh TTL so active users are not logged out mid-session.
    await redis.expire(f"session:{sid}", SESSION_TTL)
    return json.loads(raw)["user_id"]

@app.post("/login")
async def login(request: Request, response: Response):
    user_id = 42  # password verification omitted -- illustrative only
    # 128 bits of CSPRNG entropy. Never a sequential ID, never a hash of the
    # user ID: a guessable session token is account takeover (Lesson 6).
    sid = secrets.token_urlsafe(32)
    await redis.setex(f"session:{sid}", SESSION_TTL, json.dumps({"user_id": user_id}))
    # Server-side sessions (not JWTs) were chosen so support can revoke
    # instantly by deleting one Redis key. A JWT stays valid until it expires.
    response.set_cookie("sid", sid, httponly=True, secure=True, samesite="lax")
    return {"ok": True}

# ---------------------------------------------------------------- RATE LIMIT
async def check_login_rate(ip: str):
    """Shared counter. A per-process dict here would give 12 nodes x 5 = 60
    attempts instead of 5 -- a security hole created by scaling out (SS4.1 #7)."""
    key = f"rl:login:{ip}"
    n = await redis.incr(key)
    if n == 1:
        await redis.expire(key, 900)   # 15-minute window
    if n > 5:
        raise HTTPException(429, "too many attempts")

# ---------------------------------------------------------------- LOCAL CACHE
async def get_book(isbn: str) -> dict:
    """In-process cache: ALLOWED, because it is (1) non-authoritative and
    (2) unmissed if this process dies. 30s TTL means a price change is visible
    everywhere within 30s; during that window nodes may disagree. Checkout
    re-reads price inside the DB transaction, so the disagreement is cosmetic."""
    now = time.monotonic()
    hit = _catalogue_cache.get(isbn)
    if hit and now - hit[0] < CATALOGUE_TTL:
        return hit[1]
    async with pg.acquire() as c:
        row = await c.fetchrow("SELECT isbn,title,price_paise FROM books WHERE isbn=$1", isbn)
    book = dict(row)
    _catalogue_cache[isbn] = (now, book)
    return book

# ---------------------------------------------------------------- UPLOADS
@app.post("/covers/presign")
async def presign_cover(isbn: str):
    """Bytes never touch this node. The browser PUTs straight to object storage,
    so no file exists on local disk for the other 11 nodes to be missing.
    Side benefit: a 4MB upload never occupies an app worker (SS7, violation 2)."""
    return {"url": s3_presigned_put(f"covers/{isbn}.jpg", expires=300,
                                    max_bytes=5_000_000,
                                    content_type="image/jpeg")}

# ---------------------------------------------------------------- SHUTDOWN
async def graceful_shutdown():
    """The sequence from SS4.5. Omitting the sleep is the single most common
    cause of 502s on every deploy."""
    global SHUTTING_DOWN
    SHUTTING_DOWN = True                 # 1. /readyz starts returning 503

    # 2. KEEP SERVING while the LB notices. With interval=10s and
    #    unhealthy_threshold=2, the LB needs up to 20s to remove us. Exiting
    #    now would mean the LB is still routing traffic to a dead socket.
    await asyncio.sleep(DRAIN_SLEEP)

    # 3-4. Uvicorn stops accepting new connections and waits for in-flight
    #      requests, bounded by DRAIN_TIMEOUT (must exceed p99.9 duration).
    server.should_exit = True
    deadline = time.monotonic() + DRAIN_TIMEOUT
    while in_flight_requests() > 0 and time.monotonic() < deadline:
        await asyncio.sleep(0.1)

    # 5. Close pools cleanly so PostgreSQL/Redis do not accumulate dead
    #    connections, and flush buffered logs/metrics before the process dies.
    await pg.close()
    await redis.aclose()
    # 6. exit 0

def handle_sigterm(signum, frame):
    asyncio.get_event_loop().create_task(graceful_shutdown())

signal.signal(signal.SIGTERM, handle_sigterm)   # SIGTERM = "please stop"
# SIGKILL cannot be caught. If the grace period expires mid-drain, everything
# above is wasted -- hence the ordering constraint in the manifest below.
```

```yaml
# deployment.yaml -- the orchestrator side. ILLUSTRATIVE.
# The timings here MUST agree with the application's, or draining is decoration.
apiVersion: apps/v1
kind: Deployment
metadata: { name: booknook-app }
spec:
  replicas: 12
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 1   # never drop below 11/12 capacity during a deploy
      maxSurge: 2         # bring up 2 extra so capacity never dips
  template:
    spec:
      # ORDERING CONSTRAINT (SS4.5):
      #   grace period (40) > DRAIN_SLEEP (10) + DRAIN_TIMEOUT (25) = 35  OK
      # If this were 30, SIGKILL would land mid-drain and kill live requests.
      terminationGracePeriodSeconds: 40
      containers:
        - name: app
          image: booknook/app:9f3c2a1     # IMMUTABLE: a git SHA, never ":latest"
          env:
            - name: APP_VERSION
              value: "9f3c2a1"
            - name: DRAIN_SLEEP
              value: "10"
            - name: DRAIN_TIMEOUT
              value: "25"
            - name: DATABASE_URL          # secrets from a manager, not the image
              valueFrom: { secretKeyRef: { name: booknook, key: database-url } }

          # STARTUP probe gates the others during a slow boot. Without it, a node
          # needing 60s to warm up is killed by the liveness probe at 30s, forever.
          startupProbe:
            httpGet: { path: /healthz, port: 8000 }
            periodSeconds: 5
            failureThreshold: 24          # allow up to 120s to come up

          # LIVENESS: restarts the container. Points at /healthz, which checks
          # NOTHING external -- see the comment in app.py.
          livenessProbe:
            httpGet: { path: /healthz, port: 8000 }
            periodSeconds: 10
            failureThreshold: 3

          # READINESS: controls traffic only. Checked often so removal is fast.
          readinessProbe:
            httpGet: { path: /readyz, port: 8000 }
            periodSeconds: 5
            failureThreshold: 2           # ~10s to be pulled from the Service

          # Read-only root filesystem: makes "write a file to local disk" FAIL
          # LOUDLY at development time instead of silently breaking on node 2.
          securityContext:
            readOnlyRootFilesystem: true
          volumeMounts:
            - { name: scratch, mountPath: /tmp }   # within-request scratch only
      volumes:
        - name: scratch
          emptyDir: {}    # explicitly ephemeral: dies with the pod, by design
```

## 9. Numerical example

Two questions: **what does statelessness cost per request, and what does it buy in deploy safety and availability?**

### Part A — the latency and capacity cost of externalising sessions

**Assumptions**

1. Peak load 6,000 RPS across 12 nodes (500 RPS per node).
2. Every authenticated request does one Redis `GET` + one `EXPIRE` for the session.
3. Redis round trip in the same availability zone: **0.4 ms p50, 1.2 ms p99** (typical).
4. Baseline request time without the session lookup: 20 ms p50, 180 ms p99.
5. 85 % of requests are authenticated.

```
Added latency, p50 = 0.4 ms on a 20 ms request  = +2.0%
Added latency, p99 = 1.2 ms on a 180 ms request = +0.67%

Extra Redis operations per second:
  6,000 RPS × 0.85 authenticated × 2 ops = 10,200 ops/s
```

A single Redis node handles roughly **100,000–200,000 simple ops/sec**. 10,200 ops/s is **5–10 % of one Redis node.** 

**Memory for sessions:**

```
Assume 50,000 concurrent sessions, 400 bytes each (user_id, expiry, roles,
CSRF token -- NOT the cart, which lives in PostgreSQL):
  50,000 × 400 B = 20 MB
Redis overhead ≈ 2× for key metadata and allocator slack → ~40 MB
```

**40 MB and ~7 % of one Redis node buys interchangeability for the entire fleet.** Compare with the stateful version: 50,000 sessions × 20 KB (sessions *plus* carts in memory) = **1 GB per node, replicated 12 times = 12 GB of RAM** doing nothing but trapping users on specific machines.

**Verdict: a 2 % p50 latency increase and $0 of additional infrastructure, for the entire set of capabilities in §4.7.** This calculation is why the trade is essentially always worth making.

### Part B — deploy time and error count

**Assumptions**

1. 12 nodes, 500 RPS each, average request 200 ms, p99.9 request 2 s.
2. Rolling deploy with `maxUnavailable: 1` (one node at a time).
3. Per-node replacement: 10 s drain sleep + 2 s in-flight completion + 35 s boot + 20 s warm-up + 10 s health checks (2 × 5 s readiness) = **77 s**.

```
Requests in flight per node (Little's Law, Lesson 14):
  L = λ × W = 500 req/s × 0.2 s = 100 requests in flight at any instant
```

**Without graceful shutdown** (process exits immediately on SIGTERM):

```
Errors per node replaced   = 100 in-flight requests destroyed
Errors per full deploy     = 100 × 12 = 1,200 failed requests
At 5 deploys/day           = 6,000 user-visible errors/day
As an error rate over the deploy window:
  deploy window = 12 × 77 s = 924 s
  requests in window = 6,000 RPS × 924 s = 5,544,000
  error rate = 1,200 / 5,544,000 = 0.0217%  ← looks tiny on a dashboard,
                                              and is 1,200 angry users
```

**With graceful shutdown:** errors = **0**, because every in-flight request completes before the socket closes.

**Deploy duration, one node at a time:**

```
12 nodes × 77 s = 924 s ≈ 15.4 minutes
```

**With `maxSurge: 2` and 2 nodes at a time** (capacity never drops, because surge nodes come up first):

```
ceil(12 / 2) = 6 batches × 77 s = 462 s ≈ 7.7 minutes
```

**Compare with the stateful alternative:** a stateful fleet cannot roll at all. The deploy is "stop everything, start everything": ~60 s of hard downtime *plus* all 50,000 sessions destroyed, which is why such teams deploy at 02:00 and only monthly. The business cost is not the 60 seconds — it is that **deploy frequency drops from 5/day to 1/month**, which is a 150× increase in the amount of change per release and therefore in the risk of each one.

### Part C — availability contribution

**Assumptions:** From Lesson 19 §9 Part C, the six-node tier's residual downtime was ~58.5 min/year, dominated by correlated failures. Now attribute the pieces statelessness changes:

```
BEFORE statelessness (stateful fleet, big-bang deploys):
  Deploys:        12/year × 60 s hard downtime        = 12 min
  Deploy errors:  12/year × 1,200 requests (not time,
                  but a user-visible failure)          = 14,400 failed reqs
  Node failure:   each failure logs out 1/n of users;
                  ~10 node failures/year × 8% of
                  50,000 sessions                      = 40,000 forced logouts
  Scale-in:       impossible — pay peak 24/7

AFTER statelessness (rolling deploys, drained shutdown):
  Deploys:        1,825/year (5/day) × 0 s downtime    = 0 min
  Deploy errors:                                        = 0
  Node failure:   in-flight requests only, ~100 per
                  failure × 10 failures/year           = 1,000 failed reqs
                  forced logouts                        = 0
  Scale-in:       enabled → see below
```

**Autoscaling saving enabled by statelessness** (previewing Topic 22). Assume a daily traffic curve where the fleet needs 12 nodes for 4 peak hours, 7 nodes for 10 hours, and 4 nodes for 10 hours:

```
Static fleet (must size for peak):
  12 nodes × 24 h = 288 node-hours/day
Autoscaled fleet:
  (12 × 4) + (7 × 10) + (4 × 10) = 48 + 70 + 40 = 158 node-hours/day

Saving = 1 − 158/288 = 45.1%
At $147/node-month (m7i.xlarge): 
  static      = 12 × $147                      = $1,764/month
  autoscaled  = 1,764 × 0.549                  = $968/month
  saving                                       = $796/month = $9,552/year
```

**Summary of what the §7 audit bought:** 14,400 fewer failed requests per year, 40,000 fewer forced logouts, a 150× increase in deploy frequency, zero deploy downtime, and $9,552/year — in exchange for 0.4 ms per request and 40 MB of Redis.

## 10. Trade-offs

**Advantages**

- **Enables everything in Lesson 19 and Topic 22.** Horizontal scaling, autoscaling, rolling deploys, canaries, spot instances, multi-AZ. Not "helps with" — *enables*.
- **Node failure becomes routine.** The cost of losing a node drops from "every session on it" to "the ~100 requests in flight".
- **Deploys become invisible and therefore frequent**, which is the single largest driver of delivery speed and, counter-intuitively, of reliability — smaller changes fail less.
- **Debugging simplifies.** No "which node, and how long has it been up?" There is one behaviour.
- **Capacity planning simplifies.** Nodes are a fungible unit; you need `ceil(peak / per_node)` of them and nothing else.
- **Security improves in places.** Global rate limits actually work; sessions can be revoked centrally; a read-only root filesystem becomes possible.

**Disadvantages**

- **Latency tax on every request.** 0.4–1 ms for a session lookup, more if the store is in another AZ. At p99 under load, a slow session store becomes *your* p99.
- **The session store is a new dependency and a new correlated failure.** If Redis is down, all 12 nodes fail authentication identically. You have not removed the single point of failure; you have moved and concentrated it — which is why it must be replicated (Topic 15) and why a short Redis outage should degrade rather than kill you.
- **More moving parts to operate**: Redis, object storage, a connection pooler, a scheduler with leader election, a secret manager.
- **Some things are genuinely harder**: WebSockets, long-running multi-step workflows, anything needing a large in-memory working set per user.
- **You lose cheap in-process state**, and the discipline is easy to erode — one `@lru_cache` added in a hurry re-introduces incoherency.
- **Cache hit rates can fall.** Twelve nodes each caching independently see 1/12 of the traffic each, so each cache is colder (Lesson 19 §4.2). Shared caches fix this but add another hop.

**Operational costs**

Running Redis in HA (primary + replica + failover), lifecycle policies and access controls on object storage, a leader-elected scheduler, and — most importantly — **a test that proves statelessness**. Terminating a random node during business hours on a schedule is the only way to know. Without that test, "we're stateless" is a belief.

**When it becomes unnecessary complexity**

A single-node internal tool with 20 users and no availability requirement does not need Redis, pre-signed URLs and leader election. In-memory sessions are fine there, and the correct time to change is when you decide to add the second node. The mistake is not "being stateful"; it is "being stateful and adding a second node anyway".

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| **Session store (Redis) down** | Every user logged out simultaneously across all nodes; login fails | Redis connection errors on every node at once; 401 rate spikes to ~85 % of traffic | Redis replica promotion (seconds, if configured). Mitigate by running HA Redis, using short-lived signed tokens as a fallback read path, and *not* failing readiness on it so cached-public pages still serve |
| **No graceful shutdown implemented** | A burst of 502s on every deploy and every scale-in; blamed on "the network" for months | 502 count correlates exactly with deregistration events; ~100 per node replaced (§9 Part B) | Implement the §4.5 sequence. Verify by deploying under load and asserting zero 5xx |
| **Grace period shorter than drain timeout** | Same as above, despite drain code existing — SIGKILL lands mid-drain | Logs show the drain starting but never completing; container exit code 137 | Fix the ordering: `grace > drain_sleep + drain_timeout > p99.9` |
| **Liveness probe checks the database** | A 30-second DB blip becomes a fleet-wide restart with cold caches: 10+ minutes of severe degradation from a brief problem | Every node restarts within the same minute; restart count jumps across the whole fleet | Remove external checks from liveness. This is a config fix, and it should be audited for in code review |
| **Readiness fails on a soft dependency** | Entire fleet marked unready because the recommendations service is down; LB has no healthy backends; **total outage** | LB reports 0 healthy targets while all processes are alive and the database is fine | Separate hard from soft dependencies in `/readyz`; degrade features instead of removing nodes |
| **In-process cache re-introduced** (an `@lru_cache` in a PR) | Prices, stock or feature flags flicker between refreshes; "it fixes itself if I reload" | Almost invisible to monitoring; found by users. Detect by returning the node ID in a response header and correlating | Remove or TTL-bound it; add a lint rule banning unbounded caches on mutable data |
| **Local file write succeeds in dev, fails in prod** | Broken images / missing PDFs on 11 of 12 requests | 404 rate on user-generated content; reproduces only sometimes | Pre-signed uploads to object storage; `readOnlyRootFilesystem: true` makes this fail loudly during development |
| **Duplicate scheduled jobs** | Customers get 12 copies of an email; metrics double-count; a financial job runs twice | Duplicate-send complaints; identical log lines from 12 hostnames | Single scheduler enqueuing work; distributed lock as an interim measure, with the caveat in §7 |
| **Per-node rate limiting** | Brute-force protection silently 12× weaker; attacker is never locked out | Nothing. This is invisible until an incident or an audit | Move counters to Redis `INCR`/`EXPIRE`; test limits against the fleet, not against one node |
| **Spot instance reclaimed with 2 minutes' notice** | Nothing, if drain works. If not, ~100 failed requests per reclamation, several times a day | Spot-interruption notices in the instance metadata/event stream | Handle the interruption notice as an early `SIGTERM`: fail readiness immediately, then drain |
| **Slow startup killed by liveness probe** | Node never becomes healthy; the fleet silently runs below capacity; latency creeps up | `CrashLoopBackOff`; ready-replica count lower than desired | Add a `startupProbe` with a generous `failureThreshold` to gate liveness during boot |

## 12. Alternatives

| Approach | How it differs | Good at | Bad at | When you'd pick it |
|---|---|---|---|---|
| **Fully stateless + shared session store** | All session state in Redis/Memcached | Interchangeable nodes, instant revocation, global rate limits | A per-request network hop; the store is a correlated dependency | BookNook — the default for web APIs |
| **Stateless via signed tokens (JWT)** | State travels *in the token*; no server lookup at all | Zero session-store latency; works across services and regions | **Cannot revoke before expiry**; token size on every request; key rotation is real work | Service-to-service auth; very high RPS; short (5–15 min) lifetimes with refresh |
| **Sticky sessions / session affinity** | LB pins a user to a node by cookie or source IP | Zero migration effort; keeps in-process caches hot | Node failure logs out its users; uneven load; blocks autoscaling and canaries | A legacy app you cannot modify — explicitly a stopgap |
| **Session replication between nodes** | Each node broadcasts sessions to peers | No external store; survives single-node loss | O(n²) traffic, memory on every node, split-brain; this is the κ term of the USL | Classic Java app servers; avoid in new designs |
| **Client-side state** (cart in `localStorage`) | The browser holds it | No server storage at all; works offline | Untrusted, device-bound, lost on device change (Lesson 1 trust boundary) | Draft text, UI preferences — never prices or entitlements |
| **Sticky + replicated (the editor pattern)** | Sticky routing, but state is persisted and replayable | Makes long-lived connections survivable | Much more machinery: routing registry, sequence numbers, replay | WebSockets, collaborative editing, game servers (§7) |
| **Stateful sets with stable identity** | Nodes have durable identity and attached storage | Databases, queues, consensus members that *must* be stateful | Slow to scale, ordered rollouts, not interchangeable | Postgres, Kafka, etcd — never the web tier |
| **Serverless functions** | Statelessness enforced by the platform | Statelessness is not optional, so it cannot erode | Cold starts, no in-process cache at all, per-request cost | Spiky endpoints; teams that need the discipline enforced |

The JWT row deserves a decision rule, since it is the most common fork: **use server-side sessions when you need instant revocation or sessions larger than ~1 KB; use signed tokens when the per-request lookup is a measured bottleneck or when the verifier cannot reach your session store** (another service, another region, an edge worker). Many systems use both: a short-lived signed access token plus a server-side refresh token that *is* revocable.

## 13. When to use it

1. **Any HTTP/API tier you intend to run on more than one machine.** This is the default and should not require justification. The question to defend is why a tier would *not* be stateless.
2. **Before adding the second node** — not after. Doing the audit first converts a class of intermittent, hard-to-reproduce production bugs into a planned afternoon of work.
3. **Whenever you want to deploy during business hours.** Rolling deploys with draining are impossible otherwise, and deploy frequency is a leading indicator of engineering health.
4. **When you want autoscaling, including scale-*in*.** A stateful fleet can only grow; the savings in §9 Part C (45 %) come entirely from being able to shrink.
5. **When you want to use spot/preemptible instances.** 60–90 % discounts are available only to workloads where node loss is a non-event.
6. **When intermittent, node-dependent bugs are already costing you.** "Works if I refresh", "only on some requests", "can't reproduce locally" are all symptoms of divergent node state.
7. **Multi-AZ or multi-region deployments.** Nodes in different places are just more nodes — but only if no node holds something unique.

## 14. When *not* to use it

1. **Genuinely stateful infrastructure.** Databases, message brokers, consensus members (etcd, ZooKeeper), search index nodes. These require stable identity and attached storage, and forcing statelessness on them is a category error. Scale them vertically (Lesson 18) and shard them deliberately (Topic 18).
2. **Single-node deployments with no availability requirement.** An internal tool with 20 users does not need Redis and pre-signed URLs. Add the complexity when you add the second node — and not before.
3. **Hard real-time or ultra-low-latency paths where the extra round trip is unaffordable.** If your budget is 2 ms end to end, a 0.4 ms session lookup is 20 % of it. Use signed tokens, or keep a node-local read-through cache of sessions with a very short TTL and accept the staleness.
4. **Long-lived connection workloads, where full statelessness is not achievable.** WebSockets, game servers, collaborative editors. The goal there is *recoverable* state with shared routing (§7, second example), not pretended statelessness.
5. **Workloads with a large per-user in-memory working set.** If serving a user requires a 2 GB in-memory model, re-fetching it per request is absurd. Use sticky routing plus a warm-up path, and treat the stickiness as an explicit, stored routing decision.
6. **When the "shared store" would be a worse single point of failure than what you have.** A single, unreplicated Redis holding every session is a correlated dependency with no redundancy — arguably worse than one stateful app server, because it fails for everyone at once. Replicate it before you depend on it.

## 15. Common mistakes

**Beginner mistakes**

- **Thinking "stateless" means "no database".** It means no *per-client session state in local process memory*. The database is the point.
- **Moving sessions to Redis and leaving file uploads, caches, cron and counters on the node.** Statelessness is a checklist (§4.1), not a single change, and partial statelessness fails exactly as badly as none.
- **Using JWTs because they are "stateless", without a revocation plan.** A stolen token is valid until expiry. If the expiry is 30 days, you have built an account-takeover amplifier.
- **Not implementing `SIGTERM` handling at all**, then living with 502s on every deploy and calling it flakiness.
- **Omitting the sleep in the drain sequence.** Flipping readiness to 503 and exiting immediately changes nothing — the LB has not noticed yet.
- **Pointing the liveness probe at the database.** Converts a dependency blip into a fleet-wide restart.
- **Letting per-node rate limits ship.** A 12× weaker security control that no dashboard reveals.
- **Keeping cron entries on every node** after scaling out.
- **Testing statelessness only in staging** (one node), where every violation passes.

**Experienced-engineer mistakes**

- **Grace period, drain timeout and p99.9 duration not ordered correctly.** The drain code exists, is reviewed, and does nothing because `SIGKILL` arrives first.
- **Readiness checking soft dependencies**, so a non-critical service outage empties the load balancer and causes a total outage. This failure mode has taken down large production systems.
- **Treating the session store as infinitely available.** It needs replication, failover testing, a memory-eviction policy that does not silently drop live sessions (`maxmemory-policy noeviction` vs `allkeys-lru` is a correctness decision, not a tuning one), and a plan for what the app does when it is unreachable.
- **Forgetting that statelessness pushes load onto shared stores.** 12 nodes × 500 RPS × 2 Redis ops = 12,000 ops/s now funnels into one Redis. Size and monitor it, and remember it is now on the critical path for every request.
- **Allowing an in-process cache back in without a TTL**, re-creating incoherency months after the original fix. Add a lint rule.
- **Not running migrations as a separate pipeline step.** Twelve nodes starting simultaneously and all attempting a migration is a race with a corrupted schema at the end.
- **Assuming containers are stateless by default.** A container with a writable filesystem and a 30-day lifetime accumulates exactly as much local state as a VM. `readOnlyRootFilesystem` plus a short node lifetime is what enforces it.
- **Never testing node termination in production.** Statelessness that has not been verified by killing a node under real traffic is a hypothesis.

## 16. Interview perspective

Statelessness is the hinge between "I have read about scaling" and "I have done it". Interviewers probe it because the details are impossible to fake.

**How to use it well:**

- **Volunteer the checklist, not the slogan.** "Stateless means sessions in Redis, uploads pre-signed to S3, cron on a single leader-elected scheduler, rate-limit counters in Redis, no local temp files, and logs to stdout." Six concrete items beats one abstract sentence.
- **Make the session decision explicitly, with a reason.** "Server-side sessions in Redis, because support must be able to force-logout immediately; a JWT stays valid until expiry. The cost is 0.4 ms per request, about 2 % of p50." You have named the trade and priced it.
- **Bring up graceful shutdown unprompted.** "On SIGTERM I fail readiness, keep serving for one health-check cycle so the LB notices, then drain in-flight requests within 25 seconds — and the orchestrator's grace period must exceed that, or SIGKILL lands mid-drain." Very few candidates say this and it is immediately recognisable as production experience.
- **Distinguish liveness from readiness and say why it matters.** "Liveness must not check the database, otherwise a DB blip restarts the whole fleet." This is a specific, high-consequence detail.
- **Quantify.** "Without draining, a 12-node rolling deploy destroys about 1,200 in-flight requests — 100 per node by Little's Law at 500 RPS and 200 ms."
- **Name what statelessness unlocks.** Autoscaling scale-*in*, spot instances, canaries, chaos testing, multi-AZ. Tie it to the 45 % cost saving (§9 Part C).
- **Acknowledge the limits honestly.** "WebSockets can't be fully stateless; I'd keep the connection sticky but store the subscription registry in Redis and give clients a sequence number so they can replay after a reconnect."

**Red flags interviewers watch for:**

- Saying "we'll make it stateless" with no mechanism named.
- Proposing sticky sessions as the *solution* rather than as an explicit stopgap, without noting that node failure still loses those users.
- Choosing JWTs with no revocation story, or with multi-day expiry.
- No mention of graceful shutdown or connection draining anywhere in a design that includes deploys or autoscaling.
- Putting external dependency checks in a liveness probe.
- Claiming statelessness while leaving cron on every node, or leaving rate limits in process memory.
- Forgetting that the session store is now a correlated dependency that needs its own HA.
- Believing containers are automatically stateless.

## 17. Knowledge check

Answer these before reading on. Write your answers down — even one line each.

**Conceptual**

1. A server keeps a 60-second in-process cache of the book catalogue, and also keeps a per-user "items viewed this session" list in memory. Both are "state in process memory". Explain precisely why one is compatible with statelessness and the other is not, using the two rules from §4.3, and state what the user-visible symptom of the second one would be on a 10-node fleet.
2. Explain the difference between a liveness probe and a readiness probe, and describe the specific chain of events that occurs when a liveness probe is pointed at PostgreSQL and PostgreSQL has a 30-second outage. Why is the resulting outage *longer* than the original 30 seconds?
3. Your team moves sessions from process memory into a single Redis instance and declares the application stateless and highly available. Identify what has actually improved, what has not, and the one new failure mode that is arguably worse than the original design.

**Practical scenario**

4. BookNook is migrating to spot instances (70 % cheaper, 2 minutes' termination notice, typically reclaimed 2–4 times per day across a 12-node fleet). Describe (a) every change required in the application and deployment configuration to make this safe, (b) the specific numbers you would set for the drain sleep, drain timeout and grace period and how you would derive each from a measurement, and (c) one category of BookNook workload you would *not* put on spot, with your reasoning.

**Calculation**

5. BookNook runs 20 nodes at 450 RPS each. Average request duration is 160 ms; p99.9 is 3.2 s. Each node replacement takes 12 s of drain sleep, in-flight completion, 40 s boot, 25 s warm-up and 12 s of health checks. Deploys happen 6 times per day.
   (a) Using Little's Law, how many requests are in flight on one node at any instant?
   (b) Without graceful shutdown, how many user-visible errors does each full rolling deploy cause, and how many per year?
   (c) How long does a full rolling deploy take with `maxUnavailable: 1`, and with `maxUnavailable: 4`? What is the capacity risk of the second option at peak?
   (d) What values would you set for drain timeout and `terminationGracePeriodSeconds`, and what constraint must hold between them, the drain sleep and p99.9? Show every step and state every assumption you add.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 21.

---

**Progress checklist**

Phase 1 — Internet and System-Design Foundations: all 17 topics `[x]` complete.

Phase 2 — Scaling
- [x] 1. Vertical scaling
- [x] 2. Horizontal scaling
- [x] 3. Stateless application servers
- [ ] 4. Load balancers
- [ ] 5. Layer 4 versus Layer 7 load balancing
- [ ] 6. Load-balancing strategies
- [ ] 7. Health checks and failover
- [ ] 8. Reverse proxies and API gateways
- [ ] 9. Caching fundamentals
- [ ] 10. Browser, application, database, distributed caches
- [ ] 11. Cache-aside, read-through, write-through, write-behind
- [ ] 12. Cache invalidation and TTL
- [ ] 13. Cache stampedes, hot keys, penetration, eviction
- [ ] 14. CDNs and edge caching
- [ ] 15. Database replication
- [ ] 16. Synchronous versus asynchronous replication
- [ ] 17. Replication lag and read-after-write consistency
- [ ] 18. Database partitioning and sharding
- [ ] 19. Range, hash, directory, geographic sharding
- [ ] 20. Resharding, hot partitions, cross-shard queries
- [ ] 21. Consistent hashing and virtual nodes
- [ ] 22. Autoscaling and multi-region architecture

Phases 3–5: not started.

**NEXT LESSON: Phase 2, Topic 4 — Load balancers**

---

# Lesson 21 — Load Balancers

*Phase 2, Topic 4*

## 1. Simple definition

A **load balancer (LB)** is a component that sits in front of a group of interchangeable servers, receives every incoming request, and decides which server handles it. To clients it looks like a single server; behind it there may be two machines or two thousand.

Three terms, defined before use:

- **Backend / target / upstream** — one server behind the load balancer. (The three words mean the same thing; HAProxy says "backend", AWS says "target", nginx says "upstream".)
- **Virtual IP (VIP)** — the single IP address clients connect to, which is owned by the load balancer rather than by any backend. The VIP is what makes "one address, many machines" possible.
- **Target group / backend pool** — the set of backends the LB considers eligible, plus the health rules that decide which members are currently eligible.

The load balancer does **three jobs**, and it is worth memorising them in this order because it is how you should explain it in an interview:

1. **Distribute** incoming requests across healthy backends.
2. **Health-check** backends continuously and remove the unhealthy ones from rotation.
3. **Terminate TLS**, so backends do not each have to decrypt traffic and hold certificates.

Job 2 is the one people forget and the one that actually delivers availability. A load balancer that distributes perfectly but does not notice a dead backend is worse than no load balancer at all — it reliably sends 1/n of your traffic into a hole.

## 2. Why it exists

Lesson 19 ended with four interchangeable, stateless app nodes. Something must now answer the question **"which one?"** for every single request. That is the load balancer's reason to exist, but it solves four distinct problems:

**Problem 1 — clients cannot be trusted to choose.** You could publish all four IP addresses and let clients pick. They would pick badly, cache the choice, keep using a dead node for an hour (Lesson 4 — DNS caching), and every client platform would behave differently. Centralising the decision at a component you control is the only way to make it correct and changeable.

**Problem 2 — failure must be handled in seconds, not hours.** When node 3 dies, something must notice and stop sending it traffic. DNS cannot do this: a record with a 60-second TTL is still cached for minutes to hours by real-world resolvers and client runtimes that ignore TTLs entirely (Java's `networkaddress.cache.ttl` historically defaulted to caching forever). A load balancer notices in **10–30 seconds** and acts immediately.

**Problem 3 — the fleet must be changeable without telling anyone.** Adding a node, removing a node, draining a node for a deploy (Lesson 20) must all be invisible to clients. The LB is the indirection layer that makes the backend set a private implementation detail.

**Problem 4 — cross-cutting work should happen once, not n times.** TLS termination, request logging, rate limiting, compression, header normalisation, and IP allow-lists are all identical for every backend. Doing them at the edge means one certificate to rotate instead of twelve, and one place to change a rate limit.

There is a fifth reason that is really a consequence: **the load balancer is where you get a global view of traffic.** It is the only component that sees every request and every backend's behaviour, which makes it the natural place for request-rate, error-rate and latency metrics — and for the decisions that depend on them (Topic 6 — load-balancing strategies).

## 3. Real-world analogy

An **airport security queue manager** — the person standing at the head of the snaking queue, directing each passenger to the next free lane.

- Passengers (requests) do not choose a lane; the manager assigns one. **Distribute.**
- When a lane's scanner breaks, the manager stops sending people to it — and the people already in that lane still get processed. **Health-check plus draining.**
- Passengers remove laptops and belts at a common point before the lanes. **Cross-cutting work done once (TLS termination).**
- Opening a new lane is invisible to passengers; they just notice the queue moving faster. **Elastic backends.**
- When a new lane opens, the manager sends a few people at a time rather than half the queue at once, because the staff are still setting up. **Slow start.**

And the critical one: **if the queue manager faints, the entire checkpoint stops**, even though every lane is working perfectly. That is the load balancer as a single point of failure (§4.5), and it is the first question a good interviewer will ask.

**Where the analogy breaks down**

| Airport queue | Load balancer |
|---|---|
| The manager sees each passenger once and forgets them | The LB may hold **hundreds of thousands of concurrent TCP connections** simultaneously, each with kernel memory and state |
| Passengers arrive one at a time | Requests arrive in bursts of thousands per second, and the LB's own capacity (connections/sec, bandwidth, TLS handshakes/sec) can be the bottleneck — §9 |
| The manager can see a broken scanner | The LB only knows what a health check tells it. A backend that returns HTTP 200 while its database is down looks perfectly healthy |
| One manager is enough | One LB is a single point of failure; real deployments run **two or more** with VRRP, anycast or a cloud-managed multi-AZ fleet (§4.5) |
| Lanes are visible to passengers | Backends must be **invisible**. The LB rewrites what the backend sees (source IP becomes the LB's), which breaks logging and rate limiting unless you pass the original through — `X-Forwarded-For` / PROXY protocol (§4.7) |
| The queue is the only path | Clients can sometimes bypass you — direct backend IPs, cached DNS, old mobile app versions — so backends still need their own protections |

## 4. Detailed technical explanation

### 4.1 Job 1 — distribute

The LB holds a list of eligible backends and an algorithm for choosing among them. Topic 6 covers the algorithms in depth; the short list, so this lesson is self-contained:

- **Round robin** — each backend in turn. Simple, fair only if requests and backends are uniform.
- **Weighted round robin** — bigger backends get proportionally more. Needed for mixed fleets and for canaries.
- **Least connections** — send to the backend with the fewest in-flight requests. Much better when request durations vary widely, which they always do.
- **Least response time** — least connections, weighted by observed latency.
- **Hash-based (source IP, URL, header)** — deterministic routing, used for cache affinity and sticky sessions.
- **Power of two choices** — pick two backends at random, send to the less loaded of the two. Nearly as good as least-connections, with none of its global-state cost; what most large systems actually use.

The thing to understand now is **why round robin is often wrong**: it assumes every request costs the same. A fleet where 1 % of requests take 2 seconds and 99 % take 20 ms will, under round robin, pile slow requests onto arbitrary backends and produce wildly uneven queueing — which shows up as tail latency (Lesson 16), not as a CPU graph.

### 4.2 Job 2 — health-check

A health check is a periodic probe from the LB to each backend. Four parameters define it, and their product determines how long a dead backend keeps receiving traffic:

```
interval            how often to probe                  e.g. 10 s
timeout             how long to wait for a response     e.g. 3 s
unhealthy_threshold consecutive failures to remove      e.g. 2
healthy_threshold   consecutive successes to restore    e.g. 2

Worst-case detection time = interval × unhealthy_threshold + timeout
                          = 10 × 2 + 3 = 23 seconds
```

During those 23 seconds, that backend receives its normal share of traffic and fails it. At 6,000 RPS across 12 backends, that is `500 × 23 = 11,500` failed requests per undetected failure. Shortening the interval reduces the damage but increases probe load and the risk of **flapping** — a backend oscillating in and out of rotation because of transient slowness, which is worse than leaving it in.

Two kinds of check, and the distinction matters:

- **Passive health checks** — the LB observes real traffic. If a backend returns errors or times out `max_fails` times within `fail_timeout`, it is removed. Free (no extra traffic), reacts instantly, but it *uses real user requests as the probe*, so users experience the failures.
- **Active health checks** — the LB sends synthetic probes to a dedicated endpoint. Costs a little traffic, detects failure without user impact, and can check readiness semantics (Lesson 20 §4.4).

Use both. Active checks catch dead backends before users do; passive checks catch the partial failures that a `/readyz` returning 200 does not reveal.

**What the check should probe** is a design decision with consequences. `TCP connect` only proves something is listening — a hung application still passes. `GET /` runs real application code but may be expensive and may depend on the database. `GET /readyz` (Lesson 20) is the right answer: cheap, deliberate about which dependencies it reflects, and under your control.

### 4.3 Job 3 — terminate TLS

**TLS termination** means the load balancer decrypts HTTPS and forwards plaintext HTTP to backends. **TLS passthrough** means it forwards encrypted bytes untouched. **TLS re-encryption** (or "TLS bridging") means it decrypts, inspects, then re-encrypts to the backend.

Why terminate at the LB:

- **One certificate to manage and rotate**, not n. This alone justifies it for most teams; expired certificates are a top-five cause of outages.
- **Handshakes are expensive** and concentrate well. A single handshake costs an asymmetric-crypto operation; doing them on dedicated edge machines keeps that cost off your application nodes.
- **Layer 7 features require plaintext.** You cannot route by URL path, rewrite a header, or compress a response you cannot read (Lesson 22).
- **Session resumption works better** when handshakes land on a small, coordinated set of terminators that share a session cache or ticket key.

Why *not* terminate (or why re-encrypt):

- The LB-to-backend hop is plaintext. Inside a private VPC this is usually accepted; under PCI-DSS, HIPAA or a zero-trust policy it generally is not, and you re-encrypt.
- The LB now holds your private keys, making it a higher-value target.

**Costs, approximately (2026, per CPU core):** an RSA-2048 handshake requires a private-key operation at roughly **1,500–3,000 per second per core**; ECDSA P-256 manages roughly **10,000–20,000 per second per core**. Resumed handshakes (session tickets or TLS 1.3 PSK) skip the asymmetric operation entirely and cost roughly 1/10th as much. §9 turns this into a capacity plan.

### 4.4 Connection handling: the part that bites people

The LB sits between two sets of connections and they are **not** the same connections.

```
   client ──TCP #1──▶ [ LOAD BALANCER ] ──TCP #2──▶ backend
```

Three consequences:

**Keep-alive to backends is mandatory at scale.** If the LB opens a fresh TCP connection to a backend for every request, each request pays a TCP handshake (one round trip, ~0.5 ms in-region) and the LB churns through **ephemeral ports**. Linux's default ephemeral range is 32768–60999 — **28,232 ports** — and a closed connection sits in `TIME_WAIT` for 60 seconds:

```
Max new connections/sec per (LB source IP → one backend IP:port) pair
  = 28,232 ports / 60 s TIME_WAIT
  ≈ 470 connections/second
```

**470 per second, per backend.** A load balancer pushing 6,000 RPS to 12 backends needs 500 new connections/sec to each if it does not reuse connections — and it will start failing with "cannot assign requested address" right about there. Upstream keep-alive (nginx's `keepalive 32`, HAProxy's default HTTP/1.1 reuse) eliminates the problem by reusing a small pool.

**Connection pooling / multiplexing is the same idea, generalised.** The LB maintains a pool of persistent backend connections and multiplexes many client requests over them. With HTTP/2 to the backend (Lesson 9) a single connection carries many concurrent streams, so a handful of connections per backend suffices. This is also why a load balancer in front of a database (PgBouncer, RDS Proxy) is so effective — it converts thousands of client connections into dozens of server ones (Lesson 19 §4.1).

**Connection count drives LB sizing, not request count.** With HTTP keep-alive, a browser holds its connection open for 60+ seconds of idle time. 50,000 concurrently-browsing users is 50,000+ open connections on the LB even if the request rate is modest — each costing kernel socket memory (typically 4–16 KB) plus LB bookkeeping.

**Imbalance from long-lived connections.** If the LB balances *connections* rather than *requests* (which is what Layer 4 does — Lesson 22), and connections are long-lived, then a backend added after the connections were established receives nothing until clients reconnect. This is the most common cause of "we added capacity and one node is still at 95 %".

### 4.5 The load balancer is a new single point of failure

You added the LB to improve availability, and in doing so you created a component that fails for everybody at once. Three standard remedies:

**(a) Active-passive with a floating VIP (VRRP / keepalived).** Two LB machines. One holds the VIP and serves all traffic; the other watches it over a heartbeat. If the heartbeat stops, the standby claims the VIP by broadcasting a gratuitous ARP, and traffic follows within **1–3 seconds**.

- *Pros:* simple, cheap, no client changes, works on-premises.
- *Cons:* half your hardware is idle; failover is seconds not instant; **split-brain** if the heartbeat link fails but both machines live — both claim the VIP and the network behaves erratically. Mitigate with a second heartbeat path and a quorum/fencing mechanism.

**(b) Active-active with anycast or ECMP.** Multiple LBs announce the *same* IP address. The network layer (BGP anycast across regions, or ECMP hashing within a data centre) spreads flows across them.

- *Pros:* all capacity in use; failure of one LB removes only its share; scales horizontally; anycast also gives you latency-based routing for free (Lesson 4).
- *Cons:* requires control of routing/BGP; a routing change can **rehash flows** and break in-flight connections unless the hardware uses consistent hashing (Topic 21); operationally much more demanding.

**(c) Cloud-managed, multi-AZ.** AWS ALB/NLB, GCP Cloud Load Balancing, Azure Load Balancer. The provider runs a fleet of LB nodes across availability zones behind a DNS name or an anycast IP, and scales them for you.

- *Pros:* no LB machines to operate; multi-AZ redundancy by default; 99.99 % SLAs are typical.
- *Cons:* opaque — you cannot see the LB nodes or their load; **scaling is not instantaneous** (AWS ALB scales over minutes, so a 0-to-50,000-RPS flash sale can be throttled unless you pre-warm or use an NLB); per-request pricing; limited configurability compared with HAProxy.

**The honest framing for an interview:** "The load balancer is a single point of failure, so it must be redundant. In a cloud I'd use a managed multi-AZ LB and treat its 99.99 % SLA as the ceiling for the whole tier. On-premises I'd run active-passive keepalived with VRRP, accept a 1–3 second failover, and make sure there are two heartbeat paths to avoid split-brain."

Note the ceiling point: **your service cannot be more available than your load balancer.** If the LB is 99.99 %, twelve perfect backends do not get you to 99.999 %.

### 4.6 DNS load balancing versus anycast versus a real LB

Three different mechanisms get confused constantly. They operate at different layers and solve different problems — and in practice large systems use all three together.

| Mechanism | How it works | Failover speed | Granularity | Main limitation |
|---|---|---|---|---|
| **DNS round robin** (Lesson 4) | The authoritative server returns multiple A records, rotating order | **Minutes to hours** — TTLs are advisory and widely ignored; some clients cache forever | Per DNS lookup, not per request | Cannot react to failure quickly; no health awareness unless the DNS provider adds it; clients may pin to one record |
| **DNS with health checks** (Route 53, etc.) | Provider probes endpoints and withdraws dead records | 1–5 minutes, best case | Per lookup | Still bounded by caching; good for *region* failover, useless for node failover |
| **Anycast** | The same IP is announced from many locations; BGP routes each client to the topologically nearest | Seconds (BGP reconvergence) | Per flow | Requires BGP and your own address space; route changes can break long-lived TCP flows |
| **Real load balancer (L4/L7)** | A machine terminates connections and forwards them | **Seconds** (health-check interval) | **Per request** (L7) or per connection (L4) | It is itself a component that must be made redundant |

The production pattern that combines them: **DNS (or anycast) chooses a region; a load balancer within that region chooses a backend.** DNS is far too coarse and slow for node-level decisions and exactly right for region-level ones (Topic 22 — multi-region architecture).

### 4.7 X-Forwarded-For and the PROXY protocol

Once a load balancer is in the path, **the backend sees the LB's IP as the client's IP**. Everything that depends on client identity silently breaks: access logs, geo-IP, per-IP rate limiting, abuse blocking, fraud scoring.

**`X-Forwarded-For` (XFF)** is an HTTP header the LB appends the client's IP to. It is a *list*, growing left to right as it passes through proxies:

```
X-Forwarded-For: 203.0.113.5, 198.51.100.7, 10.0.1.4
                 ^ real client  ^ some proxy   ^ your LB
```

Two rules that people get wrong, with security consequences:

1. **Never trust the whole header.** A client can *send* `X-Forwarded-For: 1.2.3.4` and, if your LB appends rather than replaces, your backend may read a forged value. Rate limits and IP allow-lists built on an untrusted XFF are trivially bypassed. The correct approach is to count from the **right**, skipping exactly as many entries as you have trusted proxies (nginx's `real_ip_recursive` with `set_real_ip_from` for each trusted hop).
2. **XFF only exists for HTTP.** A Layer 4 load balancer (Lesson 22) forwards TCP without parsing it, so it cannot add a header. For non-HTTP protocols, or for an L4 LB in front of an L7 one, you use the **PROXY protocol**: a small plaintext (v1) or binary (v2) header prepended to the TCP stream *before* any application bytes, carrying the original source and destination addresses:

```
PROXY TCP4 203.0.113.5 198.51.100.1 56324 443\r\n
```

The critical operational gotcha: **the backend must be configured to expect the PROXY protocol.** If the LB sends it and the backend does not expect it, the backend treats `PROXY TCP4 ...` as the first line of an HTTP request and returns 400 for everything. If the backend expects it and the LB does not send it, the backend hangs or rejects. And a backend that accepts the PROXY protocol from *any* source lets anyone spoof their IP — so it must be restricted to the LB's addresses.

### 4.8 Slow start for newly added backends

A backend that has just booted is **not** equivalent to one that has been serving for an hour:

- Its connection pools are empty (each first query pays a connect).
- Its in-process caches are cold (Lesson 20 §4.3).
- A JIT-compiled runtime (JVM, .NET, V8) is still interpreting rather than running optimised machine code — often **5–20× slower** for the first thousands of requests.
- The OS page cache holds nothing of its working set.

Sending a cold backend its full 1/n share immediately produces a latency spike, and in the worst case the backend saturates, fails its health check, is removed, is restarted, and repeats — a **restart loop caused entirely by the load balancer's enthusiasm.**

**Slow start** ramps a newly healthy backend's weight from near zero to full over a configured window (typically **30–180 seconds**). HAProxy calls it `slowstart`, nginx Plus calls it `slow_start`, AWS target groups call it `slow_start.duration_seconds`, Envoy calls it a slow-start window on the cluster.

It matters most for JIT runtimes and cache-heavy services, and least for a small Go or Rust binary with no local cache. The decision rule: **if your p99 is meaningfully worse in the first minute after a node starts, you need slow start.** Measure it; do not guess.

## 5. Request / data flow

One HTTPS request to BookNook, through a cloud-managed L7 load balancer, start to finish:

1. Browser resolves `booknook.com`. DNS returns the load balancer's address — either an anycast IP or an A record for the managed LB (Lesson 4). **Not** a backend's IP; backends are never in public DNS.
2. Browser opens a TCP connection to the LB on port 443 (Lesson 8). This is **TCP connection #1**.
3. **TLS handshake with the load balancer.** The LB presents the certificate for `booknook.com` and performs the key exchange. On TLS 1.3 this is one round trip; if the browser presents a valid session ticket it is a resumed handshake, skipping the expensive asymmetric operation (§4.3).
4. Browser sends `GET /books/1492052205` over the now-encrypted connection.
5. **LB decrypts** the request. It can now read the method, path, headers and cookies — this is what makes it a Layer 7 load balancer (Lesson 22).
6. LB applies edge policy: rate limiting by client IP, request-size limits, WAF rules, a redirect from `http` to `https`, header normalisation.
7. LB consults its **routing rules**. `/books/*` and `/api/*` go to the `app` target group; `/static/*` goes to object storage or a CDN (Topic 14).
8. LB consults the **health state** of that target group. Of 12 backends, 11 are healthy; node 7 failed two consecutive `/readyz` probes 14 seconds ago and is excluded.
9. LB applies the **balancing algorithm** — say least outstanding requests — and picks node 4.
10. LB checks its **upstream connection pool** for an idle keep-alive connection to node 4. Hit, so no TCP handshake is needed. (On a miss it opens **TCP connection #2**, paying ~0.5 ms.)
11. LB rewrites the request for the backend: appends the client IP to `X-Forwarded-For`, sets `X-Forwarded-Proto: https` so the app can generate correct absolute URLs, adds a request ID for tracing, and strips any client-supplied headers it must not trust.
12. LB forwards the request as **plaintext HTTP** over the private network (or re-encrypts, if policy requires — §4.3).
13. Node 4 processes the request: Redis session lookup, catalogue cache, PostgreSQL read (Lesson 20 §5), and returns a response.
14. LB receives the response. It may **compress** it (gzip/brotli), add security headers (HSTS, `X-Content-Type-Options`), and record metrics: backend latency, status code, bytes.
15. LB **encrypts** the response onto connection #1 and sends it to the browser.
16. LB **keeps both connections open**: #1 for the browser's next request (CSS, images, `/api/reviews`), #2 in the upstream pool for a different client's next request to node 4. The two are now completely decoupled.
17. The LB emits an access log line containing the chosen backend, backend latency, total latency and status code. **This log is how you detect an imbalanced or slow backend**, and it exists only because the LB sees everything.

Meanwhile, continuously and independently of any request:

18. Every 10 seconds the LB probes `/readyz` on all 12 backends. Node 7 responds 200 twice in a row at 14:03:10 and 14:03:20, so at 14:03:20 it is returned to rotation — with **slow start**, receiving 10 % of normal weight initially and ramping to 100 % over 60 seconds (§4.8).

## 6. ASCII diagram

```
THE THREE JOBS, AND THE TWO SETS OF CONNECTIONS

  clients (50,000 concurrent keep-alive connections)
     │  │  │  │  │          TCP #1 set: many, long-lived, TLS
     ▼  ▼  ▼  ▼  ▼
 ╔═══════════════════════════════════════════════════════╗
 ║            LOAD BALANCER   (VIP 203.0.113.10)         ║
 ║                                                        ║
 ║  [3] TERMINATE TLS   ── one cert, not 12              ║
 ║        │               ECDSA ~12k handshakes/s/core    ║
 ║        ▼               resumption ≈ 10x cheaper        ║
 ║  edge policy: rate limit · WAF · size caps · XFF       ║
 ║        │                                               ║
 ║  [1] DISTRIBUTE      ── least-outstanding-requests     ║
 ║        │                                               ║
 ║  [2] HEALTH STATE    ── node 7 excluded 14s ago        ║
 ║        │               detect = interval×threshold     ║
 ║        │                        +timeout = 23 s        ║
 ║        ▼                                               ║
 ║  upstream keep-alive pool  (32 idle conns per backend) ║
 ╚══╤═══════╤═══════╤═══════╤═══════╤═══════╤════════════╝
    │       │       │       │       │       │  TCP #2 set: FEW, reused.
    ▼       ▼       ▼       ▼       ▼       ▼  Without reuse you hit
 ┌─────┐ ┌─────┐ ┌─────┐ ┌──×──┐ ┌─────┐ ┌~~~~~┐  28,232 ports / 60s
 │ n1  │ │ n2  │ │ n3  │ │ n7  │ │ n9  │ │ n12 │  TIME_WAIT ≈ 470 conn/s
 └─────┘ └─────┘ └─────┘ └─────┘ └─────┘ └─────┘  PER BACKEND. Then:
                            ▲              ▲      "cannot assign requested
                      UNHEALTHY       SLOW START   address" under load.
                      (excluded)      (10% → 100%
                                       over 60 s)

 backends see src-IP = LB, so the real client arrives via
   X-Forwarded-For: 203.0.113.5, 198.51.100.7, 10.0.1.4
                    ^client        ^proxy        ^your LB
   → count from the RIGHT past trusted hops. Trusting the leftmost
     value lets any client forge its IP and bypass rate limits.
   → L4 LBs can't add headers at all → use the PROXY protocol header
     prepended to the TCP stream, and allow it ONLY from the LB's IPs.


MAKING THE LOAD BALANCER NOT BE A SINGLE POINT OF FAILURE

 (a) ACTIVE-PASSIVE / VRRP        (b) ACTIVE-ACTIVE / anycast+ECMP
     ┌────────┐  heartbeat            BGP announces 203.0.113.10
     │ LB-A   │◀────────┐             from N locations at once
     │ holds  │         │              ┌────┐ ┌────┐ ┌────┐
     │ VIP    │    ┌────▼───┐          │LB-1│ │LB-2│ │LB-3│
     └───┬────┘    │ LB-B   │          └─┬──┘ └─┬──┘ └─┬──┘
         │         │ idle   │            └──────┼──────┘
      all traffic  └────────┘                   ▼
                                           all active
   failover 1–3 s (gratuitous ARP)   failure = lose 1/N capacity
   50% hardware idle                 needs BGP; route change can
   RISK: split brain if the          rehash and break live flows
   heartbeat link alone fails        (use consistent hashing)

 (c) CLOUD-MANAGED MULTI-AZ: provider runs the fleet across AZs.
     99.99% SLA typical. BUT: scaling takes MINUTES — a 0→50k RPS
     flash sale needs pre-warming or an NLB.

 ⚠ YOUR SERVICE CANNOT BE MORE AVAILABLE THAN YOUR LOAD BALANCER.
   LB at 99.99% + 12 perfect backends = 99.99%, not five nines.


CHOOSING A REGION vs CHOOSING A BACKEND — different tools

  DNS / anycast  ──▶ picks a REGION   (minutes / seconds, coarse)
  load balancer  ──▶ picks a BACKEND  (seconds, per-request)

  DNS is too slow and too coarse for node failover: TTLs are
  advisory and some clients cache forever.
```

## 7. Detailed example

### BookNook: introducing the load balancer, and the four things that broke

Continuing from Lesson 20: BookNook has 12 stateless nodes and needs something in front of them. The team starts with a cloud-managed Application Load Balancer and the configuration in §8.

**Day 1 — it works, and one node is at 95 % CPU while the others sit at 40 %.**

The cause is §4.4's connection-imbalance problem, in a specific form. BookNook's mobile app uses HTTP/2 and holds a single long-lived connection for the whole session. The LB was configured with round-robin at the *connection* level for one listener, so mobile clients that connected during the previous scale-in were concentrated on four nodes. The fix: balance at the **request** level (Layer 7 — Lesson 22) and switch the algorithm to **least outstanding requests**. CPU across the fleet converges to within 6 % of the mean within a minute.

**Day 3 — the rate limiter blocks the entire internet for 40 seconds.**

BookNook's per-IP limit of 100 requests/minute was implemented in the app using the **leftmost** value of `X-Forwarded-For`. That value is attacker-controlled. Worse, when a crawler sent `X-Forwarded-For: 10.0.1.4` — the LB's own private address — the app counted *all* LB traffic under that key and rate-limited everyone. The fix is §4.7 rule 1: configure `set_real_ip_from` for exactly the LB's subnet, use `real_ip_recursive on`, and take the rightmost untrusted entry.

**Day 9 — a deploy causes 1,400 errors despite Lesson 20's graceful shutdown.**

The application drained correctly. The LB's `deregistration_delay` was 5 seconds, but the app's drain sleep was 10 seconds — so the LB stopped tracking the target and terminated its connections while the app was still finishing requests. The three timings must be ordered (Lesson 20 §4.5), and the LB's deregistration delay is the *outermost* of them:

```
deregistration_delay (30 s) ≥ drain_sleep (10 s) + drain_timeout (25 s)?  NO — 35 > 30
→ raise deregistration_delay to 45 s.
```

They also discovered the LB counts deregistration delay from the moment of the API call, not from when the app notices — so the LB value must bound the application's entire drain, not just part of it.

**Day 20 — autoscaling adds four nodes during the evening peak, and p99 doubles for 90 seconds.**

The new nodes received full traffic the instant they passed health checks, with empty connection pools and cold caches. Two of the four saturated, failed their next health check, were removed, and restarted — the restart loop from §4.8. The fix: `slow_start.duration_seconds = 60` on the target group, plus raising the health-check grace period so a warming node is not judged too early. After the change, adding nodes is invisible on the p99 graph.

**Day 34 — the LB itself is the bottleneck during a flash sale.**

A 4-hour sale takes traffic from 6,000 to 48,000 RPS in under two minutes. The managed ALB scales, but **over minutes, not seconds** (§4.5c), and for the first ~3 minutes it returns 503s. The team's two fixes: request pre-warming from the provider ahead of announced events, and moving the highest-volume, simplest endpoints behind a **Network Load Balancer** (Layer 4), which scales instantly because it does far less work per connection (Lesson 22).

**Final configuration:** cloud-managed ALB, multi-AZ, least-outstanding-requests, `/readyz` health checks at a 10 s interval, TLS 1.3 termination with ECDSA certificates, 45 s deregistration delay, 60 s slow start, strict `X-Forwarded-For` handling, HTTP/2 to clients and keep-alive to backends. Measured LB-attributable availability over the following year: **99.99 %** — which, per §4.5, is now the ceiling for the entire service.

### Contrasting example: a self-managed HAProxy pair on-premises

A payment processor cannot use a cloud-managed LB: it is on-premises for regulatory reasons, and it must do **TLS passthrough** for one service (client-certificate mutual TLS, where terminating at the LB would break client authentication) and **re-encryption** for another (PCI-DSS requires encryption in transit all the way to the application).

Their design:

- **Two HAProxy machines** in active-passive with **keepalived/VRRP** holding a floating VIP. Failover measured at **1.4 seconds** in quarterly drills.
- **Two independent heartbeat paths** — a dedicated crossover cable and the production network — because a single heartbeat path makes split-brain a question of when, not if.
- A **fencing mechanism**: the standby verifies it can reach an external arbiter before claiming the VIP, so a node isolated from the network does not promote itself.
- **PROXY protocol v2** to backends, because the passthrough service is L4 and cannot use `X-Forwarded-For` (§4.7). Backends accept the PROXY header **only** from the two LB addresses.
- Capacity planned explicitly: each HAProxy machine sized for the full peak, because in active-passive one machine carries 100 % of traffic. (A common and expensive mistake is sizing each for 50 %.)

The contrast with BookNook is the point: **managed load balancers remove enormous operational burden and take away control.** When you need passthrough, mutual TLS, exact failover semantics, or on-premises operation, you run your own — and you inherit split-brain, capacity planning and certificate rotation as your problems.

## 8. Code or configuration example

An HAProxy configuration showing all three jobs plus health checks, slow start, XFF and the PROXY protocol, and a keepalived snippet for VIP failover. **Illustrative only, not production-ready:** no TLS cipher hardening, no rate-limit tuning, no logging pipeline, no certificate automation, simplified ACLs, and the keepalived configuration omits fencing and a second heartbeat path.

```haproxy
# haproxy.cfg -- ILLUSTRATIVE TEACHING CONFIGURATION, not production-ready.

global
    # One process, N worker threads. Each thread handles many thousands of
    # concurrent connections via epoll -- the event-loop model from Lesson 1.
    nbthread 8

    # HARD CAP on total concurrent connections. Exceeding the process's file
    # descriptor limit is a hard failure; this makes the LB shed load
    # predictably instead of dying. Size it with SS9.
    maxconn 200000

    # TLS: prefer ECDSA certs. An ECDSA P-256 handshake is ~5-8x cheaper in
    # CPU than RSA-2048 (SS4.3), which directly sets your handshakes/sec.
    ssl-default-bind-ciphersuites TLS_AES_128_GCM_SHA256:TLS_AES_256_GCM_SHA384
    ssl-default-bind-options ssl-min-ver TLSv1.2

    # Shared TLS session cache: resumed handshakes skip the expensive
    # asymmetric operation entirely, cutting CPU per handshake ~10x.
    tune.ssl.cachesize 200000
    tune.ssl.lifetime 3600

defaults
    mode http
    timeout client   60s    # idle client keep-alive before we close
    timeout server   30s    # max wait for a backend response
    timeout connect   2s    # max wait to ESTABLISH a backend connection
    timeout http-request 10s # guards against Slowloris: a client that sends
                             # headers one byte at a time to occupy a worker
    option httplog

# ------------------------------------------------------- JOB 3: TERMINATE TLS
frontend https_in
    # The VIP. In active-passive this address floats between LB machines.
    bind 203.0.113.10:443 ssl crt /etc/haproxy/certs/booknook.pem alpn h2,http/1.1
    bind 203.0.113.10:80
    http-request redirect scheme https unless { ssl_fc }

    # --- JOB 3b: X-FORWARDED-FOR, done safely (SS4.7).
    # DELETE any client-supplied XFF first, THEN set it ourselves. If we merely
    # appended, a client could inject a forged leftmost entry and bypass every
    # per-IP rate limit and allow-list in the system.
    http-request del-header X-Forwarded-For
    http-request set-header X-Forwarded-For %[src]
    # Tells the app the ORIGINAL scheme, so it generates https:// absolute URLs
    # instead of http:// ones behind a terminating proxy.
    http-request set-header X-Forwarded-Proto https if { ssl_fc }
    # A request ID for distributed tracing across the fleet (Phase 4, Topic 27).
    http-request set-header X-Request-Id %[uuid()] unless { req.hdr(X-Request-Id) -m found }

    # --- Edge rate limiting: done ONCE here rather than 12 times in the app.
    stick-table type ip size 1m expire 60s store http_req_rate(60s)
    http-request track-sc0 src
    http-request deny deny_status 429 if { sc_http_req_rate(0) gt 600 }

    # --- Layer 7 routing (Lesson 22): possible only because we decrypted.
    acl is_api path_beg /api/
    use_backend booknook_api if is_api
    default_backend booknook_app

# ------------------------------------------- JOBS 1 & 2: DISTRIBUTE + HEALTH
backend booknook_app
    # Round robin assumes all requests cost the same. They do not: BookNook has
    # 20ms catalogue reads and 2s search queries. leastconn sends work to the
    # node with fewest in-flight requests and is strictly better here (SS4.1).
    balance leastconn

    # --- ACTIVE health check against the READINESS endpoint (Lesson 20 SS4.4).
    # NOT "/" (expensive, may touch the DB) and NOT a bare TCP connect
    # (a hung process still accepts connections and would pass).
    option httpchk GET /readyz
    http-check expect status 200

    # --- UPSTREAM KEEP-ALIVE. Without connection reuse, each backend is
    # limited to ~470 new connections/sec by ephemeral port exhaustion
    # (28,232 ports / 60s TIME_WAIT) -- see SS4.4 and SS9.
    http-reuse safe

    # check      = probe this server actively
    # inter 10s  = probe every 10 seconds
    # fall 2     = 2 consecutive failures → remove (detection ≈ 10×2+timeout)
    # rise 2     = 2 consecutive successes → restore
    # slowstart  = ramp weight 0→100% over 60s after becoming healthy, so a
    #              cold JIT/cache node is not flooded and pushed into a
    #              restart loop (SS4.8)
    # maxconn    = per-backend concurrency cap; excess QUEUES at the LB rather
    #              than overwhelming the node (a crude but effective bulkhead)
    server n1 10.0.1.11:8000 check inter 10s fall 2 rise 2 slowstart 60s maxconn 400
    server n2 10.0.1.12:8000 check inter 10s fall 2 rise 2 slowstart 60s maxconn 400
    server n3 10.0.2.11:8000 check inter 10s fall 2 rise 2 slowstart 60s maxconn 400
    server n4 10.0.2.12:8000 check inter 10s fall 2 rise 2 slowstart 60s maxconn 400

    # Retry ONLY on connection-level failures, and only for idempotent methods.
    # Retrying a POST that may already have been processed creates duplicate
    # orders (Lesson 17). This is a correctness setting, not a tuning knob.
    retry-on conn-failure empty-response
    retries 2
    option redispatch     # on retry, pick a DIFFERENT backend

# -------------------- A TCP (Layer 4) backend that needs the PROXY protocol
backend booknook_grpc
    mode tcp
    balance leastconn
    # send-proxy-v2 prepends the original source/destination addresses to the
    # TCP stream BEFORE any application bytes. Required because in TCP mode we
    # never parse HTTP and therefore cannot add X-Forwarded-For (SS4.7).
    # WARNING: the backend MUST be configured to expect this. If it is not, it
    # reads "PROXY TCP4 ..." as the first application bytes and fails every
    # request. And it must accept PROXY headers ONLY from the LB's IPs, or
    # anyone can spoof a source address.
    server g1 10.0.1.21:50051 check send-proxy-v2
    server g2 10.0.2.21:50051 check send-proxy-v2
```

```conf
# keepalived.conf -- ACTIVE-PASSIVE VIP failover (SS4.5a). ILLUSTRATIVE.
# Omitted for brevity and present in any real deployment: a SECOND heartbeat
# path, and a fencing/arbiter check before promotion. Without those, a failure
# of the single heartbeat link causes SPLIT BRAIN: both machines claim the VIP.

vrrp_script check_haproxy {
    # Track the SERVICE, not just the machine. A box that is up with HAProxy
    # dead must hand over the VIP, otherwise it is a black hole.
    script "/usr/bin/killall -0 haproxy"
    interval 2
    weight -40        # drop priority by 40 on failure, below the peer's 100
    fall 2
    rise 2
}

vrrp_instance VI_BOOKNOOK {
    state MASTER
    interface eth0
    virtual_router_id 51
    priority 110              # MASTER 110 vs BACKUP 100; -40 forces failover
    advert_int 1              # heartbeat every 1s → failover in ~1-3s

    authentication { auth_type PASS; auth_pass <from-secret-manager> }

    virtual_ipaddress {
        203.0.113.10/24 dev eth0   # the floating VIP from haproxy.cfg
    }
    track_script { check_haproxy }

    # On promotion keepalived sends a gratuitous ARP so switches relearn which
    # MAC owns the VIP. Without it, traffic keeps going to the dead machine
    # until ARP caches expire -- minutes, not seconds.
}
```

## 9. Numerical example

**How big must BookNook's load balancer be?** Capacity planning for an LB is three independent numbers — **concurrent connections, throughput, and TLS handshakes per second** — and they are each sized by a different driver.

### Assumptions (all stated)

1. Peak 6,000 RPS, 12 backends (Lesson 19).
2. 50,000 concurrently-browsing users during peak.
3. Browsers hold HTTP keep-alive connections with a 60-second idle timeout.
4. Each user makes a burst of ~8 requests per page view, then idles.
5. Average response 40 KB of JSON; pages also pull 8 images × 200 KB, but **images are served by the CDN** (Topic 14) and never reach the LB.
6. TLS 1.3 with ECDSA P-256 certificates. Session resumption succeeds for 80 % of new connections.
7. ECDSA P-256 full handshake: **12,000/sec/core**; resumed handshake: **~120,000/sec/core** (roughly 10× cheaper).
8. Each open TCP connection costs ~10 KB of kernel + LB memory.
9. Linux ephemeral port range 32768–60999 = **28,232 ports**; `TIME_WAIT` = 60 s.

### Part A — concurrent connections (sizes memory and file descriptors)

```
Client-side connections ≈ concurrent users (keep-alive holds one each)
                        = 50,000

Plus mobile app HTTP/2 connections, partner integrations, health
checkers, monitoring:  assume +20%
                        = 60,000 concurrent client connections

Memory = 60,000 × 10 KB = 600 MB   ← comfortable on any modern LB

File descriptors: 60,000 client + upstream pool (below).
Default ulimit of 1,024 is catastrophically low — must be raised
to ~200,000 (matching HAProxy's `maxconn 200000`).
```

**Note the key insight: connection count is driven by concurrent *users*, not by RPS.** 50,000 idle-ish users at 6,000 RPS need the same 50,000 connections as 50,000 busy users at 60,000 RPS.

### Part B — upstream connections, and why keep-alive is mandatory

```
WITHOUT upstream keep-alive:
  New backend connections/sec = 6,000 RPS (one per request)
  Spread over 12 backends     = 500 conn/s per backend

  Ephemeral port limit per (LB IP → backend IP:port) pair:
      28,232 ports / 60 s TIME_WAIT ≈ 470 connections/second

  500 needed > 470 available  → PORT EXHAUSTION.
  Symptom: intermittent "cannot assign requested address",
  worsening exactly as traffic grows. 
```

So without connection reuse, BookNook's LB fails at approximately its current peak — not at 10×, now.

```
WITH upstream keep-alive (HAProxy `http-reuse safe`, nginx `keepalive 32`):
  Concurrent in-flight requests (Little's Law, Lesson 14):
      L = λ × W = 6,000 req/s × 0.020 s = 120 concurrent requests
  Spread over 12 backends = 10 concurrent per backend.

  A pool of 32 idle connections per backend is ample:
      12 backends × 32 = 384 upstream connections TOTAL
  …versus 6,000 new connections per second without it.
```

**384 persistent connections replace 6,000 connections/second.** This single configuration line is the difference between working and not working.

### Part C — TLS handshakes per second (sizes CPU)

```
New client connections/sec:
  Each user makes a page view every ~25 s; keep-alive (60 s idle timeout)
  covers the gap, so a connection survives several page views.
  Assume each connection lasts ~4 page views ≈ 100 s.

  New connections/sec = 50,000 users / 100 s = 500 new connections/sec

Split by resumption (assumption 6):
  Full handshakes    = 500 × 0.20 = 100 /sec
  Resumed handshakes = 500 × 0.80 = 400 /sec

CPU cost (assumption 7):
  Full:    100 / 12,000  = 0.0083 cores
  Resumed: 400 / 120,000 = 0.0033 cores
  TOTAL                  ≈ 0.012 cores   ← negligible
```

**Now the scenario that actually breaks load balancers — a cold stampede.** A flash sale is announced and 50,000 users arrive within 30 seconds, none with a session ticket:

```
New connections/sec = 50,000 / 30 = 1,667 /sec
ALL are full handshakes (no tickets yet):
  1,667 / 12,000 = 0.139 cores with ECDSA

Same scenario with RSA-2048 certificates (2,000 handshakes/sec/core):
  1,667 / 2,000 = 0.83 cores — 6x more expensive

Same scenario, 10x bigger event (500,000 users in 30 s):
  16,667 full handshakes/sec
    ECDSA: 16,667 / 12,000 = 1.39 cores   fine
    RSA:   16,667 /  2,000 = 8.3 cores    now a real capacity decision
```

**Conclusion:** certificate algorithm choice is a capacity decision, not a security formality. ECDSA over RSA is worth roughly **6× in handshake capacity**, and TLS session resumption is worth another **10×** on the connections that qualify.

### Part D — throughput (sizes network)

```
Application responses only (images on the CDN):
  6,000 RPS × 40 KB = 240,000 KB/s = 240 MB/s ≈ 1.92 Gbps

If images were NOT on a CDN (Lesson 1 SS9 showed the ratio):
  6,000 RPS × 8 images × 200 KB = 9,600 MB/s ≈ 76.8 Gbps
  → would require the largest instance class purely for the LB
```

**The CDN decision changes the LB's required network tier by 40×.** That is a cheaper architectural choice than any hardware purchase.

### Part E — the failure-detection cost

```
Health check: interval 10 s, unhealthy_threshold 2, timeout 3 s
Worst-case detection = 10 × 2 + 3 = 23 seconds

During detection, the dead backend still receives its share:
  6,000 RPS / 12 backends = 500 RPS
  500 × 23 s = 11,500 failed requests per undetected failure

Tightening to interval 5 s, threshold 2, timeout 2 s:
  detection = 5 × 2 + 2 = 12 s  →  500 × 12 = 6,000 failed requests
  (a 48% reduction, at the cost of 2x the probe traffic and a higher
   risk of flapping on transient slowness)
```

Combined with client retries on idempotent requests (Lesson 17), most of those 6,000–11,500 failures become a retry that succeeds on another backend — which is precisely why `option redispatch` and client-side retries exist.

### Part F — availability ceiling

```
Cloud-managed multi-AZ LB SLA:   99.99%   (52.6 min/year)
12-backend tier (Lesson 19 SS9): 99.99%   (52.6 min/year)

Serial dependency — BOTH must work:
  A_total = 0.9999 × 0.9999 = 0.99980   →  105 min/year

Self-managed active-passive HAProxy with 1.4 s failover,
assume 4 failovers/year plus 1 split-brain incident of 6 min/year:
  downtime = 4 × 1.4 s + 360 s ≈ 366 s = 6.1 min/year
  A_LB = 1 − 366/31,536,000 = 99.9988%
```

**Interesting and counter-intuitive result:** a well-run self-managed active-passive pair can beat a cloud-managed LB's *SLA* — but the SLA is a contractual floor, not measured performance, and the self-managed figure assumes competent operations, tested failover drills and no split-brain. Quote the managed figure in an interview unless you can defend the operational maturity behind the other.

## 10. Trade-offs

**Advantages**

- **Failure handling in seconds** rather than the minutes-to-hours DNS gives you. This is the availability mechanism that makes Lesson 19's redundancy real.
- **The backend set becomes a private implementation detail** — add, remove, drain and deploy without anyone knowing.
- **Cross-cutting concerns in one place:** TLS, rate limiting, WAF, compression, logging, request IDs. One certificate instead of twelve.
- **A global view of traffic.** The LB's access log is the only place that sees every request and every backend's latency — it is your primary debugging surface for imbalance.
- **Protects backends.** `maxconn` per backend, request-size limits and slow-request timeouts turn overload into predictable queueing rather than a cascade.
- **Enables progressive delivery:** canaries, blue-green and weighted traffic shifts are LB features.

**Disadvantages**

- **A new single point of failure** that must be made redundant, and that **caps your availability** (§9 Part F).
- **Added latency on every request:** typically 0.1–1 ms for L4 and 1–5 ms for L7 (Lesson 22), plus an extra network hop.
- **A new capacity dimension to plan and monitor** — connections, handshakes/sec, throughput — each with its own ceiling and its own failure symptom.
- **Obscures the client.** Everything IP-based breaks until XFF or PROXY protocol is configured *correctly*, and configuring it incorrectly is a security vulnerability, not just a bug.
- **Managed LBs scale slowly.** Minutes, which is a genuine problem for announced events.
- **Configuration complexity.** Timeouts, retries, health-check parameters and drain delays must all be consistent with the application's behaviour (§7, day 9). Inconsistency produces errors that look random.
- **Cost.** A managed ALB is roughly $25/month plus usage; a self-managed HA pair is two machines, one of them idle.

**Operational costs**

Certificate lifecycle (automated renewal, and alerting on expiry — the classic outage), health-check tuning, capacity monitoring on three dimensions, failover drills if self-managed, and keeping the LB's timeouts in sync with the application's drain behaviour. For a self-managed pair, add split-brain prevention and the discipline to size *each* machine for 100 % of peak.

**When it becomes unnecessary complexity**

With a single backend and no availability requirement, an LB adds a hop, a cost and a failure mode for nothing. The moment you have two backends, or want zero-downtime deploys, it becomes mandatory. There is essentially no middle ground.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| **Load balancer process/machine dies** (single LB) | Total outage — connection refused or timeout for everyone, despite all backends being healthy | Synthetic external monitoring; the LB's own metrics stop arriving | Active-passive VRRP failover in 1–3 s; anycast withdrawal in seconds; managed LBs fail over internally. **With one LB there is no recovery** — this is why §4.5 exists |
| **Split brain** — both LBs claim the VIP | Intermittent, bizarre behaviour: some connections work, some reset; appears network-related | Both machines log "entering MASTER state"; duplicate-IP ARP warnings on switches | Restore the heartbeat path; fencing/arbiter prevents it. Mitigate with two independent heartbeat paths |
| **Health check too lenient** (interval 30 s, threshold 3) | 1/n of requests fail for up to 93 s after a backend dies | Error rate rises and then self-heals without intervention; LB logs show the target removed long after | Tighten interval and threshold; balance against flapping risk |
| **Health check too aggressive** | Backends flap in and out; capacity oscillates; p99 is erratic | Target health-state transitions many times per hour | Raise `unhealthy_threshold`, raise timeout, use a cheaper check endpoint |
| **Health check hits an endpoint that lies** (`/` returns 200 while the DB is down) | Every request to that backend 500s; the LB keeps sending traffic because the probe passes | Backend error rate is high while health status is "healthy" — the two disagree | Point the check at `/readyz` with deliberate dependency semantics (Lesson 20 §4.4); add passive checks on real traffic |
| **Ephemeral port exhaustion** (no upstream keep-alive) | Intermittent 502s that worsen with traffic and improve when traffic drops | `cannot assign requested address` in LB logs; huge `TIME_WAIT` count in `ss -s` | Enable upstream keep-alive/connection reuse; widen the port range; add backend IPs. See §9 Part B |
| **`maxconn` / file-descriptor limit reached** | New connections hang or are refused while existing ones work fine | `too many open files`; accept queue overflow counters rising | Raise `ulimit -n` and `maxconn`; scale the LB. This fails *silently* until it doesn't |
| **TLS certificate expires** | Browser security warning on every request; total loss of trust and traffic | External certificate-expiry monitoring — **not** from the LB itself | Deploy a renewed certificate. Prevented by automated renewal plus alerting at 30/14/7 days |
| **XFF trusted blindly** | Rate limits and IP blocks bypassed by anyone who sets a header; possibly all users rate-limited under one forged key (§7 day 3) | Usually found by an attacker or an audit, not by monitoring | Delete-then-set XFF at the edge; configure trusted proxy hops explicitly |
| **PROXY protocol mismatch** | 100 % failure on that listener: every request returns 400, or hangs | Backend logs show a malformed request beginning `PROXY TCP4` | Make the LB's `send-proxy` setting and the backend's `accept-proxy` setting agree; restrict acceptance to LB source IPs |
| **No slow start on a JIT/cache-heavy backend** | p99 doubles for 60–90 s whenever a node is added; sometimes a restart loop | New-target latency far above fleet median for the first minute | Enable slow start (30–180 s) and a health-check grace period |
| **Managed LB cannot scale fast enough** | 503s at the very start of an announced event, then recovery after a few minutes | LB-generated 5xx with zero backend errors — the signature of LB-side rejection | Pre-warm with the provider; use an L4 NLB for the hot path; keep a static overflow page |
| **Deregistration delay shorter than the app's drain** | Errors on every deploy despite correct application drain logic | 5xx correlate exactly with deregistration events (§7 day 9) | `deregistration_delay ≥ drain_sleep + drain_timeout` |

## 12. Alternatives

| Approach | How it differs | Good at | Bad at | When you'd pick it |
|---|---|---|---|---|
| **Dedicated load balancer (L4/L7)** | A component in the path terminating connections | Per-request decisions, fast failure handling, TLS termination, edge policy | A SPOF to make redundant; adds a hop and latency | BookNook — the default |
| **DNS round robin** (Lesson 4) | Multiple A records; the client picks | Zero infrastructure, zero cost, works globally | Failover in minutes-to-hours; no health awareness; no per-request control | A crude free tier, or as *one layer* of a multi-region design |
| **DNS with health checks** | Provider probes and withdraws records | Region-level failover | Still TTL-bound; too coarse for node failure | Multi-region failover (Topic 22) |
| **Anycast** | One IP announced from many places; BGP decides | Latency routing, DDoS absorption, no DNS dependency | Needs BGP and your own IP space; route flaps break TCP flows | CDNs, DNS providers, large-scale edge |
| **Client-side load balancing** | The client holds the backend list and chooses | No extra hop; lowest latency; no LB to operate | Every client must implement it; service discovery required; hard to change policy | gRPC service-to-service, Finagle/Ribbon-style internal RPC |
| **Service mesh sidecar** (Envoy per pod) | A local proxy does the balancing for each instance | Per-service policy, mTLS, retries, observability, no central SPOF | Significant complexity and resource overhead; another control plane | Large microservice estates (Phase 4, Topic 23) |
| **Hardware load balancer** (F5, Citrix) | Purpose-built appliance | Very high throughput, SSL offload ASICs, mature features | Capital cost, lead time, vendor lock-in, slow to change | Regulated on-premises environments |
| **Reverse proxy as LB** (nginx, Caddy, Traefik) | A general-purpose proxy configured to balance | Cheap, flexible, familiar, one process does many jobs | You operate it, including HA (Topic 8 explores the overlap) | Small-to-mid deployments; on-premises |
| **No load balancer — one big server** (Lesson 18) | No distribution at all | Simplest possible thing | No redundancy, no zero-downtime deploys | Below two backends |

Note that the first row and the last two rows are not rivals in practice: nginx and HAProxy *are* load balancers, and the distinction between "load balancer" and "reverse proxy" is about emphasis rather than mechanism (Topic 8 draws the line precisely).

## 13. When to use it

1. **Whenever you have two or more interchangeable backends.** This is not a judgement call — the moment there are two, something must choose, and the LB is the only mechanism that chooses correctly and reacts to failure in seconds.
2. **Whenever you want zero-downtime deploys.** Rolling deploys require a component that can take a backend out of rotation and drain it (Lesson 20). Without an LB, "deploy" means "restart and hope".
3. **Whenever you need TLS termination in one place.** Managing one certificate instead of n is reason enough; the CPU and session-resumption benefits are a bonus.
4. **Whenever you need edge policy** — rate limiting, WAF, request-size caps, IP allow-lists, header normalisation. Doing these once at the edge is cheaper and more consistent than n times in the application.
5. **Whenever you need progressive delivery.** Canary releases, blue-green deployments and weighted traffic shifts are implemented by moving weights in a load balancer.
6. **Whenever you need protection from overload.** Per-backend connection caps, queueing and load shedding at the edge convert a cascading failure into controlled degradation.
7. **In front of stateful tiers too, in the right form.** PgBouncer in front of PostgreSQL and RDS Proxy are load balancers specialised for database connections — the same idea solving Lesson 19's connection-count problem.

## 14. When *not* to use it

1. **With a single backend and no availability requirement.** You are adding a hop, a cost and a failure mode in exchange for nothing. Add it when you add the second backend.
2. **For internal service-to-service traffic at very high RPS where an extra hop is unaffordable.** Client-side load balancing (gRPC) or a sidecar mesh removes the central hop and its latency, at the cost of distributing the policy.
3. **When a single centralised LB would become the bottleneck.** At millions of connections per second, a central L7 LB is itself the scaling problem; the answer is anycast/ECMP across many LBs (§4.5b) or client-side balancing, not a bigger box.
4. **For region selection.** An LB is the wrong tool for choosing *which region* a user reaches; that is DNS or anycast (§4.6, Topic 22). Using a single global LB makes one region's failure everyone's problem.
5. **When the protocol genuinely cannot be proxied.** Some peer-to-peer, bespoke UDP or latency-critical protocols need direct connections or direct server return (Lesson 22).
6. **Before the application is stateless.** An LB in front of a stateful fleet forces sticky sessions, which re-creates the original problem on every node failure (Lesson 20). Do the statelessness work first.

## 15. Common mistakes

**Beginner mistakes**

- **Treating the LB as automatically redundant.** A single load balancer in front of twelve backends is a single point of failure in front of a highly available tier. Ask "what happens when the LB dies?" on day one.
- **Health-checking `/` or a bare TCP port.** A hung process accepts TCP connections; an app whose database is down may still render a 200 homepage. Check `/readyz`.
- **Leaving default health-check timings.** 30-second intervals with a threshold of 3 means 93 seconds of sending traffic into a hole.
- **Not configuring upstream keep-alive**, then hitting ephemeral port exhaustion at a traffic level you can calculate in advance (§9 Part B).
- **Trusting the leftmost `X-Forwarded-For` value** — a direct bypass of every per-IP control you have.
- **Forgetting that backends still need their own protections.** Direct backend IPs, old DNS entries and internal traffic can bypass the LB entirely.
- **Not raising `ulimit -n`.** The default of 1,024 file descriptors fails long before any interesting load.
- **Using round robin with highly variable request costs** and then wondering why one backend is hot.
- **No certificate-expiry alerting.** The most preventable total outage in the industry.

**Experienced-engineer mistakes**

- **Deregistration delay inconsistent with the application's drain timings**, producing a permanent background error rate on every deploy (§7 day 9).
- **Sizing each machine in an active-passive pair for 50 % of peak.** In failover, one machine carries 100 %. Both must be sized for all of it.
- **Running a single heartbeat path in a VRRP pair**, making split-brain inevitable rather than unlikely.
- **Terminating TLS at the LB without checking the compliance requirement**, then discovering an audit requires encryption in transit all the way to the application.
- **Not planning for managed-LB scaling latency** before an announced event. The 503s at minute zero of a flash sale are an LB capacity problem, not a backend one, and the metrics look confusingly clean on the backend side.
- **Enabling retries at the LB without restricting them to idempotent requests**, converting a transient failure into duplicate orders (Lesson 17).
- **Omitting slow start for JIT or cache-heavy backends**, then attributing the resulting p99 spike to autoscaling itself.
- **Forgetting the LB caps total availability.** Designing a "five nines" backend tier behind a 99.99 % load balancer is arithmetic that does not work (§9 Part F).
- **Letting the LB's own metrics go unmonitored** — connection count, handshakes/sec, surge queue, rejected connections. Each has a ceiling, and each fails with a different symptom.

## 16. Interview perspective

Load balancers appear in essentially every system-design interview, usually as a box someone draws without comment. Saying anything specific about it is immediately differentiating.

**How to use it well:**

- **State the three jobs explicitly.** "The LB distributes, health-checks, and terminates TLS." Then build on each.
- **Immediately raise it as a single point of failure, and resolve it.** "The LB is now a SPOF, so it has to be redundant — a managed multi-AZ LB in the cloud, or active-passive keepalived with VRRP on-prem, accepting 1–3 seconds of failover and making sure there are two heartbeat paths."
- **Say the availability ceiling out loud.** "My service can't be more available than the LB, so at 99.99 % that's my ceiling regardless of how many backends I run."
- **Quantify failure detection.** "With a 10-second interval and a threshold of 2, detection takes about 23 seconds, during which that backend fails 500 requests per second — so I'd pair it with client-side retries on idempotent requests."
- **Mention connection handling.** "I'd enable upstream keep-alive; without it each backend is capped at about 470 new connections per second by ephemeral port exhaustion." This is a detail almost nobody knows and it is checkable.
- **Bring up `X-Forwarded-For` and its security implication**, not just its existence: "I'd delete and re-set XFF at the edge, because trusting a client-supplied value bypasses every per-IP rate limit."
- **Mention slow start** when autoscaling comes up. "New nodes get a ramp, otherwise a cold JVM gets flooded and can fall into a restart loop."
- **Distinguish DNS from an LB.** "DNS picks the region; the load balancer picks the backend. DNS failover is minutes because TTLs are advisory."

**Red flags interviewers watch for:**

- Drawing a load balancer and never addressing what happens when it fails.
- Believing DNS round robin provides failover.
- No mention of health checks at all, or health-checking the wrong thing.
- Claiming availability figures that exceed the load balancer's own.
- Not knowing that the backend loses the client IP, or trusting `X-Forwarded-For` naively.
- Proposing sticky sessions as a design choice rather than a stopgap for a stateful app.
- Enabling retries without mentioning idempotency.
- Being unable to name a single capacity dimension of the LB itself.

## 17. Knowledge check

Answer these before reading on. Write your answers down — even one line each.

**Conceptual**

1. A team runs 20 healthy backends behind one load balancer and claims "99.999 % availability because we have 20 servers." Explain precisely why this is wrong, what the actual ceiling is, and what two changes would be required to raise it.
2. Explain the difference between `X-Forwarded-For` and the PROXY protocol: what each carries, at which layer each operates, why one cannot be used in the other's situation, and the specific security precaution each requires.
3. Your load balancer health-checks `GET /` with a 30-second interval, a timeout of 5 seconds and an unhealthy threshold of 3. Give the worst-case detection time, and describe two *distinct* categories of backend failure that this check would never detect at all.

**Practical scenario**

4. BookNook's LB logs show that, over the last hour, backend `n5` served 31 % of all requests while the other 11 backends split the remaining 69 %. CPU on `n5` is 94 %; the rest are at 35 %. All backends are identical instance types and all are healthy. Describe (a) the four most likely causes in the order you would investigate them, (b) the specific LB metric or log field that would confirm or eliminate each, and (c) the configuration change you would make for each cause if confirmed.

**Calculation**

5. BookNook is preparing for a flash sale. Expected peak is 40,000 RPS from 300,000 concurrently-browsing users. Average response is 25 KB. Connections last an average of 90 seconds. Assume 70 % TLS session resumption during steady state, ECDSA P-256 at 12,000 full handshakes/sec/core and 120,000 resumed handshakes/sec/core, 10 KB of memory per connection, Linux ephemeral range 32768–60999 with a 60-second `TIME_WAIT`, and 40 backends.
   (a) How many concurrent connections must the LB hold, and how much memory is that?
   (b) What is the steady-state TLS handshake rate, split into full and resumed, and how many CPU cores does it require?
   (c) At the start of the sale, all 300,000 users connect within 60 seconds with no session tickets. What is the handshake rate and core requirement then, and how would the answer change with RSA-2048 certificates at 2,000 handshakes/sec/core?
   (d) What network throughput does the LB need, in Gbps?
   (e) Without upstream keep-alive, how many new backend connections per second would each backend receive, and would that exceed the ephemeral-port limit? Show every step and state every assumption you add.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 22.

---

**Progress checklist**

Phase 1 — Internet and System-Design Foundations: all 17 topics `[x]` complete.

Phase 2 — Scaling
- [x] 1. Vertical scaling
- [x] 2. Horizontal scaling
- [x] 3. Stateless application servers
- [x] 4. Load balancers
- [ ] 5. Layer 4 versus Layer 7 load balancing
- [ ] 6. Load-balancing strategies
- [ ] 7. Health checks and failover
- [ ] 8. Reverse proxies and API gateways
- [ ] 9. Caching fundamentals
- [ ] 10. Browser, application, database, distributed caches
- [ ] 11. Cache-aside, read-through, write-through, write-behind
- [ ] 12. Cache invalidation and TTL
- [ ] 13. Cache stampedes, hot keys, penetration, eviction
- [ ] 14. CDNs and edge caching
- [ ] 15. Database replication
- [ ] 16. Synchronous versus asynchronous replication
- [ ] 17. Replication lag and read-after-write consistency
- [ ] 18. Database partitioning and sharding
- [ ] 19. Range, hash, directory, geographic sharding
- [ ] 20. Resharding, hot partitions, cross-shard queries
- [ ] 21. Consistent hashing and virtual nodes
- [ ] 22. Autoscaling and multi-region architecture

Phases 3–5: not started.

**NEXT LESSON: Phase 2, Topic 5 — Layer 4 versus Layer 7 load balancing**

---

# Lesson 22 — Layer 4 versus Layer 7 Load Balancing

*Phase 2, Topic 5*

## 1. Simple definition

Load balancers come in two kinds, distinguished by **how much of each packet they read**.

- A **Layer 4 (L4) load balancer** operates on **transport-layer** information only: source IP, source port, destination IP, destination port, and protocol (TCP or UDP). It forwards the connection to a backend **without ever reading the payload**. It does not know whether the bytes inside are HTTP, gRPC, Postgres wire protocol, or encrypted noise — and it does not care.
- A **Layer 7 (L7) load balancer** operates on **application-layer** information. It terminates the connection, parses the protocol (almost always HTTP), and can read and modify the method, path, headers, cookies and body.

The names come from the **OSI model**, a seven-layer reference stack. Only three layers matter for this lesson:

| OSI layer | Name | What lives there | Example information available |
|---|---|---|---|
| 3 | Network | IP | Source/destination IP address |
| 4 | Transport | TCP, UDP | Ports, TCP flags, sequence numbers, connection state |
| 7 | Application | HTTP, gRPC, DNS, SMTP, TLS* | Method, URL path, headers, cookies, body, status code |

(*TLS is formally a session/presentation-layer protocol, but in load-balancer terminology "L7" means "understands the application protocol", and TLS termination is what unlocks that.)

The single-sentence distinction to carry into an interview: **an L4 load balancer moves packets between two endpoints; an L7 load balancer is itself an endpoint that speaks your application's protocol.**

## 2. Why it exists

The split exists because **reading the payload is both extremely valuable and genuinely expensive**, and different traffic justifies different trades.

**The case for L4.** To forward a TCP connection, an L4 balancer needs to do almost nothing: hash the connection's 5-tuple (source IP, source port, destination IP, destination port, protocol), look up a backend, rewrite the destination address, and forward. It never allocates a buffer for a request body, never parses a header, never decrypts anything. Modern L4 balancers run in the kernel (Linux IPVS), in eBPF (Cilium, Katran), or in dedicated silicon, and achieve **millions of packets per second per core with sub-millisecond — often sub-100-microsecond — added latency**. They also work for *any* TCP or UDP protocol, which is why your database connection pooler, your Redis proxy, your game server front-end and your DNS fleet all sit behind L4.

**The case for L7.** Almost everything you actually want a load balancer to do requires reading the request:

- Route `/api/*` to one fleet and `/static/*` to another.
- Route `api.booknook.com` and `admin.booknook.com` to different backends **on the same IP and port**.
- Send 5 % of traffic to a canary based on a header or cookie.
- Rate-limit per user rather than per IP.
- Retry a failed `GET` on a different backend — impossible at L4, where a dead connection is just a dead connection.
- Health-check with `GET /readyz` and inspect the status code, rather than merely opening a TCP socket.
- Compress responses, add security headers, rewrite URLs, serve a cached response.
- Balance **per request** rather than per connection — which, with HTTP/2 and long-lived keep-alive connections, is the difference between even load and badly skewed load (Lesson 21 §4.4).

None of these are possible without parsing, and parsing costs CPU, memory and latency.

**Why you often need both.** The two are not rivals; they are tiers. A very common large-scale shape is **L4 in front of L7**: a cheap, fast, kernel-level L4 layer absorbs enormous connection volume and spreads it across a fleet of L7 proxies, each of which does the expensive parsing for its share. §4.6 works through why.

## 3. Real-world analogy

A **postal sorting office versus a company mailroom**.

The **sorting office (L4)** reads only the address on the envelope. It never opens anything. It can therefore process enormous volumes extremely fast, and it works identically for letters, parcels, legal documents and birthday cards — it is **protocol-agnostic**. What it cannot do is act on content: it cannot send invoices to Accounts and CVs to HR, because it does not know which is which.

The **company mailroom (L7)** opens each envelope, reads the first line, and routes accordingly: invoices to Accounts, CVs to HR, anything marked urgent to the top of the pile. It can also stamp a received-date on each document, photocopy it for the archive, and refuse anything suspicious. All of that requires opening the envelope, which takes time and staff — so the mailroom handles far fewer items per hour than the sorting office.

And notice the real-world arrangement: **the sorting office feeds the mailroom.** Nobody asks the sorting office to read documents, and nobody asks the mailroom to handle the national postal volume.

**Where the analogy breaks down**

| Postal system | L4 / L7 load balancing |
|---|---|
| The sorting office handles each letter once | An L4 balancer must track **connection state** — which backend a given 5-tuple was sent to — for the lifetime of the connection, which can be hours |
| Opening an envelope is always possible | If traffic is TLS-encrypted, **L7 must decrypt it first**, which requires the private key and costs an asymmetric operation per new connection (Lesson 21 §4.3) |
| A letter is a letter | HTTP/2 multiplexes many independent requests onto **one** connection, so "balance the connection" and "balance the request" give completely different outcomes |
| The mailroom reads and passes the original on | An L7 balancer **terminates** one connection and **originates** another. Two separate TCP connections, two congestion-control states, two sets of timeouts |
| A letter's contents don't change | An L7 balancer routinely **modifies** requests and responses: adds `X-Forwarded-For`, compresses bodies, rewrites paths, strips headers |
| Both handle the same letters | An L4 balancer can carry protocols an L7 balancer cannot parse at all — Postgres wire protocol, custom binary RPC, UDP game traffic, QUIC |

## 4. Detailed technical explanation

### 4.1 What information is available at each layer

This is the heart of the lesson. You can only route on what you can see.

| Information | L4 sees it? | L7 sees it? | What it enables |
|---|---|---|---|
| Source IP / port | Yes | Yes (but as the *LB's* IP unless XFF/PROXY — Lesson 21 §4.7) | Source-IP hashing, geo-routing, IP allow-lists |
| Destination IP / port | Yes | Yes | Routing by listener |
| TCP flags, connection state | Yes | Yes (implicitly) | Connection tracking, SYN-flood mitigation |
| TLS SNI (server name) | **Partially** — it is in the ClientHello, in plaintext | Yes | Routing by hostname **without decrypting** — the one L7-ish trick L4 can do |
| HTTP method (`GET`/`POST`) | No | Yes | Route writes to one fleet, reads to another |
| URL path | No | Yes | `/api/*` vs `/static/*` routing |
| Host header | No | Yes | Many hostnames on one IP:port (virtual hosting) |
| Cookies / headers | No | Yes | Sticky sessions, canaries, per-user rate limits, A/B tests |
| Request body | No | Yes | Content-based routing, request validation, WAF rules |
| HTTP status code from the backend | No | Yes | Passive health checks, retries on 5xx, error-rate metrics |
| Individual HTTP/2 streams | No | Yes | **Per-request** balancing on a multiplexed connection |

Two entries deserve emphasis.

**TLS SNI routing** is the important edge case. The Server Name Indication field of the TLS ClientHello is sent in plaintext before encryption begins, so an L4 balancer *can* peek at it and route `api.booknook.com` and `admin.booknook.com` to different backend pools **while remaining pure passthrough** — never decrypting, never holding a key. This gives you hostname routing at L4 speed, and it is how many TLS-passthrough architectures work. (Encrypted Client Hello, as it rolls out, removes this capability, which is worth knowing.)

**HTTP/2 stream-level balancing** is the entry that most changes real behaviour. With HTTP/2 (Lesson 9), a single TCP connection carries dozens or hundreds of concurrent requests. An L4 balancer pins that entire connection to one backend, so a mobile client making 500 requests over one connection sends all 500 to one node. An L7 balancer sees each stream as an independent request and can spread all 500 across the fleet. **At L4, "balanced" means balanced connections; at L7 it means balanced requests, and those are very different when connections are long-lived.**

### 4.2 How L4 forwarding actually works

Three mechanisms, with different trade-offs:

**(a) NAT mode (destination NAT).** The LB rewrites the destination IP from the VIP to a backend's IP and forwards. The backend's reply must come *back through the LB* so the source address can be rewritten to the VIP — otherwise the client receives a packet from an address it never contacted and drops it. This means **both directions of traffic pass through the LB**, so the LB's bandwidth must cover request *and* response.

**(b) Direct Server Return (DSR), also called direct routing.** The LB forwards the request to a backend without rewriting the destination IP (it rewrites only the layer-2 MAC address, or encapsulates the packet). Each backend is configured with the VIP on a loopback interface, so it accepts the packet, and **replies directly to the client, bypassing the LB entirely**.

This matters enormously for asymmetric traffic, which is nearly all web traffic: a 500-byte request produces a 200 KB response. With DSR the LB carries only the request:

```
Without DSR (NAT):  LB bandwidth = requests + responses
                    6,000 RPS × (0.5 KB + 200 KB) = 1,203 MB/s ≈ 9.6 Gbps
With DSR:           LB bandwidth = requests only
                    6,000 RPS × 0.5 KB            = 3 MB/s ≈ 0.024 Gbps
                    → a 400x reduction in LB bandwidth
```

DSR's costs: it only works at L4 (you cannot inspect a response you never see), it requires the backends and the LB to be on the same L2 segment or to use tunnelling, it needs careful ARP configuration on backends so they do not answer ARP requests for the VIP, and it makes response-side observability impossible at the LB. It is common in on-premises and very-high-throughput environments (video, downloads, game traffic) and essentially unavailable in cloud-managed load balancers.

**(c) Maglev / ECMP hashing.** The approach used by large providers: many identical L4 nodes receive traffic via ECMP, and each independently computes the same consistent hash of the 5-tuple to pick a backend (Topic 21 — Consistent hashing). No shared state is needed between the L4 nodes, so they scale horizontally without coordination — κ ≈ 0 in the USL sense (Lesson 19 §4.5). Connection tracking is a local optimisation, not a requirement.

### 4.3 How L7 proxying actually works

An L7 balancer is a **full proxy**: it terminates connection #1 from the client and originates connection #2 to the backend (Lesson 21 §4.4). Per request it must:

1. Read bytes from the client socket until a complete set of HTTP headers has arrived (possibly across many TCP segments).
2. Decrypt, if TLS is terminated here.
3. Parse the request line and headers into structured form, allocating buffers.
4. Evaluate routing rules, ACLs, rate limits, rewrite rules.
5. Choose a backend and acquire or open an upstream connection.
6. Re-serialise the (possibly modified) request and write it out.
7. Read, parse, and possibly buffer/transform the response.
8. Write the response back, possibly compressing it.
9. Log a structured access record.

That is on the order of **tens of microseconds of CPU** per request in a well-optimised proxy, plus memory for buffers — typically 8–64 KB per in-flight request. Multiply by hundreds of thousands of requests per second and you have a real capacity problem, which is why L7 throughput per core is roughly an order of magnitude below L4.

A subtle consequence of full proxying: **the LB has its own timeouts, its own buffering behaviour and its own congestion control on each side.** A slow client can no longer stall a backend, because the LB buffers the response and feeds it out at the client's pace — this is a genuine benefit (it is why L7 proxies protect well against Slowloris-style attacks). But it also means the LB can run out of memory buffering responses for slow clients, which is a failure mode L4 simply does not have.

### 4.4 Performance comparison

Approximate figures, 2026, per load-balancer instance of a moderate size (8–16 cores). **Treat as order-of-magnitude, measured ranges, not guarantees** — real numbers depend heavily on request size, TLS, rule complexity and hardware.

| Dimension | Layer 4 | Layer 7 |
|---|---|---|
| New connections/sec | **1,000,000+** (kernel/eBPF/XDP paths reach several million) | 50,000–150,000 (TLS handshakes are the usual limit) |
| Requests/sec | n/a — it does not see requests | **50,000–150,000** per instance (nginx ~50–80k/core for plain HTTP proxying) |
| Added latency | **~50–500 µs** (often under 100 µs with DSR/eBPF) | **1–5 ms** typical; more with TLS termination, body buffering or complex rules |
| Throughput | Line rate (10–100 Gbps); with DSR, far beyond its own NIC | Limited by CPU for parsing, TLS and compression |
| CPU per request | Negligible — often no userspace involvement at all | Tens of microseconds, plus TLS |
| Memory per connection | ~200 bytes–2 KB (just connection-tracking state) | 8–64 KB (buffers, parsed structures) |
| Protocols supported | **Any TCP or UDP** | HTTP/1.1, HTTP/2, HTTP/3, gRPC, WebSocket; others need a specific parser |
| Can route by path/header/cookie | **No** | Yes |
| Can do HTTP health checks | No (TCP connect only) | **Yes** (`GET /readyz`, status-code aware) |
| Can retry a failed request | No — only reset the connection | **Yes**, on another backend (idempotent only — Lesson 17) |
| Balances per | **Connection** | **Request** (including per HTTP/2 stream) |
| Sees the client IP at the backend | Preserved with DSR; otherwise needs PROXY protocol | Via `X-Forwarded-For` |
| Typical products | AWS NLB, Google L4 ILB/Maglev, IPVS/LVS, keepalived, HAProxy `mode tcp`, Cilium/eBPF | AWS ALB, GCP HTTPS LB, nginx, HAProxy `mode http`, Envoy, Traefik, Caddy |

**The honest summary: L4 is roughly 10× the throughput and 10–50× lower added latency; L7 gives you every feature you actually want.** For BookNook at 6,000 RPS, L7's 1–5 ms on a 20 ms request is a 5–25 % latency increase that buys path routing, canaries, real health checks and retries — an easy trade. At 2,000,000 RPS of a simple protocol, the same trade is unaffordable.

### 4.5 TLS termination placement and re-encryption

Where TLS ends determines what each tier can do. Four arrangements:

| Arrangement | Client→LB | LB→backend | LB can read HTTP? | When to use |
|---|---|---|---|---|
| **Passthrough** (L4 only) | TLS | **same TLS session, forwarded** | No | Mutual TLS where the backend must verify the client certificate; end-to-end encryption with no key at the edge; protocols the LB cannot parse |
| **Termination** | TLS | **plaintext HTTP** | Yes | The common cloud default. Private network assumed trusted |
| **Re-encryption / bridging** | TLS | **new TLS session** | Yes | PCI-DSS, HIPAA, zero-trust networks. Costs a second handshake and encryption on the backend hop |
| **Offload at L4, terminate at L7** | TLS to the L7 tier; L4 passes bytes through | plaintext or TLS | L7 yes, L4 no | The two-tier shape in §4.6 |

The important interactions:

- **Passthrough blocks every L7 feature.** You cannot route by path, add `X-Forwarded-For`, compress, or health-check with HTTP, because you cannot read the bytes. SNI routing (§4.1) is the only hostname-level routing available, and client IP must be conveyed with the PROXY protocol.
- **Re-encryption roughly doubles the cryptographic work** but it is not as bad as it sounds: the LB→backend connections are long-lived and reused (Lesson 21 §4.4), so handshakes there are rare. Steady-state cost is symmetric encryption only — typically 1–3 % CPU — rather than a handshake per request.
- **Mutual TLS (mTLS) forces a choice.** If the backend must authenticate the client's certificate, you need passthrough (L4), or you terminate at the L7 tier and have *it* validate the client certificate and forward the verified identity in a trusted header. The second is more common in service meshes.

### 4.6 When you need both tiers

The two-tier pattern — **L4 in front of L7** — exists for four reasons:

1. **Connection absorption.** An L4 tier can hold millions of connections cheaply and spread them over an L7 fleet that could never terminate them all. The L4 tier is the shock absorber.
2. **The L7 tier becomes horizontally scalable.** L7 proxies are themselves servers that can die, be deployed, and be scaled. Something must balance *them* — and that something must be fast and stateless, which is L4.
3. **DDoS and SYN-flood absorption.** Volumetric attacks are cheapest to drop at L4 (or in eBPF/XDP before the kernel network stack even processes the packet). An L7 proxy forced to parse attack traffic is an expensive victim.
4. **Mixed protocols on one entry point.** HTTP goes to the L7 tier; gRPC-over-TLS passthrough, Postgres and UDP go directly to their own backends at L4.

```
clients → [ anycast / ECMP ] → [ L4 tier: millions of conns, µs latency ]
                                        │
                                        ├──▶ [ L7 fleet: parse, route, TLS ] ──▶ app nodes
                                        ├──▶ Postgres (passthrough)
                                        └──▶ UDP / QUIC services
```

This is roughly how every large provider's edge is built, and it is worth being able to draw it.

### 4.7 How Kubernetes maps onto L4 and L7

Kubernetes makes this distinction concrete, and interviewers like it because it tests whether you understand the model rather than the vocabulary.

| Kubernetes object | Layer | What it actually does |
|---|---|---|
| **Service** (`ClusterIP`) | **L4** | A virtual IP inside the cluster. `kube-proxy` programs iptables or IPVS rules that DNAT a connection to one of the Pod IPs backing the Service. It never reads HTTP. Balancing is **per connection**, by random or round-robin rule selection |
| **Service** (`NodePort`) | **L4** | The same, exposed on a port on every node |
| **Service** (`LoadBalancer`) | **L4** | The same, plus the cloud provider is asked to create an external L4 load balancer (an AWS NLB, for example) pointing at the nodes |
| **Ingress** / **Gateway API** | **L7** | A set of HTTP routing rules (host, path, header) implemented by an **ingress controller** — nginx, Envoy, Traefik, HAProxy — running as Pods in the cluster. It terminates TLS, parses HTTP, and routes to Services |
| **Service mesh sidecar** (Envoy) | **L7** | A per-Pod proxy giving per-request balancing, retries, mTLS and observability without a central tier |

Two consequences follow directly from the table and are frequent sources of confusion:

- **A plain Kubernetes Service does not balance HTTP requests; it balances connections.** With HTTP/2 or gRPC — which hold one long-lived connection — a client Pod talking to a 10-replica Service sends *everything* to one replica. The fix is an L7-aware client, a headless Service with client-side balancing, or a sidecar/mesh. This is the single most common "why is one of my gRPC replicas at 100 % CPU" question in production Kubernetes.
- **An Ingress is not a load balancer you get for free.** It is configuration consumed by a controller that is itself a set of Pods which must be sized, scaled and made highly available — typically behind an L4 `LoadBalancer` Service. That is the §4.6 two-tier pattern expressed in Kubernetes objects.

## 5. Request / data flow

The same BookNook request, traced through both kinds of balancer, so the difference is concrete.

**Flow A — through an L4 load balancer (NAT mode):**

1. Client sends a TCP `SYN` to the VIP `203.0.113.10:443`.
2. The L4 LB receives the packet. It extracts the **5-tuple**: `(203.0.113.5, 54312, 203.0.113.10, 443, TCP)`.
3. It computes a consistent hash of that 5-tuple (Topic 21) and selects backend `10.0.1.14`. **It has read nothing beyond the IP and TCP headers.**
4. It records the mapping in its connection-tracking table so every subsequent packet of this flow goes to the same backend.
5. It rewrites the destination IP to `10.0.1.14`, recomputes checksums, and forwards. Elapsed LB time: **~60 microseconds.**
6. The backend completes the **TCP handshake directly with the client's sequence numbers** — the LB is not an endpoint, it is a packet rewriter.
7. The **TLS handshake happens between the client and the backend.** The LB sees encrypted bytes and forwards them. It never has the private key.
8. The client sends `GET /books/1492052205`. The LB forwards the packets. **It does not know this is HTTP, nor what the path is.**
9. The backend responds. The response packets return through the LB, which rewrites the source IP back to the VIP (or, with DSR, go **directly to the client**, never touching the LB).
10. The connection stays open. **Every subsequent request on this connection goes to `10.0.1.14`**, regardless of load, because the balancing decision was made once, at connection time.
11. If `10.0.1.14` dies, the connection is reset. The client sees a TCP reset and must reconnect. **The LB cannot retry the request** — it never knew what the request was.

**Flow B — through an L7 load balancer:**

1. Client sends `SYN` to the VIP. The **LB completes the TCP handshake itself** — it is an endpoint.
2. **TLS handshake with the LB.** The LB holds the certificate and private key for `booknook.com` and decrypts from here on (Lesson 21 §4.3).
3. Client sends `GET /books/1492052205` with headers and cookies.
4. The LB **buffers bytes until the full header block has arrived**, then parses method, path, `Host`, cookies.
5. It evaluates rules: `Host: booknook.com` + path `/books/*` → the `app` target group. (`/api/*` would go elsewhere; `/static/*` to the CDN.)
6. It applies policy: per-user rate limit keyed on the session cookie, WAF rules, request-size cap.
7. It checks a canary rule: `if cookie "beta=1" → canary target group (5% weight)`. Not matched.
8. It selects a backend by **least outstanding requests**, using live per-backend in-flight counts — information L4 does not have.
9. It takes an **idle keep-alive connection from its upstream pool** to node 4, or opens one.
10. It rewrites the request: sets `X-Forwarded-For`, `X-Forwarded-Proto: https`, adds `X-Request-Id`, strips untrusted headers.
11. It forwards the request over connection #2 as plaintext HTTP (or re-encrypts, per §4.5).
12. Node 4 responds `200` with 40 KB of JSON.
13. The LB **reads the status code**, records backend latency, and may mark node 4 unhealthy on repeated 5xx (passive health checking).
14. It compresses the body with brotli, adds HSTS and security headers.
15. It encrypts and writes to the client on connection #1.
16. **The next request on the same client connection is balanced independently** — it may go to node 9. Per-request balancing.
17. If node 4 had failed to connect, the LB could **retry the `GET` on node 9** transparently; the client would never know. (It must not do this for `POST` — Lesson 17.)

**Count the differences:** steps 5–8, 13–14 and 16–17 of Flow B are all impossible in Flow A. Flow A finished its work in ~60 µs; Flow B took 1–5 ms.

## 6. ASCII diagram

```
WHAT EACH LAYER CAN SEE  (you can only route on what you can read)

 ┌──────────────────────────────────────────────────────────────┐
 │ ETH │ IP src/dst │ TCP ports │ TLS record │ HTTP method/path  │
 │     │            │           │            │ headers, cookies  │
 └──┬──┴─────┬──────┴─────┬─────┴──────┬─────┴─────────┬─────────┘
    │        │            │            │               │
    │   ◀─── L4 READS THIS ───▶        │               │
    │   (+ TLS SNI, which is plaintext in the ClientHello)
    │                                  │               │
    │   ◀───────────── L7 READS ALL OF THIS ──────────▶│
                                   (requires decryption first)

 L4 decision input:  hash(src_ip, src_port, dst_ip, dst_port, proto)
 L7 decision input:  Host, path, method, header, cookie, body, + all of L4


FORWARDING MODELS

 (a) L4 NAT — both directions traverse the LB
     client ──req──▶ [LB rewrite dst] ──▶ backend
     client ◀─resp── [LB rewrite src] ◀── backend
     LB bandwidth = requests + responses = 9.6 Gbps at BookNook scale

 (b) L4 DIRECT SERVER RETURN — responses bypass the LB entirely
     client ──req──▶ [LB rewrites MAC only] ──▶ backend (VIP on loopback)
     client ◀══════════ resp (direct, huge) ══════════ backend
     LB bandwidth = requests only = 0.024 Gbps  ← 400x less
     COST: L4 only, same L2 segment or tunnelling, careful ARP config,
           zero response-side observability, not available in cloud LBs

 (c) L7 FULL PROXY — two separate TCP connections, LB is an endpoint
     client ──TCP#1──▶ [ TERMINATE · DECRYPT · PARSE · ROUTE · REWRITE ]
                                        ──TCP#2──▶ backend
     Slow clients can't stall backends (LB buffers) — but the LB can
     run out of memory buffering for them. A failure mode L4 lacks.


HTTP/2: WHY "BALANCED" MEANS TWO DIFFERENT THINGS

  one mobile client, ONE HTTP/2 connection, 500 requests

  via L4:                          via L7:
   conn pinned at SYN time          each STREAM balanced separately
   ┌────┐                           ┌────┐┌────┐┌────┐┌────┐
   │ n4 │ ◀── all 500               │ n1 ││ n4 ││ n7 ││n12 │ ◀ ~125 each
   └────┘                           └────┘└────┘└────┘└────┘
   others idle                      evenly loaded
   ⚠ this is why a 10-replica gRPC Service in Kubernetes
     sends everything to one replica


THE TWO-TIER SHAPE (what large edges actually look like)

  clients
     │ anycast / ECMP
     ▼
 ┌─────────────────────────────────────────┐
 │ L4 TIER   1M+ conn/s · ~60 µs · any      │ ← absorbs connections
 │ protocol · DDoS/SYN flood sink · eBPF    │   and DDoS cheaply
 └───┬───────────────┬──────────────┬───────┘
     │               │              │
     ▼               ▼              ▼
 ┌─────────┐   ┌──────────┐   ┌───────────┐
 │L7 proxy │   │L7 proxy  │   │ Postgres  │ ← TLS passthrough,
 │TLS·route│   │ 50-150k  │   │ UDP/QUIC  │   protocols L7 can't parse
 │ ~1-5 ms │   │ req/s ea │   └───────────┘
 └────┬────┘   └────┬─────┘
      └──────┬──────┘
             ▼
        app nodes


KUBERNETES MAPPING

  Service (ClusterIP/NodePort/LoadBalancer) ─▶ L4  (kube-proxy iptables/IPVS)
      balances CONNECTIONS, never reads HTTP
  Ingress / Gateway API                     ─▶ L7  (nginx/Envoy/Traefik Pods)
      balances REQUESTS, terminates TLS, routes by host/path
  ⚠ Ingress controllers are Pods — they need an L4 Service in front.
    That IS the two-tier shape above, written in Kubernetes objects.
```

## 7. Detailed example

### BookNook: three listeners, three different answers

By this point BookNook serves four kinds of traffic, and the team discovers that one load balancer configuration cannot be right for all of them.

**Traffic 1 — the web and REST API (6,000 RPS). L7, unambiguously.**

The requirements are path routing (`/api/*`, `/books/*`, `/admin/*` to three fleets), per-user rate limiting keyed on the session cookie, canary deployments at 5 %, retries of idempotent `GET`s, brotli compression, and `GET /readyz` health checks. Every one of these requires reading the request.

The cost: ~2 ms of added p50 latency on a 20 ms request (10 %), and TLS termination at the edge. The team measured it before and after: p50 went 20 → 22 ms, p99 went 180 → 184 ms. **Entirely acceptable for the feature set.**

**Traffic 2 — the mobile app's gRPC endpoint (18,000 RPS at peak). A trap, then a fix.**

BookNook's mobile app uses gRPC over HTTP/2 with mutual TLS: the app presents a client certificate that the backend validates. The team's first attempt put it behind the same L7 ALB, which **broke mTLS immediately** — the LB terminated TLS and the backend never saw a client certificate.

Second attempt: a **Layer 4 NLB in TLS passthrough mode**, so the TLS session runs end-to-end between the app and the backend. mTLS works. But a new problem appears within a day: the mobile app holds **one long-lived HTTP/2 connection for the whole session**, and the L4 LB pins that connection to one backend. Load distribution across 12 backends was 41 %, 19 %, 14 %, and single digits for the rest — the §4.1 HTTP/2 problem in its purest form.

Final design, and it is the §4.6 two-tier shape:

- **L4 NLB** in front, for connection absorption and raw throughput.
- Behind it, a fleet of **Envoy proxies** that terminate mTLS, *validate the client certificate themselves*, forward the verified identity in a trusted header, and balance **per gRPC stream** rather than per connection.
- Load distribution after the change: every backend within 4 % of the mean.

The lesson generalises: **when a long-lived multiplexed protocol meets an L4 balancer, you get connection-level skew, and the fix is an L7-aware tier — even if the reason you chose L4 was a legitimate one like mTLS.**

**Traffic 3 — the PostgreSQL connection pooler. L4, no debate.**

PgBouncer sits behind a pair of IPs for HA. The PostgreSQL wire protocol is not HTTP; no L7 proxy parses it meaningfully. An L4 balancer in TCP mode with a TCP health check is the correct and only sensible answer. Client IP is conveyed with the PROXY protocol (Lesson 21 §4.7) so Postgres logs are accurate.

**Traffic 4 — book cover images and static assets. Neither; use a CDN.**

Lesson 1 §9 showed images were 40× the application's bandwidth. Putting 76.8 Gbps of image traffic through *any* load balancer is a sizing problem you can simply refuse to have. These go to a CDN (Topic 14) and never touch the origin's LB. **The best load-balancing decision is sometimes to remove the traffic.**

**The final BookNook edge:**

```
booknook.com        → L7 ALB              → web/API fleet      (6,000 RPS)
grpc.booknook.com   → L4 NLB → Envoy L7   → gRPC fleet        (18,000 RPS)
db-pool internal    → L4 NLB              → PgBouncer pair
static.booknook.com → CDN                 → object storage    (never hits origin)
```

### Contrasting example: a video streaming edge

A video platform serves 400 Gbps of segment traffic from one point of presence. Each HTTP request fetches a 4 MB video segment; the request itself is 600 bytes.

Running this through an L7 proxy would be absurd: at 400 Gbps the proxy would have to read, buffer and re-emit every byte, requiring an enormous fleet purely to shuffle data it never needs to inspect.

Their design:

- **L4 with Direct Server Return.** The LB sees only the 600-byte requests — `12,500 req/s × 600 B ≈ 7.5 MB/s` — while the 400 Gbps of responses go **straight from the cache servers to the clients**, never touching the LB. §4.2's 400× reduction, at scale.
- **Consistent hashing on the URL path**, so the same video segment always lands on the same cache server, maximising cache hit rate (Topic 21). Note this is *content-aware* routing done at L4 — made possible because the hash input is the destination of the flow, not the payload.
- A **small L7 tier alongside** for the API: login, playback manifests, entitlement checks, analytics. A few thousand RPS of small, feature-rich requests, where L7's cost is irrelevant.

The contrast with BookNook is the general rule: **route the big, dumb, uniform traffic at L4; route the small, varied, policy-heavy traffic at L7.** Most real systems have both, and the mistake is forcing one tool onto both kinds.

## 8. Code or configuration example

The same logical service configured three ways: L4 with IPVS, L7 with nginx, and both expressed as Kubernetes objects. **Illustrative only, not production-ready:** no TLS hardening, no DSR ARP configuration shown in full, no persistence tuning, simplified health checks, no observability wiring.

```bash
#!/usr/bin/env bash
# l4.sh -- a Layer 4 load balancer with Linux IPVS (LVS). ILLUSTRATIVE.
# IPVS runs IN THE KERNEL: packets are forwarded without ever entering
# userspace, which is why L4 reaches millions of connections/sec.

# Create a VIRTUAL SERVICE on the VIP. Note what is specified: an IP, a port,
# and a protocol (-t = TCP). NOTHING about HTTP -- IPVS has no idea what is
# inside these packets, and that is precisely the point.
ipvsadm -A -t 203.0.113.10:443 \
  -s sh \            # scheduler: 'sh' = source hashing. The decision input is
                     # the 5-tuple, the ONLY information available at L4.
                     # Alternatives: rr, wrr, lc (least connection), wlc.
  --persistent 300   # pin a source IP to the same backend for 300s. This is
                     # the closest L4 can get to "sticky sessions" -- it is
                     # per-IP, so everyone behind one corporate NAT lands on
                     # one backend. A real limitation of L4 stickiness.

# Add backends. '-g' = GATEWAY mode = DIRECT SERVER RETURN (SS4.2b):
# only the REQUEST traverses this box; responses go straight to the client.
# Requires: backends on the same L2 segment, VIP bound to their loopback,
# and arp_ignore/arp_announce set so backends do NOT answer ARP for the VIP.
# Get the ARP configuration wrong and the VIP is claimed by a backend --
# a confusing, total outage.
ipvsadm -a -t 203.0.113.10:443 -r 10.0.1.11:443 -g -w 100   # -w = weight
ipvsadm -a -t 203.0.113.10:443 -r 10.0.1.12:443 -g -w 100
ipvsadm -a -t 203.0.113.10:443 -r 10.0.2.11:443 -g -w 100

# What this configuration CANNOT do, and no L4 LB can:
#   - route /api/* differently from /static/*   (cannot see the path)
#   - health check with GET /readyz             (can only TCP-connect)
#   - retry a failed request on another backend (never knew the request)
#   - add X-Forwarded-For                       (cannot modify a payload
#                                                it does not parse)
# For client IP preservation here, use DSR (source IP is untouched) or the
# PROXY protocol at the application (Lesson 21 SS4.7).
```

```nginx
# l7.conf -- the SAME service at Layer 7 with nginx. ILLUSTRATIVE.
# Everything below is impossible at L4, and all of it costs CPU per request.

upstream app_fleet   { least_conn; server 10.0.1.11:8000; server 10.0.1.12:8000;
                       keepalive 32; }
upstream api_fleet   { least_conn; server 10.0.3.11:8000; server 10.0.3.12:8000;
                       keepalive 32; }
upstream canary_fleet{ server 10.0.4.11:8000; keepalive 8; }

# Per-SESSION rate limiting. The key is a COOKIE -- L4 cannot see cookies,
# so at L4 the best available key is the source IP, which lumps every user
# behind one NAT together.
limit_req_zone $cookie_sid zone=per_session:10m rate=100r/m;

# Canary routing by cookie: another L7-only capability.
map $cookie_beta $target_pool {
    default  "app_fleet";
    "1"      "canary_fleet";
}

server {
    # TLS TERMINATION. This is what makes everything below possible: without
    # decrypting, nginx could not read a path, a header or a cookie.
    listen 443 ssl http2;
    ssl_certificate     /etc/nginx/certs/booknook.pem;
    ssl_certificate_key /etc/nginx/certs/booknook.key;

    # ROUTE BY PATH -- the canonical L7 feature.
    location /api/ {
        limit_req zone=per_session burst=20 nodelay;
        proxy_pass http://api_fleet;
        include proxy_common.conf;
    }

    location /static/ {
        # Serve from a CDN origin instead of the app fleet. Removing this
        # traffic from the app tier is worth more than any balancing
        # algorithm (SS7, traffic 4).
        proxy_pass https://booknook-static.cdn.example.com;
    }

    location / {
        proxy_pass http://$target_pool;      # cookie-driven canary
        include proxy_common.conf;
    }
}

# proxy_common.conf
#   Required for upstream keep-alive to engage (Lesson 21 SS4.4).
proxy_http_version 1.1;
proxy_set_header Connection "";
#   Give the backend the real client IP. An L4 LB cannot do this at all.
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
#   RETRY on another backend -- possible only because nginx knows what the
#   request was. 'non_idempotent' is deliberately ABSENT: retrying a POST
#   that may have succeeded creates duplicate orders (Lesson 17).
proxy_next_upstream error timeout http_502 http_503;
proxy_connect_timeout 2s;
proxy_read_timeout   10s;

#   RE-ENCRYPTION (SS4.5) would be: proxy_pass https://app_fleet; plus
#   proxy_ssl_verify on; -- used when policy forbids plaintext on the
#   internal hop. Cost is low in steady state because upstream connections
#   are long-lived and reused, so handshakes there are rare.
```

```yaml
# k8s.yaml -- the two layers as Kubernetes objects (SS4.7). ILLUSTRATIVE.
---
# LAYER 4. A Service is kube-proxy programming iptables/IPVS DNAT rules.
# It balances CONNECTIONS and never parses HTTP.
apiVersion: v1
kind: Service
metadata: { name: booknook-app }
spec:
  type: ClusterIP
  selector: { app: booknook }
  ports: [{ port: 80, targetPort: 8000 }]
  # ⚠ THE GRPC/HTTP2 TRAP (SS4.7): a client holding ONE long-lived HTTP/2
  # connection sends EVERY request to the ONE Pod that connection landed on.
  # A 10-replica Service then has 1 busy replica and 9 idle ones. Fixes:
  # an L7-aware client, a headless Service with client-side balancing, or a
  # sidecar proxy. Changing the Service will NOT help -- it cannot see streams.
---
# LAYER 7. Ingress is HTTP routing rules, implemented by a controller
# (nginx/Envoy/Traefik) that runs as PODS in the cluster.
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: booknook
  annotations:
    # L7-only capabilities, all requiring a parsed HTTP request:
    nginx.ingress.kubernetes.io/canary: "false"
    nginx.ingress.kubernetes.io/limit-rps: "100"
    nginx.ingress.kubernetes.io/proxy-next-upstream: "error timeout http_502"
spec:
  tls: [{ hosts: [booknook.com], secretName: booknook-tls }]  # TLS terminates HERE
  rules:
    - host: booknook.com
      http:
        paths:
          - path: /api          # routing by PATH: the defining L7 feature
            pathType: Prefix
            backend: { service: { name: booknook-api, port: { number: 80 } } }
          - path: /
            pathType: Prefix
            backend: { service: { name: booknook-app, port: { number: 80 } } }
# ⚠ The ingress controller is itself Pods. It needs an L4 Service of
# type: LoadBalancer in front of it to receive external traffic.
# That is exactly the L4-in-front-of-L7 two-tier pattern of SS4.6,
# expressed in Kubernetes objects.
```

## 9. Numerical example

**Question: for BookNook's three traffic classes, which layer, how many load-balancer instances, and what does each cost in latency and money?**

### Assumptions (all stated)

1. Web/API: 6,000 RPS, 40 KB average response, 600-byte average request, p50 application latency 20 ms, p99 180 ms.
2. gRPC/mobile: 18,000 RPS, 2 KB responses, over 30,000 long-lived HTTP/2 connections.
3. L7 instance capacity: **80,000 RPS** of simple proxying per 16-core instance; **50,000 RPS** with TLS termination, compression and rule evaluation.
4. L4 instance capacity: **1,200,000 new connections/sec**; effectively line-rate throughput.
5. Added latency: **L4 = 0.08 ms**; **L7 = 2.0 ms p50, 4.5 ms p99**.
6. Instance cost: L7 proxy on `m7i.4xlarge` = $589/month; L4 managed NLB ≈ $25/month + ~$0.006 per LCU-hour (call it ~$120/month at this volume).

### Part A — latency cost of choosing L7

```
Web/API, p50:  20 ms + 2.0 ms = 22.0 ms   → +10.0%
Web/API, p99: 180 ms + 4.5 ms = 184.5 ms  → +2.5%

Same traffic at L4:
               20 ms + 0.08 ms = 20.08 ms → +0.4%
```

**The L7 tax is 1.92 ms at p50 — a 9.6 % increase.** In exchange: path routing, cookie-keyed rate limiting, canaries, HTTP health checks, retries, compression. For a 20 ms request this is clearly worth it.

Now the same calculation for a hypothetical 2 ms internal RPC:

```
2 ms + 2.0 ms = 4.0 ms  →  +100% latency
```

**Identical absolute cost, completely different verdict.** This is why internal, low-latency service-to-service calls so often use L4 or client-side balancing, and why the right answer genuinely depends on the baseline latency — a specific dependency, not a vague "it depends".

### Part B — instance count and cost

```
WEB/API at L7 (TLS + compression + rules → 50,000 RPS per instance):
  Instances needed for 6,000 RPS       = ceil(6,000 / 50,000)    = 1
  N+1 for availability (Lesson 19 §9)  = 2
  Across 2 AZs                          = 2
  Cost = 2 × $589                       = $1,178/month
  Utilisation = 6,000 / (2 × 50,000)    = 6%   ← paying for redundancy,
                                                 not for throughput

GRPC at L4 (connection-bound, not request-bound):
  New connections/sec: 30,000 connections lasting ~600 s on average
                     = 30,000 / 600 = 50 new connections/sec
  L4 capacity 1,200,000/s → utilisation = 0.004%
  One managed NLB (multi-AZ by default) = ~$120/month

SAME GRPC AT L7 INSTEAD:
  18,000 RPS / 50,000 per instance = 1 instance, N+1 = 2 → $1,178/month
  …and it would break mTLS (§4.5) unless Envoy re-validates certificates.
```

**Note the shape of the L4 number: 0.004 % utilisation.** L4 capacity is so large relative to normal traffic that it is almost never the constraint — which is exactly why it is the right shock absorber in front of an L7 tier.

### Part C — the two-tier design at 10× scale

Suppose BookNook grows to **600,000 RPS** and **3,000,000 concurrent connections**.

```
L7-ONLY design:
  Instances = ceil(600,000 / 50,000) = 12, N+2 across 3 AZs = 15
  Cost = 15 × $589 = $8,835/month
  Connections: 3,000,000 / 15 = 200,000 per instance
    × 32 KB of buffers each = 6.4 GB per instance
    → m7i.4xlarge has 64 GB; feasible, but connection memory is now
      the binding constraint rather than CPU

TWO-TIER (L4 in front of L7):
  L4 tier: 3,000,000 connections, assume a 300 s average lifetime
         = 10,000 new connections/sec  → 0.8% of ONE L4 instance
         2 managed NLBs = ~$240/month
  L7 tier: unchanged at 15 instances = $8,835/month
  Total = $9,075/month

  The extra $240 buys:
    - DDoS/SYN-flood absorption before the expensive tier
    - the ability to scale the L7 fleet without touching DNS
    - mixed protocols (Postgres, UDP) on the same entry point
    - instant scaling (managed ALBs scale over MINUTES — Lesson 21 §4.5c)
```

**$240/month, 2.6 % of the bill, for the edge's resilience.** This is the easiest trade in the lesson.

### Part D — Direct Server Return, where it matters

Applying §4.2b to the video-streaming contrast from §7:

```
Workload: 12,500 req/s, 600-byte requests, 4 MB responses

L4 NAT mode (both directions through the LB):
  Request bytes  = 12,500 × 600 B      =     7.5 MB/s =  0.06 Gbps
  Response bytes = 12,500 × 4,194,304  = 52,428 MB/s = 419.4 Gbps
  LB must carry                                       = 419.5 Gbps
  → requires ~5 instances at 100 Gbps each, purely to shuffle bytes

L4 with DSR (responses bypass the LB):
  LB carries requests only             =     7.5 MB/s = 0.06 Gbps
  → a SINGLE small instance, with 1,600x headroom

Reduction factor = 419.5 / 0.06 ≈ 7,000x
```

Applied to BookNook's own web traffic (40 KB responses, 600-byte requests):

```
NAT: 6,000 × (0.6 + 40) KB = 243.6 MB/s = 1.95 Gbps
DSR: 6,000 × 0.6 KB        =   3.6 MB/s = 0.03 Gbps
Reduction ≈ 68x
```

**DSR's benefit scales with response:request asymmetry.** At 4 MB responses it is transformative; at 40 KB it is merely large; for a chatty API with 1 KB responses it is marginal. And remember it costs you all L7 features and all response-side observability — which is why BookNook, needing path routing and canaries, does **not** use it, despite the 68×.

### Part E — the HTTP/2 skew, quantified

From §7, traffic 2. 12 backends, 30,000 mobile connections, 18,000 RPS.

```
IDEAL distribution:     18,000 / 12 = 1,500 RPS per backend

L4 (per-connection) with unequal connection lifetimes and a scale-out
event that happened after most connections were established:
  measured: 41%, 19%, 14%, 8%, 6%, 4%, 3%, 2%, 1%, 1%, 1%, 0%
  busiest backend = 0.41 × 18,000 = 7,380 RPS
  → 4.9x the fair share. If one backend saturates at 2,000 RPS,
    this backend is 3.7x over capacity while 8 others are nearly idle.
  Effective fleet capacity = 2,000 / 0.41 = 4,878 RPS
  → 12 backends deliver the throughput of 2.4

L7 (per-stream):
  measured: every backend within 4% of 1,500 RPS
  Effective fleet capacity = 12 × 2,000 = 24,000 RPS
```

**Per-connection balancing on a multiplexed protocol cost 80 % of the fleet's usable capacity.** This single number is the best argument for understanding the distinction, and it is the answer to "why is one gRPC replica at 100 % CPU".

## 10. Trade-offs

**Layer 4 — advantages**

- **Throughput and latency:** 1M+ connections/sec, ~50–500 µs added latency, often entirely in-kernel or in eBPF.
- **Protocol-agnostic:** works for Postgres, Redis, SMTP, custom binary RPC, UDP, QUIC — anything L7 cannot parse.
- **Cheap per connection:** ~200 bytes–2 KB of state versus 8–64 KB.
- **No keys at the edge:** TLS passthrough keeps private keys on the backends, which some compliance regimes require and which supports mutual TLS.
- **Direct Server Return possible**, removing response bytes from the LB entirely (§9 Part D).
- **Excellent DDoS sink:** volumetric and SYN-flood traffic is cheapest to drop here.
- **Simple to reason about and very stable:** fewer moving parts, fewer parsing bugs, fewer CVEs.

**Layer 4 — disadvantages**

- **Cannot route by path, host, header, cookie or body.** (SNI routing is the only exception, and Encrypted Client Hello removes it.)
- **Cannot do HTTP health checks** — a hung application with an open socket passes a TCP check.
- **Cannot retry a failed request**; the client sees a reset and must handle it.
- **Balances connections, not requests** — catastrophic skew with HTTP/2 and gRPC (§9 Part E).
- **No per-user rate limiting**, no WAF, no compression, no header manipulation.
- **Client IP needs DSR or the PROXY protocol**, and the latter is an easy misconfiguration.
- **Stickiness is per source IP**, so everyone behind one corporate NAT lands on one backend.

**Layer 7 — advantages**

- **Every routing and policy feature you actually want:** path/host/header/cookie routing, canaries, A/B tests, rewrites.
- **Real health checks** with status-code semantics against `/readyz`.
- **Transparent retries** of idempotent requests on another backend.
- **Per-request balancing**, including per HTTP/2 stream — even load regardless of connection lifetime.
- **Cross-cutting work in one place:** TLS, compression, security headers, request IDs, WAF, per-user rate limits.
- **Rich observability:** status codes, per-backend latency, per-route metrics. The access log becomes your primary debugging surface.
- **Protects backends from slow clients** by buffering (strong Slowloris mitigation).

**Layer 7 — disadvantages**

- **1–5 ms added latency**, which is 10 % of a 20 ms request and 100 % of a 2 ms one.
- **~10× lower throughput per instance**, so more instances, more cost, more to operate.
- **Requires decryption**, so private keys live at the edge and mutual TLS needs special handling.
- **HTTP-shaped only**; non-HTTP protocols need a bespoke parser or fall back to L4.
- **More memory per connection** (8–64 KB), which becomes the binding constraint at millions of connections (§9 Part C).
- **A larger attack surface**: a protocol parser handling untrusted input is where request-smuggling and header-injection vulnerabilities live.
- **More configuration to get wrong**, and more of it is security-relevant (XFF handling, retry idempotency, buffering limits).

**Operational costs**

L4 is close to set-and-forget but gives you almost no visibility — when something is wrong you will be reading backend logs, not LB logs. L7 gives you excellent visibility and demands continuous care: rule complexity grows, TLS certificates rotate, buffer and timeout tuning matter, and the proxy itself needs patching for protocol-level CVEs.

**When each becomes unnecessary complexity**

Adding an L4 tier in front of L7 below ~100,000 RPS usually buys little beyond faster scaling and DDoS absorption — real benefits, but weigh them against another tier to operate. Conversely, inserting an L7 proxy in front of a protocol it cannot parse gives you a slow, fragile L4 balancer with extra steps.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| **L4 used for gRPC/HTTP/2; one backend overloaded** | Intermittent slowness and timeouts for a subset of users; the rest are fine | Per-backend RPS/CPU wildly uneven while the LB reports all targets healthy (§9 Part E) | Add an L7 tier (Envoy) for per-stream balancing, or use client-side balancing. Restarting the hot backend only moves the problem |
| **L4 TCP health check passes a hung application** | Every request to that backend hangs until the client times out; the LB keeps sending | Backend latency at the timeout ceiling while target health shows "healthy" | Move to an L7 health check against `/readyz`, or add an L7 tier. A TCP check fundamentally cannot detect this |
| **L7 proxy CPU-saturated by TLS handshakes** | Connection timeouts and 503s at the very start of a traffic spike | LB CPU at 100 % with backends idle; handshake-rate metric at its ceiling | Scale the L7 fleet; switch RSA → ECDSA (≈6×, Lesson 21 §9); enable session resumption; add an L4 tier to absorb connections |
| **L7 memory exhausted buffering responses for slow clients** | Random 502s, worst on mobile networks; the proxy restarts periodically | Proxy RSS climbing with connection count; OOM kills in the proxy's logs | Cap `proxy_buffers` / enable streaming rather than full buffering; limit concurrent connections; scale out |
| **TLS passthrough chosen, then an L7 feature is needed** | Nothing visible — it is a design dead end discovered during planning | A requirement for path routing, compression or WAF cannot be implemented | Terminate at an L7 tier behind the L4 one, and have the L7 tier validate client certificates if mTLS is required (§4.5) |
| **DSR misconfigured — a backend answers ARP for the VIP** | Total outage or bizarre partial outage; traffic for everyone lands on one backend | Duplicate-IP warnings on switches; one backend receiving 100 % of traffic | Set `arp_ignore=1` / `arp_announce=2` on all backends; bind the VIP to loopback, never to a live interface |
| **PROXY protocol mismatch on an L4 listener** | 100 % failure on that port — every request returns 400 or hangs | Backend logs show a request starting `PROXY TCP4 ...` | Make the LB's `send-proxy` and the backend's `accept-proxy` agree; accept PROXY headers only from LB source IPs |
| **Kubernetes Service used for a gRPC client** | One replica at 100 % CPU, nine idle; scaling the Deployment changes nothing | Per-Pod CPU wildly uneven; connection count per Pod equally uneven | Headless Service + client-side balancing, or a sidecar/mesh, or an L7 Ingress for that traffic (§4.7) |
| **L7 retries a non-idempotent request** | Duplicate orders, double charges | Duplicate rows with timestamps milliseconds apart; customer complaints | Restrict retries to idempotent methods and connection-level failures; enforce idempotency keys server-side (Lesson 17) |
| **SNI routing breaks under Encrypted Client Hello** | A subset of newer clients route to the wrong backend or are rejected | Failures correlated with specific browser versions | Terminate TLS at an L7 tier for those hostnames, or use separate IPs per hostname |
| **Two-tier added, timeouts not aligned** | Sporadic 504s; each tier's logs blame the other | L4 idle timeout shorter than L7 keep-alive, so connections are silently dropped mid-reuse | Align timeouts so each outer tier's idle timeout exceeds the inner tier's keep-alive |

## 12. Alternatives

| Approach | Layer | How it differs | Good at | Bad at | When you'd pick it |
|---|---|---|---|---|---|
| **L4 load balancer** | 4 | Forwards packets by 5-tuple | Throughput, latency, any protocol, DDoS absorption, DSR | No content routing, no HTTP health checks, no retries, connection-level skew | Non-HTTP protocols, mTLS passthrough, enormous volume |
| **L7 load balancer** | 7 | Terminates and parses HTTP | Routing, canaries, retries, TLS, WAF, observability | Latency, throughput, HTTP-only, key custody | BookNook's web and API tier |
| **L4 in front of L7 (two-tier)** | 4+7 | Each tier does what it is good at | Scale, resilience, mixed protocols, DDoS | Two tiers to operate and align | Above ~100k RPS, or any mixed-protocol edge |
| **TLS passthrough at L4 + L7 behind** | 4→7 | Encryption reaches the L7 tier, not the L4 one | mTLS plus L7 features | Key custody moves to the L7 tier, not the backends | Client-certificate authentication with path routing |
| **Client-side load balancing** | app | The client holds the backend list and picks per request | No extra hop; per-request balancing for gRPC without a proxy | Every client must implement it; service discovery needed; policy changes require client deploys | gRPC service-to-service |
| **Service mesh sidecar (Envoy)** | 7 | An L7 proxy per workload instance | Per-request balancing, mTLS, retries, tracing, no central SPOF | Significant CPU/memory per Pod; a control plane to run | Large microservice estates (Phase 4, Topic 23) |
| **eBPF/XDP balancing (Cilium, Katran)** | 4 | Packet processing before the kernel network stack | Extreme throughput; near-zero latency | Kernel-version dependent; specialised skills; L4-only | Hyperscale edges |
| **Anycast + per-POP L4/L7** | 3+4+7 | Routing chooses the location; LBs choose the backend | Global latency, DDoS dispersal | BGP and address-space ownership required | CDNs, global services (Topic 22) |
| **CDN in front of everything** | 7 | Caches and serves at the edge | Removes the majority of bytes before any LB sees them | Dynamic/personalised traffic still reaches the origin | Static assets — do this first (Topic 14) |

The ordering advice that falls out of this table: **first remove traffic (CDN), then choose the layer that matches the protocol and the feature set, then add a second tier only when volume or protocol mix demands it.**

## 13. When to use it

**Use Layer 4 when:**

1. **The protocol is not HTTP.** PostgreSQL, Redis, Kafka, SMTP, custom binary RPC, UDP-based game or telemetry traffic. There is no decision to make here — L7 has nothing to parse.
2. **You need TLS passthrough**, because the backend must see the original TLS session (mutual TLS with client-certificate validation) or because policy forbids private keys at the edge.
3. **Volume is extreme and the work is uniform.** Millions of connections per second where every request is treated identically; the per-request features would be unused anyway.
4. **Response:request asymmetry is large and DSR is available.** Video, downloads, large file serving — §9 Part D's 7,000× reduction.
5. **You are fronting an L7 fleet** (§4.6). Something must balance the proxies, and it must be cheap, fast and stateless.
6. **You need maximum DDoS absorption**, dropping volumetric traffic before it reaches anything expensive.

**Use Layer 7 when:**

1. **You need routing by content** — path, host, header or cookie. The moment one IP:port must serve two different backends based on the request, you are at L7.
2. **You need real health checks.** `GET /readyz` with status-code semantics is the only way to detect an application that is listening but broken (Lesson 20 §4.4).
3. **You need progressive delivery** — canaries, blue-green, weighted shifts, A/B tests. These are all request-level decisions.
4. **The protocol is multiplexed** (HTTP/2, HTTP/3, gRPC) and connections are long-lived. Per-connection balancing produces the 80 % capacity loss of §9 Part E.
5. **You need per-user policy:** rate limiting by session or API key, per-tenant quotas, WAF rules.
6. **You want transparent retries** of idempotent requests on another backend.
7. **You need edge observability** — per-route latency, status-code breakdowns, per-backend error rates.

**Use both when:** volume exceeds what a manageable L7 fleet can terminate, you serve mixed protocols from one entry point, you need instant scaling that managed L7 LBs cannot provide, or DDoS resilience is a stated requirement.

## 14. When *not* to use it

**Do not use Layer 4 when:**

1. **You need any content-based decision.** Path routing, canaries, per-user limits, HTTP health checks. Choosing L4 here is choosing to build those features somewhere else, usually worse.
2. **Traffic is HTTP/2 or gRPC over long-lived connections and you have more than a handful of backends.** The skew is not a tuning problem; it is structural (§9 Part E).
3. **You need to know *why* requests fail.** L4 sees resets, not status codes. Debugging a 5xx problem from an L4 LB's metrics is nearly impossible.
4. **Sticky behaviour must be per user.** Source-IP persistence puts an entire corporate network or mobile carrier NAT on one backend.

**Do not use Layer 7 when:**

1. **The protocol cannot be parsed.** Forcing a database or custom binary protocol through an HTTP proxy gives you a slower L4 balancer with a larger attack surface.
2. **Mutual TLS must terminate at the backend.** Either pass through at L4, or accept that the L7 tier becomes the authenticator.
3. **The latency budget cannot absorb 1–5 ms.** A 2 ms internal RPC doubles (§9 Part A). Use client-side balancing or an L4 path.
4. **Throughput is dominated by large response bodies.** Proxying 400 Gbps of video through a parser you do not need is pure cost; use L4 with DSR.
5. **At connection volumes where buffer memory becomes the constraint.** Millions of concurrent connections × 32 KB of buffers is tens of gigabytes per instance; absorb them at L4 first.

## 15. Common mistakes

**Beginner mistakes**

- **Thinking "load balancer" is one thing.** The L4/L7 choice determines which features are even possible, and it is often the first thing an interviewer probes after you draw the box.
- **Expecting path-based routing from an L4 balancer.** It cannot see the path. No configuration fixes this.
- **Using a TCP health check and believing it means the application works.** A deadlocked process still accepts connections.
- **Putting gRPC behind a plain Kubernetes Service and wondering why one Pod is hot.** §4.7's most common production surprise.
- **Assuming TLS termination at the LB is always acceptable**, then discovering mTLS or a compliance requirement.
- **Thinking L7 is "better".** It is more capable and 10× slower per core. "Better" depends on the protocol and the latency budget.
- **Forgetting that an L7 balancer is an endpoint**, so backends see the LB's IP and every IP-based control breaks (Lesson 21 §4.7).
- **Treating an Ingress as free infrastructure** rather than a fleet of proxy Pods that must be sized and made redundant.

**Experienced-engineer mistakes**

- **Choosing L4 for mTLS, then not noticing the connection-level skew it introduces** for a multiplexed protocol (§7, traffic 2). Both decisions were individually reasonable; the combination was not.
- **Enabling DSR without the ARP configuration on backends**, producing a VIP conflict and a confusing total outage.
- **Not aligning timeouts across a two-tier edge.** The outer L4 idle timeout shorter than the inner L7 keep-alive produces silently dropped reused connections and sporadic 504s.
- **Terminating TLS at L7 and forwarding plaintext inside a VPC without checking the compliance requirement**, then needing re-encryption retrospectively.
- **Relying on SNI routing at L4 without a plan for Encrypted Client Hello.**
- **Sizing an L7 fleet by RPS and ignoring connection memory**, then hitting an OOM at a connection count the CPU graph said was fine (§9 Part C).
- **Enabling retries at L7 without restricting them to idempotent methods**, converting transient failures into duplicate writes.
- **Buffering entire responses at L7 by default** for large downloads, so a few slow clients consume all proxy memory. Stream instead.
- **Forgetting that L4 preserves the client IP with DSR but not with NAT**, and building a rate limiter on whichever happened to be true in staging.

## 16. Interview perspective

This distinction is one of the highest-signal, lowest-effort things you can get right. Most candidates draw "LB" and move on; naming the layer and justifying it immediately marks you out.

**How to use it well:**

- **Name the layer every time you draw a load balancer**, with one reason. "L7 here, because I need path routing between the API and the web fleet and HTTP health checks against `/readyz`."
- **State what information each layer has.** "L4 sees the 5-tuple and can peek at TLS SNI; L7 sees method, path, headers, cookies and the body — but only after decrypting."
- **Raise the HTTP/2 and gRPC trap unprompted.** "I wouldn't put gRPC behind a plain L4 Service — it balances connections, so one long-lived HTTP/2 connection pins every request to one backend. I'd use an L7 proxy or client-side balancing." This single observation signals production experience better than almost anything else in Phase 2.
- **Quantify the trade.** "L4 is roughly a million connections per second at under 100 microseconds; L7 is 50–150k requests per second at 1–5 ms. On a 20 ms request that's a 10 % tax for the entire feature set, which I'd pay. On a 2 ms internal RPC it's 100 %, which I wouldn't."
- **Draw the two-tier shape when volume is large.** "L4 at the edge to absorb connections and DDoS, an Envoy fleet behind it for routing and retries."
- **Be precise about TLS placement.** "Passthrough if the backend must validate client certificates; terminate at the edge for the HTTP tier; re-encrypt on the internal hop if we're under PCI."
- **Mention DSR when responses are large.** "For video segments I'd use L4 with direct server return — the LB carries only the 600-byte requests, not the 4 MB responses."
- **Map it to Kubernetes if the conversation goes there.** "Service is L4, Ingress is L7, and the ingress controller itself needs an L4 Service in front of it."

**Red flags interviewers watch for:**

- Drawing a load balancer and never saying which layer, or being unable to say when pressed.
- Claiming an L4 balancer can route by URL path.
- Proposing an L7 proxy for PostgreSQL, Redis or a custom binary protocol.
- Not knowing that a Kubernetes Service is L4 and what that means for gRPC.
- Assuming TLS termination at the edge is always fine, with no mention of mTLS or compliance.
- Believing L7 is strictly better, with no awareness of the throughput or latency cost.
- Being unable to explain why an L4 balancer cannot retry a failed request.
- No mention of how the backend learns the client IP.

## 17. Knowledge check

Answer these before reading on. Write your answers down — even one line each.

**Conceptual**

1. An L4 load balancer can route `api.example.com` and `admin.example.com` to different backend pools without ever decrypting traffic. Explain exactly how this is possible, which field it uses, why this does *not* make it an L7 balancer, and one upcoming change that removes this capability.
2. Explain precisely why an L4 load balancer cannot retry a failed request on a different backend, while an L7 load balancer can — and state the one condition under which the L7 balancer still must not do so.
3. A team puts a 20-replica gRPC service behind a Kubernetes `Service` of type `ClusterIP` and finds that two replicas run at 90 % CPU while eighteen sit near idle. Explain the mechanism causing this, why scaling the Deployment to 40 replicas will not help, and give two structurally different fixes.

**Practical scenario**

4. BookNook must add a new partner API with these requirements: partners authenticate with **client certificates (mutual TLS)**; requests must be routed by URL path to three different internal services; each partner has a different rate limit identified by the certificate's subject; and peak volume is 40,000 RPS over roughly 200 long-lived HTTP/2 connections. Design the edge for this. State (a) which layer(s) you use and in what arrangement, (b) where TLS terminates and who validates the client certificate, (c) how the per-partner rate limit is implemented and what identity it keys on, and (d) one requirement that would make you change the design entirely.

**Calculation**

5. A service receives 250,000 RPS with 8 KB average responses and 700-byte average requests, over 1,500,000 concurrent connections with an average lifetime of 400 seconds. Assume: L7 instance capacity 50,000 RPS with TLS and 32 KB of buffer memory per connection, on instances with 64 GB of RAM costing $589/month; L4 instance capacity 1,200,000 new connections/sec at ~$120/month; added latency L4 = 0.08 ms, L7 = 2.5 ms; backend application p50 latency 15 ms.
   (a) How many L7 instances are required by RPS alone, and how many by connection memory? Which is the binding constraint, and by how much?
   (b) What is the new-connection rate, and what fraction of one L4 instance does it consume?
   (c) Compare the monthly cost of an L7-only design (sized by the binding constraint, N+2 across 3 AZs) against a two-tier L4+L7 design, and state what the difference buys.
   (d) What percentage does each layer add to end-to-end p50 latency?
   (e) If DSR were used at L4, how much response bandwidth would be removed from the load-balancing tier, and why can that benefit not be combined with the L7 features this service needs? Show every step and state every assumption you add.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 23.

---

**Progress checklist**

Phase 1 — Internet and System-Design Foundations: all 17 topics `[x]` complete.

Phase 2 — Scaling
- [x] 1. Vertical scaling
- [x] 2. Horizontal scaling
- [x] 3. Stateless application servers
- [x] 4. Load balancers
- [x] 5. Layer 4 versus Layer 7 load balancing
- [ ] 6. Load-balancing strategies
- [ ] 7. Health checks and failover
- [ ] 8. Reverse proxies and API gateways
- [ ] 9. Caching fundamentals
- [ ] 10. Browser, application, database, distributed caches
- [ ] 11. Cache-aside, read-through, write-through, write-behind
- [ ] 12. Cache invalidation and TTL
- [ ] 13. Cache stampedes, hot keys, penetration, eviction
- [ ] 14. CDNs and edge caching
- [ ] 15. Database replication
- [ ] 16. Synchronous versus asynchronous replication
- [ ] 17. Replication lag and read-after-write consistency
- [ ] 18. Database partitioning and sharding
- [ ] 19. Range, hash, directory, geographic sharding
- [ ] 20. Resharding, hot partitions, cross-shard queries
- [ ] 21. Consistent hashing and virtual nodes
- [ ] 22. Autoscaling and multi-region architecture

Phases 3–5: not started.

**NEXT LESSON: Phase 2, Topic 6 — Load-balancing strategies**

%%APPEND%%
