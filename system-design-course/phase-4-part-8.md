# Lesson 90 — High Availability and Disaster Recovery

*Phase 4, Topic 27*

## 1. Simple definition

Two disciplines that are constantly confused because they both answer "how do we keep running when something breaks". They differ in **scope** and in **what the user experiences**.

- **High availability (HA)**: survive the failure of an **individual component** — a process, a node, a disk, an availability zone — **automatically**, in **seconds to minutes**, with **no data loss** and ideally **no user-visible interruption**. HA is a property of the running system's design.
- **Disaster recovery (DR)**: survive the loss of an entire **site, region or system** — including data — usually with **some** interruption, sometimes **manual** initiation, in **minutes to hours**. DR is a property of your *preparedness*, and it is measured, not designed (L92).

```
  HA  = "a server died at 03:14 and nobody noticed"
  DR  = "the region went away at 03:14 and we were serving from Frankfurt by 03:41"
```

Terms defined before use:

- **Fault domain**: a set of components that can fail *together*. The hierarchy is hardware → host → rack → AZ → region → provider → and, crucially, **software**: deploy, config, dependency, human.
- **Single point of failure (SPOF)**: any component whose failure takes the service down. HA is the practice of finding and removing them.
- **Blast radius**: how much fails when a given thing fails.
- **N+1 / N+2**: capacity sized for N units plus 1 (or 2) spare, so failures are absorbed without degradation.
- **Correlated failure**: two "redundant" components failing for the same reason — the failure mode that defeats naive redundancy (§4.4).
- **Failover**: the transition to a redundant component, including detection, decision, execution and warm-up (L79 §4.5).
- **Warm-up**: the time a promoted component needs before it can serve (index load, cache fill, connection pools).
- **Game day**: a planned exercise in which a failure is injected deliberately, so the response is tested rather than assumed.

The one sentence to carry out of this lesson: **availability is limited by your weakest *shared* dependency and your slowest *detected* failure — not by how many replicas you have — so the work is finding single points of failure and removing correlated ones, then proving it by breaking things on purpose.**

## 2. Why it exists

Because at scale, failure is continuous, and because the assumption "we have redundancy" is almost always wrong in a way nobody has tested.

**Failure is a rate, not an event.** BookNook's estate at modest scale:

```
  120 application instances, 1,200 disks across 40 hosts, 3 AZs

  annualised failure rates (typical hardware):
      disk           2 %/yr   → 1,200 × 0.02 = 24 disk failures/yr  = 1 per 2 weeks
      host           4 %/yr   → 40 × 0.04    = 1.6 host failures/yr
      network link   1 %/mo   → 3 AZs        = 36 link events/yr
      zone-level     0.1 %/yr → 3 AZs        = 1 event per 3.3 years
      deploy         41/day   → deploys are the most frequent "failure
                                 injection" in the system, by three orders
                                 of magnitude

  ⇒ "we had an incident" is not bad luck; with 41 deploys a day, change is
    the dominant source of unavailability, and the design must assume that
    components are failing continuously.
```

**And redundancy is usually correlated.** BookNook's own catalogue of "we thought we were safe":

| Claimed redundancy | Why it was not |
|---|---|
| 3 replicas across 3 AZs | All three in the same **region**, so a regional control-plane outage took all three |
| 2 NAT gateways | Both in one AZ; the AZ failure removed outbound internet for everything |
| Active-passive database | The passive replica was promoted by a runbook nobody had run in 14 months — it took 3 h 47 m, not the documented 30 min (L92) |
| Multi-AZ load balancers | Both health checks used the same DNS name, which was itself the SPOF |
| Blue/green deploys | Both colours read the same cache cluster; a cache flush took down both |

**HA and DR exist as separate disciplines because they are different problems with different prices**: HA is bought with architecture and automation (cheap, continuous); DR is bought with infrastructure duplication and *rehearsal* (expensive, rare). A team that has HA but no DR survives Tuesday's disk failure and not the region outage that happens once every three years — and the second is the one that makes the news.

## 3. Real-world analogy

**A ship.**

**High availability is watertight compartments.** The hull is divided into sections with sealed doors. A breach floods one compartment; the ship trims, loses some speed, and **keeps sailing** — automatically, immediately, with no decision by anyone. That is exactly HA: a component fails, redundancy absorbs it, users notice at most a slight degradation. Ship design even quantifies it — "two-compartment standard" is the naval version of **N+1**.

**Disaster recovery is the lifeboats and the muster drill.** They are for the failure too large for compartments: the ship is lost. The lifeboats are useless if nobody knows how to launch them, if the davits are seized, or if the drill has never been run. **A lifeboat inventory is not a lifeboat plan**, and this is precisely the difference between "we have backups" and "we can restore in 40 minutes" (L92). The lifeboat also has an **RTO** (how long until everyone is in the water and picked up) and an **RPO** (what was left on the ship).

The analogy carries the two lessons that matter:

**One shared flaw sinks everything.** A ship with beautiful compartments and a single hull breach *below* the watertight deck — a shared fuel line, a shared electrical bus, a single propeller shaft — has compartments that do not save it. That is the **correlated failure** (§4.4): redundancy that shares a failure domain is decoration. Software's equivalents are the shared region, the shared database, the shared config push, and the shared deploy.

**And drills are the only proof.** A crew that has never closed a watertight door will not close it correctly at 03:00 in a storm. Nobody accepts "the doors are designed to close" as evidence of seaworthiness — yet teams routinely accept "we have a failover process" as evidence of availability, having never run it.

## 4. Detailed technical explanation

### 4.1 The fault-domain hierarchy, including the embarrassing ones

```
  ┌─ provider outage ────────────────────────────────────┐  years
  │ ┌─ region ─────────────────────────────────────────┐ │  ~3 years
  │ │ ┌─ availability zone ──────────────────────────┐ │ │  ~1/year
  │ │ │ ┌─ rack / host ────────────────────────────┐ │ │ │  months
  │ │ │ │ ┌─ process / container ────────────────┐ │ │ │ │  days
  │ │ │ │ │ ┌─ SOFTWARE fault domains ─────────┐ │ │ │ │ │
  │ │ │ │ │ │  deploy      (41/day)  ←── most  │ │ │ │ │ │
  │ │ │ │ │ │  config push (~30/day)   frequent │ │ │ │ │ │
  │ │ │ │ │ │  dependency (external)            │ │ │ │ │ │
  │ │ │ │ │ │  DNS / certificate / quota        │ │ │ │ │ │
  │ │ │ │ │ │  human error                      │ │ │ │ │ │
  │ │ │ │ │ └──────────────────────────────────┘ │ │ │ │ │
  │ │ │ │ └──────────────────────────────────────┘ │ │ │ │
  │ │ │ └──────────────────────────────────────────┘ │ │ │
  │ │ └──────────────────────────────────────────────┘ │ │
  │ └──────────────────────────────────────────────────┘ │
  └──────────────────────────────────────────────────────┘

  ⇒ the top of the hierarchy costs the most to defend against (a second
    region) and fails the rarest. The bottom is nearly free and fails
    CONSTANTLY. This is why HA work (bottom) comes before DR work (top):
    it buys more availability per pound, and it is the foundation DR needs.
```

### 4.2 What redundancy actually buys, and the arithmetic

```
  INDEPENDENT failures, need k of n:
      n = 2, k = 1: A = 1 − (1−0.999)² = 0.999999      (from 99.9 %)
      n = 3, k = 2: A = 1 − [3(0.001)² − 2(0.001)³] ≈ 0.999997
      n = 3, k = 1: A ≈ 0.999999999
  ⇒ redundancy is powerful — IF failures are independent.

  CORRELATED failures destroy the arithmetic:
      if a shared dependency has availability 0.99, then no amount of
      application redundancy exceeds 99 %:
          A(system) = A(shared) × A(redundant part) ≤ 0.99
  ⇒ the useful formula is not "how many replicas do I have" but
        A(system) = min over SHARED dependencies (A) × A(redundancy)
    which is why a single shared database, DNS zone, certificate, NAT
    gateway or config push caps your availability regardless of replica count.
```

### 4.3 The failover budget — where the time actually goes

HA is often described as "instant", and it is not. The budget has five parts (extending L79 §4.5):

```
  DETECTION   how long before something is known to be dead
              (health check interval × failure threshold, or a TCP timeout)
  DECISION    the failover logic; must be quorum-based or fenced to avoid
              split-brain (L79 §4.4, L80 §4.2)
  EXECUTION   promote the replica, reconfigure routing, reroute traffic
  WARM-UP     the new instance becomes USEFUL: index loaded, cache filled,
              pool warmed, JIT compiled
  VERIFICATION the system confirms it is healthy (and automation often
              forgets this, so a broken failover looks like an outage)

  BookNook's measured values for a database failover:
      detection 30 s (health check 10 s × 3) · decision 2 s (lease-based)
      execution 18 s (promotion + connection re-resolution)
      warm-up 0 s (hot standby) · verification 25 s (automated checks)
      ⇒ 75 seconds, documented as "instant". Users saw 75 s of errors.
```

### 4.4 The correlated-failure catalogue

The important part of this lesson. Each entry is a real way redundancy fails:

| Shared thing | The failure | Prevention |
|---|---|---|
| **Region** | All "AZ-redundant" replicas lost together | Multi-region (costly — L91) or accept the risk explicitly |
| **Deploy** | A bad build goes to 100 % in one rollout | Canary + progressive rollout (L87 §4.6) |
| **Config push** | A bad flag reaches every instance in 2 s | Rings, validation, one-click revert (L87 §7.2) |
| **Database** | Every stateless replica depends on one primary | Replicate it, or degrade reads from cache (L77 §4.4) |
| **DNS** | One zone/A record serves everything; TTL 3600 | Short TTLs on failover records, health-checked routing |
| **Certificate** | Expires at 02:00; every instance fails at once | Automated rotation + expiry alarms at 30/14/7 days |
| **Quota** | A shared cloud quota is exhausted by a retry storm | Per-service quotas, alarms at 70 % |
| **NAT / egress** | One NAT gateway in one AZ | Per-AZ NAT, or VPC endpoints |
| **Cache** | A flush empties the cache and the DB cannot absorb the load (L76 §2) | Gradual warm, request coalescing (L29) |
| **Human** | The same engineer approves every change | Four-eyes, automated gates, break-glass reviews |
| **Dependency** | A third party's outage is your outage | Fallbacks and degradation (L77 §4.4) |

### 4.5 Multi-AZ versus multi-region: the cost and latency decision

| | Multi-AZ | Multi-region (active-passive) | Multi-region (active-active) |
|---|---|---|---|
| Survives | AZ failure | Region failure | Region failure |
| Data | Synchronous replication, RPO 0 | Async replication, RPO minutes | Conflict resolution needed (L91) |
| Latency added | 1–2 ms (cross-AZ RTT) | 0 in normal operation | 0 in normal operation |
| Failover time | Seconds–minutes, automated | Minutes, often semi-automated | Near-zero (traffic shifts) |
| Extra cost | ~1.3–1.5× | ~2× | ~2× plus significant complexity |
| Failure probability | ~1/year | ~1 per 3 years | ~1 per 3 years |
| **Do it when** | **Always** | Regulated, or RTO minutes | Only with a genuine requirement |

The sequencing rule: **multi-AZ first (cheap, frequent failures), then multi-region (expensive, rare failures), then active-active (very expensive, needs conflict resolution).** Jumping to active-active to solve an AZ-failure problem is how teams spend 3 years on a project that a second AZ would have solved in a week.

### 4.6 The dependency chain caps everything

```
  BookNook checkout, availability of each critical dependency:
      our services (multi-AZ, redundant)        99.99 %
      PostgreSQL (multi-AZ)                     99.95 %
      Redis (multi-AZ)                          99.9  %
      payment gateway (external)                99.95 %
      email provider (external)                 99.9  %

  if all are on the critical path, with no fallbacks:
      A = 0.9999 × 0.9995 × 0.999 × 0.9995 × 0.999 = 0.99690  = 99.69 %
      ⇒ 2 h 14 m per month of unavailability, no matter how many replicas
        we add to our own stateless tier.
  with fallbacks: cached reads when Redis is down (−0.1 % impact), queued
  email (removes 99.9 % from the path), and a payment circuit breaker that
  degrades instead of failing:
      A ≈ 0.9999 × 0.9995 × 0.99995 = 0.99935 = 99.94 %
      ⇒ 25 minutes per month.
  ⇒ HA is a WEAKEST-LINK problem, and the links that matter are the shared
    external ones. That is why L77's degradation ladder is an availability
    technique, not a nicety.
```

### 4.7 Testing: untested HA and DR do not exist

```
  THE LADDER OF EVIDENCE, weakest to strongest:
    1. "We have a design."                      — no evidence
    2. "We have a runbook."                     — no evidence
    3. "We ran the runbook once, 14 months ago." — weak, and it has rotted
    4. "We run it quarterly, timed, with a rollback." — evidence
    5. "We break it automatically, continuously, in production." — chaos
       engineering, and the only way to be confident in HA

  WHAT TO TEST, in order of value per hour spent:
    1. RESTORE a backup into a fresh environment and time it (L92). Most
       teams discover their restore is 5× slower than documented.
    2. KILL a database primary, measure detection→verification, confirm
       the app reconnected without manual intervention.
    3. KILL an AZ (or a large fraction of nodes) during business hours.
    4. EXPIRE a certificate in staging; confirm rotation works.
    5. PUSH a bad config to one ring; confirm automatic rollback.
    6. FILL a disk; confirm alarms fire before the outage, not after.
```

## 5. Request / data flow

A zone failure, and what the user experiences at each stage.

```
 ── 03:14:00 · AZ-b becomes unreachable ────────────────────────────────────
  running: 60 instances spread 20/20/20 across AZ-a/b/c; RDS primary in AZ-a,
           standby in AZ-b; 2 NAT gateways (both in AZ-b); ElastiCache with
           replicas in AZ-a and AZ-c.

  03:14:00  AZ-b instances stop responding entirely (network partition)
  03:14:10  LB health checks: 3 failures × 10 s... at 03:14:20 the 20 AZ-b
            instances are marked unhealthy and removed          [DETECTION]
  03:14:20  traffic redistributes to 40 instances in AZ-a/c, each now at
            1.5× CPU. Latency p99 rises 210 ms → 640 ms              [LOAD]
  03:14:25  outbound calls start failing: both NAT gateways were in AZ-b
            ⇒ every instance in AZ-a/c cannot reach the payment gateway
            ⇒ CHECKOUT DOWN even though "the AZ failed and we were multi-AZ"
  03:19:00  on-call identifies egress as the problem (trace shows connection
            failures at the NAT boundary — thank you, L88)
  03:26:00  routes added via a NAT gateway in AZ-a (there was no spare, so
            one is created and its route tables updated)              [FIX]
  03:31:00  checkout recovering; p99 820 ms under 1.5× load               [VERIFY]
  03:44:00  back under the SLO
  ⇒ 30 minutes of degraded service and ~18 minutes of failed checkout, from
    a failure that the architecture "was designed to survive".

 ── WHAT SHOULD HAVE HAPPENED ──────────────────────────────────────────────
  with a NAT gateway per AZ: egress continues via AZ-a/c; users see only the
  p99 rise to 640 ms (still within the 500 ms latency SLO? no — 640 > 500, so
  a partial SLO breach, but no errors). Recovery: none needed.
  ⇒ the difference between 18 minutes of outage and zero was ONE piece of
    shared infrastructure in the wrong failure domain.
```

## 6. ASCII diagram

HA versus DR, drawn on an axis of scope and time:

```
  scope
   ▲
   │  ┌──────────────── DR ──────────────────┐
   │  │ whole region / provider / data loss  │
   │  │ minutes → days                       │
   │  │ cost: duplicate infrastructure       │
   │  │ proof: restore and failover DRILLS   │
   │  └──────────────────────────────────────┘
   │        ┌──────────── HA ─────────────────────┐
   │        │ one component: process, node, disk, │
   │        │ AZ; seconds → minutes; automatic    │
   │        │ cost: redundancy + automation       │
   │        │ proof: chaos / game days            │
   │        └─────────────────────────────────────┘
   └──────────────────────────────────────────────────► user impact

  THE WEAKEST LINK, which decides the whole system:
      A(system) ≈ min over SHARED dependencies × A(redundancy) × A(degradation)
      not the number of replicas (§4.6)

  THE CORRELATED-FAILURE PICTURE:
       ┌───────────────────────────────────────────────┐
       │ 3 replicas, 3 AZs, 1 region, 1 DNS zone,      │
       │ 1 database primary, 1 config push, 48 h TTL   │
       └───────────────────────────────────────────────┘
              ▲                                  ▲
              │ the thing you designed            │ the things that will
              │ for                               │ actually take you down
```

## 7. Detailed example

### 7.1 The audit that found 14 SPOFs

BookNook ran a structured SPOF audit before a compliance review, using a simple method: for each component, ask *"if this disappears, what happens, and who notices?"*

```
  found (14), the top 6 by severity:
    1. Both NAT gateways in one AZ                        → 18 min outage (§5)
    2. Single-region everything, documented as "DR-ready"  → no DR existed
    3. Redis cluster with no replica in a third AZ          → cache loss =
                                                              DB overload (L76)
    4. A hardcoded IP in a legacy service's config          → survived by luck
    5. Certificate renewal by calendar reminder only        → 9 domains, 1
                                                              missed in 2025
    6. Backup bucket in the same AWS account as the DB      → a compromised
                                                              account loses both
  cost to fix all 14: 9 engineer-weeks, £2,100/month of extra infrastructure
  measured effect over the following 12 months:
      incidents involving a SPOF: 7 (previous 12 months) → 1
      monthly availability: 99.61 % → 99.93 %
      ⇒ ~2 h 20 m/month of unavailability removed for £2,100/month. At
        £9/minute of checkout impact (L89 §9 step 4), that is ~£1,260/month
        of direct revenue plus two hours of engineering each month.
```

The method is worth more than the list: **enumerate components, ask "if this dies, what breaks, and what tells me?"** Every component with no answer and no alarm is a potential outage nobody will notice until customers do.

### 7.2 The DR plan that was a document

The DR plan said:

```
  RTO: 30 minutes. RPO: 5 minutes.
  Procedure: promote the replica in eu-central-1, update DNS, verify.
  Last tested: (blank)
```

The audit ran the first drill:

```
  measured:
    decision + authorisation to run it during business hours:  41 min
    promote replica (it had 6 months of untested lag and needed a full
      re-sync because it had fallen behind):                    94 min
    update DNS: TTL was 3600 s, so propagation took up to:      60 min
    verify + reconcile application state:                       32 min
    ─────────────────────────────────────────────────────────────────
    actual RTO: 3 h 47 m     (documented: 30 min)   ← 7.6×
    actual RPO: 4 h 20 m     (documented: 5 min)    ← the replica had been
                              lagging since a network change nobody noticed
  ⇒ the plan was not wrong; it was UNVERIFIED, and everything in it had rotted
    in the 14 months since it was written. This is the normal state of untested
    DR (Lesson 92 is entirely about this).
```

The three fixes, in order of value:

```
  1. AUTOMATE the failover, then TEST it monthly. The runbook was replaced by
     a script; the script is run in a drill every month and takes 11 minutes.
  2. MONITOR REPLICATION LAG as an RPO signal, with an alarm at the RPO
     threshold — the 4 h 20 m lag was visible in metrics the whole time.
  3. LOWER the DNS TTL on the failover records to 60 s (from 3600), which
     removed 59 of the 60 minutes of propagation.
  post-fix measured RTO: 14 minutes (from 3 h 47 m); measured RPO: 40 s.
```

## 8. Code or configuration example

```yaml
# ── Kubernetes: spread across zones, survive node loss ───────────────────
spec:
  replicas: 12
  template:
    spec:
      topologySpreadConstraints:
        - maxSkew: 1                    # even spread
          topologyKey: topology.kubernetes.io/zone
          whenUnsatisfiable: DoNotSchedule     # fail loudly rather than
          labelSelector: {…}                   # silently pile into one AZ
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: ScheduleAnyway     # prefer distinct hosts
      # Never schedule 2 replicas of the SAME instance on one host
      affinity:
        podAntiAffinity:
          preferredDuringSchedulingIgnoredDuringExecution:
            - weight: 100
              podAffinityTerm:
                topologyKey: kubernetes.io/hostname
                labelSelector: {…}
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata: { name: checkout }
spec:
  minAvailable: 80%          # voluntary disruptions (node drains, upgrades)
  selector: {…}              # must leave the service above its SLO floor

# ── Egress: a NAT gateway PER AZ. The §5 outage in one line. ─────────────
# (Terraform sketch)
# for_each = toset(["eu-west-1a", "eu-west-1b", "eu-west-1c"])
# nat_gateway per az + route table per az subnet  ⇒ an AZ loss removes 1/3 of
# egress capacity, not all of it.
```

```bash
# ── The drill scripts. An untested DR plan is a document, not a capability. ─

# 1. KILL A PRIMARY, measure every phase (quarterly, in business hours)
start=$(date +%s)
aws rds failover-db-cluster --db-cluster-identifier booknook-prod
until [ "$(aws rds describe-db-clusters --db-cluster-identifier booknook-prod \
        --query 'DBClusters[0].DBClusterMembers[?IsClusterWriter==`true`].DBInstanceIdentifier' \
        --output text)" != "$OLD_WRITER" ]; do sleep 2; done
echo "PROMOTED after $(( $(date +%s) - start ))s"
# then: assert the app reconnected (error rate < SLO within 2 min), and
# record the number in the DR register — the MEASURED RTO, not the designed one.

# 2. RESTORE A BACKUP and time it (monthly — the highest-value drill, L92)
start=$(date +%s)
aws rds restore-db-cluster-from-snapshot ... --db-cluster-identifier drill-restore
until [ "$(aws rds describe-db-clusters --db-cluster-identifier drill-restore \
        --query 'DBClusters[0].Status' --output text)" = "available" ]; do
  sleep 10; done
echo "RESTORE took $(( $(date +%s) - start ))s"
# then VERIFY: row counts, a checksum on a known table, and one real query.
# A restore that completes but returns wrong data is worse than no restore.

# 3. Assert the alarm fires BEFORE the outage (continuous)
#    disk > 75 % → ticket, > 85 % → page, > 95 % → imminent outage
```

```yaml
# ── Alarms that map to HA/DR capability, not to symptoms ────────────────
alerts:
  - alert: ReplicaLagExceedsRPO
    expr: pg_replication_lag_seconds > 60        # your stated RPO
    severity: page        # your RPO claim is currently false

  - alert: AZCapacityLoss
    expr: count(up{job="app"} == 0 by (az)) and
          count by (az) (up{job="app"}) < 2
    severity: page        # you are one AZ from a full outage

  - alert: CertificateExpiring
    expr: probe_ssl_earliest_cert_expiry - time() < 14 * 86400
    severity: page

  - alert: BackupAge
    expr: time() - backup_last_success_timestamp_seconds > 26 * 3600
    severity: page        # your RPO is now unbounded

  - alert: DRDrillOverdue
    expr: time() - dr_drill_last_success_timestamp_seconds > 90 * 86400
    severity: ticket      # the capability has not been proven in a quarter
```

## 9. Numerical example

BookNook: 3 AZs, one region, 40 hosts, 1,200 disks, 41 deploys/day.

**Step 1 — expected failures, which is why HA is continuous work.**

```
  disks:      1,200 × 2 %/yr            = 24/yr  = 1 every 2 weeks
  hosts:      40 × 4 %/yr               = 1.6/yr
  AZ events:  3 AZs × 0.33/yr           = 1/yr
  deploys:    41/day × 365              = 14,965/yr ← 600× all hardware events
  ⇒ every ~2 weeks something in the infrastructure fails, and every ~21
    minutes a change is applied. HA must be AUTOMATIC, because a human
    cannot respond 24 times a year at 02:00 without eventually not
    responding.
```

**Step 2 — the availability arithmetic, per layer.**

```
  hypotheses: single AZ only, 5 instances, A(instance) = 99.9 %
      A = 1 − (0.001)^5 ≈ 99.9999999 %  ← but ONLY against instance failure
      AZ failure: A = A(az) = 99.5 %     ⇒ 3.6 h/month
  multi-AZ, 3 AZs, 2 of 3 needed:
      A ≈ 99.98 %                        ⇒ 8.6 min/month
  + multi-region warm standby:
      A ≈ 99.99 %                        ⇒ 4.3 min/month
  + degradation/fallbacks on external dependencies (L77):
      A ≈ 99.95 %... see §4.6 — the number is capped by the SHARED
      dependencies, so improving the application tier beyond 99.99 % has no
      effect on the user-visible number.
  ⇒ after ~99.9 %, the binding constraint moves to shared dependencies,
    and money spent on application redundancy is wasted.
```

**Step 3 — the cost of each step, and what it buys.**

```
  step                                  annual cost    availability gain
  ──────────────────────────────────────────────────────────────────────
  single AZ → multi-AZ                  +£48,000       99.5 → 99.98 %
  multi-AZ → warm standby region        +£210,000      99.98 → 99.99 %
  warm standby → active-active          +£390,000      99.99 → 99.995 %
  (plus engineering: 2 wks / 10 wks / 9 months)
  ⇒ minutes saved per month:  3,600 → 8.6 → 4.3 → 2.2
  ⇒ cost per minute-of-downtime-avoided per month:
       multi-AZ:        £48,000/12 / (3,600−8.6) ≈ £1.12 per minute saved
       warm standby:    £210,000/12 / (8.6−4.3)  ≈ £4,070 per minute saved
       active-active:   £390,000/12 / (4.3−2.2)  ≈ £15,476 per minute saved
  ⇒ the cost curve is brutally non-linear (as L89 §9 step 4 showed). For a
    bookstore, multi-AZ is obviously right and active-active is obviously
    not; for a payments processor with a regulatory RTO of 2 minutes, the
    third row is mandatory and the arithmetic is irrelevant.
```

**Step 4 — the value of removing SPOFs, versus buying redundancy.**

```
  the 14 SPOF fixes (§7.1):
      cost: 9 engineer-weeks + £2,100/month
      measured: 99.61 % → 99.93 % ⇒ 2 h 20 m/month of unavailability removed
      ⇒ 140 minutes/month for £2,100 + labour
  compare with the warm-standby region: 4.3 minutes/month for £17,500/month
  ⇒ REMOVING SPOFS IS ~28× MORE COST-EFFECTIVE than buying a second region.
    This is the single most important economic fact in this lesson, and it
    is why the audit comes before the architecture.
```

**Step 5 — failover time versus user impact.**

```
  database failover: detection 30 s + decision 2 s + execution 18 s +
      warm-up 0 s + verification 25 s = 75 s
  at 400 rps with the app failing fast: ~30,000 failed requests
      (if the connection pool does not retry) or ~0 (if it does, with jitter)
  measured difference in BookNook: adding a retry-with-backoff in the
      connection layer (L65 §8.3) reduced user-visible errors during a
      failover from ~4,900 to ~40.
  ⇒ the failover time is a property of the infrastructure; the USER IMPACT is
    a property of the client's retry design. Both must be engineered.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **Multi-AZ** | Survives the most common real failure | 1.3–1.5× cost, 1–2 ms latency | **Always** |
| **Multi-region passive** | Survives region loss; RPO minutes | ~2× cost; failover is a project | Regulated data, or downtime > cost |
| **Multi-region active-active** | Near-zero RTO | ~2× cost + conflict resolution + 6–12 months | RTO in seconds is a hard requirement |
| **N+1 capacity** | Absorbs one failure with no degradation | +1 unit of idle capacity | Any critical tier |
| **N+2** | Absorbs two failures (e.g. an AZ *during* a deploy) | +2 units | Peak-critical, or when failures correlate |
| **Automated failover** | Seconds-to-minutes, no human | Automation must be tested, and can misfire | Anything with an RTO under 30 min |
| **Manual failover** | Human judgement; safer against split-brain | Detection + decision + execution = hours | Rare, high-stakes, with a drill culture |
| **Chaos/game days** | Finds the correlated failure before customers | Engineer time; occasional self-inflicted incidents | Once the basics are automated |
| **DR in a document** | Nothing | The illusion of preparedness (§7.2) | Never |

## 11. Failure scenarios

**11.1 — All NAT gateways in one AZ** (§5). An AZ loss removed *all* egress; checkout down 18 minutes. **Fix:** per-AZ egress, and treat any shared network appliance as a SPOF by default.

**11.2 — The DR plan that was never tested** (§7.2). Documented 30 min RTO / 5 min RPO; measured 3 h 47 m / 4 h 20 m. **Fix:** automate, drill monthly, alarm on replication lag, and record measured RTO/RPO rather than designed ones (L92).

**11.3 — Backups in the same account** (§7.1 item 6). A credential compromise would have deleted the database and all its backups. **Fix:** cross-account, cross-region, immutable (object-lock) backups; test the restore path from the *isolated* copy.

**11.4 — The cache flush cascade.** A Redis primary failure promoted a replica with an empty dataset; the DB received 40× load and saturated (L76 §2, L29 §11). **Fix:** cache warming, request coalescing, and a circuit breaker in front of the database.

**11.5 — Certificate expiry.** A certificate expired at 02:00; every instance failed at once regardless of AZ, node or replica count — total redundancy defeated by one shared artifact. **Fix:** automated rotation and 30/14/7-day alarms.

**11.6 — The failover that caused the outage.** Automatic failover promoted a database replica during a 20-second network blip; the old primary recovered and continued accepting writes; split-brain for 40 seconds (§L79 §7.2). **Fix:** failover decision via a lease/quorum, and fencing (L78 §4.5, L80 §4.2).

**11.7 — The shared quota.** A retry storm (L65 §8.3) exhausted a cloud API quota; every service using that API failed. **Fix:** per-service quotas, alarms at 70 %, retry budgets, and jitter.

**11.8 — Failover succeeded, failback did not.** After a regional failover, the team ran on the standby for four months because no one owned the failback; when it was finally attempted, it required a 6-hour downtime window and a schema reconciliation that took another day. **Fix:** plan failback as part of the failover procedure, with a target date, and keep replication bidirectional during the temporary state.

## 12. Alternatives

| Instead of | Consider | When |
|---|---|---|
| Multi-region | **Multi-AZ + better degradation** | The failure you actually see is AZ-local or dependency-local |
| Redundancy | **Removing the SPOF** | Usually 10–30× more cost-effective (§9 step 4) |
| Automated failover | **Manual, drilled failover** | High-stakes decisions where a wrong automatic promotion is worse |
| Warm standby | **Active-passive with a tested runbook** | RTO of an hour is acceptable |
| Chaos engineering | **Quarterly game days** | You need evidence, but not continuous |
| Own DR | **A managed multi-region service** | The provider's RTO/RPO beats yours and is cheaper |
| Full DR | **Graceful degradation only** (L77) | The requirement is "keep the important 20 % working" |

## 13. When to use it

- **Multi-AZ**: every production system, always. It is the highest value per pound.
- **SPOF audits**: before buying redundancy, and quarterly thereafter.
- **Automated failover with quorum or lease-based decisions**: anything with an RTO under 30 minutes.
- **Deployed fallbacks** for every external dependency on the critical path (§4.6).
- **Game days and drills**: at least quarterly, restoring from backup monthly.
- **Multi-region**: when a regulatory requirement, a contractual RTO, or the downtime arithmetic says so (§9 step 3).

## 14. When *not* to use it

- **Active-active before multi-AZ.** You will spend a year on conflict resolution for a problem a second AZ solves in a week.
- **Redundancy that shares a failure domain** — same AZ, same region, same account, same config push (§4.4).
- **Automated failover without fencing or quorum** — that is a split-brain generator (L79 §4.4).
- **A DR plan you have never executed** — it is not a capability (§7.2).
- **Multi-region for a system with a 5-minute SLO on a 30-minute tolerance** — the cost does not buy anything anyone can perceive.
- **Chaos engineering before monitoring exists** — you will inject a failure you cannot observe.

## 15. Common mistakes

1. **Counting replicas as availability** without finding shared dependencies (§4.2, §4.6).
2. **Redundancy inside a single failure domain** (§4.4).
3. **SPOFs left in networking** (NAT, DNS, load balancer certificates).
4. **DR plans that are documents, not rehearsed procedures** (§7.2).
5. **Quoting designed RTO/RPO instead of measured** (L92).
6. **Backups in the same account/region**, or unverified.
7. **Automatic failover without fencing/quorum** (§11.6).
8. **No warm-up consideration** in the failover budget (§4.3).
9. **No failback plan** (§11.8).
10. **Capacity sized for N, not N+1**, so a failure causes degradation into an outage (L76 §2).
11. **Cache treated as optional**, so its loss cascades to the database.
12. **Certificate and DNS expiry** unmonitored.
13. **Chaos engineering without observability** (L88) or without abort criteria.
14. **Assuming a managed service is multi-AZ** because it is managed — check the actual replication.

## 16. Interview perspective

**What is actually being tested.** Whether you can distinguish HA from DR, name the **correlated failures** that defeat naive redundancy, and prioritise by cost-effectiveness rather than by ambition. Strong candidates do the SPOF audit before proposing a second region, and quote measured rather than designed RTO. Weak candidates say "we run in three AZs" and consider availability solved.

**Answers that land well:**

- *"How do you make this highly available?"* — "First I'd find the shared dependencies, because they cap everything else: if all three AZs are in one region and every instance depends on one database primary and one NAT gateway, then replica count is irrelevant. The audit — for each component, what breaks if it dies and what tells me — found 14 single points of failure on our estate; fixing them moved monthly availability from 99.61 % to 99.93 % for about £2,100 a month. That is roughly 28× more cost-effective per minute of downtime than buying a second region."
- *"The plan says 30-minute RTO. Is that true?"* — "Only if we've measured it. Ours said 30 minutes; the first drill measured 3 hours 47 minutes — promotion took 94 minutes because the replica had silently fallen behind, DNS TTL added 60, verification 32. Now failover is a tested script at 14 minutes and we alarm on replication lag as an RPO signal, because the 4-hour RPO was visible in metrics the whole time and nobody was watching it."
- *"Active-active or active-passive?"* — "Depends on the RTO and whether the data can tolerate multi-writer. Active-active means conflict resolution on every entity that both regions write, and a doubled capacity requirement because each region must handle 100 % on failure. For us, warm standby gave an RTO of about 12 minutes at 38 % extra cost; active-active would have been 2× plus nine months of engineering for four more minutes."
- *"How do you know it works?"* — "We break it on purpose. Monthly restore-into-a-fresh-environment with the timer running, quarterly AZ and database failover drills during business hours, and a rule that a failover is only 'done' when verification passes — otherwise a broken failover looks exactly like an outage."

**Common interview tasks:** identify SPOFs in a described architecture; choose multi-AZ vs multi-region with cost arithmetic; design a failover with quorum safety; compute availability from a dependency chain; plan a game day; explain why replicas in one region do not make you region-resilient.

**Red flags interviewers watch for:**

- "Three AZs" as a complete availability answer, with one region.
- No concept of correlated failure.
- Automatic failover with no fencing or quorum.
- DR described as a document or a runbook that "exists".
- Designed RTO quoted as fact.
- No fallbacks for external dependencies.
- Buying a second region before removing obvious SPOFs.
- No numbers: no availability computation, no failover time, no cost.

## 17. Knowledge check

**Conceptual**

1. Distinguish high availability from disaster recovery by scope, trigger, time and cost. Explain why the sequencing rule is multi-AZ → multi-region → active-active, and what goes wrong when it is reversed.
2. Explain why availability is a weakest-link property, and compute the effect of shared dependencies on a system whose own tier is 99.99 %. Then describe the architectural change that raises the user-visible number without any additional redundancy.
3. Decompose the failover budget into five parts and explain which is most often forgotten. Then explain why the same failover time produces wildly different user impact depending on the *client's* behaviour.

**Practical scenario**

4. BookNook runs 60 instances across 3 AZs in one region, with a single-region PostgreSQL primary + standby, a Redis cluster in 2 AZs, 2 NAT gateways in the same AZ, DNS with a 3600 s TTL, and a documented DR plan (RTO 30 min, RPO 5 min) that has never been tested. Traffic is 400 rps, AOV £18, and the checkout SLO is 99.5 %. Perform the audit: list every SPOF with its blast radius and detection, rank the fixes by cost-effectiveness, give the measured-vs-designed RTO/RPO analysis you would present, and design a 90-day remediation plan with the drills, the alarms, and the arithmetic that justifies each pound spent.

**Calculation**

5. A system runs 5 instances across 3 AZs, each instance 99.9 % available, each AZ 99.5 % available. (a) Compute availability with a single AZ. (b) Compute it across 3 AZs needing any 2. (c) Compute it if a shared database at 99.95 % and a shared payment provider at 99.9 % are on the critical path with no fallbacks. (d) Recompute (c) with fallbacks that remove the payment provider from the critical path, and state which component now binds.

6. A database failover takes 75 seconds; traffic is 400 rps; the client retries with jittered backoff and 92 % of retries succeed. (a) Compute the requests affected during the failover with no retry. (b) Compute the user-visible failures with the retry configured. (c) Compute the error-budget consumption of the failover for a 99.5 % SLO over 28 days with 20 M valid requests. (d) If the failover happens 4 times a year, state whether it alone threatens the SLO, and what the dominant risk actually is.

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
  - [x] 22. Transactional outbox and change data capture (L85)
  - [x] 23. Monoliths, modular monoliths and microservices (L86)
  - [x] 24. Service discovery and configuration (L87)
  - [x] 25. Observability: logs, metrics, traces (L88)
  - [x] 26. SLIs, SLOs and error budgets (L89)
  - [x] **27. High availability and disaster recovery (L90) ← you are here**
  - [ ] 28. Active-passive versus active-active (L91)
  - [ ] 29. RTO and RPO (L92)

**NEXT LESSON: Phase 4, Topic 28 — Active-passive versus active-active**

---

---

# Lesson 91 — Active-Passive versus Active-Active

*Phase 4, Topic 28*

## 1. Simple definition

Two ways to arrange redundant capacity across sites.

**Active-passive**: one site serves all traffic; the other stands ready. On failure, traffic moves. The standby is **hot** (fully replicated, instantly promotable), **warm** (replicated but needs minutes of warm-up) or **cold** (infrastructure exists, data needs restoring). The four standard DR strategies — backup/restore, pilot light, warm standby, and multi-site active-passive — are points on this ladder.

**Active-active**: **both** sites serve traffic simultaneously, all the time. On failure, traffic simply leaves the failed site. It sounds strictly better, and it is not: it requires every piece of state to be either (a) owned by exactly one site at a time (**single-writer**), or (b) mergeable when two sites write concurrently (**conflict resolution**). That requirement, not the infrastructure, is what makes active-active expensive.

Terms defined before use:

- **RTO / RPO**: recovery time objective and recovery point objective — the two numbers that choose the topology (L92).
- **Hot / warm / cold standby**: how ready the passive side is, measured in warm-up time.
- **Single-writer**: each key has exactly one region that may write it, at any time. The cleanest way to do active-active.
- **Multi-writer**: more than one region may write the same data concurrently, which requires **conflict resolution** (LWW, version vectors, CRDTs) or **application-level merging**.
- **Conflict resolution**: the rules that decide what a value becomes when two regions wrote it independently.
- **Geo-routing**: sending each user to the nearest (or designated) region — DNS, anycast, or a global load balancer.
- **Failback**: returning traffic to the original topology after a failover — a second migration, with its own risk (§4.7).
- **Split-brain**: both sides believing they are authoritative, producing divergence (L79 §4.4).

The one sentence to carry out of this lesson: **active-active is not a redundancy setting — it is a data-model decision, because two regions that both write the same key must have an answer to "which value wins", and that answer must be designed before the first request arrives.**

## 2. Why it exists

Because the choice determines both your recovery time and the shape of your data model, and most teams make it implicitly.

BookNook's trigger was contractual: a new enterprise customer required an **RTO of 15 minutes and an RPO of 1 minute** for order data, with a second region.

```
  Option A — ACTIVE-PASSIVE (warm standby):
      primary eu-west-1 serves everything; eu-central-1 replicates async
      failover: promote replica, repoint DNS, warm caches
      RTO ≈ 12–15 min (measured 14 min after automation, L90 §7.2)
      RPO ≈ 40 s (async replication lag, p99)
      extra cost: +38 % (passive infrastructure at 30 % scale, plus
                  cross-region data transfer)
      engineering: 6 weeks

  Option B — ACTIVE-ACTIVE:
      both regions serve traffic; each handles ~50 % (or by geo-proximity)
      RTO ≈ 0 (traffic leaves the failed region)
      RPO: depends entirely on the data model —
          · inventory counters: two writers → conflicts → needs a merge rule
          · orders: single-writer per order (home region) → RPO 0 locally,
            but cross-region reads may be stale
          · sessions: multi-writer → last-write-wins is fine
      extra cost: +105 % (both regions at full capacity, since each must
                  handle 100 % on failure)
      engineering: 9 months, including the conflict-resolution work,
                   the geo-routing layer, and the testing matrix
```

Option A met the contractual requirement. Option B met it too — for **£390,000/year more** and **7.5 more months of engineering**, to improve RTO from 14 minutes to roughly 0, against a contract that asked for 15.

That comparison is the reason this lesson exists as a separate topic: **active-active is often justified on resilience grounds and actually justified only when the RTO requirement is measured in seconds, or when traffic routing itself provides a benefit** (geo-locality: lower latency for users, and no single region as a bottleneck). If neither is true, active-passive is the better engineering.

The other reason: active-active **does not fail gracefully when the data model has not been designed for it**. Two regions writing a stock counter will oversell (§7.2); two regions writing a customer's address will silently pick a winner and lose someone's edit. The infrastructure is the easy half.

## 3. Real-world analogy

**Two restaurants, versus one restaurant and a prepped second kitchen.**

**Active-passive** is one open restaurant with a second kitchen down the street: stocked, staffed, and ready, but dark. When the first kitchen floods, the team moves and reopens. The cost is the same rent and a standby team that is not producing; the benefit is that **there is exactly one till, one recipe book, and no ambiguity about who is serving**. Reopening takes time — you must light the kitchen, restock the pass, and brief the staff: that is **warm-up**, and it is the dominant part of the RTO.

**Active-active** is two restaurants, both open, both serving. Now consider where the trouble actually is — and it is not the buildings:

- **One menu, two kitchens.** If both kitchens can change the specials board, they will disagree; one board must be authoritative, or you need a rule for merging them (§4.4).
- **One customer, two restaurants.** A regular who books at branch A must not be told at branch B that they have no booking. Cross-branch data must be readable everywhere, which means **replication**, which means **lag**, which means the answer at branch B may be a few seconds old. That is the RPO, expressed in a customer complaint.
- **Stock.** Two kitchens both promise the last portion of the fish. Now you need either a rule that one kitchen owns the fish (single-writer), or a rule for what happens when both sold it (conflict resolution, and an apology).
- **Failure of the *link* between them.** If the phone line between branches fails, both keep serving, and now they **cannot see each other's bookings at all** — so they must decide, in advance, whether to keep taking bookings (risking double-booking) or to refuse (losing revenue). That decision is the "partition behaviour" question, and it is the same one CAP poses (L80).

The analogy's punchline is the one teams miss: **two restaurants is not twice the resilience for twice the cost — it is twice the operational complexity, plus a rulebook for every way they can disagree.** Active-passive has one decision (when to move), made once, with all the information available. Active-active has a decision for every datum, made continuously, with incomplete information.

## 4. Detailed technical explanation

### 4.1 The ladder, with numbers

| Strategy | What exists at the DR site | RTO | RPO | Relative cost |
|---|---|---|---|---|
| **Backup & restore** | Backups only | 4–24 h | 1–24 h | 1.0× |
| **Pilot light** | Data replicated; core services pre-provisioned, scaled to zero | 1–4 h | 1–15 min | 1.15× |
| **Warm standby** | Full stack running at reduced scale, data replicated | 10–60 min | seconds–minutes | 1.4× |
| **Multi-site active-passive** (hot standby) | Full stack at full scale, idle | 1–10 min | seconds | ~2× |
| **Active-active (single-writer per key)** | Full stack, both serving | ~0–30 s | per-key 0; cross-region eventual | ~2× + engineering |
| **Active-active (multi-writer)** | Full stack, both writing all data | ~0 | **depends on conflict resolution** | ~2× + large engineering |

The row that surprises people is the last: **multi-writer active-active does not have a clean RPO**, because "how much data did we lose" is replaced by "how did we merge what both regions wrote, and was that merge correct?" — a question with a business answer, not an infrastructure one.

### 4.2 How traffic actually moves

```
  GEO-DNS (Route 53 latency/failover records, or a global accelerator)
      + no extra hop, cheapest, works for any stack
      − TTL-bound propagation (30–300 s minimum in practice), and clients
        with cached DNS ignore your failover
      − health-check granularity is coarse
  ANYCAST + BGP
      + fails over in seconds, at the network layer, no DNS involvement
      − requires provider support; connections break on reroute
  GLOBAL LOAD BALANCER / GATEWAY (with per-region backends)
      + health-based routing, per-request decisions, drains properly
      − an extra hop, and a global component that is itself critical
  APPLICATION-LEVEL ROUTING (client picks a region from a config service)
      + fine control, per-tenant routing possible
      − every client implements it; the config service is a SPOF
```

**The DNS TTL trap deserves its own paragraph**, because it is the most common cause of "our RTO was 15 minutes but it took 75":

```
  You cannot fail over faster than the maximum of:
      DNS TTL × (client caching behaviour)   +  connection drain time
  BookNook's measured: TTL 3600 s → users took up to 60 minutes to move,
      even though the infrastructure was ready in 14. (§90 §7.2.)
  Practical minimum: TTL 60 s on failover records, accepting that you keep
      some records short-TTL permanently (a small DNS-query cost) — or use
      an anycast/global-LB design where the TTL is irrelevant.
  ⇒ THE RTO OF A DNS-BASED FAILOVER IS YOUR DNS TTL, not your automation.
```

### 4.3 Active-passive, done properly

```
  primary (eu-west-1)                  standby (eu-central-1)
  ┌────────────────────┐               ┌────────────────────┐
  │ app: 60 instances  │  async repl   │ app: 12 instances  │ ← warm: scaled
  │ db: primary        │ ────────────► │ db: read replica   │   down but warm
  │ cache: warm, full  │               │ cache: cold/empty  │
  │ 100 % traffic      │               │ 0 % traffic        │
  └────────────────────┘               └────────────────────┘
        ▲ failover: promote replica (18 s) → scale app (90 s) → warm cache
        │ (a measured 6 min for a 4M-key cache) → repoint DNS (TTL 60 s)
        │ → verify (25 s)  ⇒ measured RTO 14 min for our stack
        └ ⇒ the warm-up steps DOMINATE; the promotion is not the problem.
```

Two design rules for the passive side, both learned from failed failovers: **keep it warm enough to be useful** (a cold cache needing 6 minutes to fill is part of your RTO), and **exercise the path continuously** — replication must be monitored as an *RPO* signal (L90 §8), not as an infrastructure metric.

### 4.4 Active-active: the three data models, in order of tractability

**Model A — single-writer per key (the recommended default).**

```
  Every entity has a HOME region. Writes for that entity go to its home.
  Reads can happen anywhere (with bounded staleness) or be routed home.

      order 90210  → home = eu-west-1   (hash of order_id)
      order 90211  → home = eu-central-1

  on region failure: entities homed in the failed region become READ-ONLY
      (or unavailable) until their home is restored or reassigned.
  ⇒ conflict-free by construction: only one writer exists per key, ever.
  ⇒ RTO = the time to re-home affected entities (which is a *decision* —
    usually "wait for the region", because re-homing is a migration).
  ⇒ this is what most "active-active" systems actually are, and it is
    honest: it fails over for *traffic*, not for *data ownership*.
```

**Model B — multi-writer with conflict-free data types (CRDTs).**

```
  counters (add-only), sets (union), registers (LWW), sequences.
  merges are deterministic and commutative → any ordering of operations
      produces the same result, so concurrent writes never need negotiation.
  ⇒ usable for: view counts, presence, "recently viewed" lists, rate limits
    (approximate), inventory *if* modelled as reservations, not counters.
  ⇒ NOT usable for: balances, stock counts, unique constraints, anything
    where "the answer must be exactly right".
```

**Model C — multi-writer with application-level merging.**

```
  both regions accept writes; a conflict is detected later and merged,
  sometimes requiring a human (a customer-service decision).
  ⇒ appropriate when the business genuinely wants concurrent edits and has
    a merge UI (document collaboration, support tickets, CRM records).
  ⇒ inappropriate for money and inventory, where the merge is "un-sell the
    thing you sold twice".
```

### 4.5 Conflict resolution rules, and why LWW is a data-loss mechanism

| Rule | How | Failure |
|---|---|---|
| **Last-write-wins by timestamp** | Newest timestamp wins | Silent data loss, and it depends on clock sync (L66 §4.3) — with skewed clocks, **the older edit can win** |
| **Last-write-wins by version** | Highest version wins | Still loses the other write, but at least deterministically |
| **Version vectors** | Detect true concurrency; surface both versions | Requires a resolution step (app or human); correct |
| **CRDTs** | Merge is defined by the data type | Only works for specific shapes (§4.4 model B) |
| **Home-region (single-writer)** | No conflict is possible | Requires routing writes to the home |

BookNook's experiment with LWW on a customer-address field: **412 addresses were silently reverted** over three months, because the region whose clock was 900 ms behind won the tie-break with an older value. The fix was home-region routing — one line of routing logic instead of a conflict policy.

### 4.6 Partitions between the sites: the decision you must make in advance

```
  When region A cannot talk to region B, each side must choose:
      1. KEEP SERVING both sides → on reconnection, divergence must be merged
         (or accepted). Requires multi-writer or home-region data.
      2. STOP serving the minority side → availability loss, but no divergence
         (this is what consensus-based systems do — L81).
      3. SERVE READS ONLY on the isolated side → degrades gracefully, no
         divergence, and writes queue or fail (L77 §4.4).
  ⇒ option 2 is correct for money and inventory. Option 1 is correct for
    carts and "likes". Option 3 is a good compromise for most read-heavy
    systems, and it is what BookNook chose.
  ⚠ NONE of these is a default. The system does one of them whether or not
    anybody decided — usually option 1, by accident, with no merge.
```

### 4.7 Failback: the half nobody plans

```
  Failover is a rehearsal of a rare event. FAILBACK is a second migration,
  performed while everybody is tired and the incident review is open.

  BookNook's failback (after 4 days on standby): 6-hour window,
      3 h of schema reconciliation (the standby had accepted writes that the
      primary had not), and a second overnight verification.
  ⇒ Rules: (a) keep replication bidirectional after failover, or (b) declare
    the new region the permanent home and only fail back deliberately, months
    later, as a planned migration with its own project.
  ⇒ What you must NEVER do is fail back ad hoc, mid-week, because the primary
    "looks healthy again".
```

## 5. Request / data flow

A region failure, in both topologies, with the numbers that differ.

```
 ── ACTIVE-PASSIVE (warm standby, RTO target 15 min) ───────────────────────
 03:14:00  eu-west-1 loses network connectivity (region-level)
 03:14:30  external health checks (3 × 10 s) mark the region unhealthy
 03:15:00  on-call is paged; decides to fail over (authorisation pre-granted
           for this scenario)                                      [DECISION 60 s]
 03:15:10  promote replica in eu-central-1                          [18 s]
 03:15:30  scale app tier from 12 → 60 instances                    [90 s]
 03:17:00  caches warming (4M keys, 11k keys/s)                     [~6 min]
 03:20:00  DNS failover records updated (TTL 60 s) → clients move   [≤60 s]
 03:21:00  verification suite passes (order creation, payment auth,
           catalogue read)                                         [25 s]
 03:21:30  serving at full rate; p99 640 ms (cold caches)
 03:29:00  p99 back under 500 ms; "TOTAL OUTAGE" duration 7 min 30 s
 ⇒ measured RTO (failure → serving): 7 min 30 s; full recovery: 15 min.
 ⇒ RPO: the replica's lag at 03:14:00 was 38 s ⇒ at most 38 s of writes lost.

 ── ACTIVE-ACTIVE (single-writer per key) ──────────────────────────────────
 03:14:00  eu-west-1 loses connectivity
 03:14:00  geo-routing detects failures in ~10 s and shifts new requests to
           eu-central-1 — RTO ≈ 10–30 s for NEW requests.
 03:14:10  eu-central-1 is already warm: full cache, warm pools, no scaling
           step needed (it serves 50 % normally)  ⇒ no warm-up phase.
 03:14:30  but: entities HOMED in eu-west-1 are now unavailable for writes
           (orders created there, inventory rows, that region's sessions).
           Reads may be served stale from a replica — 38 s behind.
           ⇒ writes to those entities FAIL FAST (or queue) until the region
             returns or is re-homed: an unbounded "RTO" for that subset.
 03:15:00  re-homing decision: migrate 210,000 affected entities to
           eu-central-1 (measured: 11 minutes, and it requires consensus on
           ownership to avoid two homes — L79 §4.4).
 03:26:00  all entities writable; total degradation 12 minutes — but the
           SITE never went down, and 50 % of users noticed nothing at all.
 ⇒ the honest comparison: active-active gave a better p50 experience and a
   comparable tail experience, for 2× cost and 9 months of work.
```

## 6. ASCII diagram

The ladder, plotted on RTO against cost:

```
  RTO
   24 h ┤ ● backup & restore                                    cost 1.0×
    4 h ┤    ● pilot light                                      1.15×
   30 m ┤        ● warm standby                                1.4×
    5 m ┤              ● hot standby (active-passive)           2.0×
   30 s ┤                    ● active-active, single-writer     2.0× + eng
    0 s ┤                          ● active-active, multi-writer 2.0× + lots
        └────┴────┴────┴────┴────┴────┴──────────────────────────────────►
                                                     complexity / cost

  ⇒ the curve is exponential. Each step buys fewer minutes for more money.
    Stop where the RTO requirement, not the ambition, says to stop.

  ACTIVE-ACTIVE with home regions, drawn:

                    ┌── geo router ──┐
                    ▼                ▼
          ┌──────────────────┐  ┌──────────────────┐
          │ eu-west-1        │  │ eu-central-1     │
          │ serves 50 %      │  │ serves 50 %      │
          │                  │  │                  │
          │ HOME for         │  │ HOME for         │
          │  orders ████     │  │  orders ████     │  ← disjoint sets:
          │  sku   ████      │  │  sku   ████      │    no key has two
          │  users ████      │  │  users ████      │    writers, ever
          └────────┬─────────┘  └─────────┬────────┘
                   │   async replication  │
                   └─────────►◄───────────┘
                        (for reads + DR)
  ⚠ when a region dies, its HOME data is unwritable until it returns or is
    re-homed. That is the cost of conflict-freedom, and it must be stated
    in the design — it is not a detail to discover during the incident.
```

## 7. Detailed example

### 7.1 Why BookNook chose warm standby, with the full arithmetic

| Criterion | Active-passive (warm) | Active-active |
|---|---|---|
| RTO (measured/expected) | 14 min | ~30 s for traffic; 12 min for re-homed data |
| RPO | 38 s | 38 s for reads; 0 for home-region writes |
| Contractual requirement | RTO ≤ 15 min, RPO ≤ 1 min | same |
| **Meets requirement?** | **Yes** (14 ≤ 15, 38 s ≤ 60 s) | Yes, with a caveat on re-homing |
| Cost/year | £210,000 | £390,000 |
| Engineering | 6 weeks | 9 months |
| Conflict resolution needed | none | home-region routing + re-homing tooling |
| Latency benefit | none | 40 ms better for APAC users (unmeasured benefit: small) |
| Risk of a *wrong* merge | zero | moderate (LWW on addresses lost 412 edits in the trial) |
| **Decision** | **chosen** | deferred until RTO ≤ 1 min is required |

The engineering judgement in one sentence, and the one to reuse: **the contract asked for 15 minutes; we could deliver 14 with six weeks of work instead of nine months, and the extra nine months bought nothing the customer had asked for.**

### 7.2 The active-active trial that oversold stock

The team ran a 7-day active-active trial on the *inventory* path in staging, with both regions accepting writes and a CRDT counter for available stock:

```
  model: inventory as a G-counter (increments on restock) + P-counter
         (decrements on sale), merged by summing. Mathematically clean.
  observed over 20,000 simulated concurrent purchases with 100 ms of
  inter-region latency:
      both regions read "5 left" → both sold 5 → merged count = 0
      but 10 items were sold.
  ⇒ the CRDT merged the *counts* correctly and the *inventory* incorrectly:
    a counter is conflict-free, but "do not sell the last one" is not a
    counter property — it is an invariant, and invariants need a single
    writer (L80 §4.2: W > N/2 for writers).
  fix: inventory became HOME-REGION PER SKU (model A), with the last-unit
    case explicitly routed to the home region. Slower for the 3 % of SKUs
    that are contested, correct for all of them.
  ⇒ THE GENERAL LESSON: conflict-free data types solve convergence, not
    invariants. Anything with an invariant ("never below zero", "unique
    email", "balance ≥ 0") needs a single writer somewhere.
```

## 8. Code or configuration example

```hcl
# ── Geo-routing: latency-based with health checks and a failover target ──
resource "aws_route53_record" "app" {
  zone_id = var.zone
  name    = "www.booknook.example"
  type    = "A"

  set_identifier = "eu-west-1"
  latency_routing_policy { region = "eu-west-1" }
  health_check_id = aws_route53_health_check.eu_west.id   # MUST be set,
  alias {                                                 # or failover does
    name                   = var.alb_eu_west_dns         # not happen
    zone_id                = var.alb_eu_west_zone
    evaluate_target_health = true
  }
}

resource "aws_route53_record" "app_standby" {
  zone_id        = var.zone
  name           = "www.booknook.example"
  type           = "A"
  set_identifier = "eu-central-1"
  latency_routing_policy { region = "eu-central-1" }
  failover_routing_policy { type = "SECONDARY" }
  alias { name = var.alb_eu_central_dns, zone_id = var.alb_eu_central_zone,
          evaluate_target_health = true }
}

resource "aws_route53_health_check" "eu_west" {
  fqdn              = "health.eu-west-1.booknook.internal"
  type              = "HTTPS"
  resource_path     = "/healthz/deep"      # must exercise the DB and the
  failure_threshold = 3                    # cache, or it passes while broken
  request_interval  = 10                   # ⇒ detection in ~30 s
}
# ⚠ TTL: alias records inherit the alias TTL; for a failover you cannot
#   control, use a 60 s CNAME instead, and accept the extra DNS queries.
```

```python
# ── Home-region routing: single-writer by construction (model A) ─────────
REGIONS = ["eu-west-1", "eu-central-1"]
HEALTH = HealthMap()          # from the geo-routing layer / a config service

def home_region(key: str) -> str:
    """Stable, deterministic: every service computes the same home."""
    h = int(hashlib.sha256(key.encode()).hexdigest(), 16)
    return REGIONS[h % len(REGIONS)]

def write_entity(key: str, payload):
    home = home_region(key)
    if not HEALTH.is_healthy(home):
        # A REGION FAILURE IS NOT A REASON TO WRITE ELSEWHERE. Writing to a
        # non-home region creates the two-writer problem we designed out.
        # The honest options are: fail fast, or queue until the home returns.
        raise RegionUnavailable(home, retry_after=queue_depth_estimate(home))
    return clients[home].write(key, payload, idempotency_key=key)

def read_entity(key: str, allow_stale=True):
    home = home_region(key)
    if not HEALTH.is_healthy(home) and allow_stale:
        # reads may be served from any replica, with a staleness bound stated
        # in the API contract (38 s p99, per our replication lag SLO — L90 §8)
        return replicas.any().read(key, max_staleness_s=60)
    return clients[home].read(key)
```

```bash
# ── The re-homing tool, which is the real "RTO" of active-active ────────
# Re-homing must be: explicit, audited, rate-limited, and CONSENSUS-AGREED
# (two regions must not both believe they own a key — L79 §4.4).
booknook-admin rehome \
  --from eu-west-1 --to eu-central-1 \
  --selector 'entities_homed_in=eu-west-1' \
  --max-rate 500/s \
  --require-lease booknook/rehome/eu-west-1   # a lease, with a fencing term
# before: assert the source region is unreachable or quiesced
# during: monitor write errors (must stay 0) and replication direction
# after:  verify with a full scan of home_region(key) == key's actual writer
```

## 9. Numerical example

BookNook: 400 rps, 20 M orders/28 days, AOV £18, checkout SLO 99.5 % (L89).

**Step 1 — the cost of active-active, and why it is 2×, not 1.5×.**

```
  active-passive (warm standby):
      primary: full capacity           = 1.0 C
      standby: 30 % capacity, warm     = 0.3 C
      + cross-region data transfer, duplication, monitoring = 0.08 C
      total ≈ 1.38 C   ⇒ +38 % over single-region
  active-active:
      both regions serve traffic, so normal load is 0.5 C each
      BUT on failure, ONE region must handle 100 % ⇒ each must be sized at
      1.0 C, and normal utilisation is 50 %
      total = 2.0 C    ⇒ +105 % over single-region (and a 50 %-utilised fleet
      is a worse place to be on the latency curve — L76 §4.1)
  ⇒ active-active pays for full capacity twice, and runs everything at 50 %
    utilisation so that a failure degrades latency rather than errors.
```

**Step 2 — what the money buys, in minutes of downtime.**

```
  single region:            ~30 min/month of checkout impact (measured)
  active-passive warm:      ~4.5 min/month
  active-active:            ~2.5 min/month (traffic shift is ~30 s, but
                            dependency and re-homing effects remain)
  ⇒ +38 % cost removes 25.5 min/month; another +67 % removes 2.0 min/month.
      per minute removed:  warm standby ≈ £686/minute (annualised)
      per minute removed:  active-active ≈ £15,600/minute
  ⇒ at £9/minute of revenue impact (L89 §9 step 4), NEITHER pays back on
    revenue alone. They are bought for contract, regulation, or reputation —
    which is exactly what happened at BookNook (an enterprise contract).
```

**Step 3 — the RTO of a DNS-based failover, dominated by TTL.**

```
  components: health check 30 s + decision 60 s + promotion 18 s +
              scale-up 90 s + cache warm 360 s + DNS 60 s + verify 25 s
      = 643 s ≈ 10.7 min
  with the old 3600 s TTL: up to 3600 s (60 min) of *client* delay for
      cached records ⇒ users would keep failing for an hour after the
      backend was healthy. Measured in the drill as "RTO 3 h 47 m" (L90 §7.2),
      of which 60 min was DNS and 94 min was an untested promotion.
  ⇒ the automation removed 94 s of 94 min; the TTL change removed 59 min.
    The lesson generalises: LOOK FOR THE BIGGEST COMPONENT, not the most
    interesting one.
```

**Step 4 — conflict rates, which decide whether multi-writer is viable.**

```
  BookNook's write profile, per data set:
      sessions      1.2 M writes/day, 0.8 % concurrent across regions
          ⇒ 9,600 conflicts/day; LWW is acceptable (a session is disposable)
      addresses     4,100 writes/day, 0.05 % concurrent
          ⇒ 2 conflicts/day; LWW is NOT acceptable (silent data loss, and we
            measured 412 reverted edits in 3 months)
      inventory     240,000 writes/day, 3.1 % concurrent (flash sales: 40 %)
          ⇒ 7,400 conflicts/day; an invariant ⇒ single-writer REQUIRED
  ⇒ the conflict rate is not the criterion; the COST OF A WRONG MERGE is.
    Sessions: 0. Addresses: a customer's trust. Inventory: real money.
```

**Step 5 — the re-homing time, which is active-active's hidden RTO.**

```
  210,000 affected entities at 500/s with verification:
      210,000 / 500 = 420 s = 7 min, plus a full verification scan of 210,000
      rows at 20,000 rows/s = 10.5 s, plus the decision (60 s) ≈ 8.2 min
  ⇒ the true RTO of active-active is NOT 30 seconds: it is 30 seconds for
    traffic and ~8 minutes for the data those users care about. Quoting
    "RTO ≈ 0" is a claim about packets, not about orders.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **Backup & restore** | Cheapest | RTO hours–days | Non-critical systems, dev/staging |
| **Pilot light** | Cheap; core ready | RTO hours; scale-up is untested | RTO of a few hours |
| **Warm standby** | RTO ~15 min at +40 % cost | Scaling and cache warm-up in the RTO | **The default for multi-region** |
| **Hot standby (active-passive)** | RTO minutes, no warm-up | ~2× cost for idle capacity | RTO < 10 min, data has invariants |
| **Active-active, single-writer** | ~30 s traffic RTO; both regions productive | Re-homing complexity; per-key unavailability on failure | RTO seconds, and the domain partitions cleanly |
| **Active-active, multi-writer** | Concurrent writes everywhere | Conflict resolution; silent merges | Carts, presence, collaborative docs, analytics |
| **Multi-writer for invariants** | — | Overselling, double-spend, lost edits | **Never** (§7.2) |
| **DNS failover** | Cheap, universal | **RTO = TTL** (§4.2) | Non-critical, or with 60 s TTLs |
| **Anycast / global LB** | Seconds, no DNS involvement | Provider dependency, cost | RTO seconds |
| **Home-region routing** | No conflicts by construction | Some entities unwritable during a failure | The recommended active-active model |

## 11. Failure scenarios

**11.1 — Split-brain between regions** (L79 §7.2). Both regions accepted writes during a 40-second partition; 1,400 orders existed in one and not the other; the reconciliation took days. **Fix:** home-region writes plus a lease/quorum so only one side can own a key, and a fencing term (L78 §4.5).

**11.2 — LWW silently reverting edits** (§4.5). 412 customer addresses reverted because of 900 ms clock skew. **Fix:** version-based detection with surfaceable conflicts, or home-region routing.

**11.3 — The oversell trial** (§7.2). A CRDT counter converged correctly but broke the "never below zero" invariant; 10 items sold from 5. **Fix:** single-writer for anything with an invariant.

**11.4 — Failover that left clients behind.** DNS TTL 3600; the backend was healthy in 14 minutes but ~40 % of users kept hitting the dead region for up to an hour. **Fix:** 60 s TTL on failover records, or anycast/global LB.

**11.5 — The standby that had drifted.** A warm standby's application version was 5 releases behind and its schema 2 migrations behind; promotion failed at the first write. **Fix:** deploy the standby with *every* release (same pipeline), and run automated schema-version assertions between regions.

**11.6 — Failback without reconciliation.** After 4 days on the standby, failback took a 6-hour window and a day of reconciliation (§4.7). **Fix:** bidirectional replication during the temporary state, or declare the new home permanent and migrate deliberately.

**11.7 — The 50 %-utilisation latency trap.** Active-active regions running at 50 % utilisation each looked fine — until a region failed and the survivor ran at 100 %, where the latency curve is vertical (L76 §4.1): p99 went from 210 ms to 9 s. **Fix:** shed (L76 §4.3) rather than absorb, and load-test the survivor at 100 % — which is a *different* test from normal load.

**11.8 — Cross-region reads that violated read-your-writes.** A user wrote in eu-west-1 and was routed (by latency) to eu-central-1 on the next request, reading a 38-second-stale view; 1 in 700 saw a "missing" order and re-ordered (L80 §4.7). **Fix:** session pinning, or route reads for a session to the write's home region for a bounded window.

## 12. Alternatives

| Instead of | Consider | When |
|---|---|---|
| Multi-region active-active | **Warm standby** | The RTO requirement is ≥ 10 minutes (§7.1) |
| Multi-region | **Multi-AZ + degradation** | The failures you actually see are AZ-local (L90 §4.5) |
| Active-active multi-writer | **Home-region single-writer** | Almost always (§4.4 model A) |
| DNS failover | **Anycast / global load balancer** | RTO is measured in seconds |
| Region-level DR | **Cell-based architecture** | You want blast radius bounded, not just duplicated |
| Multi-region | **Queue-and-replay** | The work can be deferred; the user does not need a live answer |
| Re-homing tooling | **Accept read-only for a failed region's data** | The downtime is tolerable and the tooling is not worth it |

## 13. When to use it

- **Active-passive (warm standby)**: the default for any multi-region requirement. Six weeks of work, RTO ~15 minutes, +40 % cost.
- **Active-active (single-writer)**: when the RTO requirement is under a minute, or both regions must be productive for capacity/latency reasons.
- **Multi-writer active-active**: only for data with no invariants — carts, presence, preferences, analytics counters, collaborative documents with a merge UI.
- **Anycast/global LB**: when your RTO is seconds and DNS cannot deliver it.
- **Failback planning**: as part of the failover design, never after.

## 14. When *not* to use it

- **Active-active to solve an AZ-failure problem** — multi-AZ is 10× cheaper and addresses the frequent failure (L90 §4.5).
- **Multi-writer for anything with an invariant** — money, stock, unique keys, counters with a floor (§7.2).
- **Active-active for a service whose data cannot be partitioned** (a single global counter, a global unique sequence).
- **DNS-based failover with a long TTL** — the RTO claim will be false (§4.2).
- **Active-active without a decision on partition behaviour** — the system will pick one by accident (§4.6).
- **A second region before the first is multi-AZ and SPOF-free** (L90 §9 step 4: 28× more cost-effective).

## 15. Common mistakes

1. **Confusing traffic redundancy with data redundancy** (§4.4, §7.1).
2. **Multi-writer for invariants** (§7.2, §11.3).
3. **LWW as a conflict policy**, silently losing writes (§4.5, §11.2).
4. **No partition-behaviour decision** (§4.6).
5. **Long DNS TTLs** on failover records (§4.2, §11.4).
6. **Standby drift** — different versions or schemas (§11.5).
7. **No failback plan** (§4.7, §11.6).
8. **Sizing the survivor at 50 %**, so a failover saturates it (§11.7).
9. **Quoting "RTO ≈ 0"** without accounting for re-homing (§9 step 5).
10. **Read-your-writes violations** from latency routing (§11.8).
11. **Active-active without measuring the conflict rate per data set** (§9 step 4).
12. **Trusting a managed service to be multi-region** when it is multi-AZ.
13. **No cross-region monitoring of replication lag** as an RPO signal (L90 §8).
14. **Re-homing without a lease or fencing**, creating two owners (§8, L79 §4.4).

## 16. Interview perspective

**What is actually being tested.** Whether you know that active-active is a **data-model decision**, not an infrastructure setting — and whether you can pick a topology from RTO/RPO with cost arithmetic. Strong candidates distinguish traffic redundancy from data ownership, insist on single-writer for invariants, and quote the DNS TTL as the real RTO of a DNS failover. Weak candidates say "active-active for zero downtime" without mentioning conflict resolution.

**Answers that land well:**

- *"Active-active or active-passive?"* — "Depends on the RTO and on whether the data partitions. We chose warm standby: 14 minutes measured against a 15-minute contractual RTO, +38 % cost, six weeks of work. Active-active would have given about 30 seconds for traffic but 8 minutes for re-homing the entities homed in the failed region, at 2× cost and nine months — including conflict resolution we did not need."
- *"How do you handle conflicting writes in two regions?"* — "By designing them out. Every key has a home region, so there is exactly one writer; a region failure makes its keys unwritable until they are re-homed under a lease. For genuinely multi-writer data — carts, presence — I use a merge rule that is safe for that data. What I never do is last-write-wins on anything with an invariant, and I would not put inventory in a CRDT counter: we tried, and the counter converged while the 'never below zero' invariant broke."
- *"What is the real failover time?"* — "The largest component, which is usually not the interesting one. Ours was documented as 30 minutes and measured at 3 h 47 m: 94 minutes because the replica had drifted, 60 minutes because the DNS TTL was an hour, 32 minutes of verification. Now the TTL is 60 seconds, promotion is a tested script, and the measured number is 14 minutes."
- *"Failback?"* — "That is a second migration, and it is where teams get caught. Either keep replication bidirectional while you are on standby, or declare the new region the permanent home and migrate back deliberately months later. Never fail back spontaneously because the old region looks healthy."

**Common interview tasks:** choose a topology from stated RTO/RPO with costs; design conflict handling for a given data set; explain why a CRDT counter does not prevent overselling; compute the survivor's capacity requirement; explain how traffic moves and what determines the RTO; plan failback.

**Red flags interviewers watch for:**

- "Active-active means no downtime" with no data discussion.
- LWW assumed to be safe, or unaware of clock dependence.
- Multi-writer for inventories, balances, or unique constraints.
- No partition-behaviour decision.
- Not accounting for the doubled capacity requirement.
- No failback plan.
- No awareness that DNS TTL bounds the RTO.
- No numbers: no cost multiplier, no RTO decomposition, no conflict rate.

## 17. Knowledge check

**Conceptual**

1. Distinguish traffic redundancy from data redundancy in an active-active design, and explain why the second is the hard part. Then explain why "RTO ≈ 0" is a claim about packets rather than about orders.
2. Explain the three data models for active-active and the criterion for choosing between them, using the cost of a wrong merge rather than the conflict rate. Then explain why a CRDT counter converged correctly while still overselling (§7.2).
3. Decompose the failover time of a DNS-based active-passive design, and explain why the DNS TTL often dominates. State the two alternatives that remove that component and the cost of each.

**Practical scenario**

4. BookNook must meet an RTO of 2 minutes and an RPO of 30 seconds for order data across two regions, with 400 rps, 20 M orders/28 days, and data sets: sessions (1.2 M writes/day), customer addresses (4,100 writes/day), inventory counters (240,000 writes/day, 3 % cross-region contention rising to 40 % in flash sales). Choose the topology with full arithmetic; specify the data model per data set with the conflict policy; design the traffic-routing layer and state which component bounds the RTO; design the re-homing process with its lease and verification; specify the partition behaviour; plan failback; and list the alarms with thresholds.

**Calculation**

5. An active-passive design has: health check 30 s, decision 45 s, promotion 20 s, scale-up 100 s, cache warm 240 s, DNS 60 s, verification 30 s. (a) Compute the total failover time. (b) Compute which component dominates and how much of the total it is. (c) Recompute with a hot standby (no scale-up, no cache warm) and a 15-second health check. (d) Compute the annual cost per minute of RTO removed if the hot standby costs £120,000 more per year.

6. An active-active deployment runs two regions at 50 % utilisation each, 400 rps total. (a) Compute each region's capacity requirement and the total capacity multiplier versus a single region. (b) If p99 is 210 ms at 50 % utilisation and 9 s at 100 %, compute the p99 experienced by all users when one region fails with no shedding. (c) Compute the shed rate needed to hold utilisation at 85 % on the surviving region. (d) Compute the re-homing time for 180,000 entities at 400/s plus a 60 s decision and a 15 s verification scan.

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
  - [x] 22. Transactional outbox and change data capture (L85)
  - [x] 23. Monoliths, modular monoliths and microservices (L86)
  - [x] 24. Service discovery and configuration (L87)
  - [x] 25. Observability: logs, metrics, traces (L88)
  - [x] 26. SLIs, SLOs and error budgets (L89)
  - [x] 27. High availability and disaster recovery (L90)
  - [x] **28. Active-passive versus active-active (L91) ← you are here**
  - [ ] 29. RTO and RPO (L92)

**NEXT LESSON: Phase 4, Topic 29 — RTO and RPO**

---

---

# Lesson 92 — RTO and RPO

*Phase 4, Topic 29*

## 1. Simple definition

Two numbers that describe, from the business's point of view, how bad a disaster is allowed to be.

- **RTO (recovery time objective)**: the maximum acceptable **time** to restore service after a disruption. *"Checkout must be serving again within 15 minutes."*
- **RPO (recovery point objective)**: the maximum acceptable **data loss**, expressed as a **duration** backwards from the failure. *"We may lose at most 1 minute of orders."* An RPO of 5 minutes means you must be able to restore to a point 5 minutes before the failure — so your backups/replication must be at most 5 minutes stale.

Three more terms make them usable:

- **MTD (maximum tolerable downtime)**: the point at which the business is permanently harmed — the customer contract is lost, the regulator is notified, the company fails. `MTD ≥ RTO + WRT`.
- **WRT (work recovery time)**: the time needed to make the restored system *useful* — data verification, reconciliation, cache warm, backlog drain, business checks. Often excluded from RTO claims and always present in reality.
- **Actual RTO / actual RPO**: the **measured** values from a drill. The gap between these and the designed values is the most important number in this lesson.

Terms used throughout:

- **PITR (point-in-time recovery)**: restoring to an arbitrary moment using a base backup plus a replayable log (WAL/binlog). The standard mechanism for a short RPO.
- **Replication lag**: how far a replica is behind its primary — the *current, live* RPO of an async-replicated system.
- **Restore rate**: how fast data can be physically restored (GB/hour), which sets a floor on RTO for large datasets.
- **Air gap / immutability**: backups that cannot be deleted or encrypted by an attacker (or by you, by mistake).
- **DR register**: the document recording measured RTO/RPO per system, per drill, with dates.

The one sentence to carry out of this lesson: **RTO and RPO are business decisions that become engineering requirements — and until you have measured them with a stopwatch during a drill, you have neither; you have a hope with a number written next to it.**

## 2. Why it exists

Because "we have backups" and "we have disaster recovery" are both false until they are quantified, and the gap is routinely enormous.

BookNook's DR plan, written by competent engineers, stated:

```
  RTO: 30 minutes     RPO: 5 minutes
  Last tested: —
```

The first drill measured:

```
  actual RTO: 3 h 47 m   (7.6× the claim)
  actual RPO: 4 h 20 m   (52× the claim, because the replica had been lagging
                          since a network change nobody had noticed)
```

Neither number was a lie — they were **designs**. The design said "promote the replica in 30 minutes"; the replica took 94 minutes to promote because it had fallen so far behind that it needed a full re-sync. The design said "5 minutes of data loss"; nothing was measuring the lag, so nothing knew it was 4 h 20 m.

That is why this topic is its own lesson, and it has three practical consequences:

1. **The numbers determine the architecture.** An RPO of 0 requires synchronous replication (with its latency cost, L80 §4.8); an RPO of 5 minutes requires continuous log shipping; an RPO of 24 hours permits nightly backups. Choosing the architecture without the numbers is guessing.
2. **The numbers determine the cost.** L90 §9 step 3 showed a 30× difference in cost per minute of downtime removed between multi-AZ and active-active. RTO/RPO are what tell you where to stop.
3. **The numbers must be measured, not designed.** A restore that has never been timed is an assumption; and assumptions fail exactly when the business is watching.

And the reason it matters beyond engineering: **RTO and RPO are usually contractual or regulatory.** A bank may be required to restore within 2 hours with 15 minutes of data loss; a healthcare system may have statutory retention and availability requirements. When they are external obligations, "we'll do our best" is not a plan — and the measured number is the only defensible evidence.

## 3. Real-world analogy

**A household's fire plan.**

Every household implicitly has an RTO and an RPO, whether or not it has written them down.

**RPO is how much you can lose.** If your documents are in a fireproof box updated monthly, your RPO is a month: you lose anything created since the last update. If they are backed up nightly to a cloud account, your RPO is a day. If you photograph each document as it arrives, your RPO is minutes — and you have paid for that with effort on every single document, which is the cost side of a short RPO.

**RTO is how long you can live elsewhere.** If you have a hotel booked and a bag packed (warm standby), you can be functional tonight. If you would need to find a hotel, buy clothes, and replace documents (backup and restore), you are looking at days. Crucially, **RTO includes the boring parts**: not just "somewhere to sleep" but insurance calls, school runs, and replacing the passport — which is **WRT**, and it is why "we can move into the flat in an hour" is not the same as "we are living normally in an hour".

**MTD is when the household gives up.** If the house is uninhabitable for six months, the family moves away permanently and the "recovery" never happens. That is the maximum tolerable downtime, and it is the number that should drive everything else.

And the analogy's real lesson is the one about **drills**. A household that has written a fire plan, bought extinguishers, and never once walked the escape route at night with children will discover at 03:00 that the back door is blocked by a pram. The plan was never wrong; it was never **exercised**. Every organisation believes its DR plan works; the ones that know are the ones that have timed it.

## 4. Detailed technical explanation

### 4.1 The timeline, with every phase named

```
                    ◄──────── RPO ────────┤
        last recoverable point             │
        (last backup / replica lag)        ▼
  ──────────────────────────────────────●──────────────────────────────►
                                        │ FAILURE  (t=0)
                                        │
        ◄──── DETECTION ────────────────┤   alarms, health checks, a human
                                        │   noticing (§4.2)
        ◄──── DECISION ─────────────────┤   authorise the recovery; is it
                                        │   pre-approved, or does it need
                                        │   an incident commander?
        ◄──── EXECUTION ────────────────┤   promote, restore, reroute
        ◄──── VERIFICATION ─────────────┤   is it actually working? (the
                                        │   step automation always omits)
                                        │
        ◄──────────── RTO ──────────────┤   "service is restored"
                                        │
        ◄──────────── WRT ──────────────┤   reconcile, drain backlog, rebuild
                                        │   caches, verify business totals
        ◄──────────── MTD ────────────────────────────────────────────►
                                        the business is permanently harmed
  ⇒ MTD ≥ RTO + WRT. A design where RTO ≈ MTD has NO margin for WRT and
    will fail its business objective even when the infrastructure works.
```

### 4.2 RTO is a sum, and detection usually dominates

BookNook's measured components, and what they were before the work:

| Phase | Before | After | What changed |
|---|---|---|---|
| **Detection** | 30 min | 30 s | Health checks every 10 s × 3, plus a synthetic transaction probe |
| **Decision** | 41 min | 60 s | Pre-authorised runbook for the defined scenario; paging the on-call directly |
| **Execution** | 94 min | 18 s | Automated promotion (the replica's lag was fixed; the manual steps removed) |
| **Warm-up** | 0 (hot standby) | 0 | Unchanged — the DB standby was hot |
| **Verification** | 32 min | 25 s | An automated verification suite |
| **Routing (DNS)** | 60 min | 60 s | TTL 3600 → 60 on the failover records |
| **Total** | **3 h 47 m** | **14 min** | — |

The pattern is general and worth internalising: **the infrastructure step (execution) was 94 minutes and got automated to 18 seconds; the human and caching steps (decision, verification, DNS) were 133 minutes and got automated to 2 minutes.** RTO work is mostly **removing humans and TTLs from the critical path**, not buying faster hardware.

### 4.3 RPO: six mechanisms, and what each actually guarantees

| Mechanism | RPO | Cost | Note |
|---|---|---|---|
| **Nightly full backup** | up to 24 h | lowest | Plus restore time; often the RTO killer too |
| **Incremental + daily full** | up to 24 h (to the last incremental) | low | Restore needs the full chain — a corrupt link loses everything |
| **Continuous WAL/binlog shipping (PITR)** | seconds–minutes | moderate | **The standard for databases**; restore = base + replay |
| **Asynchronous replica** | = current replication lag | moderate | Live, but lag grows under load and is invisible unless measured |
| **Synchronous replica** | **0** | latency (1 RTT per commit) | L80 §4.8; makes writes slower for everyone, always |
| **Quorum write** (W > N/2) | 0 for acknowledged writes | consensus latency | L80 §4.2 / L81; the strongest practical option |

**The critical distinction**, and the one that surprises people:

```
  A replica is not a backup.
     · a replica faithfully replicates DELETEs, schema mistakes and
       ransomware encryption within seconds — RPO 0, data loss 100 %
     · a backup is POINT-IN-TIME: it can go back before the mistake
  ⇒ you need BOTH: replicas for RTO (fast failover), backups/PITR for RPO
    against CORRUPTION (not just hardware failure).
  ⇒ and the RPO that matters most is not "how much did we lose when the disk
    died" but "how far back can we go when someone ran DELETE without a WHERE".
```

### 4.4 Measuring a restore, which sets a hard floor on RTO

```
  RTO(restore) ≥ data_size / restore_rate + index_rebuild + verification

  BookNook's 1.2 TB database:
      restore rate (from object storage, parallel)  ≈ 240 MB/s ⇒ 1.4 hours
      index rebuild (if restored logically, not physically) ≈ 40 min
      verification (checksums + spot checks)         ≈ 25 min
      ⇒ 2 h 45 m FLOOR for a full restore, before any human steps.
  ⇒ an RTO requirement of 1 hour is arithmetically impossible with a
    full-restore strategy on this database. It requires a replica
    (already warm) or a much smaller dataset (sharded/celled, L91 §12).
  ⇒ THIS is why "we will restore from backup within an hour" is so often
    false: the data is simply too big to restore that fast.
```

### 4.5 Deriving the targets from business impact

The targets should come from a cost curve, not from aspiration:

```
  downtime cost per minute for BookNook checkout:
      0–5 min      £9/min   (users retry; almost no revenue lost)
      5–30 min     £38/min  (cart abandonment begins; support load)
      30–120 min   £120/min (customers go elsewhere; SLA credits trigger)
      >2 h         £400/min (churn, press, contract renegotiation)
  ⇒ cumulative: 30 min ≈ £780; 2 h ≈ £6,300; 6 h ≈ £44,000
  MTD (where the enterprise contract is lost) ≈ 4 hours.

  ⇒ RTO selection: the £/min curve is flat until 5 minutes and steep after
    30, so an RTO of 15 minutes captures most of the value; going to 2
    minutes costs £390k/year (L91 §9 step 2) to avoid maybe £120 of the
    first 15 minutes. NOT worth it.
  ⇒ RPO selection: losing an hour of orders means re-creating ~2,400 orders
    (400 rps… no: 0.5 orders/sec × 3,600 = 1,800 orders) at ~£2.65 each in
    handling = £4,770 per event, plus customer trust. At 0.3 events/year
    that is ~£1,400/year — but a 1-minute RPO costs ~£0 extra because WAL
    shipping is part of the standard setup.
  ⇒ the answer is usually: RPO as close to 0 as the standard tooling gives
    for free (PITR), and RTO where the cost curve flattens.
```

### 4.6 Data tiers, because one RTO for everything is a budget disaster

| Tier | Data | RPO | RTO | Strategy | Annual cost |
|---|---|---|---|---|---|
| **1** | Payments, orders, ledger | **0–1 min** | 15 min | Sync/quorum replication + hot standby + PITR | ~£210k |
| **2** | Catalogue, customers, sessions | 5 min | 1 h | Async replica + warm standby + PITR | ~£48k |
| **3** | Search index, recommendations | 1 h (rebuildable) | 4 h | Rebuild from source; no backup needed | ~£6k |
| **4** | Analytics, logs, warehouse | 24 h | 3 days | Nightly snapshots, object storage | ~£3k |

```
  ⇒ the tiering saves ~60 % versus applying tier 1 to everything, and it is
    defensible because tier 3's "data" is DERIVED — it can be rebuilt from
    tier 1 and 2 sources. Ask of every data set: "could we reconstruct this
    from something else?" If yes, its RPO/RTO can be much looser.
```

### 4.7 The traps, in order of how often they cause real incidents

1. **RTO quoted as failover time only.** Detection, decision, verification, DNS and warm-up are all in the RTO (§4.2: they were 85 % of it).
2. **RPO claimed as 0 because replication is "synchronous".** Check whether it is actually synchronous for *all* writes, and check what happens on a replica failure (does it silently fall back to async?).
3. **Unmeasured replication lag.** The lag *is* your live RPO, and it grows with load. Alarm on it (L90 §8).
4. **Replicas treated as backups** (§4.3). Replication replicates deletion.
5. **Backups in the same account/region/credentials.** One compromised credential = no backups. Use cross-account, immutable, object-locked storage.
6. **Untested restores.** The most common finding, and the most expensive (§7.1).
7. **RTO measured from detection, not from failure.** A 20-minute detection phase silently becomes 20 minutes of unbudgeted downtime.
8. **Restore rate ignored.** A 1.2 TB database cannot be restored in 20 minutes at any price (§4.4).
9. **The dependency's RTO.** Your RTO cannot be shorter than your critical providers' recovery time. If your cloud provider's regional recovery is 4 hours, your 1-hour RTO requires a second region — check the contract.
10. **WRT not budgeted.** Restoring service and restoring *correctness* are different; reconciliation after an RPO event takes hours.

## 5. Request / data flow

A real incident, with every phase timed, so the numbers add up.

```
 ── THE EVENT ──────────────────────────────────────────────────────────────
 02:11:40  a storage controller failure corrupts the primary database's
           data files; the database stops accepting writes.
           ⇒ last recoverable point = the last WAL archive = 02:11:12
             (RPO contribution: 28 s of writes will need replay from the
              application's outbox — see below)

 ── DETECTION ──────────────────────────────────────────────────────────────
 02:11:40  synthetic transaction probe fails
 02:12:10  3 consecutive failures × 10 s → alarm fires           [30 s]

 ── DECISION ───────────────────────────────────────────────────────────────
 02:12:10  on-call acknowledged; scenario is "primary unavailable > 2 min"
 02:12:55  pre-authorised: promote the hot standby               [45 s]
           (a data-corruption scenario would require a PITR decision instead,
            which is NOT pre-authorised — see WRT below)

 ── EXECUTION ──────────────────────────────────────────────────────────────
 02:13:15  standby promoted, connections re-resolved             [20 s]
 02:13:20  application reconnects; writes resume                 [5 s]
           ⇒ traffic is served again at 02:13:20

 ── VERIFICATION ───────────────────────────────────────────────────────────
 02:13:45  automated suite: order creation, payment auth, catalogue read,
           a reconciliation count on the last 5 minutes       [25 s]

 ── RTO ACHIEVED ───────────────────────────────────────────────────────────
           02:13:45 − 02:11:40 = 125 s = 2 min 5 s
           (against a 15-minute objective ⇒ 4× margin)

 ── WRT: THE PART THE SLA DOES NOT SEE ─────────────────────────────────────
 02:13:45  the writes between 02:11:12 and 02:11:40 were lost by the
           promotion. But every one of them is in the OUTBOX (L85) — so the
           events exist and can be replayed.
 02:14:00 → 03:02:00  replay 340 outbox rows, reconcile payment
           authorisations, re-drive 12 failed sagas (L84 §4.6)  [48 min]
 03:02:00  business totals match; WRT complete
           ⇒ RTO 2 min, WRT 48 min, total exposure 50 min — still inside the
             4-hour MTD, which is why the incident was a "degraded service"
             rather than a "contract renegotiation".

 ⇒ NOTE THE SHAPE: the infrastructure RTO was 2 minutes; the BUSINESS
   exposure was 50. Every design that reports only the first number is
   reporting the wrong one.
```

## 6. ASCII diagram

RTO vs RPO on one timeline, with the tiers and the cost curve:

```
  data
  ────────────────────────────────────────────────────────────────► time
                       ▲                    ▲
                 last good copy         FAILURE
                       │◄──── RPO ───────►│
                       │                  │
                       │   ┌─ DETECT ─────┤
                       │   ├─ DECIDE ─────┤
                       │   ├─ EXECUTE ────┤
                       │   ├─ VERIFY ─────┤
                       │   │        RTO ──┤
                       │   │              ├── WRT: reconcile, drain, rebuild
                       │   │              │
  ◄────────────────────┴───┴──────────────┴─────────── MTD ──────────►
   the business is permanently harmed somewhere out here

  ┌──────────────────────────────────────────────────────────────────────┐
  │ RTO  ≠  failover time        (add detection, decision, verify, DNS)  │
  │ RPO  ≠  "we replicate"       (lag IS the RPO; measure it)            │
  │ RTO  ≥  data_size / restore_rate + rebuild + verify   (a hard floor) │
  │ MTD  ≥  RTO + WRT                                     (budget both)  │
  └──────────────────────────────────────────────────────────────────────┘

  the cost curve, and why the last minutes are absurdly expensive:

  £/year
    ▲                                              ╱
    │                                            ╱
    │                                     ╱─────╱   active-active
    │                          ╱──────────╱
    │              ╱───────────╱    hot standby
    │     ╱────────╱   warm standby
    │ ╱────╱  multi-AZ
    └──┴─────┴────────┴──────────┴────────────────────► RTO achieved
       24 h   4 h      30 min     5 min      30 s
```

## 7. Detailed example

### 7.1 From claimed to measured: the BookNook remediation

| Phase | Designed | Measured (2024 drill) | Measured (2026, after remediation) |
|---|---|---|---|
| Detection | "alarms" | 30 min | 30 s |
| Decision | "on-call decides" | 41 min | 60 s |
| Execution | "promote replica" | 94 min | 20 s |
| Verification | — (not planned) | 32 min | 25 s |
| DNS | — (not planned) | 60 min | 60 s |
| **RTO** | **30 min** | **3 h 47 m** | **2 min 35 s** |
| **RPO** | **5 min** | **4 h 20 m** | **28 s** |

What the remediation actually consisted of, in order of value:

```
  1. MONITOR THE RPO. A replication-lag SLI with an alarm at 60 s. The
     4 h 20 m RPO had been visible in metrics the whole time; nobody was
     watching the right number. Cost: 4 hours of work. Removed 4 hours of
     data loss.
  2. AUTOMATE THE FAILOVER, then run it monthly. Script + drill harness.
     Cost: 2 weeks. Removed 175 minutes of RTO.
  3. PRE-AUTHORISE the defined scenarios. The 41-minute decision was a human
     finding a person to say yes. Cost: a one-page policy. Removed 40 minutes.
  4. AUTOMATE VERIFICATION. A 12-assertion suite instead of manual checks.
     Cost: 3 days. Removed 31 minutes.
  5. FIX THE DNS TTL (3600 → 60). Cost: one config change. Removed 59 minutes.
  6. RECORD THE MEASURED VALUES in a DR register, quarterly, owned by a name.
  ⇒ total effort: ~5 engineer-weeks. Result: RTO 3 h 47 m → 2 m 35 s,
    RPO 4 h 20 m → 28 s. The cost was £0 in infrastructure — it was all
    measurement, automation and removal of humans from the critical path.
```

### 7.2 The tabletop that found the backups were deletable

A ransomware tabletop exercise (a discussion-based drill, no systems touched) asked one question: *"the attacker has domain-admin in the cloud account; what happens to our backups?"*

```
  findings:
    · backups were in the same AWS account, with the same credentials
    · the backup bucket had no object lock and no versioning
    · the account held both production and backup IAM roles
    · the documented "restore" procedure required credentials held only in
      that account
  ⇒ the answer was: the backups would have been deleted, and the ability to
    restore would have been deleted with them. RPO would have been "all of
    it" — i.e., the business would have ceased to exist.
  remediation (3 weeks):
    · a separate backup account, with no trust relationship to production
    · S3 object lock in compliance mode (immutable for 35 days)
    · cross-region replication of backups
    · restore credentials held outside the production account, in a break-
      glass procedure with two-person approval
    · a quarterly restore from the ISOLATED copy, timed
  ⇒ the lesson: the RPO you have is the RPO of the copy you can still reach
    after the worst day. A backup that shares a credential with production
    is not a backup; it is a copy.
```

## 8. Code or configuration example

```bash
# ── Point-in-time restore, timed end to end (PostgreSQL / RDS) ────────────
# RPO is set by the WAL archive interval; RTO is set by this command's duration.
START=$(date +%s)
aws rds restore-db-instance-to-point-in-time \
  --source-db-instance-identifier booknook-prod \
  --target-db-instance-identifier booknook-drill-$(date +%s) \
  --restore-time "$(date -u -d '10 minutes ago' +%Y-%m-%dT%H:%M:%SZ)" \
  --db-instance-class db.r6g.2xlarge

until [ "$(aws rds describe-db-instances \
        --db-instance-identifier booknook-drill-* \
        --query 'DBInstances[0].DBInstanceStatus' --output text)" = "available" ]; do
  sleep 10
done
echo "RESTORE (infra) took $(( $(date +%s) - START ))s"

# VERIFY — a restore that completes but returns wrong data is worse than none
psql -h "$DRILL_HOST" -c "SELECT count(*) FROM orders WHERE created_at > now() - interval '1 day'" \
  | tee /tmp/rowcount
psql -h "$DRILL_HOST" -c "SELECT sum(total_pence) FROM orders WHERE id % 1000 = 0" \
  | tee /tmp/checksum
# compare against the expected values captured BEFORE the drill
diff <(cat /tmp/rowcount /tmp/checksum) expected.txt || { echo "RESTORE INVALID"; exit 1; }

echo "TOTAL RTO (restore + verify): $(( $(date +%s) - START ))s"
# ⇒ write this number into the DR register. The DESIGNED number is fiction.
```

```yaml
# ── Alarms that make RPO and RTO live properties, not documents ──────────
alerts:
  - alert: ReplicaLagBreachesRPO          # your RPO claim is currently false
    expr: pg_replication_lag_seconds > 30        # RPO target: 1 minute
    for: 2m
    severity: page

  - alert: BackupOlderThanRPO
    expr: time() - backup_last_success_timestamp_seconds > 3600   # > 1 h
    severity: page

  - alert: WALArchiveFailing              # RPO silently degrading to "since"
    expr: rate(pg_stat_archiver_failed_count[10m]) > 0
    severity: page

  - alert: RestoreDrillOverdue
    expr: time() - dr_restore_drill_last_timestamp_seconds > 30 * 86400
    severity: ticket          # unmeasured RTO is a hope

  - alert: RestoreRateBelowPlan           # the RTO floor is slipping
    expr: dr_last_restore_bytes_per_second < 150e6
    severity: ticket          # 1.2 TB at 150 MB/s = 2 h 13 m, over budget
```

```python
# ── The DR register: measured, dated, owned. The only defensible evidence. ─
DR_REGISTER = [
    {"system": "orders-db", "scenario": "primary failure",
     "designed_rto_s": 900, "measured_rto_s": 155, "measured_rpo_s": 28,
     "drill_date": "2026-10-14", "owner": "platform",
     "verification": "12 assertions passed", "notes": "hot standby, TTL 60"},
    {"system": "orders-db", "scenario": "data corruption (DELETE without WHERE)",
     "designed_rto_s": 7200, "measured_rto_s": 9840, "measured_rpo_s": 60,
     "drill_date": "2026-09-02", "owner": "platform",
     "verification": "PITR to -60 s; row counts matched",
     "notes": "PITR restore 2 h 44 m — restore rate is the floor"},
]
# ⇒ publish this to the business. A gap between designed and measured is a
#   finding, not a failure; an ABSENT entry is the actual failure.
```

## 9. Numerical example

BookNook: 1.2 TB database, 400 rps, checkout AOV £18, enterprise contract MTD 4 h.

**Step 1 — the hard floor on RTO, from restore rate.**

```
  full restore: 1,200 GB at 240 MB/s  = 5,000 s = 83 min
  + index rebuild (logical restore)   = 40 min
  + verification                       = 25 min
  ⇒ 2 h 28 m floor. An RTO of 30 minutes is IMPOSSIBLE by full restore.
  ⇒ options: (a) hot standby (promotion, seconds) — chosen; (b) shard the
    database so each shard is small enough to restore fast; (c) accept
    a longer RTO and say so.
  ⇒ "restore within 30 minutes" for a 1.2 TB database is a claim that has
    never been arithmetic — which is exactly why drills find 5× gaps.
```

**Step 2 — RPO from replication lag, including the peak-load effect.**

```
  async replication lag, measured:
      off-peak (60 events/s)   p50 40 ms, p99 300 ms
      peak (1,000 events/s)    p50 1.8 s, p99 38 s, max 4 m 20 s (the incident)
  ⇒ your RPO is not a constant; it is a distribution that worsens exactly
    when failures are most likely. Quote the p99, not the mean.
  ⇒ to guarantee RPO ≤ 1 min you must alarm at lag > 30 s (so you have
    time to act before breaching), which is what the §8 alarm does.
```

**Step 3 — what an RPO breach actually costs, which sizes the investment.**

```
  lost writes: 4 h 20 m at 0.5 orders/sec = 7,800 orders
  re-creation: impossible without source data → the customers must re-order.
      measured recovery rate in the 2024 incident: 61 % of affected customers
      re-ordered; 39 % did not.
      revenue lost: 39 % × 7,800 × £18 = £54,700, plus support (£2.65 ×
      4,758 contacts = £12,600), plus 2 enterprise renewals delayed.
  ⇒ a single RPO event cost ~£67,000. The fix (lag monitoring + alarm) cost
    4 hours of engineering. THE ASYMMETRY IS THE ARGUMENT.
```

**Step 4 — MTD, WRT and the margin nobody budgets.**

```
  MTD (business): 4 h (the enterprise contract's termination clause)
  measured RTO:  2 m 35 s
  measured WRT:  48 min (outbox replay + saga re-drive + reconciliation)
  ⇒ exposure 50 min = 21 % of MTD. Comfortable.
  if the design had shipped with RTO 3 h 47 m (the original), exposure would
  have been 4 h 35 m = 115 % of MTD ⇒ the contract would have been breached
  even though "the service came back". THAT is what WRT accounting catches:
  a design that "meets" an RTO of 4 h still fails an MTD of 4 h, because
  restore ≠ correct.
```

**Step 5 — tiering, and the 60 % it saves.**

```
  applying tier-1 treatment (RPO 1 min / RTO 15 min) to everything:
      1.2 TB + 400 GB search index + 6 TB warehouse
      ≈ £210k (orders) + £180k (index, if hot-standby'd) + £130k (warehouse)
      = £520k/year
  tiered (L92 §4.6):
      tier 1 orders £210k + tier 2 catalogue £48k + tier 3 rebuildable
      index £6k + tier 4 nightly £3k = £267k/year
  ⇒ £253k/year saved by asking one question per data set: "can this be
    rebuilt from something else?" The search index can (from the catalogue);
    the orders cannot.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **RPO 24 h (nightly backup)** | Cheapest | Up to a day of data lost | Derived/rebuildable data |
| **RPO minutes (PITR)** | Protects against corruption, not just hardware | WAL storage, restore time | **All databases** |
| **RPO 0 (sync replication)** | No data loss on failover | Every write pays a RTT; a replica failure can stall writes | Money, ledgers |
| **RTO hours (backup/restore)** | Cheapest | Long outages; restore rate is a hard floor | Non-critical systems |
| **RTO ~15 min (warm standby)** | Captures most of the value curve | +40 % cost; scaling and warm-up in the RTO | The default multi-region answer |
| **RTO seconds (active-active)** | Near-zero exposure | 2× cost, conflict resolution, re-homing (L91) | Contract or regulation demands it |
| **Tiered objectives** | ~60 % cost saving; proportionate investment | Requires classification, and defending it | Always |
| **Measured over designed numbers** | The only defensible evidence | Drill time, quarterly | Always |
| **Automated failover + verification** | Removes humans from the critical path | Must be tested; can misfire | RTO < 30 min |
| **WRT budgeted separately** | Realistic business exposure | Reveals uncomfortable numbers | Always |

## 11. Failure scenarios

**11.1 — The designed-vs-measured gap** (§7.1). 30 min → 3 h 47 m; 5 min → 4 h 20 m. **Fix:** measure quarterly, publish the register, fix the largest component.

**11.2 — Unmeasured replication lag** (§9 step 2). Lag was 4 h 20 m peak, invisible; nobody had a lag alarm. **Fix:** lag as an SLI, alarm at half the RPO.

**11.3 — Backups deleted with the account** (§7.2). Same account, same credentials, no immutability. **Fix:** separate account, object lock, cross-region, break-glass restore credentials.

**11.4 — Replication as backup.** A `DELETE FROM orders` without a `WHERE` replicated in 0.8 s; "RPO 0" became "we lost everything created after the last backup, 19 hours ago". **Fix:** PITR, plus a delayed replica (e.g. 1 hour behind) for exactly this scenario.

**11.5 — Restore rate ignored** (§9 step 1). An RTO of 30 minutes vs a 2 h 28 m arithmetic floor. **Fix:** compute the floor; use a standby, shard, or change the objective.

**11.6 — Detection outside the RTO.** Alarms were on a 5-minute evaluation interval; detection took 15 minutes and was never counted in the 30-minute RTO claim. **Fix:** include detection, and use a synthetic transaction probe.

**11.7 — The dependency's RTO.** BookNook's RTO was 15 minutes; the managed database provider's regional-recovery SLA for a lost region was 4 hours. **Fix:** read the provider's contract; a shorter RTO requires your own cross-region replica (L91).

**11.8 — WRT unbudgeted** (§9 step 4). The service was "restored" in 2 minutes and the business was exposed for 50; an earlier design would have breached a 4-hour MTD despite "meeting" its 4-hour RTO. **Fix:** report exposure as RTO + WRT, and design the reconciliation path (L85 outbox replay, L84 reconciler) as part of DR.

## 12. Alternatives

| Instead of | Consider | When |
|---|---|---|
| Backup/restore for RTO | **A standby** | Restore rate exceeds the RTO floor |
| Replication for RPO | **PITR** | You need protection from corruption, not just hardware |
| One RTO for all data | **Tiering** | Always (§4.6) |
| Designed numbers | **Measured drills** | Always |
| Multi-region | **Tiered + multi-AZ** | The MTD is hours, not minutes |
| Full restore | **Sharding / cells** | The dataset is too large to restore within the RTO |
| Custom DR | **Provider-managed DR** | Their measured RTO beats yours and is cheaper |

## 13. When to use it

- **Every production system: define RTO and RPO, per data tier, before choosing an architecture** — because they are the inputs to L90 and L91's decisions.
- **Whenever there is a contract or regulation** — those define MTD and are not negotiable after the fact.
- **Quarterly drilling, with the stopwatch** — the only source of defensible numbers.
- **Alarm on replication lag and backup age** — they make RPO a live property.
- **WRT planning** — reconciliation, backlog drain and business verification are part of recovery.

## 14. When *not* to use it

- **As aspirational targets nobody funds.** An RTO the architecture cannot meet is a liability, not a goal.
- **A single tier for everything** — it wastes money on derived data (§9 step 5).
- **Designed numbers in a customer contract** unless they have been measured.
- **RTO claims that exclude detection, verification or DNS** (§4.2).
- **Replication presented as a backup** (§4.3).
- **DR plans whose restore has never been run** (§7.2).

## 15. Common mistakes

1. **Quoting designed RTO/RPO** (§11.1).
2. **Not measuring replication lag**, or measuring it but not alarming (§11.2).
3. **Backups sharing credentials/account/region with production** (§11.3).
4. **Treating replicas as backups** (§11.4).
5. **Ignoring restore rate** (§11.5).
6. **Excluding detection, decision, verification and DNS** from the RTO (§4.2).
7. **Ignoring the provider's own recovery SLA** (§11.7).
8. **Not budgeting WRT** (§11.8).
9. **One objective for all data sets** (§9 step 5).
10. **Restore drills without verification** — a restore that returns wrong data is worse than none.
11. **No named owner** for the DR register.
12. **Drills only in staging** — production restores hit real quotas, real IAM, real sizes.
13. **Assuming backups are immutable** without object lock.
14. **Not testing the scenario that matters most**: corruption and deletion, not just hardware failure.

## 16. Interview perspective

**What is actually being tested.** Whether you treat RTO/RPO as **sums of measured components** and as **inputs to architecture**, rather than as numbers in a document. Strong candidates decompose the RTO, compute the restore-rate floor, insist on measuring with drills, and separate WRT from RTO. Weak candidates say "RTO 15 minutes, we have backups" and cannot say where the 15 minutes goes.

**Answers that land well:**

- *"What's your RTO?"* — "For orders, 15 minutes designed; 2 minutes 35 seconds measured, from a drill this quarter, broken down as 30 seconds detection, 60 decision, 20 execution, 25 verification, 60 DNS. It used to be 3 hours 47 minutes — 94 of those minutes were an untested promotion and 60 were a DNS TTL of an hour. The measured number is in a register with a date and an owner, because the designed number was fiction."
- *"How do you get an RPO of one minute?"* — "Continuous WAL archiving and a replica, plus an alarm on replication lag at half the RPO — because lag *is* the live RPO and it grows with load: ours was 40 milliseconds off-peak and 38 seconds at p99 during peak. And replication is not a backup: it replicates a `DELETE` in under a second, so I also keep PITR and a delayed replica for the corruption case."
- *"Can you restore in 30 minutes?"* — "Not from backup: 1.2 terabytes at 240 megabytes a second is 83 minutes before index rebuild and verification, so the floor is about 2.5 hours. That is why the answer is a hot standby for failover and PITR for corruption — two different mechanisms for two different objectives."
- *"What is WRT and why do you care?"* — "Work recovery time — reconciling, draining backlogs, re-driving sagas, proving the totals. Ours was 48 minutes after a 2-minute RTO. If your MTD is four hours and your RTO is three, you have already breached it once WRT is added, even though the service 'came back'."

**Common interview tasks:** decompose and compute an RTO; choose mechanisms for a stated RPO; compute a restore-rate floor; design a tiering scheme; plan a drill programme; explain why replication is not a backup; derive targets from a downtime-cost curve.

**Red flags interviewers watch for:**

- RTO quoted without decomposition.
- "We have backups" with no restore time.
- RPO claimed without lag monitoring.
- Replication treated as backup.
- No drill, or a drill only in staging.
- One objective for all data.
- No WRT, and no MTD concept.
- No numbers: no restore rate, no lag distribution, no measured value.

## 17. Knowledge check

**Conceptual**

1. Define RTO, RPO, MTD and WRT, and explain the constraint `MTD ≥ RTO + WRT`. Then explain why a design that "meets" its RTO can still breach its MTD, using the §9 step 4 arithmetic.
2. Decompose the RTO into its phases and explain which phases most often dominate. Then explain why RTO work is usually about removing humans and TTLs rather than buying faster hardware.
3. Explain why a replica is not a backup, and describe the two mechanisms that provide protection against corruption and deletion. Then state what an RPO of 0 actually requires and what it costs.

**Practical scenario**

4. BookNook has a 1.2 TB orders database, 400 rps, an enterprise contract with a 4-hour MTD, a 15-minute RTO requirement and a 1-minute RPO requirement. Current state: nightly backups only, one async replica with unmonitored lag (measured p99 38 s at peak, max 4 h 20 m), manual failover with a documented 30-minute RTO, DNS TTL 3600, and backups in the production account. Design the complete remediation: the mechanisms for RPO and RTO with the arithmetic, the restore-rate floor calculation, the tiering for the other data sets, the alarms with thresholds, the drill programme with cadence and acceptance criteria, the DR register entries, and the business case in pounds and minutes.

**Calculation**

5. A 900 GB database must be restored from backup. The restore rate is 180 MB/s, index rebuild takes 35 minutes, and verification 20 minutes. (a) Compute the restore floor. (b) If the RTO requirement is 1 hour, compute the shortfall and state three ways to close it with the cost implication of each. (c) Recompute the floor for a 150 GB shard if the database were split into six shards. (d) State which option you would choose for an RTO of 1 hour and why.

6. An async replica has lag p50 1.5 s, p99 35 s, max 15 minutes at peak. (a) State the RPO you can honestly claim. (b) Compute the alarm threshold you would set and justify it. (c) If a failure occurs at peak and the promotion takes 2 minutes, compute the total data-loss window and the number of orders lost at 0.5 orders/sec. (d) Compute the monthly cost of that exposure at £18 AOV and 61 % re-order rate, and compare it with 4 hours of engineering to add the lag alarm.

**Progress checklist**

- Phase 1 — Foundations (L1–17): **complete** ✅
- Phase 2 — Scaling (L18–39): **complete** ✅
- Phase 3 — Data and Storage (L40–63): **complete** ✅
- Phase 4 — Distributed Systems (L64–92): **COMPLETE** ✅
  - [x] 1–26 (L64–L89) — all complete
  - [x] 27. High availability and disaster recovery (L90)
  - [x] 28. Active-passive versus active-active (L91)
  - [x] **29. RTO and RPO (L92) ← you are here**
- Phase 5 — Complete system designs (L93–106): **next**

**NEXT: Phase 4 wrap-up — revision sheet, glossary and exercises · then Phase 5 begins with Topic 1 — Design Pastebin (L93)**

---

---

# Phase 4 Wrap-Up — Distributed Systems

Lessons 64–92, covering Phase 4 Topics 1–29.

Phase 1 (L1–17) built the foundations. Phase 2 (L18–39) scaled the stateless tier. Phase 3 (L40–63) hit the wall that cannot be cloned. Phase 4 was the consequences of that wall: **the moment your system is more than one machine, every property you relied on — that time is unambiguous, that a call either happens or does not, that data has one copy, that failure is visible — stops being true.** Twenty-nine lessons, and they are all variations on four problems: partial failure, ordering, coordination, and knowing what is happening.

This wrap-up is the revision artefact: everything that matters, compressed, plus the work to do before Phase 5. The continuing example throughout is **BookNook**, an online bookstore. All figures are the numbers used in the lessons, so the arithmetic can be re-derived.

---

## 1. Revision sheet

### Topic 1 — Why distributed systems are difficult (L64)

| Fact | Detail |
|---|---|
| The core problem | **Partial failure**: a distributed system can be partially broken, and the parts disagree about whether they are. A single machine either works or is dead; a network of machines can be half-working, indefinitely. |
| The four impossibilities | You cannot reliably distinguish **slow** from **dead**; you cannot **order** events across nodes without clocks you trust; you cannot have **global state** without coordination; you cannot have all of **latency, consistency and availability** at once (CAP/PACELC — L59, L60). |
| The fallacies | The network is reliable · latency is zero · bandwidth is infinite · the network is secure · topology does not change · there is one administrator · transport cost is zero · the network is homogeneous. Each one is a real outage. |
| Why "just add a timeout" fails | A timeout is a **guess** that converts an unknown outcome into a decision. If the call actually succeeded, the caller has lost an acknowledgement and will retry, creating a duplicate — which is why **idempotency** (L74) is the load-bearing discipline of Phase 4. |
| The number that matters | Same-datacentre RTT ≈ **0.5 ms**; cross-AZ ≈ **1–2 ms**; cross-region (EU–US) ≈ **75 ms**; a retry that doubles a p99 tail on 5 hops takes p99 from 260 ms to **1,095 ms** (L86 §7). |
| The design response | Never assume; always bound: timeouts with jittered retries, idempotency keys, quorum reads/writes, and a fallback that is **deployed and tested** rather than documented (L77). |

### Topic 2 — Partial failures and network partitions (L65)

| Fact | Detail |
|---|---|
| Partition | Two sets of nodes cannot communicate. **Both sides are alive**; the network between them is not. Not a failure of a node, and not detectable from inside either side. |
| The detection problem | Every signal (ping, heartbeat, TCP close, DNS) is itself a network operation. A node cannot distinguish "peer dead" from "network broken" from "peer slow"; only **indirect evidence** (quorums, leases) helps. |
| The three states of a call | Success · **definite** failure (connection refused: the peer is not listening) · **indefinite** failure (timeout: unknown). Only the third is dangerous, and it is the common one. |
| Timeout choice | Timeout = `p99.9 of latency × safety factor`, not a round number. Too short: false failures and retry storms. Too long: threads and connections pile up (L76 §2). |
| Retry amplification | 3 retries across 5 hops = up to **243** attempts at the leaf. Exponential backoff **plus jitter** (full jitter) plus a **retry budget** (e.g. retries ≤ 10 % of requests) is the only safe form. |
| The partition decision | On partition you must choose: keep serving both sides (divergence), serve one side (availability loss), or serve reads only (degradation). **Doing nothing chooses the first option by accident** (L91 §4.6). |

### Topic 3 — Clock differences and ordering events (L66)

| Fact | Detail |
|---|---|
| Wall clock | NTP-synchronised, typically **±1–100 ms** drift, occasionally far worse; not monotonic (it *goes backwards* on a correction). Fine for logging, unsafe for ordering or expiry decisions. |
| Monotonic clock | Guaranteed non-decreasing; the only clock usable for measuring durations, timeouts and lease expiry. Never for timestamps shared with others. |
| Lamport clock | A single integer, incremented on send/receive event; gives a **partial order** consistent with causality. Cannot tell concurrent events apart (both get the same or arbitrary order). |
| Vector clock | One counter per node; **detects concurrency** (neither dominates) at the cost of O(n) size per value. Used for conflict *detection* (L91 §4.5). |
| Hybrid logical clock (HLC) | Physical time with a logical counter to keep the order consistent with causality even when clocks jump — the modern choice (CockroachDB, Spanner-derived designs). |
| TrueTime / bounded-uncertainty | Spanner waits out the clock uncertainty (typically **≤7 ms**) before committing, converting a physical clock into a **guarantee**. The cost is latency on every commit. |
| BookNook's incident | Last-write-wins with a 900 ms skew reverted **412** customer address edits over three months; the fix was home-region ownership, not a better clock (L91 §4.5). |

### Topic 4 — Synchronous versus asynchronous workflows (L67)

| Fact | Detail |
|---|---|
| Synchronous | Caller blocks until the callee answers. Simple, immediate feedback, and the caller's availability is now the **product** of its dependencies (L90 §4.6). |
| Asynchronous | Caller hands off to a broker or log and returns. Decouples availability and latency; costs eventual consistency, ordering ambiguity, and a new operational surface. |
| The availability calculation | 5 synchronous dependencies at 99.9 % each = **99.5 %** end to end. Making the fourth asynchronous removes it from the product entirely. |
| The latency calculation | Each hop adds p50 and, far worse, **tail**. 5 hops each at p99 gives ~**4.9 %** of requests above one hop's p99; the chain's p99 ≈ the sum of the p99s (L86). |
| When synchronous is right | The user needs the answer to proceed (auth, price, payment authorisation) and the dependency is fast and highly available. |
| When asynchronous is right | The work is durable-but-deferrable (email, index update, ledger posting), or the dependency is slow/flaky/external. |
| The hybrid | Synchronous **accept**, asynchronous **complete**: write to a durable store and return; do the work from a queue/log with a status endpoint or a webhook (the "202 Accepted" pattern). |

### Topic 5 — Message queues (L68)

| Fact | Detail |
|---|---|
| What it is | A durable buffer between producer and consumer; the producer's write completes when the broker has the message, not when the work is done. |
| What it buys | **Load levelling** (a spike is absorbed), **decoupling** (producer does not know consumers), **retry** (failed work is redelivered), and **durability across crashes**. |
| What it costs | A second system to operate, a consistency boundary (the DB commit and the enqueue are not atomic — hence L85), and a new failure mode: the queue. |
| Key knob | **Visibility timeout / ack deadline.** Too short: work is redelivered while still in progress (duplicates). Too long: a crash strands messages for minutes. |
| Sizing | Consumers needed = `arrival rate × p95 processing time`; BookNook's indexer needed `2,000/s × 300 ms = 600 × 1.3 = 780` workers to keep up with the peak (L68 §9). |
| Failure mode to expect | The **poison message**: one bad payload retried forever, blocking its partition. This is why DLQs exist (L75). |

### Topic 6 — Publish-subscribe systems (L69)

| Fact | Detail |
|---|---|
| What it is | Producers publish to a **topic**; each **subscription** receives a copy. One message, many independent consumers. |
| Versus a queue | A queue is **one message, one consumer** (work sharing); pub-sub is **one message, every subscription** (event notification). Models differ in whether a message is *owned* or *observed*. |
| What it buys | Adding a consumer is a configuration change, not a producer change — the whole point of an event-driven boundary. |
| What it costs | Nobody knows who consumes what (the "event soup" problem), the payload becomes a **de facto API** (version it), and a slow consumer is invisible to the producer. |
| Subscription durability | Durable subscription + a retained offset means a consumer can be offline and catch up. Without it, messages published while offline are simply gone. |
| Governance | A schema registry, a documented event catalogue, and an owner per event type. BookNook's audit found **61 of 214** event types with no owner (L87 §7.3). |

### Topic 7 — Event streams (L70)

| Fact | Detail |
|---|---|
| What it is | An **append-only, ordered, retained log**. Consumers read at their own offset; the log is the source of truth, not a transient buffer. |
| What it buys | **Replay** (rebuild a read model by rewinding), **ordering per partition**, **multiple independent consumers** over the same data, and the ability to add a consumer that reads history. |
| What it costs | Retention storage; consumers must manage offsets; the log's partitioning **is** your parallelism ceiling. |
| Ordering | Guaranteed **within a partition only**. Keying by `order_id` puts all events for an order in one partition, in order — which is what you almost always want. |
| The log as system of record | Event sourcing: state is derived by folding the log. Powerful (auditability, time travel) and expensive (schema evolution forever, snapshots needed, deletes are awkward). |
| Retention arithmetic | 12,000 events/s × 800 B × 7 days = **5.8 TB** on disk at RF=3, ≈ 17.4 TB raw (L70 §9). |

### Topic 8 — RabbitMQ-style queues versus Kafka-style logs (L71)

| Fact | Detail |
|---|---|
| Queue semantics | A message is **consumed and removed**; delivery is a hand-off. Per-message ack, per-message retry, complex routing (exchanges, bindings), priorities and delays. |
| Log semantics | Messages are **retained**; consumption is a **cursor**. Sequential I/O, high throughput, replay, but no per-message ack and no per-message delay without extra machinery. |
| Throughput reality | A log reaches **hundreds of MB/s** per broker sequentially; a queue is in the **thousands to tens of thousands of messages/s** per broker, limited by per-message bookkeeping. |
| Choose a queue when | Work is a **task** with a lifecycle (retry, delay, dead-letter, priority), throughput is modest, and you do not need replay. |
| Choose a log when | You need **ordering at scale, replay, multiple consumers over history**, or the event is a fact rather than a task. |
| The migration trap | Teams move to Kafka "for throughput" and then rebuild delayed retries, per-message DLQ and priority queues **on top of it** — reimplementing the queue badly. |

### Topic 9 — Consumer groups, partitions, offsets and ordering (L72)

| Fact | Detail |
|---|---|
| Consumer group | A set of consumers sharing a subscription; **each partition is assigned to exactly one consumer** in the group at a time. |
| The parallelism ceiling | Consumers beyond the partition count are **idle**. 12 partitions ⇒ at most 12 consumers doing work. |
| Rebalancing | Adding/removing a consumer triggers reassignment; a naive rebalance **stops the world** (eager) or migrates incrementally (cooperative). Every rebalance is a latency spike. |
| Offset commit | The position is a cursor. Commit **after** processing (at-least-once) or before (at-most-once). Batch commits are cheaper but replay more on crash. |
| Ordering rule | Order is guaranteed **per partition**. If two events must be ordered, they must share a partition key. |
| Hot partition | One key at 4,000 events/s in a 12-partition topic puts 100 % of that key's order — and **1/12 of total capacity** — on one consumer; that consumer is the bottleneck regardless of adding others (L72 §11). |

### Topic 10 — At-most-once, at-least-once and exactly-once (L73)

| Fact | Detail |
|---|---|
| At-most-once | Commit before processing. May lose work; **never** duplicates. Correct for metrics and idempotent-overwriting updates. |
| At-least-once | Commit after processing. Never loses acknowledged work; **may** duplicate. The correct default for almost everything. |
| Exactly-once | Not a delivery property but an **effect** property: at-least-once + idempotent consumption (L74), or a transactional read-process-write within the log. |
| Why "exactly-once delivery" is a lie | The ack itself can be lost, so the broker cannot know whether the work happened. Any claim of exactly-once delivery is a claim about a closed system. |
| Kafka transactions | `read_committed` + idempotent producer + offset-in-transaction gives exactly-once **within Kafka**. Crossing into an external database re-opens the problem (L85). |
| The user-visible requirement | "Effect exactly once" — which is achievable with an idempotency key, and is what a payment provider actually means. |

### Topic 11 — Idempotent consumers and deduplication (L74)

| Fact | Detail |
|---|---|
| Idempotent | Applying the operation twice has the same effect as once. **The load-bearing property of Phase 4**, because retries and redeliveries are unavoidable. |
| Natural idempotency | `SET status = 'shipped'` (idempotent) versus `SET balance = balance - 10` (not). Prefer absolute writes to relative ones where the domain allows it. |
| Deduplication | Store the message id with the effect, **in the same transaction**; reject a repeat. Requires a durable, bounded store of seen ids (TTL ≥ the maximum redelivery window). |
| Key choice | A **caller-supplied** idempotency key, not a broker message id — because the caller retries with the same key, and the broker generates a new id per publish. |
| Retention arithmetic | 2,000/s × 86,400 s × 7 days = **1.2 G keys** at ~120 B = **145 GB** just for the dedupe table (L74 §9). Bound it by TTL, not by wishful thinking. |
| The classic bug | Dedupe check and effect in **separate** transactions: the process crashes between them, the retry is not deduplicated, and the effect happens twice. |

### Topic 12 — Dead-letter queues and retry queues (L75)

| Fact | Detail |
|---|---|
| DLQ | A destination for messages that failed permanently or exhausted retries, so they **stop blocking** the main flow and remain inspectable. |
| Retry queue | A delay mechanism: backoff by re-enqueueing with a delay (or scheduled visibility). Exponential with jitter: 1 s, 5 s, 30 s, 5 m, 30 m. |
| Retry classification | **Transient** (timeout, 503, deadlock) → retry. **Permanent** (400, schema violation, missing entity) → DLQ immediately. Retrying a permanent failure is a self-inflicted outage. |
| The DLQ is not a graveyard | It needs an owner, a dashboard, an alarm on depth, a replay tool, and a retention policy. An unmonitored DLQ is a silent data-loss mechanism. |
| BookNook's numbers | 0.4 % of 2,000 msg/s failed permanently = **8/s = 691,000/day**; with a 3-retry budget the DLQ rate fell to **0.02 % = 345/day**, which is reviewable by a human (L75 §9). |
| The reaper | A scheduled job that retries the DLQ after a fix, with a **dedupe key** so the replay is safe (L74). |

### Topic 13 — Backpressure and load shedding (L76)

| Fact | Detail |
|---|---|
| Backpressure | The signal that a downstream cannot keep up, propagated upstream so work is **not accepted** rather than **accepted and dropped**. |
| Why queues do not solve overload | A queue converts an overload into **unbounded latency** and then into a full disk. A lagging consumer with a growing backlog is an outage with a delay fuse. |
| Little's Law | `L = λ × W`. At 2,000 msg/s and 300 ms processing, 600 messages are always in flight; if capacity drops to 1,500/s the backlog grows at **500/s** and doubles every 20 minutes at that scale. |
| Shedding policy | Shed **cheapest-to-abandon** work first (recommendations before checkout), return **429/503 with Retry-After**, and keep the load balancer's queue shallow (a deep queue is a latency bomb). |
| Utilisation | Latency curves are flat to ~70 % and vertical after ~85 %. Running at 90 % utilisation for protection is buying nothing and risking a cliff. |
| The autoscaling delay | Scale-out takes 60–180 s; a spike arrives in 5 s. Autoscaling is not a load-shedding substitute. |

### Topic 14 — Circuit breakers, bulkheads and graceful degradation (L77)

| Fact | Detail |
|---|---|
| Circuit breaker | After N consecutive failures, **stop calling** for a cooldown (open), then probe (half-open). Converts a slow dependency from a latency amplifier into a fast failure. |
| Why it matters | Without it, threads block on a dead dependency until the pool is exhausted, and a **single** dependency takes down the caller — the most common cascade in practice. |
| Bulkhead | Separate pools/connections per dependency or per tenant, so one saturated path cannot consume all capacity. Named after ship compartments, for the same reason (L90 §3). |
| Degradation ladder | Full → cached/stale → reduced feature set → read-only → maintenance page. Each rung must be **built and tested**, not improvised. |
| Fallback honesty | A stale price shown as current is worse than an error. Label stale data. |
| BookNook's numbers | Recommendation calls at 2.5 s p99 blocked 200 threads, exhausting the pool in **40 s**; a 300 ms timeout + breaker removed the cascade and cut p99 from 4,100 ms to **210 ms** (L77 §7). |

### Topic 15 — Distributed locks and leases (L78)

| Fact | Detail |
|---|---|
| The problem | Mutual exclusion across processes that can pause, partition, and lose the lock without knowing it. |
| A lock is a **lease** | It has a **TTL**, an owner, and an expiry. There is no "release" that can be trusted; expiry is the only real release. |
| The pause problem | A GC pause or a partitioned node can hold a "lock" it no longer owns. **The lock cannot prevent this; only fencing can.** |
| Fencing token | A monotonically increasing number issued with the lease; the **resource** rejects writes with a token lower than the highest it has seen. This is the only correct solution. |
| The Redlock debate | Locks on N independent Redis nodes do not give safety under clock skew and pauses (Kleppmann). Use a consensus store (L81) or fencing, not a clever multi-node TTL. |
| Lease duration | Long enough to cover the work, short enough that a dead holder does not block for long. BookNook's 30 s lease with a 10 s renewal produced **40 s** stalls on GC pauses of 12 s (L78 §7). |

### Topic 16 — Leader election (L79)

| Fact | Detail |
|---|---|
| Why | Some operations need exactly **one** actor: assignment, scheduling, single-writer coordination, and any invariant that fails with two writers (L91 §7.2). |
| Mechanism | Contested via consensus (L81) or leases: candidates bid for a term, a quorum grants it, and the winner must be able to **prove** it (fencing) before acting. |
| Split-brain | Two leaders for one term, usually from a partition plus a too-generous timeout. Prevention: quorum-based grants, and the leader verifying its lease **before every side effect**. |
| Term / epoch number | Monotonic, part of every message the leader sends, so followers can reject a stale leader. |
| Failover budget | Detection 3 × heartbeat + election (1–2 RTT) + new leader warm-up. BookNook measured **2.7 s** with 1 s heartbeats and a 3-node quorum (L79 §9). |
| The anti-pattern | Leader election to avoid designing for concurrency. If two writers are survivable with idempotency, you do not need a leader — and you avoid an entire class of outage. |

### Topic 17 — Quorums (L80)

| Fact | Detail |
|---|---|
| Definition | With N replicas, a write must be acknowledged by **W** and a read must consult **R**. `R + W > N` guarantees read-write overlap. |
| The classic configurations | N=3: (W=2, R=2) balanced; (W=3, R=1) read-fast, write-fragile; (W=1, R=3) write-fast, read-fragile (and no durability guarantee). |
| What a quorum does **not** give | Atomicity, isolation, invariants across keys, or ordering. A quorum gives you a **read-your-write** guarantee under contention assumptions, nothing more (L80 §4.2). |
| Sloppy quorums and hinted handoff | Improve write availability at the cost of `R+W>N` — you can read a version that does not include an acknowledged write. |
| Latency arithmetic | Quorum latency ≈ the **median** of the N replica latencies, not the mean: for 3 replicas with p50 5 ms, p99 40 ms, a W=2 write is ~p75 ≈ 8 ms and still has a 1-in-10,000 slow tail (L80 §9). |
| The invariant rule | Anything with an invariant ("never below zero", "unique email") needs a **single writer** — quorum reads and writes will let two writers both see "1 left" and both sell it (L80 §11). |

### Topic 18 — Consensus and Raft (L81)

| Fact | Detail |
|---|---|
| Consensus | Getting a set of nodes to agree on a value **despite failures**, in a fixed order, so every node applies the same log. The substrate of every strongly consistent distributed store. |
| Raft's three parts | **Leader election** (terms, randomised timeouts), **log replication** (append + quorum ack), **safety** (a candidate can only win with an up-to-date log; committed entries are never lost). |
| The commit rule | An entry is committed when a **majority** has it **and** the leader has committed an entry from its own term. Violating the second clause is the classic Raft bug. |
| Cost | Every write is a **majority round trip** plus an fsync. Same-AZ 3-node Raft: ~**2–4 ms** per commit — 10–50× a local write, and it caps throughput per group at a few thousand writes/s. |
| When not to use it | Read-heavy workloads (put reads on followers/all nodes), high-write-per-key workloads (shard into many groups — that is what CockroachDB/Spanner do), or anything where a single writer would do. |
| Multi-group arithmetic | 600 groups × 3 nodes = 1,800 processes to operate; the cost of consensus is **operational**, not theoretical (L81 §9). |

### Topic 19 — Distributed transactions (L82)

| Fact | Detail |
|---|---|
| The problem | An atomic unit of work spanning multiple databases or services, where local ACID cannot reach. |
| The three strategies | **2PC** (atomic commit protocol, L83), **saga** (compensating workflow, L84), and **avoidance** — redesign so no cross-boundary transaction is needed (L85 outbox/CDC). |
| What is genuinely hard | Not the protocol; the **failure during the decision**. The window between "everyone voted yes" and "everyone learned the outcome" can last as long as a node is unreachable. |
| Avoidance, ranked | (1) Keep the data together (modular monolith, L86); (2) one writer + an event (outbox, L85); (3) saga (L84); (4) 2PC (L83) — only within one trust/latency domain. |
| Isolation ≠ atomicity | Even with 2PC, you do not get serializable isolation across services without distributed locking on the read path. Most "distributed transactions" are atomic-but-not-isolated. |
| The interview answer | "I would first remove the need for it: which of these two writes could be derived from the other by an event?" — then, if genuinely needed, a saga with a pivot point and compensation, or 2PC with a bounded participant count. |

### Topic 20 — Two-phase commit (L83)

| Fact | Detail |
|---|---|
| Phases | **Prepare** (each participant locks and votes) → **Commit/abort** (coordinator sends the decision). |
| The lock hold time | From prepare until the decision is applied — including any coordinator or participant failure. This is the whole cost. |
| The arithmetic | Same-AZ: 2 fsyncs + 2 RTTs = **3.2 ms p50** ⇒ **312 ops/sec** on one contended row. With one 150 ms external participant, lock hold = 158 ms ⇒ **6.3 ops/sec** (L83 §9). |
| In-doubt transactions | If the coordinator dies after prepare, participants hold locks and cannot decide. BookNook sized `max_prepared_transactions` at **32** for a 20 s failover at 0.67 txn/s ⇒ 13.3 in-doubt. |
| Heuristic outcomes | The escape hatch when a decision never arrives: heuristic commit/abort, which **breaks atomicity** and requires reconciliation. Cost: 1–3 engineer-days per event (L83 §9). |
| When it is acceptable | Few participants (2–3), short lock times, same trust domain and low latency (XA within one database cluster, or a TCC pattern) — never across a slow external API. |

### Topic 21 — Saga pattern (L84)

| Fact | Detail |
|---|---|
| Definition | A long-running workflow as a sequence of **local** transactions, each with a **compensating action** that undoes it semantically (not physically). |
| Orchestration vs choreography | An orchestrator holds the state machine (visible, debuggable, testable); choreography uses events (decoupled, but the workflow exists only implicitly across services). BookNook chose orchestration. |
| The pivot point | The step after which compensation is expensive or impossible (usually "capture payment"). Everything before is cheap to undo; everything after is not — so **pivot late**. |
| Compensation is not rollback | It is a *new* business action: a refund, a cancellation, a restock. It can fail, and its failure needs its own handling. |
| BookNook's numbers | 7-step checkout, pivot after `CAPTURE`; early pivot cost **1,900 refunds = £5,035** in one sale event. Per-step 99.5 % → 96.6 % first-pass, 99.48 % with retries; measured compensation rate **2.4 % = 960/month = 32/day** (L84 §9). |
| The tail | p50 470 ms, p99 780 ms, p99.9 3.1 s, max 22 s ⇒ reservation TTL **15 minutes** with a reconciler threshold at 10 minutes. |

### Topic 22 — Transactional outbox and change data capture (L85)

| Fact | Detail |
|---|---|
| The dual-write problem | `db.commit()` and `broker.publish()` are two systems; one can succeed while the other fails, **in either order** — losing events or publishing phantom ones. |
| The outbox | Write the event into an `outbox` **table** in the same transaction as the state change; a separate relay publishes it. Atomicity comes free from the database. |
| The relay's guarantee | At-least-once, ordered **per aggregate** if the outbox is keyed and drained in order. Duplicates are handled by idempotent consumers (L74). |
| CDC | Read the database's **log** (WAL/binlog) — Debezium, etc. — instead of a table. No polling, no application coupling, captures changes made by anything. Cost: schema coupling and a fragile connector. |
| BookNook's numbers | Polling the outbox every 200 ms at 2,000 events/s: **1.6 s** p99 publish latency; a 10 ms poll with `FOR UPDATE SKIP LOCKED` and 50-row batches brought it to **180 ms** at 4 % CPU (L85 §9). |
| The ordering trap | A single relay preserves order; N parallel relays do not. Partition by aggregate key if you need order (L72). |

### Topic 23 — Monoliths, modular monoliths and microservices (L86)

| Fact | Detail |
|---|---|
| The honest framing | A **modular monolith** is a well-structured single deployable; microservices are separately deployable services with their own data. The question is not fashion but **which axis needs independence**: scaling, cadence, failure isolation, compliance, runtime. |
| The cost arithmetic | 34 services × 18 h/month of platform overhead = **3.7 FTE**; consolidation to 11 services × 0.11 FTE = **1.2 FTE** — 2.5 FTE released (L86 §9). |
| The tail arithmetic | 5 hops each at p99 ⇒ **~4.9 %** of requests slow; the same budget requires each hop at **p99.8**. A monolith's p99 260 ms became a chain's p99 **1,095 ms**. |
| What improves | Deploy frequency 18 → **41/day**, MTTD 68 → **19 min**, shared tables 41 → **0**, engineers/service 1.2 → **4.1**. |
| The extraction criteria | Scale profile differs measurably · cadence genuinely differs · failure must be isolated · compliance boundary · runtime/language must differ. Otherwise keep it in the monolith. |
| The modular monolith discipline | Enforced module boundaries (no cross-module table access), a shared-nothing data rule, and an architecture test in CI. Cheap, and it preserves the option to extract later. |

### Topic 24 — Service discovery and configuration (L87)

| Fact | Detail |
|---|---|
| Discovery models | **Client-side** (the client queries a registry and load-balances), **server-side** (a proxy/LB does it), and **DNS** (simple, TTL-bound, no per-instance health). |
| Registry consistency | It is a distributed store (Consul/etcd/ZooKeeper) — it needs a quorum (L80) and it **must not** be on the critical request path with a hard dependency that fails closed. |
| Readiness vs liveness | **Readiness** = "send me traffic" (fails → removed from rotation, kept alive). **Liveness** = "restart me" (fails → killed). Confusing them causes restart storms under load. |
| The staleness window | Propagation 0.1–1 s + poll 1–30 s + DNS TTL 0–300 s + keep-alive (unbounded). **This window is why draining matters.** |
| BookNook's deploy failures | 12–60 instances changing hourly, 41 deploys/day, no draining ⇒ ~**1,000 failed requests per deploy**; after `preStop` 8 s drain + SIGTERM readiness 503 (−60 %), JVM DNS TTL 5 s (−30 %), watch instead of a 30 s poll (−8 %), startup probes (−2 %) ⇒ **0 failures/deploy**, deploy p99 2.4 s → **210 ms**. |
| Config safety | Rings/staged rollout, validation before propagation, one-click revert, and a blast-radius calculation: 400 rps × 660 s = **264,000** failed requests all-at-once versus **2,520** with a 1/48 canary (≈100× reduction). |
| Load balancing | Power-of-two-choices beats random under skew: 48 instances with 1 % degraded gives random p99 **900 ms** versus P2C **120 ms** (L87 §9). |

### Topic 25 — Observability: logs, metrics and traces (L88)

| Fact | Detail |
|---|---|
| The three signals | **Metrics** (aggregated, cheap, good for alerting), **logs** (detailed, expensive, good for causes), **traces** (causal, sampled, good for "where the time went"). None replaces another. |
| RED and USE | RED for request-driven services (Rate, Errors, Duration); USE for resources (Utilisation, Saturation, Errors). Pick by what the thing *is*. |
| Percentiles | Never average percentiles. Use **histograms** with a bucket edge at the SLO (500 ms), and compute p99 from merged buckets. |
| The cardinality bomb | 10 services × 40 routes × 5 methods × 8 statuses × 12 buckets = **192,000** series (fine); adding `customer_id` with 125,000 values ⇒ **2.1 M** active series, Prometheus 6 → 58 GB, OOM, a **26-minute blind spot**, and the bill from £1,400 to **£18,600/month** (L88 §9). |
| Sampling | Head sampling 1 % + **tail sampling** keeping 100 % of errors and anything ≥500 ms. 1 % of a 0.3 % error rate = **0.03 sampled per 1,000** — which is why tail sampling is mandatory for errors. |
| Cost arithmetic | Traces at 21.6 KB/s sampled = **1.9 GB/day**; unsampled = **186 GB/day** (100×). A request-path log was 12 GB/day versus 62 GB/day for per-call INFO. |
| Alerts | Alert on **symptoms** (SLO burn, L89) not causes (CPU). A cause-based alert is a hypothesis; a symptom-based alert is the thing the user feels. |

### Topic 26 — SLIs, SLOs and error budgets (L89)

| Fact | Detail |
|---|---|
| SLI | A measured ratio: good events / valid events. *"Requests under 500 ms / all valid requests."* Precise numerator and denominator, both written down. |
| SLO | The target for that SLI over a window (28 days, rolling). BookNook checkout: availability **99.5 %**, latency 99.0 % < 500 ms; catalogue 99.9 %. |
| Error budget | `1 − SLO`. At 20 M requests and 99.5 %, the budget is **100,000 failures = 3 h 22 m = 7.2 min/day** (L89 §9). |
| The nines table | 99 % → 6 h 43 m/mo · 99.5 % → 3 h 22 m · 99.9 % → **40 m 19 s** · 99.99 % → **4 m 2 s**. Each nine is ~10× the cost (L89 §9 step 4: 99.5→99.9 ≈ £140k/yr, 99.9→99.99 ≈ £610k/yr, 99.99→99.999 ≈ £2.1M/yr). |
| Burn rate | `error_rate / (1 − SLO)`. Multi-window alerts: **14.4×** over 1 h + 5 m (page), **6×** over 6 h + 30 m (page), **1×** over 3 d + 6 h (ticket). |
| The denominator rule | Exclude caller 4xx (`validation|auth_expired|idempotency_conflict`) but **count 429** — the user was not served. Getting this wrong makes the SLO meaningless in both directions. |
| Dependency arithmetic | 0.999 end to end with 3 serial dependencies ⇒ each must be ≥ **99.97 %**. And the budget is not free: a 40-minute gateway outage is a **22× burn = 61 %** of the monthly budget in one hour. |

### Topic 27 — High availability and disaster recovery (L90)

| Fact | Detail |
|---|---|
| The distinction | **HA** survives a component (process, node, AZ) automatically, in seconds-to-minutes, with no data loss. **DR** survives a site/region, usually with interruption, and is measured rather than designed. |
| The formula that matters | `A(system) ≈ min over SHARED dependencies × A(redundancy) × A(degradation)` — not the replica count (§4.6). |
| Correlated failures | Shared region, deploy, config push, database, DNS, certificate, quota, NAT, cache, human. Each one defeats naive redundancy (§4.4). |
| The cost curve | Multi-AZ +£48k/yr for 99.5 → 99.98 %; warm standby +£210k for 99.98 → 99.99 %; active-active +£390k for 99.99 → 99.995 % (§9 step 3). |
| The audit beats the architecture | Fixing **14 SPOFs** for 9 engineer-weeks + £2,100/month moved monthly availability 99.61 → 99.93 % — ~**28×** more cost-effective per minute than a second region (§9 step 4). |
| Testing | The ladder of evidence: a design < a runbook < a runbook run once < a **timed quarterly drill** < continuous chaos. BookNook found its RTO was 7.6× the claim in the first drill (§7.2). |

### Topic 28 — Active-passive versus active-active (L91)

| Fact | Detail |
|---|---|
| Active-passive | One site serves; the other is hot/warm/cold. RTO is dominated by **warm-up** and by the **DNS TTL**. |
| Active-active | Both serve. Not a redundancy setting but a **data-model decision**: every key needs one writer, or a merge rule. |
| The three data models | **Single-writer per key** (home region — recommended), **CRDTs** (conflict-free but only for specific shapes), **application merge** (needs a human). |
| Why CRDTs do not prevent overselling | A counter converges correctly; **"never below zero" is an invariant**, not a counter property. BookNook sold 10 items from 5 in the trial (§7.2). |
| The cost | Active-active is ~**2×** capacity (each region must handle 100 % on failure) plus engineering. BookNook: warm standby at +38 % cost and 6 weeks delivered RTO 14 min against a 15-min requirement; active-active was 2× and 9 months (§7.1). |
| The RTO truth | "RTO ≈ 0" is a claim about **packets**. Re-homing 210,000 entities took **8.2 minutes** — that is the real RTO for orders (§9 step 5). |
| Failback | A second migration. Keep replication bidirectional, or declare the new region permanent and migrate deliberately (§4.7). |

### Topic 29 — RTO and RPO (L92)

| Fact | Detail |
|---|---|
| Definitions | **RTO** = maximum acceptable restoration time. **RPO** = maximum acceptable data loss, as a duration. `MTD ≥ RTO + WRT`. |
| RTO is a sum | Detection + decision + execution + warm-up + verification + routing. BookNook's 3 h 47 m breakdown: 94 min execution, 60 min DNS, 41 min decision, 32 min verification, 30 min detection (§4.2). |
| The restore-rate floor | `RTO ≥ data/restore_rate + rebuild + verify`. 1.2 TB at 240 MB/s = 83 min + 40 min + 25 min = **2 h 28 m floor** — so a 30-minute RTO is arithmetically impossible with full restore (§4.4). |
| RPO mechanisms | Nightly (24 h) · WAL/PITR (seconds–minutes) · async replica (= **current lag**) · sync replica (0, latency cost) · quorum write (0 for acked writes). |
| Replicas ≠ backups | Replication copies a `DELETE` in under a second. PITR and a **delayed replica** are the corruption defences (§4.3). |
| Tiering | Tier 1 (payments: RPO 0–1 min, RTO 15 min) · tier 2 (catalogue: 5 min / 1 h) · tier 3 (rebuildable: 1 h / 4 h) · tier 4 (analytics: 24 h / 3 d). Tiering saves ~**60 %** versus tier 1 for everything (§9 step 5). |
| Measured, not designed | BookNook: designed 30 min / 5 min; measured **3 h 47 m / 4 h 20 m**; after remediation (lag alarm, automated failover, pre-authorisation, verification suite, TTL fix) **2 m 35 s / 28 s** — for ~5 engineer-weeks and £0 of infrastructure (§7.1). |

---

## 2. Glossary

| Term | Meaning |
|---|---|
| **ACID** | Atomicity, Consistency, Isolation, Durability — the transaction guarantees *within one database*. |
| **At-least-once** | Delivery may duplicate but will not silently drop acknowledged work. The safe default. |
| **Backpressure** | A downstream signal propagated upstream so work is refused rather than accepted-and-dropped. |
| **Bulkhead** | Isolated resource pools per dependency or tenant, so one saturation cannot spread. |
| **Burn rate** | `error_rate / (1 − SLO)`; how fast the error budget is being consumed relative to the SLO window. |
| **CAP** | Under a **partition**, choose consistency or availability. Not a system-wide label; a per-operation choice. |
| **CDC (change data capture)** | Reading a database's log to emit changes as events, without dual writes. |
| **Choreography** | A workflow defined implicitly by services reacting to each other's events (contrast: orchestration). |
| **Circuit breaker** | A state machine (closed → open → half-open) that stops calling a failing dependency. |
| **Compensation** | A *new* business action that semantically undoes a completed step (refund, cancel, restock). Not a rollback. |
| **Conflict resolution** | The rule that decides what a value becomes when two sites wrote it concurrently (LWW, vector clocks, CRDTs, home region). |
| **Consumer group** | Consumers sharing a subscription; each partition is assigned to exactly one member at a time. |
| **Correlated failure** | Two "redundant" components failing for the same reason — the failure mode that defeats naive redundancy. |
| **CRDT** | Convergent replicated data type: merges are commutative and deterministic. Solves convergence, not invariants. |
| **DLQ (dead-letter queue)** | Destination for messages that failed permanently or exhausted retries, so they stop blocking the flow. |
| **Dual write** | Writing to two systems (database + broker) without atomicity — the bug that the outbox pattern exists to fix. |
| **Error budget** | `1 − SLO`: the allowable failure over the window, spent deliberately on releases and risk. |
| **Fencing token** | A monotonically increasing number issued with a lease; the resource rejects stale tokens. The only correct defence against a paused lock holder. |
| **HLC (hybrid logical clock)** | Physical time plus a logical counter, keeping ordering consistent with causality across clock jumps. |
| **Home region** | The single region that owns writes for a key in active-active. Removes conflicts by construction. |
| **Idempotency key** | A caller-supplied identifier that lets a repeated request be recognised and its effect applied once. |
| **In-doubt transaction** | A prepared transaction whose coordinator failed before delivering the decision; participants hold locks and cannot decide. |
| **Lamport clock** | A logical counter giving a partial order consistent with causality; cannot distinguish concurrent events. |
| **Leader election** | Choosing a single actor per term via consensus or leases, with fencing to prevent two. |
| **Lease** | A time-bounded grant of ownership. Expiry is the only trustworthy release. |
| **Little's Law** | `L = λ × W` — concurrency equals arrival rate times time in system. |
| **Liveness probe** | "Is this process alive?" — failing it causes a **restart**. Never use it to manage traffic. |
| **LWW (last-write-wins)** | A conflict policy choosing the newest timestamp or version. Silent data loss, and clock-dependent. |
| **MTD (maximum tolerable downtime)** | The point beyond which the business is permanently harmed. `MTD ≥ RTO + WRT`. |
| **Multi-writer** | More than one site may write the same data concurrently; requires conflict resolution or an invariant-safe design. |
| **Orchestration** | A central component holding the workflow state machine (saga, retries, timeouts). |
| **Outbox** | An events table written in the same transaction as the state change; a relay publishes it. |
| **PACELC** | If **P**artition then **A** or **C**, **E**lse **L**atency or **C**onsistency — the everyday trade CAP omits. |
| **Partial failure** | Some part of the system is broken, and the parts disagree about it. The defining problem of distributed systems. |
| **Partition (network)** | Sets of nodes cannot communicate while all remain alive. Indistinguishable from failure from inside either side. |
| **PITR** | Point-in-time recovery: base backup plus log replay to an arbitrary moment. |
| **Pivot point** | In a saga, the step after which compensation becomes expensive or impossible. |
| **Power of two choices** | Pick two instances at random, send to the less loaded — dramatically better tail latency than random under skew. |
| **Prepared transaction** | A participant that has voted yes and holds locks, awaiting the coordinator's decision. |
| **Quorum** | A majority of replicas; with `R + W > N` reads and writes overlap, guaranteeing read-your-writes across the set. |
| **Raft** | A consensus protocol: leader election, replicated log, and safety rules that never lose a committed entry. |
| **Readiness probe** | "Should I receive traffic?" — failing it **removes from rotation** without restarting. |
| **Retry budget** | A cap on retries as a fraction of requests (e.g. 10 %), preventing retry storms. |
| **RPO** | **Recovery point objective** — the maximum acceptable data loss, as a duration. |
| **RTO** | **Recovery time objective** — the maximum acceptable time to restore service. |
| **Saga** | A long-running workflow of local transactions with compensating actions for each step. |
| **SLI / SLO** | Service level indicator (a measured ratio) / service level objective (its target over a window). |
| **Split-brain** | Two nodes (or regions) both believing they are authoritative, producing divergence. |
| **Staleness window** | Registry propagation + poll interval + DNS TTL + keep-alive — why pods receive traffic after they "should" be gone. |
| **Tail sampling** | Sampling that decides **after** the request completes, keeping all errors and slow traces. |
| **Term / epoch** | A monotonic number identifying a leadership period, used to reject stale leaders. |
| **Test pyramid (distributed)** | Unit → contract → integration → chaos; each level finds a different class of failure. |
| **Two-phase commit** | Atomic commit across participants: prepare (lock, vote), then commit/abort. Correct, and expensive in lock hold time. |
| **Vector clock** | One counter per node; detects concurrency, at O(n) size. |
| **Warm-up** | The time a promoted or scaled instance needs before it is *useful* (cache, index, pool, JIT). Part of the RTO. |
| **Wormhole / logical hole** | A gap in a log where a message was lost or filtered — consumers of a log must handle it. |
| **WRT (work recovery time)** | Time to make the restored system *correct and useful*: reconcile, drain backlogs, verify totals. |
| **Write amplification** | Bytes written to storage per logical byte written (WAL + page + index + replica + compaction). |

---

## 3. Twenty quiz questions

Answer in your own words. Calculation questions expect a number *and* the working.

1. A payment call times out after 5 s. Explain precisely what the caller knows and does not know, and give the two mechanisms that make a retry safe. Name the state of the network that makes this the common case rather than the rare one.
2. BookNook's checkout calls 5 services synchronously, each 99.9 % available, and a sixth each at 99.95 % for the database. Compute the end-to-end availability. Then state the single change that would raise it most, and compute the result.
3. A lease has a 30 s TTL, renewed every 10 s. The holder pauses for 45 s. Describe exactly what another node can do during the pause, what the pausing node believes on resume, and the mechanism that prevents its next write from corrupting state.
4. Explain why `R + W > N` gives read-your-writes but not an invariant such as "stock never goes below zero". Give the minimum change to the data model that makes the invariant enforceable.
5. A Kafka topic has 12 partitions and receives 24,000 events/s, of which 9,000/s carry one merchant key. Compute the per-partition average rate, the rate on the hot partition, and the consumer count beyond which adding consumers does nothing. Then name the fix.
6. Explain the difference between at-least-once delivery and exactly-once *effect*, and describe the exact transactional arrangement that makes a consumer's effect idempotent. Then explain what breaks if the dedupe row is written in a separate transaction.
7. A saga step has 99.5 % success and is retried 3 times. Compute the first-pass success of a 7-step saga and the success after retries. Then explain why the compensation rate is not `1 − success` in practice, and give the number BookNook measured.
8. Compute the lock hold time and throughput of a 2PC transaction with two same-AZ participants (2 fsyncs + 2 RTTs at 3.2 ms) and a third external participant at 150 ms. State the operation rate on a single contended row in both cases and the design conclusion.
9. Compare a RabbitMQ-style queue and a Kafka-style log for: 20,000 events/s, four independent consumers, need for replay, need for per-message delay and priority. State which you choose and what you would have to build if you chose the other.
10. BookNook's outbox relay polls every 250 ms with 40-row batches at 2,000 events/s. Compute the p99 publish latency contribution from the poll, then design the changes that would bring it under 250 ms, naming the SQL construct that allows parallel relays without losing order.
11. A service has 10 routes, 5 methods, 8 statuses and 12 latency buckets, and someone adds a `customer_id` label with 125,000 values. Compute the series count before and after and explain, quantitatively, why the metric store fails while the query is still cheap.
12. Given a 99.5 % SLO over 28 days and 20 M valid requests, compute the error budget in failures, minutes, and minutes per day. Then compute the budget consumed by a single 40-minute outage at 22× burn.
13. Design the alerting for a 99.9 % SLO: give the multi-window burn-rate thresholds, the windows, the page-versus-ticket decision, and the reasoning for each threshold. State what a single-threshold 5-minute alert gets wrong.
14. BookNook runs 41 deploys/day on 48 instances with a 30 s discovery poll, a 3600 s DNS TTL and no `preStop` hook. Estimate failed requests per deploy and state which of the four fixes removes the most failures, with the percentage.
15. Explain why a data-centre-wide power failure should take down all replicas of a 3-node Raft group but should not lose committed data, and name the two things a correct implementation must have done before the failure.
16. Active-active: two regions, inventory as a CRDT counter, 5 items left. Both regions sell 5. Explain what the merge produces, why it is "correct" as a data type and wrong as a business outcome, and what the correct model is.
17. A DR plan claims 30 min RTO / 5 min RPO. A drill measures 3 h 47 m / 4 h 20 m. Decompose the difference into its components, rank the five fixes by minutes recovered per engineer-week, and compute the storage cost of achieving the 5-minute RPO with 5-minute WAL archiving on a 1.2 TB database growing 4 GB/day.
18. Compute the monthly availability of a system with a shared database at 99.95 % and a payment provider at 99.9 % in series, ignoring everything else. Then compute it after the payment provider is moved off the critical path with a queued authorisation, and state which component now binds.
19. BookNook's orders database is 1.2 TB, restore rate 240 MB/s, index rebuild 40 min, verification 25 min. Compute the restore floor and state whether a 30-minute RTO is achievable. Then compute the floor if the data were split into six shards of 200 GB.
20. Define SLI, SLO, error budget, RTO, RPO, MTD and WRT in one sentence each, then explain in one paragraph how a *release* decision, a *DR* decision and a *staffing* decision would each change if the checkout SLO moved from 99.5 % to 99.9 %.

*Reply with your answers and they will be assessed question by question — marked correct, partially correct or incorrect, with the reasoning and the corrected version where needed. Answers are deliberately not shown here.*

---

## 4. Five practical exercises

All five run locally. Assume Docker, Python 3.11+, and `docker compose`; each exercise is designed to be completable in 45–90 minutes.

### Exercise 1 — Kill a broker and observe every failure mode

```bash
# 3-node Kafka (KRaft) + a producer + two consumers, one deliberately slow.
docker compose up -d
# After 60 s of steady traffic, kill one broker mid-batch:
docker compose stop kafka-2
```

Deliverables:

1. A timeline of what the producer, the consumers and the consumer group's coordinator each did, in the order it happened, with timestamps from the logs.
2. The **measured** duplicate rate on the fast consumer and the **lag** on the slow one; explain the difference using the commit/ack model (L73).
3. Restart the broker and show the rebalance: how long the group was not consuming, and what happened to the offsets.
4. Now set the consumer's `max.poll.interval.ms` to 5 s with a 12 s processing time, and explain the loop you observe (redelivery every 5 s, duplicates forever) and the two fixes.

What you should conclude: **partition assignment is not static, and an ack policy is a data-loss policy.**

### Exercise 2 — Build an idempotent consumer and break it deliberately

```python
# A consumer that charges a payment and records the charge, with an
# idempotency key, a dedupe table, and an injectable crash point.
```

Deliverables:

1. Run 10,000 messages with a 2 % redelivery injection and show **zero** double charges.
2. Move the dedupe insert into a separate transaction and reproduce the double charge; capture the log line that proves it (L74 §11).
3. Add a TTL of 24 h to the dedupe table, then replay messages from 48 h ago and explain the resulting duplicate charges — and why the TTL must be **longer** than the maximum redelivery window, not shorter.
4. Measure the dedupe table's size and growth rate and compute the 7-day retention cost at 2,000 msg/s.

What you should conclude: **idempotency is a transaction boundary, not a boolean.**

### Exercise 3 — Reproduce a retry storm and then fix it

```bash
# A load generator, a service with an artificial 1 % failure rate and 800 ms
# p99, and 5 upstream hops. Run three variants.
```

Variants: (a) no retries; (b) 3 immediate retries; (c) 3 retries with exponential backoff + full jitter + a 10 % retry budget.

Deliverables:

1. Total requests reaching the leaf service in each variant, and the amplification factor versus (a).
2. p50, p95 and p99 end-to-end latency in each variant; explain why (b) makes the tail *worse* than (a) even though it improves the error rate.
3. The load at which (b) saturates the leaf service, and whether (c) avoids it.
4. Add a circuit breaker and show the recovery curve; explain the half-open state's role in the graph.

What you should conclude: **retries are a load amplifier, and jitter is not a nicety — it is what makes retries safe at scale.**

### Exercise 4 — Measure a failover and compute a real RTO

```yaml
# PostgreSQL primary + streaming replica (async), a client with retries, and
# a script that promotes and repoints — deliberately WITHOUT automation first.
```

Deliverables:

1. Time each phase: detection (health check interval × threshold), decision (you, with a stopwatch), execution (promotion), verification, routing (use a DNS TTL of 300 s in a second run and 30 s in a third).
2. Write 1,000 rows, fail over, and count the **actual** rows lost — that is the measured RPO, and compare it with the replica's lag at the moment of failure.
3. Make the replica intentionally lag by 60 s (run a long transaction on the primary) and repeat: this is the "unmeasured lag" failure from L92 §7.1.
4. Add a lag metric and an alarm; set the threshold at half your target RPO and justify it.
5. Restore from the base backup plus WAL into a fresh container and time it end to end, including verification. Compare that figure with your "designed" RTO and explain the gap.

What you should conclude: **RTO is a sum of six phases you can measure, and RPO is a lag you must alarm on.**

### Exercise 5 — Build the SLO and its burn-rate alerts

```python
# A service emitting request outcomes; a Prometheus + a recorder; a 28-day
# accelerated clock (1 simulated hour per real second).
```

Deliverables:

1. Define the SLI precisely (numerator and denominator), then implement it. Include the `4xx` exclusion rule and the 429 case (L89 §4.4) and show the difference in the computed SLI with and without.
2. Compute the error budget for 99.5 % over 28 days at your traffic rate, in minutes.
3. Implement the three multi-window burn-rate alerts (14.4× / 6× / 1×) and demonstrate: a 40-minute full outage (pages, then holds), a 0.8 % background error rate (ticket, slowly), and a 2-minute blip (neither) — explain which alert fired for which and why.
4. Deliberately misconfigure a histogram so the 500 ms boundary falls *between* buckets, then show how much the p99 estimate shifts; fix the buckets and re-measure. (Use **`le="0.5"` as an explicit bucket edge.**)
5. Add a dependency: an upstream that fails 20 % of requests. Show the SLO impact and write the one-page error-budget policy that says what happens when the budget is exhausted (freeze releases? fix reliability? both, with dates).

What you should conclude: **an SLO is only a management tool if its alerts are actionable and its budget has consequences.**

---

## 5. One mini system-design exercise

**Design BookNook's order pipeline: from "Place order" to "book on its way", at 400 rps sustained and 3,000 rps during a flash sale, with a 99.5 % checkout SLO, a 15-minute RTO, a 1-minute RPO, and an unreliable payment provider.**

Work through the following, in order, and write it up as a one-page design plus a capacity and failure table. Assume: 20 M orders/28 days, AOV £18, 7-step checkout, inventory in the same database as orders, search index rebuilt from the catalogue, email provider 99.9 %.

**Phase A — boundaries and data.**
1. Draw the service boundaries and name the **one** write that must be a single serialised decision (inventory decrement). Justify why it cannot be eventual (L80 §4.2, L91 §7.2).
2. Decide where state lives: which data is authoritative where, and which is derived (search index, recommendation cache, analytics). State the **reconstruction path** for each derived store.
3. Choose the durability mechanism for the order record: local transaction + outbox (L85), and state why a dual write would break the 1-minute RPO.

**Phase B — the workflow.**
4. Lay out the 7 saga steps, marking the **pivot** and the compensation for each. Give the compensation's failure mode (a refund that fails, a restock that fails) and its handler.
5. Set retry and timeout policy per step, with numbers: payment provider p99 3 s and 0.4 % failure ⇒ timeout, retry budget, circuit-breaker threshold.
6. Specify the reservation TTL and the reconciler threshold, using the tail measurements from L84 (p50 470 ms, p99 780 ms, p99.9 3.1 s, max 22 s).

**Phase C — overload and degradation.**
7. Design the shed policy for the flash sale: what is refused first, what is queued, what degrades, and the exact response codes. Compute the capacity shortfall at 3,000 rps against 1,200 rps of provisioned checkout capacity.
8. Give the degradation ladder for a payment-provider outage: what works, what does not, and what the user sees. Justify why "queue the authorisation" is or is not acceptable here (L91 §12, L92 §4.1).

**Phase D — reliability.**
9. Compute the SLO burn of: a 40-minute provider outage, a 25-minute database failover, and a bad deploy at 2 % error for 12 minutes. Which breaches first?
10. Design the discovery, config and rollout safety: rings, canary, drain, the failure counts per deploy from L87 §9, and the blast-radius arithmetic for a config push at 400 rps.
11. Specify RTO/RPO for each data tier with the mechanism, the measured drill cadence, and the DR register entry. State which component bounds your RTO and why (L92 §4.2).

**Phase E — observation.**
12. Name the five dashboards and the alerts on each, with symptoms rather than causes. Give the RED metrics per service and the one business metric that would have caught the §5 L90 outage fastest.
13. Specify the trace sampling policy and the cardinality budget, with the arithmetic from L88 §9 showing why `customer_id` is banned as a metric label.

**Marking guide (what a strong answer contains).** A single serialised inventory decision with an explanation of the invariant. Derived stores with explicit reconstruction paths. An outbox, not a dual write. A pivot placed late (after capture), with compensations that can themselves fail. Numbers everywhere: timeouts derived from p99s, TTLs derived from the saga tail, capacity derived from rps. A shed policy that protects checkout by refusing recommendations and personalisation. An SLO burn comparison showing the provider outage dominates. A TTL and a drain window for discovery, and a canary ring for config. Tiered RTO/RPO with **measured** values and a named drill cadence. Alerts on burn rate and on user-visible symptoms. Trace sampling that keeps all errors.

**A weak answer** draws boxes for services with no stated boundaries, uses eventual consistency for inventory "for availability", dual-writes to Kafka, has a pivot at step 2 with no compensation failure handling, states RTO as "we have backups", and lists CPU alerts. Each of those is a specific lesson in this phase — and each is a specific interview question in §6.

---

## 6. Common interview questions

| # | Question | What the interviewer is actually probing |
|---|---|---|
| 1 | "What makes distributed systems hard, in one sentence?" | Whether you say "partial failure" rather than "network latency". |
| 2 | "A call times out. What do you know?" | Understanding that a timeout is an unknown outcome, and that idempotency is the consequence. |
| 3 | "Design a retry policy." | Jitter, backoff, budgets, and which errors are retryable — plus the amplification arithmetic. |
| 4 | "Two events, different services. Which happened first?" | Clocks: Lamport/vector/HLC, and why wall-clock timestamps are not an ordering mechanism. |
| 5 | "Queue or log for this workload?" | Whether you choose by semantics (task vs fact) rather than by throughput folklore. |
| 6 | "How many consumers do you need?" | Little's Law, partition ceilings, and hot keys. |
| 7 | "How do you guarantee exactly-once?" | Whether you know it is an *effect* property and can name the dedupe transaction. |
| 8 | "Your consumer keeps reprocessing the same message. Why?" | Visibility timeout vs processing time, commit ordering, rebalance churn. |
| 9 | "Where do failed messages go, and who looks at them?" | DLQ ownership, alarms, replay safety — the operational half. |
| 10 | "The queue is 4 million messages deep. Diagnose." | Producer/consumer rate arithmetic, hot partitions, poison messages, and why "add consumers" may do nothing. |
| 11 | "Design the checkout so a dead recommendation service cannot take it down." | Timeouts, breakers, bulkheads, the degradation ladder, and whether the fallback is deployed. |
| 12 | "How would you implement a distributed lock?" | Leases, fencing tokens, the pause problem, and why Redlock is a trap. |
| 13 | "Two nodes both think they are the leader. What now?" | Quorum grants, terms, fencing, and the partition behaviour. |
| 14 | "Explain `R + W > N`." | Precision: read-your-writes, not atomicity or isolation; and where it fails for invariants. |
| 15 | "Explain Raft well enough that I believe you understand the commit rule." | Sealed terms and the current-term requirement — the standard depth probe. |
| 16 | "When is 2PC acceptable?" | Lock hold arithmetic, participant count, trust/latency domain, in-doubt sizing. |
| 17 | "Design a saga for this checkout. Where's the pivot?" | Compensation design, pivot placement, and compensation-failure handling. |
| 18 | "You commit to the database and publish to Kafka. What can go wrong?" | Dual writes; whether you reach for the outbox or CDC, and can compare them. |
| 19 | "Should this be a microservice?" | The extraction criteria (scale, cadence, isolation, compliance, runtime) and the organisational cost. |
| 20 | "A pod receives traffic for 10 seconds after it should be gone. Why?" | Staleness window, `preStop`, readiness on SIGTERM, keep-alives, DNS TTLs. |
| 21 | "p99 is 4 seconds and CPU is 40 %. Where do you look?" | Queues and pools, not CPU: saturation of a dependency, thread pools, GC, a slow hop. |
| 22 | "What are your SLOs, and what happens when the budget is spent?" | Whether SLOs are real (consequences, policies) or decorative. |
| 23 | "Design alerting for this service." | Burn-rate multi-window alerts, symptom over cause, and the ticket/page split. |
| 24 | "How do you make this highly available?" | Shared-dependency analysis and correlated failures — not replica counts. |
| 25 | "Active-active or active-passive, and what does it cost?" | Whether you treat it as a data-model decision with a cost multiplier. |
| 26 | "What's your RTO, and how do you know?" | Measured drills, the six phases, the restore-rate floor, and WRT. |
| 27 | "Your database is 1.2 TB. Restore it in 20 minutes." | The arithmetic floor, and the three honest options (§4.4 of L92). |
| 28 | "A user updates their address, refreshes, and sees the old one." | Read-your-writes across replicas — the most common real distributed-systems bug. |

---

## 7. Topics to revisit if you struggled

| If you found this hard | Re-read | Why it matters later |
|---|---|---|
| Partial failure and indefinite outcomes | L64, L65 | Every Phase 5 design has an unreliable dependency; if you cannot state what a timeout leaves unknown, your idempotency design will be guesswork. |
| Clocks and event ordering | L66 | Feed ordering, session semantics and dedup windows in Phase 5 all depend on whether timestamps are trustworthy. |
| Timeouts, retries and jitter | L65 | Phase 5's rate-limiter and crawler designs are retry-policy problems in disguise; a crawler with naive retries is a self-DDoS. |
| Sync vs async and the availability product | L67 | Choosing sync vs async per call is the single biggest availability lever in every Phase 5 design. |
| Queue vs log semantics | L71 | Chat, news feed and notification designs are decided by this choice before any schema exists. |
| Consumer groups and ordering | L72 | Real-time chat ordering and news-feed fan-out both live or die on partition key choice. |
| Delivery semantics and idempotency | L73, L74 | Payment processing (Phase 5) is *entirely* this lesson with money attached. |
| DLQs, retries and poison messages | L75 | Notification and webhook delivery designs are DLQ designs. |
| Backpressure and shedding | L76 | The flash-sale and rate-limiter designs are shedding policies; without Little's Law you will size queues by vibes. |
| Circuit breakers and degradation | L77 | Video streaming's bitrate ladder and e-commerce's "buy now" path are both degradation ladders. |
| Locks, leases and fencing | L78, L79 | Ride-sharing dispatch, inventory reservation and payment capture all need a single-writer decision. |
| Quorums and consensus | L80, L81 | Any "strongly consistent" claim in Phase 5 is a claim about quorums; if you cannot state `R + W > N`, audit it as marketing. |
| 2PC vs saga vs outbox | L82–L85 | The e-commerce/inventory and payment-processing designs are exactly this decision tree, at scale. |
| Monolith vs microservice economics | L86 | Every Phase 5 answer needs a service count, and a justification for it that is not fashion. |
| Discovery, drain and config safety | L87 | Deploy-time failure is the most common production outage; Phase 5 designs that ignore it are untestable. |
| Metrics, traces and cardinality | L88 | The metrics/logging platform design (L106) is this lesson as a product. |
| SLIs, SLOs and burn rates | L89 | Phase 5's reliability sections and every "how would you operate this?" question start here. |
| HA, correlated failures, SPOF audits | L90 | The video-streaming and ride-sharing designs are dominated by failure isolation. |
| Active-active and conflict resolution | L91 | News feed and chat multi-region designs need per-entity ownership; without it you will promise something you cannot deliver. |
| RTO/RPO arithmetic and restore floors | L92 | The file-storage and payment designs both quote an RTO; if it is not derived from restore rate, it is a wish. |
| Capacity arithmetic in general | L64 §9, L68 §9, L86 §9, L89 §9 | Every Phase 5 design interview is scored on whether the numbers are derived. This remains the most transferable skill in the course. |

---

**NEXT LESSON: Phase 5, Topic 1 — Design Pastebin (L93)**

---
