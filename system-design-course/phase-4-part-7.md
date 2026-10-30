# Lesson 86 — Monoliths, Modular Monoliths and Microservices

*Phase 4, Topic 23*

## 1. Simple definition

These three words describe **two different things at once**, which is why the argument about them is so often confused: **deployment topology** (one process or many) and **modularity** (how well-separated the parts are).

- **Monolith**: one deployable unit. All the code runs in one process, talks through function calls, and shares a database. Modularity is entirely a matter of internal discipline.
- **Modular monolith**: still one deployable unit, but with **enforced internal boundaries** — modules own their data, expose explicit interfaces, and cannot reach into each other's tables. It gives you most of the design benefit of microservices without the distributed-systems cost.
- **Microservices**: many deployable units, each owning its data, communicating over the network.
- **Distributed monolith**: the failure mode of a failed decomposition — many deployables that must be deployed together, share a database, and call each other synchronously in chains. It has the costs of both topologies and the benefits of neither. **This is the most common real-world outcome of a microservices migration, and it is the thing to be able to name.**

Terms defined before use:

- **Deployment unit**: what you build, ship and restart as one thing.
- **Bounded context**: a boundary within which a domain model has one consistent meaning (a `Product` means something different to the catalogue and to the warehouse).
- **Data ownership**: exactly one service may write a given table. The boundary is real only if this is true.
- **Independent deployability**: you can ship one part without shipping the others. This is the definitional benefit of microservices, and the only one that reliably pays.
- **Conway's law**: system structure mirrors the communication structure of the organisation that built it.
- **Strangler fig**: incremental migration where new code grows around the old until the old can be removed.
- **Nano-service**: a service too small to justify its operational cost — usually one per entity, created by splitting on nouns instead of on business capabilities.

The one sentence to carry out of this lesson: **a network boundary converts a function call into every problem in Lessons 64–85 — so you should pay that price only for a boundary you actually need, and the default should be a modular monolith, not microservices.**

## 2. Why it exists

Because the choice determines how many of this phase's problems you have.

```
  In a monolith, "reserve stock and create the order atomically" is:
      BEGIN; INSERT orders...; UPDATE stock SET qty=qty-1 WHERE ...; COMMIT;
      ⇒ 3 ms, one transaction, no compensation, no saga, no outbox.

  In microservices, the same requirement is:
      a saga with compensations and a reconciler (L84), idempotent steps (L74),
      retries with DLQs (L75), an outbox and CDC (L85), circuit breakers (L77),
      distributed tracing to debug it (L88), and a distributed transaction
      design discussion (L82) to justify not using 2PC (L83).
      ⇒ 470 ms, 7 services, and an on-call rota per service.
```

That comparison is not an argument against microservices. It is an argument for **knowing what you are buying**, because the costs are real and permanent while the benefits are conditional:

| Benefit claimed | Actually delivered when |
|---|---|
| Independent deployability | Boundaries are real (no shared DB, no lockstep releases) |
| Independent scaling | One part genuinely has a different load profile |
| Fault isolation | Failures are contained by bulkheads and timeouts (L77), not by hoping |
| Technology heterogeneity | You have a genuine reason and can staff the operational cost |
| Team autonomy | Teams own *and operate* their services |
| Faster change | The monolith was actually the bottleneck — often it was the tests, or the review process, not the deploy |

BookNook's own history is the useful data point, and it is typical:

```
  2019  Rails-style monolith, 940k lines, 6 teams
        deploy: 40 minutes, once a day, release train on Thursdays
        the actual complaint was not "the monolith"; it was "our test suite
        takes 52 minutes and we deploy once a day"

  2021  broke into 34 microservices (the "big bang")
        deploy independence: real, and it did help the 4 teams whose services
        changed daily
        new problems: 14 services needed a saga, a shared database grew back,
        p99 went from 180 ms to 620 ms on the checkout chain, and 9 of the 34
        services were changed less than once a month and cost a full on-call
        rotation each

  2023  consolidated to 11 services + a modular monolith
        the 11 are the ones with a genuinely different scaling profile, change
        cadence, or compliance boundary
        p99 back to 240 ms; on-call rotas down from 34 to 12; deploy frequency
        up (multiple times a day for the services that need it)
```

The lesson from that sequence, stated generally: **the number of services you need is roughly the number of genuinely different reasons to deploy, scale, or fail — not the number of nouns in your domain.**

## 3. Real-world analogy

**A restaurant kitchen versus a food hall.**

**The monolith is a single restaurant kitchen.** One pass, one head chef, one walk-in fridge. If the fish is off, the chef changes the menu on the spot — no negotiation, no paperwork, no delay. Everything is fast because everything is adjacent, and every change is *coordinated by proximity*. Its weakness: the whole kitchen stops when the extractor fan breaks, and you cannot hire a pastry chef who insists on their own equipment.

**The modular monolith is the same kitchen, organised in stations.** Garde manger, sauté, pastry — each with its own prep area, its own mise en place, and a clear rule that **you do not reach into another station's fridge**. The stations are physically in one room (one deployable), but a change to the pastry section does not require re-learning the sauté section. Almost all of the benefit of separation, none of the walking.

**Microservices are a food hall.** Separate stalls, separate owners, separate kitchens, separate fridges. Now:

- **A stall can close, open, or change its menu without the others** (independent deployability). This is the real benefit, and it is large.
- **But an order spans stalls, and now there is a corridor between them.** The burger stall needs the bun from the bakery stall: that is a **network call**, and it can be slow, fail, or time out — so the burger stall needs a fallback bun (L77), and if the order must be all-or-nothing, somebody has to chase the refunds (L84).
- **The food hall's shared plumbing is a new, critical dependency.** The ventilation and the bins serve all stalls; when the ventilation fails, every stall fails. In software that is the platform, the service mesh, and the shared database someone inevitably reintroduces.
- **The failure mode nobody plans for is the food hall where all the stalls share one fridge.** They look independent to customers, but a power cut takes them all down together, and no stall can change its stock without the others' agreement. That is the **distributed monolith**, and it is the most common outcome — because sharing a fridge is so much easier than buying four.

The analogy's final point: **a food hall is the right answer when you have many independent businesses with different rhythms. It is a silly answer when you have one kitchen serving one menu.** Most software systems are somewhere in between, and the modular monolith is the in-between that people forget exists.

## 4. Detailed technical explanation

### 4.1 The three shapes, compared honestly

| | Monolith | Modular monolith | Microservices |
|---|---|---|---|
| Deploy | One unit, all at once | One unit, all at once | Independent per service |
| Calls | Function calls (~ns) | Function calls with interfaces | Network (0.5–60 ms), can fail |
| Transactions | Local ACID | Local ACID **within** a module; cross-module needs a rule | Sagas, outboxes, eventual consistency |
| Boundary enforcement | Convention only | **Tooling** (arch tests, schemas) | Physical (separate processes) |
| Scaling | Whole app | Whole app (or a few extracted) | Per service |
| Failure blast radius | Whole app | Whole app | Per service (if isolated properly) |
| Operational cost | 1 pipeline, 1 on-call | 1 pipeline, 1 on-call | N pipelines, N on-calls, mesh, tracing |
| Debugging | One stack trace | One stack trace | Distributed traces (L88) |
| Cognitive load | Grows with size | Grows with module count | Grows with service count × network paths |
| Best for | Small systems, early products | **Most systems** | Genuinely independent domains |

### 4.2 What "real" modularity requires — and how to enforce it

A modular monolith is only modular if the boundary is **mechanically enforced**. Four rules, each with a tool:

```
  1. NO CROSS-MODULE TABLE ACCESS
     each module owns a database schema; no module may read another's tables.
     enforcement: separate DB roles + GRANTs, or one schema per module in
     Postgres and a migration lint that rejects cross-schema joins.

  2. EXPLICIT INTERFACES ONLY
     module A may only call module B's published API (a small set of classes
     or functions), never its internals.
     enforcement: import-linter / dependency-cruiser / ArchUnit rules in CI.

  3. NO SHARED MUTABLE DATA STRUCTURES
     if two modules share a type, it lives in neither — it is a published
     contract with a version.

  4. ASYNCHRONOUS OR EXPLICIT CROSS-MODULE WORKFLOWS
     anything spanning modules is either a single explicit call with defined
     failure semantics, or an in-process domain event, never a lockstep
     side-effect chain.

  ✓ The test: could you extract module X into a service in two weeks? If
    yes, the boundary is real. If "we would have to untangle the queries",
    it is a convention, not a boundary.
```

That test is the practical definition, and it is worth applying before a migration: **the modular monolith is the preparation for extraction, not a compromise instead of it.** A team that cannot separate modules inside one process will not separate them across a network; it will produce a distributed monolith.

### 4.3 The extraction criteria — the only five reasons that justify splitting

| Reason | Signal | Example |
|---|---|---|
| **Different scaling profile** | One part needs 20× the instances of the rest | Image transcoding, search indexing |
| **Different change cadence** | One part changes daily, the rest monthly | Pricing engine vs invoice archive |
| **Different failure importance** | One part failing must not affect the rest | Checkout must survive catalogue outages |
| **Different compliance/security boundary** | Regulatory data isolation | Payment card data, health records |
| **Different runtime/language requirement** | A genuine need, not a preference | ML serving in Python vs the app in Java |

Everything else — "it is cleaner", "microservices are best practice", "our domain has 30 entities" — is not a reason. Those are **modularity** goals, and modularity is achievable in one process.

The corollary: **service per team, not service per entity.** Ten services for ten CRUD entities means ten pipelines, ten on-calls and ten deploy coordinations for one team's work.

### 4.4 The distributed monolith, and its five symptoms

```
  ┌──────────────────────────────────────────────────────────────┐
  │  34 services, but:                                           │
  │   • one shared database (everyone joins across boundaries)   │
  │   • a release requires deploying 12 of them together         │
  │   • request path calls 8 services synchronously in a chain   │
  │   • one shared "commons" library with shared domain models   │
  │   • a single outage takes everything down                    │
  │  ⇒ you have a monolith with network latency and 34 pipelines │
  └──────────────────────────────────────────────────────────────┘
```

Symptoms to watch for, in order of appearance:

1. **A shared database.** The moment two services read each other's tables, the boundary is decorative — it is a monolith with extra steps. This is the #1 regression in every failed migration.
2. **Lockstep deploys.** If a change to A requires a change to B, they were one service.
3. **Synchronous chains.** `gateway → orders → inventory → pricing → catalog → DB` means the p99s multiply (§9 step 1) and any link's outage is everyone's outage. Fix: collapse the chain into one service, or make the hops asynchronous.
4. **Shared domain models in a library.** Two services sharing an `Order` class means they change together forever.
5. **One outage takes all.** No bulkheads, no timeouts, no isolation (L77) — so the topology is cosmetic.

### 4.5 Migration: the strangler fig, in the order that works

```
  ✗ BIG BANG: rewrite everything, cut over once. Two years, then a
    permanently-frozen old system and a new one with the same bugs.

  ✓ STRANGLER, in this order:
    1. Modularise the monolith FIRST (schemas, interfaces, arch tests),
       because that is where the value is and where extraction starts.
    2. Put a routing layer in front (gateway/proxy) so traffic can be moved
       per-route without a redeploy.
    3. Extract the module with the CLEAREST boundary and the best reason
       (§4.3) — usually the one with the different scaling profile.
    4. Give it its own data. If it must share the database, extract a read
       model instead (CDC, L85 §4.8) — never a shared write path.
    5. Move traffic: 1 % → 10 % → 50 % → 100 %, with both paths live and
       comparable, and a rollback at every step.
    6. Delete the old path in the same quarter. The most common failure is
       leaving both live forever, doubling the maintenance.
```

Step 4 is the one that determines success: **the database is the real boundary.** A service that shares its database with the monolith is not extracted; it is a separately deployed module. And step 1 is the one teams skip: extracting from an unmodularised monolith is how the distributed monolith is born.

### 4.6 Team structure is not a side issue

Conway's law is descriptive, not optional: your architecture will mirror your communication structure. The practical consequences:

- **Split services along team boundaries, not across them.** A service owned by three teams has three deploy queues and no owner.
- **A team should own a service end to end**, including on-call, or the operational cost lands on a platform team that does not understand it.
- **The inverse Conway manoeuvre** — restructuring teams to produce the architecture you want — works, but it takes 6–18 months and is an organisational change, not a technical one.
- **For a 6-person team, microservices are almost never justified**, because the operational cost per service exceeds the coordination cost it removes.

## 5. Request / data flow

The same checkout, in three topologies, so the cost of each boundary is visible.

```
 ── MONOLITH ───────────────────────────────────────────────────────────────
 client → app (one process)
   validate (2 ms) → reserve stock (local tx, 3 ms) → create order (0.5 ms)
   → authorise payment (external, 150 ms) → confirm (1 ms) → respond
   ⇒ one transaction for the local parts; no saga, no compensations.
   p50 ≈ 158 ms   p99 ≈ 260 ms   (dominated by the payment gateway)

 ── MODULAR MONOLITH ───────────────────────────────────────────────────────
 identical to above, but: inventory module exposes reserve() and publishes
 an in-process event; the orders module cannot read inventory's tables.
   ⇒ same latency, same transaction. The difference is that inventory could
     be extracted in two weeks, and the code cannot rot into a tangle.
   p50 ≈ 158 ms   p99 ≈ 265 ms   (+1 ms of interface indirection)

 ── MICROSERVICES (properly isolated) ──────────────────────────────────────
 client → gateway (2 ms) → checkout-svc (3 ms)
            ├→ inventory-svc  (p50 12 ms, p99 60 ms)   reserve, TTL 15 min
            ├→ payment (external, p50 150 ms, p99 900 ms)
            ├→ orders-svc     (p50 8 ms, p99 40 ms)
            └→ fulfilment-svc (p50 15 ms, p99 70 ms)
   ⇒ 4 network hops + the external call. p50 ≈ 190 ms;
     p99 ≈ 900 + 60 + 40 + 70 + jitter ≈ 1,100 ms — because TAIL LATENCIES
     ADD when calls are sequential (§9 step 1).
   ⇒ plus: a saga state machine, 4 compensations, an outbox per service,
     a reconciler, and 4 on-call rotations.
```

## 6. ASCII diagram

The three topologies, and where the boundary is enforced:

```
  MONOLITH                    MODULAR MONOLITH            MICROSERVICES
  ┌────────────────────┐      ┌────────────────────┐      ┌──────┐ ┌──────┐
  │ ┌────┐ ┌────┐      │      │ ┌────┐ ┌────┐      │      │ inv  │ │ ord  │
  │ │ord │ │inv │ ← any│      │ │ord │ │inv │      │      │ own  │ │ own  │
  │ └─┬──┘ └─┬──┘ call │      │ └─┬──┘ └─┬──┘      │      │ DB   │ │ DB   │
  │   └──┬───┘         │      │   │API═══╡ (only   │      └──┬───┘ └──┬───┘
  │      ▼             │      │   └──────┘  via the │         │        │
  │  ┌────────┐        │      │  ┌────────┐  API)   │      ┌──▼────────▼──┐
  │  │ ONE DB │        │      │  │ ONE DB │ schemas│      │  NETWORK     │
  │  └────────┘        │      │  └────────┘ per    │      │  (can fail,  │
  │  1 deploy          │      │  1 deploy  module  │      │   can be slow)│
  │  convention only   │      │  ENFORCED by CI    │      │  N deploys   │
  └────────────────────┘      └────────────────────┘      └──────────────┘

  THE DISTRIBUTED MONOLITH — what usually happens instead:
  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐   34 deployables...
  │ svc1 │ │ svc2 │ │ svc3 │ │ svc4 │   ...and no isolation
  └───┬──┘ └───┬──┘ └───┬──┘ └───┬──┘
      └────────┴────┬───┴────────┘
                 ┌──▼─────────────────────┐
                 │  ONE SHARED DATABASE   │  ← the boundary was never real
                 └────────────────────────┘
      + lockstep deploys, + synchronous chains, + one shared models library
      ⇒ all of the cost, none of the benefit
```

## 7. Detailed example

### 7.1 The 34 → 11 consolidation, with numbers

BookNook's figures, before and after consolidation (§2):

| Metric | 34 services (2021) | 11 services + modular monolith (2023) |
|---|---|---|
| Checkout p50 | 470 ms | 210 ms |
| Checkout p99 | **1,240 ms** (34 services) | 240 ms |
| Deploys/day (org-wide) | 18 | 41 |
| Services changed < once/month | 9 | 0 |
| On-call rotations | 34 | 12 |
| Mean time to first diagnosis (checkout incident) | 68 min | 19 min |
| Shared database tables | 41 (of 96) | 0 |
| Sagas in the checkout path | 2 | 1 (payment only) |
| Engineers per service | 1.2 | 4.1 |

The interesting rows:

- **Deploys went up after consolidation.** The 34-service split had created *coordination* — 12 services had to ship together — so fewer deploys were possible. This is the distributed monolith in one number.
- **MTTD fell by 49 minutes** because 6 of the collapsed services had been synchronous hops on the checkout path with no tracing between them (L88).
- **The 11 survivors** are the ones satisfying §4.3: search indexing (scaling), media transcoding (scaling), payments (compliance), identity (security), notifications (cadence), plus six others with a genuine failure-isolation requirement.

### 7.2 The shared database that re-grew

Six months after the 2021 split, an audit found that **41 of 96 tables were read or written by more than one service**. The mechanism was always the same and always reasonable at the time:

> "We need the customer's name for the invoice PDF, and calling the customer service would add 20 ms. It's only a read."

Each decision was locally rational and collectively fatal: the "services" could no longer change their schema independently, so every migration required a coordinated release, which is exactly the property the split was meant to remove.

The three remedies, in the order they were applied:

```
  1. For READS: a CDC-fed read model (L85 §4.8) — the invoice service keeps
     its own projection of the customer name, updated by events. Cost: 40 ms
     of eventual consistency, which no invoice cares about.
  2. For WRITES: the table moved to exactly one owner, and the others got an
     API. Cost: 8 ms per call, plus a circuit breaker (L77).
  3. For genuinely shared reference data (country codes, tax rates): treated
     as a published dataset, versioned, replicated — not as a table.
  ⇒ 41 shared tables → 0. The extraction only became real two years after
    the "split", which is the honest timeline for most migrations.
```

## 8. Code or configuration example

```python
# ── Enforcing module boundaries in a modular monolith: a CI test ─────────
# Without this, the monolith WILL decay. Architecture is enforced, not agreed.
import ast, pathlib, sys

MODULES = {"orders", "inventory", "payments", "catalogue", "shipping"}
ALLOWED = {("orders", "inventory"), ("orders", "payments"), ("orders", "catalogue")}

violations = []
for path in pathlib.Path("app").rglob("*.py"):
    parts = path.parts
    if len(parts) < 2 or parts[1] not in MODULES:
        continue
    owner = parts[1]
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            target = node.module.split(".")[0]
            # 1. no reaching into another module's internals
            if ".internal" in node.module or ".models" in node.module:
                if target in MODULES and target != owner:
                    violations.append(f"{path}: imports internals of {target}")
            # 2. only allow-listed module-to-module dependencies
            if target in MODULES and target != owner and (owner, target) not in ALLOWED:
                violations.append(f"{path}: {owner} → {target} not in the allow-list")

if violations:
    print("\n".join(violations))
    sys.exit(1)          # fail the build. A convention that does not fail
                         # the build is a comment.
```

```sql
-- ── Schema per module, with grants that make cross-module queries fail ────
CREATE SCHEMA orders;  CREATE SCHEMA inventory;  CREATE SCHEMA payments;
CREATE ROLE svc_orders;  CREATE ROLE svc_inventory;

GRANT USAGE ON SCHEMA orders TO svc_orders;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA orders TO svc_orders;
-- svc_orders gets NOTHING on inventory:
GRANT USAGE ON SCHEMA inventory TO svc_inventory;   -- and only that role
-- ⇒ the boundary is now enforced by the database, not by review. A developer
--   who "just needs the name" gets a permission error in CI, not a code
--   review comment that nobody enforces.
```

```yaml
# ── The extraction scorecard: decide with evidence, not fashion ──────────
service_candidate:
  name: media-transcoding
  scaling_ratio: 22          # needs 22× the instances of the app  ✓
  change_cadence: daily      # vs monthly for the app             ✓
  failure_isolation: true    # app must work while transcoding is down ✓
  compliance_boundary: false
  runtime_requirement: ffmpeg + GPU                                 ✓
  -> extract: YES  (4 of 5 criteria)

service_candidate:
  name: customer-profile
  scaling_ratio: 1.1         # same as the app
  change_cadence: monthly    # same as the app
  failure_isolation: false
  compliance_boundary: false
  runtime_requirement: none
  -> extract: NO   (0 of 5) — make it a module with an enforced boundary
```

## 9. Numerical example

BookNook checkout, three topologies, one requirement.

**Step 1 — why synchronous chains are the hidden cost.**

```
  per-hop p50 / p99:
      gateway 2/10, checkout-svc 3/15, inventory 12/60, orders 8/40,
      fulfilment 15/70, payment (external) 150/900

  MONOLITH: the local hops are function calls (~0.1 ms total).
      p50 ≈ 158 ms, p99 ≈ 260 ms   (the tail is the payment gateway plus
                                     one local commit)

  MICROSERVICES, sequential:
      p50 = 2+3+12+8+15+150 = 190 ms
      p99 ≈ 10+15+60+40+70+900 = 1,095 ms
  ⇒ the p99 nearly DOUBLES relative to the monolith, and it is not because
    any service is bad: it is because a chain of five 99th percentiles is
    a 1-in-20 event, not a 1-in-100 one. Independence makes tails ADD.
  ⇒ the mono-to-micro p99 ratio is ~4× on this path, and that is normal.
```

**Step 2 — the probability that a sequential chain hits a slow dependency.**

```
  if each hop independently exceeds its p99 with probability 1 %:
      P(any of 5 hops slow) = 1 − 0.99^5 = 4.9 %
  ⇒ your "p99" is served at a rate closer to what a p96 would be in a
    monolith. To deliver a 99th-percentile experience across 5 hops you need
    each hop at ~p99.8: 1 − 0.998^5 = 1.0 %.
  ⇒ THE BUDGET RULE: a 5-hop chain needs each hop at p99.8, or the hops must
    be parallelised, or the chain must be collapsed.
```

**Step 3 — the operational cost per service, which is what "34 services" really meant.**

```
  per service, per month:
      pipeline/maintenance       2 h
      dependency upgrades        1.5 h
      on-call share (1 of 6)     12 h
      dashboards/alerts upkeep   1 h
      security patching          1.5 h
      ─────────────────────────────────
      ≈ 18 h/month = 0.11 FTE
  34 services × 0.11 = 3.7 FTE spent on maintaining the SHAPE of the system,
      before writing any product code.
  11 services × 0.11 = 1.2 FTE.
  ⇒ consolidation released ~2.5 FTE, which is 2.5% of a 100-engineer org —
    not decisive on its own, but at 4.1 engineers per service (vs 1.2) the
    real gain was that each service had an OWNER rather than a rota.
```

**Step 4 — the benefit, quantified, to show why some splits were right.**

```
  media-transcoding extraction (satisfies 4 of 5 criteria):
      transcoding load: 22× the app's CPU; instances 60 vs 12
      before extraction, transcoding spikes caused app-wide latency spikes:
         p99 480 ms → 2,100 ms during a video upload surge (4×/week)
      after extraction: transcoding autoscales independently; app p99
         unaffected. Measured over 3 months: 0 correlated spikes.
      ⇒ 4 hours/week of incidents eliminated, ~£0 cost, because the
        boundary satisfied the criteria.
  ⇒ THE POINT: the same organisation, using criteria, kept the splits that
    paid (transcoding, search, payments) and reversed the ones that did not.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **Monolith** | Speed of change, one deploy, local transactions | Coupling grows; whole-app scaling; blast radius | Small team, early product, unclear domain |
| **Modular monolith** | Boundaries without network cost; extractable later | Needs enforced discipline and CI rules | **The default for most systems** |
| **Microservices, criteria-driven** | Independent deploy/scale/fail for the parts that need it | Operational cost, latency tails, distributed data | §4.3 criteria met |
| **Microservices, "best practice"** | Nothing specific | Everything above, permanently | Never |
| **Shared database across services** | Fast reads, no API work | Destroys the boundary; forces lockstep deploys | Never (§4.4, §7.2) |
| **Synchronous chains** | Simple to reason about locally | Tails add; one outage is everyone's outage | ≤ 3 hops, with timeouts and bulkheads |
| **Service per team** | Clear ownership, one deploy queue | Fewer, larger services | Almost always |
| **Service per entity** | Looks like a clean diagram | Nano-services, coordination cost | Never |

## 11. Failure scenarios

**11.1 — The shared database** (§7.2). 41 of 96 tables multi-owner; every schema change required a coordinated release. **Fix:** read models from CDC, writes via API, reference data as a published dataset.

**11.2 — The synchronous chain.** `checkout → orders → inventory → pricing → catalogue`; a pricing slowdown made checkout p99 8 s. **Fix:** collapse to fewer services, or make hops parallel/asynchronous with timeouts and fallbacks (L77).

**11.3 — Lockstep deploys.** A change to the `Order` model required shipping 12 services; deploys fell to once a week, exactly what the migration intended to fix. **Fix:** version the contract; one owner of the model; consumers tolerate unknown fields.

**11.4 — The nano-service estate.** 34 services, 9 changed less than monthly, 1.2 engineers each. Everyone owned something and nobody owned the outcome. **Fix:** consolidation into 11, criteria-based (§9 step 4).

**11.5 — The extraction without a boundary.** A "service" was extracted but kept reading the monolith's tables "for now". Two years later it could not be changed independently, and the monolith could not change its schema. **Fix:** extract the data first (or a read model), and treat "for now" as a scheduled ticket with a date.

**11.6 — The whole-app scaling trap.** A monolith scaled to 40 instances for one CPU-heavy feature, quadrupling the cost of everything else. **Fix:** extract exactly that module (a valid §4.3 criterion) or move the work to a worker pool with its own queue — which is often enough.

**11.7 — The testing collapse.** After the split, no one could run the system locally; integration tests had to deploy 14 services; the test suite went from 9 minutes to 40, and defects escaped. **Fix:** contract tests per service, a local compose environment for the top 3 dependencies, and — realistically — accept fewer end-to-end tests, which is a real cost of the topology.

**11.8 — The monolith nobody modularised.** A team extracted services from a 900k-line tangle; every extraction required untangling shared queries, so each took a quarter instead of two weeks, and three were abandoned half-done (leaving the worst state: two places with the logic). **Fix:** modularise first (§4.5 step 1), which is also why the modular monolith is the prerequisite rather than the alternative.

## 12. Alternatives

| Instead of | Consider | When |
|---|---|---|
| Microservices | **Modular monolith** | Almost always as the starting point |
| Microservices | **A monolith + worker processes** | One feature has a different load profile (a valid split, cheaply) |
| Service per entity | **Service per bounded context / team** | Always |
| Synchronous chain | **Async events + read models** (L85) | The caller does not need the answer to proceed |
| Shared database | **API composition** or **CQRS read model** | Data is needed elsewhere (L85 §4.8) |
| Big-bang rewrite | **Strangler fig** (§4.5) | Any migration, always |
| Extraction | **In-process module with an enforced boundary** | The criteria in §4.3 are not met |

## 13. When to use it

- **Monolith**: a small team, an unclear domain, or a product still searching for its shape. Optimise for speed of change.
- **Modular monolith**: the default for anything past the prototype stage — it buys modularity now and optionality later.
- **Microservices**: only where §4.3 criteria are met, ideally one extraction at a time, each with its own data.
- **A mesh/gateway**: when the service count genuinely justifies centralising retries, timeouts and mTLS (otherwise it is more moving parts).
- **Extract the database before the code**, always.

## 14. When *not* to use it

- **Microservices for a team of fewer than ~10 engineers.** The operational cost per service exceeds the coordination cost it removes.
- **Microservices to fix a code-quality problem.** Coupling follows you across the network and becomes harder to see.
- **Splitting on nouns** (one service per entity) — that is a data model, not an architecture.
- **Extraction without data ownership** — you get a distributed monolith.
- **A monolith for a genuinely independent domain with a different scaling profile** — you will scale and deploy it all together forever.
- **Microservices where a monolith's transactions are load-bearing** — the saga cost is permanent and it will be paid in incidents.

## 15. Common mistakes

1. **Extracting services before modularising** (§11.8).
2. **Sharing a database** (§11.1).
3. **Splitting on entities** rather than capabilities (§11.4).
4. **Synchronous chains** with no timeouts or bulkheads (§11.2).
5. **Lockstep deploys** from a shared model (§11.3).
6. **No boundary enforcement** in a modular monolith, so it decays into a tangle.
7. **Ignoring operational cost per service** in the decision.
8. **Big-bang rewrites** (§4.5).
9. **Extraction "for now" with a shared table** and no date to remove it (§11.5).
10. **Scaling the whole monolith** for one feature (§11.6).
11. **Underestimating the testing cost** of many services (§11.7).
12. **Assuming microservices make teams autonomous** — they only do if teams own on-call and data.
13. **Using a service mesh to paper over bad boundaries.** It does not fix a shared database.
14. **Not writing down the criteria**, so the architecture becomes fashion rather than engineering.

## 16. Interview perspective

**What is actually being tested.** Whether you can name the **costs** of a network boundary precisely — latency tails, partial failure, distributed data, operational surface — and then choose on criteria rather than on fashion. Strong candidates default to a modular monolith and justify each extraction with a specific reason. Weak candidates say "microservices for scalability" without being able to say what scales or what it costs.

**Answers that land well:**

- *"Monolith or microservices?"* — "Modular monolith unless a service has a reason to exist: a different scaling profile, a different change cadence, a failure that must not propagate, a compliance boundary, or a genuine runtime requirement. Those five. In practice that is 8–12 services for a mid-sized product, not 34, and the split that pays is usually the one with the odd resource profile — transcoding, indexing, search."
- *"What does a network boundary cost?"* — "Everything in this phase. A function call becomes a partial failure, a transaction becomes a saga with compensations, and the p99s add: five sequential hops each with a 1 % chance of being slow means a 4.9 % chance the request is slow, so a 5-hop chain needs each hop at p99.8 to deliver a p99 experience. Plus one pipeline and one on-call per service."
- *"How would you migrate?"* — "Strangler, in order: modularise the monolith first with enforced boundaries — schema per module, import rules that fail the build — then extract the module with the clearest boundary and give it its own data before moving traffic 1 % → 10 % → 100 % with both paths live. The database is the real boundary; a service that shares it is a separately deployed module."
- *"What is a distributed monolith?"* — "Services that cannot be deployed independently: shared database, lockstep releases, synchronous chains, shared models. All the cost, none of the benefit. The tell is that a change to one requires deploying several."

**Common interview tasks:** choose a topology and defend it with criteria; compute the latency and p99 impact of splitting; design a migration; identify a distributed monolith from a description; decide where a transaction boundary belongs; explain what a service mesh does and does not fix.

**Red flags interviewers watch for:**

- "Microservices for scalability" with no further analysis.
- No mention of the data boundary, or a shared database assumed acceptable.
- Assuming a network call has the same p99 as a function call.
- No consideration of operational cost or team size.
- Splitting on entities.
- No migration plan, or a big-bang rewrite.
- Unable to define what would make a good extraction candidate.

## 17. Knowledge check

**Conceptual**

1. Distinguish deployment topology from modularity, and explain why the four combinations (including the distributed monolith) are all real. Then state which one is the most common outcome of a careless migration and how you would recognise it.
2. Explain why tail latencies add across a synchronous chain, using the arithmetic in §9 steps 1–2. Then state the two structural remedies and the cost of each.
3. Explain what "the database is the real boundary" means, and why a service that shares a database has not been extracted. Then describe the three remedies for a shared table in order of preference.

**Practical scenario**

4. BookNook has a 940k-line monolith, 6 teams, a 52-minute test suite, one deploy a day, and four candidate extractions: media transcoding (22× CPU, GPU, daily changes), customer profile (identical profile to the app, monthly changes), payments (PCI compliance), and search indexing (rebuilt hourly, CPU-heavy, bursts). For each candidate: decide extract or modularise in-process, using the five criteria; specify what the boundary needs (data ownership, API, events); design the extraction order and the traffic migration; state the expected latency change; and give the operational cost you are accepting per extracted service, with arithmetic.

**Calculation**

5. A checkout path has 4 internal hops with p50s of 3, 12, 8 and 15 ms and p99s of 15, 60, 40 and 70 ms, plus an external call at p50 150 ms and p99 900 ms. (a) Compute the sequential p50 and p99. (b) Compute the probability a request is slow if each hop independently exceeds its p99 with probability 1 %. (c) Compute the per-hop percentile required to deliver an overall p99. (d) Compute the p99 if the two slowest internal hops are parallelised.

6. An organisation runs 34 services. Per-service monthly overhead is 18 engineer-hours (pipeline 2, upgrades 1.5, on-call share 12, dashboards 1, patching 1.5). (a) Compute the annual cost in FTE. (b) Recompute for 11 services. (c) If each engineer costs £95,000 fully loaded, compute the annual saving. (d) State two benefits of consolidation that the arithmetic does not capture, and one benefit of the 34-service topology that would justify keeping a service despite the arithmetic.

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
  - [x] **23. Monoliths, modular monoliths and microservices (L86) ← you are here**
  - [ ] 24. Service discovery and configuration (L87)
  - [ ] 25. Observability: logs, metrics, traces (L88)
  - [ ] 26. SLIs, SLOs and error budgets (L89)
  - [ ] 27. High availability and disaster recovery (L90)
  - [ ] 28. Active-passive versus active-active (L91)
  - [ ] 29. RTO and RPO (L92)

**NEXT LESSON: Phase 4, Topic 24 — Service discovery and configuration management**

---

---

# Lesson 87 — Service Discovery and Configuration Management

*Phase 4, Topic 24*

## 1. Simple definition

Two related problems that both come down to **"how does a running process find out what it needs to know?"**

**Service discovery** answers *where*: given a logical name (`inventory-svc`), which IP addresses and ports are currently healthy and available to serve? In a world where instances are created, destroyed and moved constantly, a static address list is wrong within minutes.

**Configuration management** answers *with what settings*: which values, limits, credentials and switches does this process use — and how do they change without a redeploy?

They are grouped because they share a mechanism (a registry or config store, plus a way to watch it), a risk (a bad value propagating everywhere at once), and a failure mode (**split brain knowledge**: different processes believing different things about the world).

Terms defined before use:

- **Service registry**: the authoritative map of service name → healthy instances (Eureka, Consul, etcd, Kubernetes `Endpoints`).
- **Registration**: how an instance makes itself known — self-registration (heartbeat) or third-party (the orchestrator registers it).
- **Liveness vs readiness**: liveness asks "should I be restarted?"; readiness asks "should I receive traffic?". **Conflating them is the most common discovery bug** (§4.2).
- **Draining**: letting in-flight requests finish before an instance stops. Without it, every deploy fails a fraction of requests.
- **Client-side discovery**: the caller queries the registry and picks an instance. **Server-side**: the caller sends to a load balancer, which picks.
- **Propagation delay**: the time between an instance becoming unhealthy and every caller knowing. It is the number that determines your deploy-time error rate (§4.3).
- **Config drift**: two instances of the same service running different settings because they were started at different times.
- **Feature flag**: a configuration value that changes behaviour at runtime, without a deploy.

The one sentence to carry out of this lesson: **discovery and configuration are both caching problems with a staleness window — so the design question is never "how do they find out" but "what happens during the seconds when they know something that is no longer true".**

## 2. Why it exists

Because the assumptions of a static deployment stopped being true somewhere around the move to autoscaling and containers.

BookNook's numbers are typical:

```
  app instances:          12–60, changing several times an hour
  deploys per day:        41
  config values:          1,240 across services
  config changes per day: ~30 (flags, limits, timeouts, allow-lists)
  ⇒ a hardcoded address list is stale within minutes, and a config file
    baked into an image requires a rebuild to change a timeout.
```

And the two failures that follow are both expensive:

**Failure 1 — traffic to something that is not there.** During a rolling deploy of 10 pods with a 12-second startup, roughly **1.4 % of requests** hit an instance that had been removed from the registry but was still in a client's cached list. Over a 3-minute deploy at 400 rps, that is **1,000 failed requests per deploy**, 41 deploys a day. Users see random 502s and it is blamed on "the network".

**Failure 2 — config drift and blast radius.** The timeout on calls to the payment gateway lived in a config file in the image, so raising it required a rebuild. Someone raised it *and* accidentally changed the cache TTL in the same commit; 40 minutes later the site was serving stale prices to 30 % of users, because different pods had different images. And when the team moved to a dynamic config service to fix exactly this, they pushed a bad value to **100 % of instances at once** and took the site down for 11 minutes (§7.2) — because dynamic configuration is a *global write to production*, and it needs the same rollout discipline as code.

That pairing is the lesson's real content: **discovery failures are usually temporary and local; configuration failures are usually instantaneous and global.** They need different controls.

## 3. Real-world analogy

**The company phone directory.**

**Discovery** is the directory. When you need the accounts team, you do not memorise a desk number; you look it up. When someone moves floors, the directory is updated and you do not need to know. The problems arrive with **staleness**, and they are exactly the ones production systems have:

- **The directory is printed monthly.** You call an extension that belongs to someone else now, or to nobody. That is a stale registry entry, and the "wrong number" is a request routed to a dead pod.
- **You memorised the number to save time.** That is a cached endpoint with a long TTL, and it is the single biggest cause of deploy-time errors (§4.3).
- **The directory itself is at the switchboard, and the switchboard is down.** Now nobody can look anything up — so a good directory design lets you **keep the last known list** and operate from it. That is "fail static", and it is why a registry outage should degrade rather than halt (§4.8).

**Configuration** is the office handbook — the policies on expenses, opening hours, and what to do in a fire. Its problems are different:

- **A wrong handbook gets printed and distributed to everyone at once.** Nobody notices until the fire alarm goes off and 400 people follow the wrong exit. That is a bad config pushed globally (§7.2).
- **Photocopied amendments taped into individual copies** produce **drift**: two people following different rules, both believing they are correct.
- **Fire drill instructions should be readable without asking anyone** — configuration needed at startup must be available *at* startup, locally, not fetched from a service that might be down when you boot (§4.6).

And the analogy's final, useful distinction: **nobody needs the directory to be perfectly current — they need it to be current enough, with a fallback when it is not.** Design for the staleness window, and make the fallback explicit; that is the whole discipline.

## 4. Detailed technical explanation

### 4.1 The registry, and the four things it must get right

| Requirement | What goes wrong otherwise |
|---|---|
| **Registration** — instances appear | New capacity is not used; scaling does nothing |
| **Health checking** — only healthy instances | Traffic to broken instances; the classic "one bad pod in ten" |
| **Deregistration + draining** — instances leave cleanly | Every deploy fails a fraction of requests (§4.3) |
| **Fast propagation** — callers learn quickly | Stale routing during changes; long tail of errors after a deploy |

There are two registration models and they fail differently:

```
  SELF-REGISTRATION (Eureka, Consul agent, most app-level service registries)
      instance starts → registers itself → heartbeats every 10 s
      dies → heartbeats stop → registry evicts after TTL (e.g. 30 s)
      ⇒ simple, works outside Kubernetes; but the service must know about the
        registry, and eviction is TTL-bound, so staleness is structural.

  ORCHESTRATOR REGISTRATION (Kubernetes)
      the kubelet reports pod state; the control plane updates EndpointSlices
      ⇒ no registry library in your code; propagation is event-driven and fast
        (sub-second), but you must get readiness right or the orchestrator
        will happily route traffic to a pod that is not ready.
```

### 4.2 Readiness vs liveness — the distinction that causes outages

```
  LIVENESS  "is this process broken beyond recovery?"
            failure → the orchestrator RESTARTS the container
            must be SHALLOW (is the event loop alive? can I open a socket?)

  READINESS "should this process receive traffic right now?"
            failure → the instance is REMOVED from the endpoint list
            may be DEEPER (can I reach my database? is my cache warm?)
```

Three rules that follow, each learned from an incident:

1. **Never make liveness depend on a downstream dependency.** If liveness checks the database and the database has a 30-second blip, **every instance restarts simultaneously** — turning a dependency hiccup into a self-inflicted total outage, and often a crash loop. This is the single most damaging configuration in Kubernetes.
2. **Startup must be gated by readiness, not by sleeping.** A `readinessProbe` with an appropriate `initialDelaySeconds` (or a `startupProbe`) removes the "traffic to a starting pod" class of failure.
3. **Readiness may check dependencies, but with a short timeout and a circuit breaker** (L77), or a slow dependency will mark every instance unready and the load balancer will 503 the whole service — a **cascading readiness failure**, §11.4.

### 4.3 The staleness window, quantified

This is the number that determines your deploy error rate:

```
  During a rolling deploy, an instance is removed from the registry and then
  its clients must learn. The lag has four components:

    1. endpoint propagation (K8s)          ~0.1–1 s
    2. registry poll interval (client)     1–30 s
    3. DNS/TTL caching (if DNS-based)      0–300 s   ← the killer
    4. connection reuse (keep-alive)       indefinite ← the other killer

  Requests that fail during the window ≈ (stale_fraction) × (rps) × (window)

  Worked example (BookNook, before the fix):
      10 pods, 1 removed per 15 s during the deploy, 400 rps
      stale window per removed pod: 30 s (DNS TTL) + keep-alive reuse
      ⇒ ~400 rps × (30/15) × (1/10) ≈ 80 failed requests per deploy at a 30 s
        TTL; measured 1,000+ because JVM DNS caching ignored the TTL and
        cached for the process lifetime (§11.1).
```

The mitigations, in order of effectiveness:

| Mitigation | Effect |
|---|---|
| **Connection draining** (`preStop` sleep + `SIGTERM` handling) | Removes in-flight failures — the largest single win |
| **Short client poll / watch-based discovery** | Window → sub-second |
| **Correct DNS TTL handling in the client runtime** | Removes the unbounded cache |
| **Retry on a different instance for idempotent requests** (L74) | Hides the residual window |
| **Mesh sidecar** | Handles all of the above outside your code |

### 4.4 Choosing an instance: load-balancing algorithms that actually help

| Algorithm | Property | When |
|---|---|---|
| **Round robin** | Even distribution by count | Homogeneous instances, uniform request cost |
| **Least connections** | Adapts to variable request cost | Long-lived or highly variable requests |
| **Power of two choices (P2C)** | Near-optimal *tail* latency, \(O(1)\) state | **The default for a service mesh / client LB** |
| **EWMA latency** | Adapts to actual observed latency | Mixed hardware or noisy neighbours |
| **Consistent hashing** | Same key → same instance | Cache locality, sharded state |
| **Zone-aware** | Prefers the local zone | Cross-AZ latency and cost (L80 §4.8) |
| **Outlier ejection** | Removes instances with anomalous error rates | Alongside anything else — a circuit breaker per instance (L77) |

**P2C is worth the arithmetic**, because it is the cheapest large improvement available in discovery:

```
  Random pick of 100 instances where 1 % are slow (p99 900 ms, others 10 ms):
      P(pick a slow one) = 1 %  ⇒ p99 ≈ 900 ms
  P2C (pick two at random, take the less loaded):
      P(both are slow) = 0.01² = 0.01 %
      ⇒ the tail collapses. Measured in production: p99 900 ms → 120 ms
        with no change to the servers at all.
  ⇒ the tail of a service is often a LOAD-BALANCING problem, not a server
    problem. Check the balancer before adding capacity.
```

### 4.5 Configuration: four kinds, four mechanisms

| Kind | Example | Mechanism | Change cadence |
|---|---|---|---|
| **Static** | ports, log format | Image/binary + env vars | Per deploy |
| **Environment** | DB host, region, feature set | Env vars / ConfigMap per environment | Per deploy |
| **Dynamic** | timeouts, rate limits, thresholds | Config service with watch (etcd/Consul/ConfigMap) | Minutes, no deploy |
| **Secrets** | API keys, DB passwords | Vault / secret manager / workload identity | Rotation, not hand-edited |

And the precedence rule, which must be stated once and enforced: **secrets > dynamic > environment > static defaults**, with every effective value logged at startup (secrets redacted) so that "what config is this process actually using?" has an answer.

### 4.6 Dynamic config is a global write to production

The mistake that follows from treating config as "just a value" is a push that changes production instantly and everywhere. Controls that make it safe:

```
  1. VALIDATE before applying — schema, ranges, and cross-field rules.
     A timeout of 0 or a negative pool size must be REJECTED by the config
     service, not discovered by the app.
  2. CANARY the rollout — 1 % of instances → 10 % → 100 %, with the same
     metrics and abort criteria as a code deploy ("ring" or "wave" rollout).
  3. VERSION and AUDIT every change — who, when, from what to what, and
     which instances applied it.
  4. MAKE IT REVERSIBLE IN SECONDS — a config change you cannot roll back is
     worse than a deploy you cannot rollback, because it skips CI entirely.
  5. FAIL STATIC — if the config service is unreachable at startup, start
     with the last known good values from disk, not with defaults that
     silently disable safety (see #6).
  6. NEVER let a config-fetch failure be silently "safe-looking". A missing
     rate limit that defaults to unlimited is a loaded gun; missing tuning
     that defaults to a sane value is fine. Decide per key.
```

### 4.7 Feature flags are configuration with a lifecycle

Feature flags are the highest-leverage and most abused form:

| Type | Lifetime | Example |
|---|---|---|
| **Release** | Days–weeks | Hide a half-built feature |
| **Ops** | Permanent | Kill switch for an expensive path (L76 §4.6) |
| **Permission** | Permanent | Enable for internal users / a tenant tier |
| **Experiment** | Weeks | A/B test |

The discipline: **every flag has an owner, a creation date, and a removal date.** Flags that live forever become invisible branching — BookNook's audit found **214 flags**, of which **61 % had not changed in six months** and **9 were read in no code path at all**. Untangling them took three weeks, and the risk is not aesthetic: stale flags encode decisions nobody remembers and make behaviour untestable.

## 5. Request / data flow

An instance's lifecycle, with the timings that decide whether deploys are invisible.

```
 ── STARTUP ────────────────────────────────────────────────────────────────
 t=0.0   container starts
 t=0.2   config loaded: static defaults ← env ← dynamic (watch) ← secrets
         (dynamic fetch has a 2 s timeout; on failure, last-known-good from
          disk, and a metric: config.stale=1)
 t=0.4   connections warmed: DB pool (min 10), HTTP pool, cache client
 t=3.1   app ready (JIT warm, route table built)
 t=3.1   /readyz returns 200      ← READINESS ONLY NOW. Before this, the pod
 t=3.1   /livez has been 200 since t=0.4   is in the endpoint list but would
 t=3.2   EndpointSlice updated; callers begin sending traffic in ~0.3 s
         ⇒ NO traffic reached the pod while it was starting.

 ── STEADY STATE ───────────────────────────────────────────────────────────
 config watch: the config service pushes a change; the instance validates it,
 applies atomically (swap a config object, do not mutate in place), logs it
 with a version, and emits config.version{value="v41"}.

 ── SHUTDOWN (the part most systems get wrong) ─────────────────────────────
 t=0.0   SIGTERM received (deploy, scale-in, node drain)
 t=0.0   /readyz → 503 immediately  ← stop being a candidate for NEW traffic
 t=0.0   continue serving in-flight requests for the drain period
 t=0.5   endpoint removal propagates to callers (0.1–1 s in K8s)
 t=8.0   preStop sleep ends; the app finishes in-flight work (longest request
         is bounded by the request timeout, 5 s, + margin)
 t=8.1   connections closed gracefully, DB pool drained
 t=8.2   process exits 0
       ⇒ ZERO failed requests per deploy. Without t=0.0–t=8.0, every deploy
         fails ~1 % of requests, which at 41 deploys/day is ~1,600 errors/day.
```

## 6. ASCII diagram

The two discovery models, and where staleness lives:

```
  CLIENT-SIDE DISCOVERY                  SERVER-SIDE DISCOVERY
  ┌────────┐  1. where is inv?          ┌────────┐  1. request (always
  │ caller │ ──────────────────►        │ caller │     to the same LB)
  │ + LB   │    registry              │        │ ──────────┐
  │ logic  │ ◄──── [inv: 10.0.1.4,     └────────┘           │
  └───┬────┘            10.0.2.7]                            ▼
      │ 2. pick one locally                       ┌────────────────────┐
      │    (P2C, least-conn,                       │ load balancer /    │
      │     zone-aware)                            │ mesh sidecar       │
      ▼                                            │  registry client   │
  ┌────────┐                                       └─────────┬──────────┘
  │ inv pod│                                                 ▼
  └────────┘                                       ┌────────────────────┐
      + lowest latency (no extra hop)              │ inv pods           │
      + caller controls policy                     └────────────────────┘
      − every caller embeds registry logic             + callers are dumb
      − staleness is per-caller, hard to see          + one place to fix
                                                      − one more hop + SPOF
                                                        (make it a mesh, not
                                                         a single appliance)

  THE STALENESS WINDOW, and the four contributors (§4.3):
    removed ──► propagation ──► poll/watch ──► DNS TTL ──► keep-alive reuse
     from        (0.1–1 s)      (1–30 s)      (0–300 s)    (unbounded!)
     registry                                                    ▲
                                                                 └─ the one
                                                                    people
                                                                    forget
```

Config rollout rings, because a config change is a production write:

```
  ┌───────────┐   ┌───────────┐   ┌───────────┐   ┌───────────┐
  │ canary    │──►│ ring 1    │──►│ ring 2    │──►│ ring 3    │
  │ 1 instance│   │ 10 %      │   │ 50 %      │   │ 100 %     │
  └───────────┘   └───────────┘   └───────────┘   └───────────┘
        │               │               │               │
        └─ 5 min, error rate + latency + business metric all green, or
           AUTOMATIC ROLLBACK to the previous version (which must be one
           click, and must not require a deploy).
```

## 7. Detailed example

### 7.1 The stale-endpoint errors, and the four-part fix

BookNook's symptom: **~1,000 failed requests per deploy**, surfacing as `502`/`connection reset`, blamed on the load balancer for four months.

```
  investigation found four independent contributors:
    1. NO DRAINING. SIGTERM killed the process immediately; in-flight
       requests died. Contributed ~60 % of the failures.
    2. JVM DNS caching default (networkaddress.cache.ttl = -1 = FOREVER) meant
       long-lived clients kept a removed IP forever. Contributed ~30 %.
    3. Client-side discovery polled every 30 s. Contributed ~8 %.
    4. Readiness had no startupProbe, so pods received traffic at t=0.4 s
       while ready at t=3.1 s. Contributed ~2 %.
  fix, in that order:
    1. preStop: sleep 8; handle SIGTERM: /readyz→503, drain, exit. −60 %
    2. networkaddress.cache.ttl=5 in the JVM. −30 %
    3. watch-based discovery instead of polling. −8 %
    4. startupProbe with failureThreshold 30 × 1 s. −2 %
  result: failed requests per deploy 1,000 → 0 (measured over 300 deploys);
      deploy-time p99 latency 2.4 s → 210 ms (no more requests stalling on
      a half-dead connection).
  ⇒ none of this was a "discovery product" problem. All four were details.
```

### 7.2 The config push that took the site down in 11 minutes

BookNook moved a `cache.ttl` value (and 40 other tunables) from the image into a dynamic config service — the right move. Then:

```
  t=0:00  an engineer edits cache.ttl: 60 → 0 in the config UI, intending to
          disable caching for a single product page test. The key is GLOBAL.
  t=0:00  the config service validates the type (integer ✓) but not the range.
  t=0:02  all 48 instances have applied it. Cache hits → 0.
  t=0:04  database QPS rises 40× (from 1,200 to 48,000), connection pools
          saturate, p99 goes from 180 ms to 12 s.
  t=0:06  the load balancer's health checks (dependent on DB, see §4.2 rule 3)
          start failing → ALL instances removed from the endpoint list
  t=0:07  total outage. Readiness cascading had turned a cache problem into
          a 100 % failure.
  t=0:11  config reverted; instances recover over 90 s.
  ⇒ 11 minutes of downtime, triggered by a text field, made total by
    (a) no range validation, (b) no canary rollout, (c) readiness depending on
    the database.
```

The controls added, which are the ones to copy:

```
  1. Schema + RANGE validation in the config service (cache.ttl ∈ [10, 3600]).
     0 is rejected at write time — the failure never reaches production.
  2. Ring rollout for config: canary 1 instance, wait 5 min, compare error
     rate and latency, then 10 %/50 %/100 %. Automatic rollback on regression.
  3. Readiness no longer checks the database; it checks "is my event loop
     responsive and is my config loaded". Dependency health is a metric and a
     circuit breaker, not a readiness gate (§4.2).
  4. Per-key blast radius: keys that affect capacity or correctness are
     marked `requires_canary: true` and cannot be pushed globally.
  5. A kill-switch page: one click to revert the last config change, available
     to anyone on call.
```

## 8. Code or configuration example

```yaml
# ── Kubernetes: the four settings that eliminate deploy-time errors ───────
spec:
  template:
    spec:
      terminationGracePeriodSeconds: 30
      containers:
        - name: inventory
          startupProbe:                 # don't let readiness fail the pod
            httpGet: {path: /livez, port: 8080}
            failureThreshold: 30        # up to 30 s to start
            periodSeconds: 1
          livenessProbe:
            httpGet: {path: /livez, port: 8080}
            periodSeconds: 10
            failureThreshold: 3         # SHALLOW only: no DB check (§4.2)
          readinessProbe:
            httpGet: {path: /readyz, port: 8080}
            periodSeconds: 2
            failureThreshold: 2         # fast removal from endpoints
          lifecycle:
            preStop:
              exec:
                # 1. give endpoint removal time to propagate (0.1–1 s),
                # 2. let in-flight requests finish (max request timeout 5 s),
                # 3. THEN exit. Without this, every deploy fails requests.
                command: ["sh", "-c", "sleep 8"]
```

```python
# ── Dynamic config with validation, atomic swap, and fail-static ─────────
class Config:
    SCHEMA = {
        "cache.ttl_s":       (int,   (10, 3600)),     # range-validated
        "payment.timeout_ms": (int,  (100, 5000)),
        "rate_limit.rps":    (int,   (1, 10000)),
        "checkout.enabled":  (bool,  None),
    }

    def __init__(self, store, local_path="/etc/app/last-known-good.json"):
        self.store, self.local_path = store, local_path
        self.values, self.version, self.stale = {}, 0, False
        self._load()

    def _load(self):
        try:
            raw = self.store.fetch(timeout=2)        # etcd/Consul/ConfigMap
            self._apply(raw, version=raw["version"])
            atomic_write(self.local_path, raw)       # for next boot
            self.stale = False
        except Exception:
            # FAIL STATIC: the last known good values, not built-in defaults.
            # A missing rate limit that falls back to "unlimited" is a
            # loaded gun (§4.6 rule 6).
            raw = json.loads(read(self.local_path))
            self._apply(raw, version=raw["version"])
            self.stale = True
            metrics.gauge("config.stale", 1)

    def _apply(self, raw, version):
        validated = {}
        for key, value in raw["values"].items():
            typ, rng = self.SCHEMA.get(key, (None, None))
            if typ is None:
                raise UnknownKey(key)                # typos are rejected
            if not isinstance(value, typ):
                raise BadType(key, typ)
            if rng and not (rng[0] <= value <= rng[1]):
                raise OutOfRange(key, value, rng)     # the §7.2 outage
            validated[key] = value
        # ATOMIC SWAP: never mutate the live dict — a reader mid-request
        # would see a half-applied configuration.
        self.values = MappingProxyType(validated)
        self.version = version
        metrics.gauge("config.version", version)

    def watch(self):
        for change in self.store.watch():             # push, not poll (§4.3)
            try:
                self._apply(change, change["version"])
                log.info("config applied", extra={"version": change["version"],
                                                  "keys": list(change["values"])})
            except ValidationError as e:
                log.error("config REJECTED", extra={"err": str(e)})
                metrics.incr("config.rejected")       # keep running on the old
```

```bash
# ── Draining, verified. Run this after any deploy change. ────────────────
# Roll a deployment while sending steady traffic and assert zero failures:
hey -z 120s -q 400 -c 50 http://inventory-svc.internal/health \
  | tail -5
#   Expected: Error distribution: [48,000] status code:200
#   If you see any 502/reset, draining is not working — fix it before the
#   next release. This test takes 2 minutes and prevents ~1,600 errors/day.
```

```yaml
# ── Alerts for both halves of this lesson ───────────────────────────────
alerts:
  - expr: increase(http_requests_total{code=~"502|503"}[5m]) > 0 and on(job)
          changes(deploy_started_total[5m]) > 0
    severity: page        # deploys are failing requests: draining is broken
  - expr: config_stale == 1 for 10m
    severity: warn        # running on last-known-good; config service issue
  - expr: delta(config_version[10m]) > 3
    severity: warn        # config churn: something is being edited repeatedly
  - expr: up{job="config-service"} == 0
    severity: page
  - expr: endpoint_count{service="inventory"} / desired_replicas < 0.8 for 10m
    severity: page        # readiness is failing widely (§4.2 rule 3)
```

## 9. Numerical example

BookNook: 48 instances peak, 400 rps, 41 deploys/day, 10-pod rolling deploys.

**Step 1 — the error rate of a deploy with no draining.**

```
  in-flight requests when SIGTERM arrives: 400 rps × 0.18 s p50 = 72
  with no draining and no preStop: all 72 are dropped, plus new requests
  for the propagation window (0.5 s) until the endpoint is removed:
      400 × 0.5 = 200 requests routed to a dying pod
  ⇒ ~272 failed requests per pod removal; 10 pods per deploy = 2,720
  measured: ~1,000 (some requests were not in flight; keep-alive reuse
  spread it differently)
  at 41 deploys/day: 41,000–111,000 failed requests/day → 0.03 % of traffic,
  which is invisible in aggregate dashboards and highly visible to the
  ~41,000 users who experienced one.
```

**Step 2 — the same deploy with draining and correct TTLs.**

```
  preStop 8 s + readiness 503 at SIGTERM + watch propagation 0.3 s:
      requests routed to a dying pod after the 503: 400 × 0.3 = 120
      of those, in flight: 120 × (5 s timeout) but they COMPLETE because the
      process is still serving for 8 s.
      ⇒ failed requests: 0
  ⇒ 8 seconds of drain time per pod removal eliminates the entire class.
    Cost: deploys take 8 s longer per pod (10 pods = 80 s of extra rollout
    time, in parallel ≈ 8 s total).
```

**Step 3 — P2C versus random, and why the tail is a balancer problem.**

```
  48 instances; 1 % are in a degraded state (p99 900 ms instead of 10 ms).
  random pick:   P(hit a degraded one) = 1 %   → p99 ≈ 900 ms
  P2C:           P(both picks degraded) = 0.01² = 0.0001 → p99 ≈ 12 ms
  measured on BookNook's frontend client: p99 900 ms → 120 ms, p50 unchanged
  ⇒ 7.5× tail improvement with ZERO server-side change. Before adding
    capacity for a tail problem, check whether you are picking instances
    randomly.
```

**Step 4 — config blast radius, in requests.**

```
  a config change applied to 100 % of instances at once:
      all 48 instances apply within ~2 s (watch propagation)
      if the value is wrong, EVERY request after t+2 s is affected
      ⇒ blast radius = 400 rps × duration
  at the §7.2 outage's 11-minute duration: 400 × 660 = 264,000 failed requests
  with a canary (1 instance, 5 min, then rings):
      bad value reaches 1/48 = 2.1 % of traffic for 5 minutes
      = 400 × 0.021 × 300 = 2,520 requests, and the canary comparison would
        have flagged the DB QPS rise in the first 60 s
  ⇒ canarying a config value reduces the blast radius by ~100× for the cost
    of 5 minutes of rollout time.
```

**Step 5 — flag debt, quantified.**

```
  214 flags; 61 % unchanged in 6 months; 9 read in no code path.
  cost of untangling: 3 weeks × 2 engineers = 240 engineer-hours
  ongoing cost of NOT untangling, per flag per year:
      ~0.5 h of confusion/testing/latent-bug risk → 214 × 0.5 = 107 h/year
  ⇒ the debt pays for itself to clear at ~2 years, and the real cost is the
    incident you cannot reproduce because a stale flag is on for one tenant.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **Orchestrator registration (K8s)** | No registry library; fast propagation | Requires correct readiness; K8s-only | Default in Kubernetes |
| **Self-registration (Consul/Eureka)** | Works anywhere; rich metadata | TTL-based staleness; library in every app | Multi-platform estates |
| **Client-side discovery** | Lowest latency; caller-controlled policy | Registry logic in every client; invisible staleness | Latency-sensitive, mature clients |
| **Server-side / mesh** | Central policy, mTLS, uniform retries | Extra hop, one more component to run | Many services, many languages |
| **DNS-based discovery** | Universally supported | **TTL and negative caching**; no health nuance | Simple, coarse-grained, with short TTLs |
| **Deep readiness checks** | Broken instances leave fast | **Cascading removal** when a shared dependency is slow (§11.4) | Never make them depend on shared dependencies |
| **Static config in the image** | Immutable, versioned, auditable | Requires a rebuild to change | Values that are genuinely build-time |
| **Dynamic config** | Change without deploys; kill switches | **Global write to production** (§4.6) | Tunables, limits, flags — with rings |
| **Feature flags** | Decouple deploy from release | Flag debt; combinatorial behaviour | Release and ops flags, with an owner and expiry |
| **Config default = unlimited/disabled** | Nothing breaks at startup | The failure is the *absence* of a safety limit | Never for limits |

## 11. Failure scenarios

**11.1 — The stale endpoint** (§7.1). Four contributors, ~1,000 failures per deploy. **Fix:** drain, correct DNS TTL, watch-based discovery, startupProbe.

**11.2 — Liveness that checks the database.** A 30-second DB blip failed liveness on all 48 instances; Kubernetes restarted all of them; the app took 3.1 s to warm and the DB could not handle 48 simultaneous cold starts; the outage became 14 minutes. **Symptom:** all instances restarting together. **Fix:** liveness must be shallow — process-level only (§4.2 rule 1).

**11.3 — Deep readiness causing cascading removal.** Readiness checked the DB; when the DB slowed, every instance reported unready and the load balancer returned 503 for everything — turning a 40 % degradation into 100 % failure (§7.2). **Fix:** readiness checks local state only; dependency health belongs in a metric or breaker.

**11.4 — The registry outage.** The Consul cluster lost quorum; every service's discovery client failed; new connections could not be established and the estate degraded to a full outage even though every application was healthy. **Symptom:** app metrics green, traffic zero. **Fix:** **fail static** — cache the last known endpoint list on disk and serve from it; treat discovery as a cache, not a dependency.

**11.5 — Registry-as-single-source-of-truth split brain.** Two Consul datacenters disagreed about which instances existed; some clients routed to one set, others to another; a shard was processed by two consumers. **Symptom:** duplicate processing with no error (L72 §11.2). **Fix:** a single authoritative registry per service, and consumers using the same one; or a consistent store (L81) for the registry.

**11.6 — The config that was applied half-way.** A config update mutated a nested dict in place while requests were reading it; for ~50 ms some requests saw the new timeout and the old retry count, producing an inconsistent pair (timeout 300 ms with 5 retries = 1.5 s of retries against a 300 ms budget). **Symptom:** brief, unexplained retry storms right after config changes. **Fix:** build a new immutable config object and swap the reference (§8).

**11.7 — Boot failure because config was remote-only.** After a network policy change, instances could not reach the config service at boot and refused to start — a self-inflicted outage in which the orchestrator restarted them in a crash loop. **Symptom:** pods never Ready, all at once. **Fix:** last-known-good on local disk, plus a startup path that does not require the network.

**11.8 — Secrets in the config service.** An API key was added as a normal config key, appeared in dashboards, change logs and a support bundle, and had to be rotated across 14 integrations. **Symptom:** a leaked credential in an audit trail. **Fix:** a separate secret manager, workload identity, no secret ever in the config store's general namespace.

## 12. Alternatives

| Instead of | Consider | When |
|---|---|---|
| A registry | **Orchestrator-native endpoints** (K8s) | You are on Kubernetes — it is already there |
| Client-side discovery | **A service mesh** | Many languages/services; you want mTLS and uniform retries |
| DNS + TTL | **Watch-based discovery** | You need fast failover (§4.3) |
| Dynamic config | **Redeploy** | The value changes rarely; the extra machinery is not worth it |
| Feature flags | **Blue/green or canary deploys** | The variation is at the code level, not a runtime switch |
| A config service | **Env vars per environment** | Few values, no runtime changes needed |
| Deep readiness | **Outlier ejection in the mesh** | You want bad instances out without global removal (§4.2) |

## 13. When to use it

- **Service discovery**: whenever instances are ephemeral — always, in any container or autoscaled environment.
- **Draining** (`preStop`, readiness 503, graceful shutdown): every deployment, without exception.
- **Dynamic config**: for tunables that need changing faster than a deploy — timeouts, rate limits, thresholds, kill switches.
- **Feature flags**: to decouple release from deploy, with an owner and an expiry date.
- **A registry with health checking**: when instances can be unhealthy while their process is alive.

## 14. When *not* to use it

- **A custom registry** when your orchestrator already provides one — you will get propagation bugs it has already solved.
- **Dynamic config for values that require a restart anyway** — you have added a control plane for nothing.
- **Deep readiness checks against shared dependencies** (§4.2, §11.3).
- **Liveness checks against anything but the process** (§11.2).
- **Secrets in the general config store** (§11.8).
- **Feature flags without owners or expiry** — that is debt with a control plane (§9 step 5).
- **DNS with a long TTL** for anything that fails over (§4.3).

## 15. Common mistakes

1. **No draining** — the single largest source of deploy-time errors (§7.1).
2. **Liveness checking a dependency**, causing fleet-wide restarts (§11.2).
3. **Readiness checking a shared dependency**, causing cascading removal (§11.3).
4. **Ignoring client-side DNS caching** (JVM `networkaddress.cache.ttl = -1`, §11.1).
5. **Polling discovery at 30 s** instead of watching (§4.3).
6. **No startupProbe**, so slow-starting pods receive traffic (§5).
7. **Config changes applied to 100 % at once**, with no validation or rings (§7.2).
8. **No range validation**, so `0` is a valid value for a TTL (§7.2).
9. **Mutating config in place** instead of atomic swap (§11.6).
10. **No last-known-good on disk**, so a config-service outage prevents boot (§11.7).
11. **Secrets in the config store** (§11.8).
12. **Random load balancing** where P2C would fix the tail for free (§9 step 3).
13. **Flag debt** with no owner or expiry (§9 step 5).
14. **Assuming the registry is highly available** instead of making clients fail static (§11.4).

## 16. Interview perspective

**What is actually being tested.** Whether you understand that discovery and configuration are **caching problems with staleness windows**, and whether you know the operational details that decide whether deploys are invisible. Strong candidates talk about readiness vs liveness, draining, propagation delay, and config rollout rings. Weak candidates say "we use Consul" and cannot explain why a rolling deploy produces 502s.

**Answers that land well:**

- *"How does a service find its dependencies?"* — "Kubernetes endpoints for the common case: the kubelet reports pod state, readiness gates the endpoint list, and clients watch the EndpointSlice rather than polling. For the tail I use power-of-two-choices rather than random — with 1 % of instances degraded, random gives a 1 % chance of hitting one and P2C gives 0.01 %, which took our p99 from 900 ms to 120 ms without touching the servers."
- *"Why do rolling deploys cause errors?"* — "Because the instance is removed from the registry after it stops serving, or because clients keep using a cached address. Four fixes, all cheap: `preStop` sleep plus a readiness 503 on SIGTERM so in-flight requests finish, a short client-side DNS TTL, watch-based discovery instead of a 30-second poll, and a startupProbe so slow pods never receive traffic. Our failures per deploy went from about a thousand to zero."
- *"What makes dynamic config dangerous?"* — "It is a global write to production that bypasses CI. We took an 11-minute outage when someone set a cache TTL to zero: no range validation, applied to all instances in two seconds, and readiness checks on the database turned it into a total outage. Now every key has a schema and a range, capacity-affecting keys require a canary ring, changes are versioned and auditable, and there is a one-click revert."
- *"Liveness or readiness for a database dependency?"* — "Neither. Liveness must be process-only or a dependency blip restarts the whole fleet. Readiness may look at local readiness — config loaded, pools warmed — but not at a shared dependency, or you will remove every instance at once and turn a degradation into an outage. Dependency health is a metric and a circuit breaker, not a gate."

**Common interview tasks:** design discovery for a described environment; eliminate deploy-time errors; choose a load-balancing algorithm and justify it; design a safe config rollout; decide what belongs in readiness vs liveness; plan secret distribution and rotation.

**Red flags interviewers watch for:**

- "Consul/Eureka" with no discussion of health checking or staleness.
- No draining story.
- Liveness or readiness depending on a shared dependency.
- Config changes pushed globally with no validation or canary.
- No notion of propagation delay.
- Secrets in the config store or the repo.
- Random load balancing assumed optimal.
- No numbers: no propagation window, no error rate per deploy, no TTL.

## 17. Knowledge check

**Conceptual**

1. Explain the difference between liveness and readiness, and describe the two specific outages that follow from confusing them (one for each direction). State the rule you would enforce for what each probe may check.
2. Decompose the staleness window into its four contributors and explain why connection reuse is the one most often missed. Then explain how draining changes the *kind* of failure rather than merely reducing it.
3. Explain why a dynamic configuration service is "a global write to production", and describe five controls that make it safe. For each, state the failure it prevents from §7.2.

**Practical scenario**

4. BookNook runs 48 instances of an inventory service on Kubernetes, 400 rps, 41 deploys/day, and is seeing ~1,000 failed requests per deploy plus a `p99` of 2.4 s during rollouts. It also has 1,240 config values, 214 feature flags, and a config service that pushes changes to all instances immediately. Design the complete remediation: the discovery model and why, every probe with what it may and may not check, the shutdown sequence with timings, the load-balancing algorithm with the tail arithmetic, the config validation and rollout scheme with ring sizes and abort criteria, the flag lifecycle policy, the alarms with thresholds, and the two-minute test you would add to CI to prevent regressions.

**Calculation**

5. A rolling deploy replaces 12 pods one at a time, each removal taking 15 seconds to propagate at 600 rps. (a) Compute the requests routed to a dying pod per removal with no draining, and per deploy. (b) Recompute with an 8-second drain and a 0.3-second watch propagation. (c) If each failed request costs £0.002 in support and 0.3 % of affected users abandon, compute the monthly cost of the no-draining case at 30 deploys/day. (d) State which single fix removes the largest share.

6. A service has 100 instances, of which 2 % are degraded (p99 1,200 ms against a normal p99 of 25 ms). (a) Compute the p99 under random instance selection. (b) Compute it under power of two choices. (c) Compute the number of degraded instances you would need for P2C's p99 to exceed 100 ms. (d) Explain why P2C helps the tail but does not fix a *systemically* overloaded fleet.

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
  - [x] **24. Service discovery and configuration (L87) ← you are here**
  - [ ] 25. Observability: logs, metrics, traces (L88)
  - [ ] 26. SLIs, SLOs and error budgets (L89)
  - [ ] 27. High availability and disaster recovery (L90)
  - [ ] 28. Active-passive versus active-active (L91)
  - [ ] 29. RTO and RPO (L92)

**NEXT LESSON: Phase 4, Topic 25 — Observability: logs, metrics and distributed traces**

---

---

# Lesson 88 — Observability: Logs, Metrics and Distributed Traces

*Phase 4, Topic 25*

## 1. Simple definition

**Monitoring** answers questions you knew to ask in advance ("is CPU high?", "is the error rate above 1 %?"). **Observability** is the property that lets you answer questions you did **not** know to ask — *"why is checkout slow for customers in one region using one payment method since 14:02?"* — from the telemetry the system already emits, without shipping new code.

The practical version is three kinds of telemetry plus the thing that ties them together:

| Signal | Shape | Answers | Cost driver |
|---|---|---|---|
| **Metrics** | Numbers aggregated over time, with labels | "Is it broken, how badly, since when?" | **Cardinality** (§4.5) |
| **Logs** | Discrete timestamped events | "What exactly happened in this instance?" | Volume (§4.6) |
| **Traces** | A tree of spans for one request across services | "Where did the time go, in *this* request?" | Sampling (§4.7) |
| **Correlation** | A shared ID (and exemplars) | "Join the three together" | Design discipline |

Terms defined before use:

- **Cardinality**: the number of distinct label combinations a metric has. It multiplies, and it is the dominant cost and failure mode of metrics (§4.5).
- **Histogram**: a metric that records a distribution in buckets, from which percentiles can be computed *approximately* — the only honest way to have p99s across a fleet.
- **Exemplar**: a sample trace ID attached to a metric bucket, so a spike on a graph links directly to a specific slow request.
- **Span**: one unit of work in a trace (an HTTP call, a query). A **trace** is the tree of spans for one request.
- **Head sampling**: decide whether to record a trace at request start (cheap, may miss rare failures). **Tail sampling**: decide after the request completes, based on outcome (keeps all errors, costs buffering).
- **RED / USE / four golden signals**: the standard instrumentation recipes (§4.4).
- **SLO burn rate**: how fast you are spending your error budget — the modern basis for alerting (L89).

The one sentence to carry out of this lesson: **metrics tell you *that* something is wrong, traces tell you *where*, logs tell you *why* — and unless all three share a correlation ID, you will spend the incident joining them by hand at 03:00.**

## 2. Why it exists

Because in a distributed system, **the failure modes are emergent and the code you are debugging is not on one machine.**

BookNook's checkout p99 was 780 ms against a 500 ms objective, and nothing was obviously broken:

```
  every service's own p99, measured locally:
      gateway 10 ms · checkout 15 ms · inventory 60 ms · orders 40 ms
      payment (external) 900 ms · fulfilment 70 ms
  ⇒ no service was "failing". Every dashboard was green. The objective was
    being missed anyway, and the reason was invisible to any single service's
    metrics: tail latencies ADD along a chain (L86 §9).
```

What actually found it was a **trace**: 39 % of the p99 was spent in a `sidecar→inventory` span that no application code measured, and 12 % in a retry that no metric counted because it succeeded on the second attempt. Neither fact was visible in logs (too noisy), metrics (too aggregated), or any single service's own view. **Only the per-request tree showed it.**

Three reasons observability is a *first-class* architectural concern rather than tooling:

1. **You cannot predict the failure modes.** Lessons 64–87 describe dozens of ways distributed systems fail; no monitoring dashboard anticipates them, because each is a novel interaction. Observability is the general-purpose answer: keep enough raw material to reconstruct what happened.
2. **The cost of *not* having it is measured in minutes.** BookNook's mean time to first diagnosis on a checkout incident fell from **68 minutes to 19 minutes** after tracing and correlation (L86 §7.1). At 20 incidents a quarter, that is ~16 engineer-hours saved per quarter, plus the customer impact of 49 extra minutes per incident.
3. **It is the cheapest debugging tool you will ever buy**, because it is used on *every* incident, including the ones you cannot reproduce.

And the warning that makes this lesson more than a tooling survey: **observability can cause outages.** A metrics agent with unbounded cardinality has taken down more clusters than most bugs (§7.2), and telemetry that ships synchronously from the request path is a dependency on your own monitoring. §4.8 is the section to read twice.

## 3. Real-world analogy

**A patient in an intensive care unit.**

**Metrics are the vitals monitor.** Heart rate, blood pressure, oxygen saturation — a small number of numbers, sampled continuously, and *aggregated* so a human can see a trend at a glance. They are excellent at answering "is the patient stable?" and completely useless at answering "why did their sodium drop?" Nobody charts the raw ECG trace for a week; they chart the summary.

Crucially, **the vitals monitor can be misleading in exactly the way averages are**: a patient whose temperature averages 37 °C but swings between 35 and 39 is far sicker than one at a steady 37.5. That is why you chart distributions and peaks, not means — the p99 argument (§4.1).

**Logs are the chart notes.** Discrete, timestamped, verbose, written by a human: "14:02 — patient reported dizziness; BP 90/60; given 500 ml saline." Indispensable for causality, unreadable in bulk, and expensive to keep.

**Traces are the patient's journey through the hospital.** Not vitals, not notes — a *timeline of departments*: A&E 14 min, triage 6 min, X-ray 40 min (**there it is**), results 8 min, discharge 15 min. No single department's records show that X-ray is the problem, because X-ray's own records say "40 min turnaround, within target". The trace shows the *composition* of the total.

**And the correlation ID is the patient's wristband number.** Without it, at 03:00 you are matching a chart note to a monitor reading by timestamp and hope. With it, one number links the vitals spike, the note that explains it, and the department that caused it.

The analogy's final lesson is the one that keeps systems healthy: **an ICU has all three because the patient is too sick to be examined one instrument at a time.** A distributed system is permanently in that state — there is no "simple case" where you can look at one thing.

## 4. Detailed technical explanation

### 4.1 Metrics: why percentiles, and how histograms compute them

```
  AVERAGE is a fiction. Two services, same average latency of 100 ms:
      service A: every request 100 ms          → users are fine
      service B: 95 % at 20 ms, 5 % at 1,620 ms → 5 % of users are furious
  ⇒ the average hides the second case entirely. Use percentiles, and watch
    the tail (L16).

  To compute a p99 across 48 instances you cannot average the instances'
  p99s. You need HISTOGRAMS: counters over buckets (or an exponential sketch),
  summed across instances, then percentiles computed from the summed buckets.

  bucket layout matters. With latency buckets 5,10,25,50,100,250,500,1000 ms:
      a p99 of 800 ms is reported as "in the 500–1000 bucket", and you may
      interpolate to ~800 ms — an error of up to ±250 ms at that bucket's
      width.
  ⇒ choose bucket boundaries around your OBJECTIVES (L89): if the SLO is
    500 ms, there must be a bucket edge exactly at 500 ms, or you cannot
    compute the error rate honestly.
```

| Metric type | Use for | Watch out |
|---|---|---|
| **Counter** | Requests, errors, bytes — monotonic | Use `rate()`, never the raw value |
| **Gauge** | Queue depth, in-flight, temperature | Sampled; can miss spikes between scrapes |
| **Histogram** | Latency, payload size | Bucket choice; **cardinality cost** |
| **Summary** | Pre-computed quantiles | Cannot be aggregated across instances — avoid for fleet-wide p99 |

### 4.2 The instrumentation recipes

Two standard sets, both worth memorising, because they prevent the "we have 400 dashboards and none answer the question" problem:

```
  RED — for every service, in its request handler:
      Rate      requests per second
      Errors    failures per second (by class: 4xx, 5xx, timeout, shed)
      Duration  latency distribution (histogram, with SLO-aligned buckets)

  USE — for every resource (CPU, memory, disk, pool, queue, connection):
      Utilization  how busy (% of pool in use, % CPU)
      Saturation   how much queued (run queue, waiting threads, queue depth)
      Errors       resource-level failures (pool timeouts, OOM kills)

  FOUR GOLDEN SIGNALS (Google SRE): latency, traffic, errors, saturation.
  ⇒ At minimum: RED per service + USE per resource + a business metric.
    A dashboard without a saturation signal cannot warn you before an outage
    (L76 §4.1: latency degrades only after saturation is high).
```

### 4.3 Logs: structured, correlated, sampled, and preferably few

```
  UNSTRUCTURED:  "2026-10-30 14:02:11 ERROR failed to reserve stock for order 90210"
  STRUCTURED:    {"ts":"2026-10-30T14:02:11.412Z","level":"error",
                  "msg":"reserve_failed","order_id":"90210","sku":"SKU-4471",
                  "reason":"insufficient_stock","trace_id":"4bf92f3577b34da6",
                  "span_id":"00f067aa0ba902b7","service":"inventory",
                  "version":"2.14.1","region":"eu-west-1"}
  ⇒ only the structured form can be queried, filtered, aggregated and joined
    to a trace. Logging unstructured text in a distributed system is writing
    to a file nobody can search.
```

Four rules that keep logs affordable and useful:

1. **Log events, not progress.** "Entering function X" at 1,000 rps is the main cause of runaway log bills.
2. **Never log at INFO inside a per-request loop** unless sampled.
3. **Always include `trace_id` and `span_id`** — this is what makes logs joinable (§4.5).
4. **Errors are 100 % sampled; successes are 1–10 %.** The inverse (sampling errors) is worse than no logs.

### 4.4 Traces: the only tool that shows composition

A trace is a tree of spans, each with a start time, duration, service, operation and attributes:

```
  trace 4bf92f35 (checkout, 780 ms total)
  ├─ gateway /api/checkout           8 ms
  │  └─ checkout-svc place_order     766 ms
  │     ├─ inventory-svc reserve     58 ms
  │     ├─ payment authorise        612 ms  ← the p99 contributor
  │     │  └─ sidecar→gateway        14 ms
  │     ├─ orders-svc create         38 ms
  │     └─ retry #1 (invisible in metrics: it SUCCEEDED)
  │         └─ inventory-svc reserve 52 ms
  ⇒ 612 ms in payment is expected (external). 52 ms of RETRY is not: it is
    a hidden tax that only a trace reveals, because metrics record success.
```

**Sampling is mandatory** at any real volume, and the choice matters:

| Strategy | How | Good | Bad |
|---|---|---|---|
| **Head sampling** | Decide at the root, e.g. 1 % | Cheap, simple | A 1 % sample may contain **no** instances of a rare failure |
| **Tail sampling** | Buffer all spans of a trace, decide at completion: keep if error, or if duration > p99 | **Keeps every interesting trace** | Needs a collector that buffers; more infrastructure |
| **Per-service sampling** | Each service decides | Simple to deploy | Produces broken, unusable traces |

The rule: **sample at the root (head) for baseline volume, and use tail sampling rules to additionally keep 100 % of errors and slow traces.** A system that samples errors at 1 % cannot debug the error.

### 4.5 Correlation: the one thing that must not be left to chance

```
  browser  → gateway → checkout → inventory → DB
     trace_id generated at the EDGE and propagated in a header
     (W3C `traceparent`), never regenerated in the middle.
     Every log line, every span, every metric exemplar carries it.
  ⇒ "show me everything about request 4bf92f35" becomes ONE query.

  WITHOUT it, at 03:00 you are correlating by timestamp across 6 services,
  which fails exactly when it matters most — during a latency spike, when
  clocks disagree and volumes are highest (L66).
```

**Exemplars** are the cheap 80 % version: attach one real `trace_id` per histogram bucket per scrape interval, so clicking a spike on a latency graph opens an actual slow trace. It costs almost nothing and it removes the "which of these 40,000 requests was slow?" step entirely.

### 4.6 Cost: cardinality is multiplicative, and it is the #1 mistake

```
  metric: http_request_duration_seconds (a histogram with 12 buckets)
  labels: service (10) × route (40) × method (5) × status (8)
      = 16,000 series × 12 buckets = 192,000 data points per scrape
  add ONE label with a wide domain:
      + user_id (125,000)  ⇒ 2,000,000,000 series  ⇒ the monitoring system
                             falls over, the bill is unbounded, and the pod's
                             scrape takes longer than the scrape interval.
  ⇒ THE RULE: never use unbounded values as labels. No user ids, order ids,
    raw URLs (with ids in them), IPs, email addresses, or error messages.
    Bound them: status_code is fine, error_message is not; route TEMPLATE
    (/orders/{id}) is fine, the real path is not.
```

Log volume has the same shape but is easier to reason about:

```
  1,000 log lines/sec × 400 bytes = 400 KB/s = 34.6 GB/day
  at £0.30/GB ingested + retention: ~£10/day ingest, £90/month retention for
  30 days at 1/10th compressed… ⇒ the arithmetic is manageable UNTIL someone
  logs a 2 KB request body on every request: 1,000 × 2 KB = 2 MB/s = 173 GB/day.
  ⇒ log at the edges of failure (errors, state transitions), not in the middle
    of the happy path.
```

### 4.7 Observability failure modes — this is where it becomes architecture

```
  1. SYNCHRONOUS TELEMETRY. Shipping logs over HTTP inside the request path
     makes your monitoring a hard dependency. When the collector is slow, the
     app is slow; when the collector is down, the app blocks. Telemetry must
     be asynchronous, buffered, and DROPPABLE (L76 §4.3: shed low priority
     work first — telemetry is the lowest priority work in the system).

  2. THE AGENT THAT KILLS THE HOST. A metrics agent with a memory leak, or a
     cardinality explosion, consumes the node's memory. Bound its resources
     and run it in its own resource envelope.

  3. SAMPLING THAT HIDES THE INCIDENT. 1 % head sampling of a 0.3 % error
     rate yields ~3 sampled errors per 1,000 requests — and during a partial
     outage you may sample none of the interesting ones. Tail sampling rules
     fix this (§4.4).

  4. THE DASHBOARD NOBODY TRUSTS. Metrics that disagree with reality (wrong
     aggregation, wrong denominator, clock skew between scrapers) train
     engineers to ignore them — which is worse than having none.

  5. UNBOUNDED RETENTION. 15 months of raw logs at 173 GB/day is a data
     warehouse nobody queried. Use tiers: raw 7–14 days, downsampled metrics
     for a year, and traces sampled from day one.
```

### 4.8 What to alert on

The distinction that matters, and it is the bridge to Lesson 89:

| Alert type | Example | Good | Bad |
|---|---|---|---|
| **Symptom-based** | "checkout success rate < 99 % for 5 min" | Actionable, user-relevant, survives redesign | Can be slow to fire |
| **Cause-based** | "CPU > 80 %" "disk 85 % full" | Early warning | Pages for things users do not feel; causes alert fatigue |
| **Burn-rate** (L89) | "error budget burn 14× for 1 h" | Proportional to user harm | Requires SLOs to exist |

The rule: **page humans on symptoms, ticket on causes.** A page should mean "a user is being harmed right now"; anything else is a dashboard or a ticket. Most mature teams converge on burn-rate alerting (§L89 §4.5) plus a small number of hard infrastructure pages (disk full in 4 hours, certificate expiring).

## 5. Request / data flow

One checkout request, and the telemetry it produces, end to end.

```
  ── THE REQUEST ───────────────────────────────────────────────────────────
  browser: generates trace_id 4bf92f35..., sends
           traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
  gateway: creates span (8 ms), increments
           http_requests_total{route="/api/checkout",status="200"} and
           observes 780 ms into the latency HISTOGRAM
  checkout: child span (766 ms) → inventory (58 ms) → payment (612 ms) →
            orders (38 ms) → retry → inventory (52 ms)
  every service: logs a structured line with trace_id + span_id
  every metric: an EXEMPLAR carrying this trace_id

  ── THE TELEMETRY PATH (all asynchronous, all droppable) ──────────────────
  app ──(OTLP, batched, buffered, non-blocking)──► collector (per node)
                                                     │
        ┌────────────────────────────────────────────┼───────────────────┐
        ▼                                            ▼                   ▼
   metrics remote-write                    trace export          log shipping
   (1 s scrape / push)                 (head 1 % + tail:        (batched, 5 s,
        │                                keep errors & >p99)     compressed)
        ▼                                    ▼                       ▼
   ┌──────────────────────────────────────────────────────────────────────┐
   │ backend: metrics TSDB · trace store · log index                      │
   │  retention: metrics 13 mo (downsampled) · traces 7 d · logs 14 d     │
   └──────────────────────────────────────────────────────────────────────┘

  ── THE QUERY DURING AN INCIDENT ──────────────────────────────────────────
  1. SLO dashboard: checkout burn rate 14× → a page fires (L89)
  2. latency histogram shows p99 780 ms since 14:02
  3. click the exemplar on the p99 bucket → trace 4bf92f35
  4. the trace shows 612 ms in payment + 52 ms in a hidden retry
  5. filter logs by trace_id 4bf92f35 → "payment gateway returned 503,
     retried at 200 ms" with the gateway's own request id
  ⇒ MTTD 19 minutes, and every step is a click rather than a hunt.
```

## 6. ASCII diagram

The three pillars and the joins between them:

```
                    ┌─────────────────────────────────────┐
                    │        correlation: trace_id        │
                    └───────┬──────────────┬──────────────┘
                            │              │
      ┌─────────────────────▼──┐    ┌──────▼──────────────────┐
      │  METRICS               │    │  LOGS                   │
      │  "that it is broken"   │    │  "why it broke"         │
      │                        │    │                         │
      │  rate / errors /       │    │  structured JSON        │
      │  duration histogram    │    │  errors 100 % sampled   │
      │  USE per resource      │    │  info 1–10 % sampled    │
      │                        │    │                         │
      │  cost: CARDINALITY     │    │  cost: VOLUME           │
      └───────────┬────────────┘    └──────────▲──────────────┘
                  │ exemplar → trace_id         │ trace_id in
                  ▼                             │ every line
      ┌───────────────────────────────────────────────────────┐
      │  TRACES  "where the time went, in this request"       │
      │  span tree: gateway → checkout → {inventory, payment, │
      │              orders}                                  │
      │  head sample 1 %  +  tail sample 100 % of errors/slow │
      │  cost: SAMPLING                                       │
      └───────────────────────────────────────────────────────┘
```

The cardinality bomb, drawn:

```
  series = π(labels) × buckets

  10 services × 40 routes × 5 methods × 8 statuses = 16,000         ✓ fine
  × 12 histogram buckets                          = 192,000         ✓ fine

  add user_id (125,000 values):
  16,000 × 12 × 125,000 = 24,000,000,000 series                     ✗ dead

  ⇒ the same 4 labels are safe; one unbounded label is fatal.
    Bound every label's domain, or move the value to a trace attribute
    (traces are sampled and per-request — the right home for user_id).
```

## 7. Detailed example

### 7.1 The 49-minute MTTD improvement

BookNook's checkout incidents, before and after observability work:

| Capability added | Effect |
|---|---|
| Structured logs with `trace_id` | Replaced 6-service timestamp correlation |
| Latency histograms with SLO-aligned buckets | Made p99 visible per route, not per service |
| Distributed tracing, head 1 % + tail (errors and > p99) | Found composition problems no single service could see |
| Exemplars from histogram to trace | Removed the "which request?" step |
| One "checkout health" dashboard with RED + saturation + business metric | Gave everyone the same first screen |

```
  MTFD (mean time to first diagnosis), checkout incidents:
      before: 68 min  (median 55, worst 190)
      after:  19 min  (median 14, worst 41)
  ⇒ 49 min × 20 incidents/quarter = 16 engineer-hours/quarter recovered,
    plus 49 min less customer impact per incident.
  cost: 6 weeks of engineering + ~£1,400/month of telemetry
  ⇒ pays back in the first two incidents of each quarter, and the worst-case
    improvement (190 → 41 min) is the part that prevents status-page events.
```

### 7.2 The cardinality bomb

A developer added a "per-customer latency" panel:

```
  http_request_duration_seconds{service, route, method, status, customer_id}
  customer_id values: 125,000
  series: 10 × 40 × 5 × 8 × 125,000 × 12 buckets = 24 billion potential
  within 4 hours: 2.1 million active series (only customers who sent traffic)
  consequences, in order:
      1. Prometheus memory went from 6 GB to 58 GB; the pod was OOM-killed
      2. on restart it replayed the WAL for 26 minutes, during which there
         were NO metrics — the monitoring outage lasted 26 minutes
      3. the scrape of the affected target took 14 s against a 15 s interval,
         so 40 % of scrapes were dropped
      4. the bill rose from £1,400 to £18,600/month (the vendor charges per
         active series)
  fix: customer_id removed from the metric and moved to a TRACE ATTRIBUTE
       (sampled, per-request, no cardinality explosion), plus a CI check that
       rejects any new label with a domain > 100 values, plus a per-job series
       budget alarm.
  ⇒ the lesson: a one-line code change caused a 26-minute blind spot and a
    13× cost increase. Label hygiene needs a mechanical control, not a
    code-review convention.
```

## 8. Code or configuration example

```python
# ── Instrumentation: RED metrics + a span + a correlated log line ─────────
from opentelemetry import trace, metrics
tracer = trace.get_tracer("checkout")
meter = metrics.get_meter("checkout")

# Histogram buckets aligned to the SLO (L89): an edge EXACTLY at 500 ms,
# because "fraction of requests under 500 ms" is the SLI.
LATENCY = meter.create_histogram(
    "http_request_duration_seconds",
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5],
)
REQUESTS = meter.create_counter("http_requests_total")


def place_order(request):
    with tracer.start_as_current_span("place_order") as span:
        # BOUNDED attributes only. order_id and customer_id belong on the
        # SPAN (sampled, per-request), never on a metric label (§4.6).
        span.set_attribute("order.id", request.order_id)
        span.set_attribute("customer.id", request.customer_id)

        try:
            result = do_place_order(request)
            REQUESTS.add(1, {"route": "/api/checkout", "status": "ok"})
            return result
        except Exception as e:
            span.record_exception(e)
            span.set_status(trace.Status(trace.StatusCode.ERROR))
            REQUESTS.add(1, {"route": "/api/checkout", "status": "error",
                             "error_class": type(e).__name__})   # bounded ✓
            # Log with the ACTIVE trace context so the line is joinable (§4.5)
            ctx = span.get_span_context()
            log.error("checkout_failed", extra={
                "trace_id": format(ctx.trace_id, "032x"),
                "span_id": format(ctx.span_id, "016x"),
                "order_id": request.order_id,       # in a log: fine
                "error_class": type(e).__name__,
            })
            raise
        finally:
            LATENCY.record(elapsed(), {"route": "/api/checkout"})
```

```yaml
# ── Collector: sampling that keeps the interesting traces (§4.4) ─────────
processors:
  batch: { timeout: 5s, send_batch_size: 512 }
  memory_limiter:                 # telemetry must never take down the node
    check_interval: 1s
    limit_mib: 400
    spike_limit_mib: 100
  tail_sampling:
    decision_wait: 10s            # buffer a trace this long before deciding
    policies:
      - name: errors              { type: status_code, status_code: { status_codes: [ERROR] } }
      - name: slow                { type: latency,     threshold_ms: 500 }
      - name: baseline            { type: probabilistic, percentage: 1 }
      # ⇒ 100 % of errors and slow traces, 1 % of the rest.
exporters:
  otlphttp: { endpoint: "https://telemetry.internal:4318" }
service:
  pipelines:
    traces: { receivers: [otlp], processors: [memory_limiter, tail_sampling, batch],
              exporters: [otlphttp] }
```

```yaml
# ── The alert set: symptoms page, causes ticket ───────────────────────────
alerts:
  # PAGE — users are being harmed (symptom)
  - expr: |
      sum(rate(http_requests_total{route="/api/checkout",status=~"5.."}[5m])) /
      sum(rate(http_requests_total{route="/api/checkout"}[5m])) > 0.01
    for: 5m
    severity: page

  - expr: histogram_quantile(0.99,
            sum by (le) (rate(http_request_duration_seconds_bucket{route="/api/checkout"}[5m]))) > 1.0
    for: 10m
    severity: page

  # TICKET — early warning (cause)
  - expr: pg_replication_slots_wal_retained_bytes{slot="outbox"} > 10e9
    severity: ticket
  - expr: container_memory_working_set_bytes / container_spec_memory_limit_bytes > 0.85
    for: 15m
    severity: ticket

  # THE META-ALERT everyone forgets: is monitoring even working?
  - expr: up{job="prometheus"} == 0 or absent(up{job="otel-collector"})
    severity: page
```

## 9. Numerical example

BookNook: 400 rps at the edge, 10 services, 5 regions, 41 deploys/day.

**Step 1 — why you cannot average percentiles across instances.**

```
  48 instances, each reporting its own p99 of 30 ms.
  True fleet p99 (from summed histograms): 780 ms, because 1 % of requests
  land on instances in a degraded state and no single instance sees enough
  of them to move its own p99.
  ⇒ averaging the 48 reported p99s gives ~30 ms — a 26× UNDERESTIMATE of the
    user experience. This is the single most common metrics mistake, and it
    is invisible unless you sum histograms.
```

**Step 2 — what 1 % head sampling costs you in error visibility.**

```
  error rate 0.3 %; head sampling 1 %
  sampled errors per 1,000 requests = 1000 × 0.003 × 0.01 = 0.03
  ⇒ to see even ONE error trace you need ~33,000 requests, and during a
    partial outage (say 2 % errors for 5 minutes at 400 rps = 2,400 failures)
    you would sample 24 — enough to debug, barely.
  with 0.05 % errors during a subtle regression: 2,400 errors over 12 hours →
    24 sampled. Marginal.
  ⇒ tail sampling that keeps 100 % of errors turns "barely" into "all of
    them", at the cost of buffering 10 s of trace data in the collector.
```

**Step 3 — trace volume and cost.**

```
  head 1 % of 400 rps = 4 traces/sec; each trace has ~9 spans = 36 spans/sec
  average span payload ~600 bytes ⇒ 21.6 KB/s = 1.9 GB/day
  at 7 days retention: 13 GB — trivial.
  WITHOUT sampling: 400 × 9 = 3,600 spans/sec = 2.2 MB/s = 186 GB/day
  ⇒ sampling is a 100× cost reduction for a system that can still answer
    "where did the time go" (because the ±1 % baseline plus 100 % of errors
    and slow traces covers every question you actually ask).
```

**Step 4 — log volume and the cost of logging in the wrong place.**

```
  per-request structured log (success): 1 line, ~350 bytes
      400/sec × 350 = 140 KB/s = 12 GB/day                       (acceptable)
  adding one "entering function" log per service call (9 per request):
      400 × 9 = 3,600 lines/sec × 200 bytes = 720 KB/s = 62 GB/day (5× cost)
  adding request bodies on error only (0.3 % of 400 = 1.2/sec × 2 KB):
      2.4 KB/s = 207 MB/day                                     (fine)
  ⇒ log at state transitions and failures; a per-call INFO log is the single
    most expensive habit in telemetry.
```

**Step 5 — the value, in incident arithmetic.**

```
  incidents: 20/quarter, each with MTFD 68 → 19 min and MTTR 3.1 h → 2.4 h
  engineer cost: 2 engineers × 0.7 h saved per incident × 20 = 28 h/quarter
  customer impact: 49 min less per incident × 20 = 16.3 h of degraded
      exposure per quarter, at 400 rps ≈ 391,000 fewer failed/slow requests
  telemetry cost: £1,400/month = £4,200/quarter; engineer saving alone
      (28 h × £60) = £1,680/quarter
  ⇒ the engineer-hours alone do not justify it. The justification is the
    WORST-CASE improvement (190 → 41 min), because the incidents that last
    three hours are the ones that make the status page.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **Metrics only** | Cheap, fast, good alerting | No composition view; "which request?" is unanswerable | Tiny systems |
| **Metrics + logs** | Causality for a single instance | Correlation by timestamp; log volume cost | The common minimum |
| **+ traces** | Composition, cross-service latency, hidden retries | Sampling infrastructure; per-span overhead | Any system with >3 services |
| **Head sampling only** | Simple, cheap, predictable | Misses rare failures | Baseline volume with low error rates |
| **Tail sampling** | 100 % of errors/slow traces | Collector buffering; more complexity | **The right default** (§4.4) |
| **Rich labels** | Sliceable metrics | **Cardinality explosion** (§7.2) | Bound the domain, or use trace attributes |
| **High-cardinality data in traces** | Answers "who/what exactly" | Sampling means you may not have it | Correct home for ids |
| **Symptom alerts** | Actionable pages | Fires later than cause alerts | Page-worthy conditions |
| **Cause alerts** | Early warning | Alert fatigue; pages for non-incidents | Tickets, not pages |
| **Long raw retention** | Forensic ability | Cost, usually unqueried | Rarely; use tiers |
| **Synchronous telemetry** | Simpler to write | **Monitoring becomes a request-path dependency** (§4.7) | Never |

## 11. Failure scenarios

**11.1 — Cardinality explosion** (§7.2). A `customer_id` label: 2.1 M series, an OOM-killed metrics backend, a 26-minute blind spot, 13× cost. **Fix:** bound label domains, move ids to trace attributes, CI check on new labels, a series-budget alarm.

**11.2 — Synchronous log shipping.** Logs were sent over HTTP in the request path; the collector slowed during an incident (it was receiving more logs *because* of the incident) and the app's p99 went from 180 ms to 4 s. **Symptom:** the app gets slower exactly when the incident starts. **Fix:** asynchronous, buffered, droppable exporters with a memory limiter.

**11.3 — Sampling that hid the bug.** 1 % head sampling with no tail rules; a 0.02 % error class was never captured, so a week-long regression was invisible until a customer complained. **Fix:** tail sampling keeping 100 % of errors.

**11.4 — Averaging p99s across instances** (§9 step 1). A 26× underestimate of user experience, and an SLO that appeared to be met while it was being missed. **Fix:** histograms summed across instances; never average quantiles.

**11.5 — Bucket boundaries misaligned to the SLO.** The SLO was "99 % of requests under 500 ms" but the histogram's buckets jumped 250 → 1000 ms, so the "under 500 ms" fraction could not be computed — only estimated. **Fix:** an edge exactly at every SLO threshold.

**11.6 — Logs without correlation.** Six services, 62 GB/day of logs, and no `trace_id`; MTTD during a cross-service incident was 90 minutes of timestamp matching. **Fix:** propagate a W3C `traceparent` from the edge, and include it in every log line.

**11.7 — Dashboards that disagreed with reality.** Two panels used different denominators (one counted 4xx as errors, one did not), so the "error rate" differed by 3× between dashboards; engineers stopped trusting both. **Fix:** one canonical SLI definition (L89), computed once, referenced everywhere.

**11.8 — The monitoring that monitored itself.** The alerting pipeline's own health was not alertable; a misconfigured recording rule silenced 60 % of alerts for nine days, and the first anyone knew was a customer escalation. **Fix:** meta-alerts ("no alerts in 24 h", "prometheus up == 0"), and an external dead-man's-switch.

## 12. Alternatives

| Instead of | Consider | When |
|---|---|---|
| Three-pillar observability | **Metrics + traces only** | Logs are mostly noise; traces carry attributes |
| Distributed tracing | **Structured logs with request ids** | Very low service count; tracing overhead is not worth it |
| A metrics TSDB | **Vendor SaaS** | The operational cost of running a TSDB exceeds the bill |
| Fine-grained metrics | **Exemplars + trace attributes** | You want per-request detail without cardinality (§7.2) |
| Dashboard-driven debugging | **Continuous profiling** | The problem is CPU/memory, not latency |
| Alerts on infrastructure | **SLO burn-rate alerts** (L89) | You have SLOs and want fewer, better pages |
| Log aggregation of everything | **Sampled logs + error-only** | Volume costs exceed query value |

## 13. When to use it

- **All three signals plus correlation, from the first multi-service deployment.** Retrofitting traces during an incident is impossible.
- **RED metrics per service, USE per resource**, on every service, in the same shape, so dashboards are reusable.
- **Latency histograms with SLO-aligned buckets** — you cannot compute an SLO without them.
- **Tail sampling** keeping 100 % of errors and slow traces.
- **A single business metric per critical journey** (orders/min, checkout success) — it catches the failures that no technical metric sees (L69 §11.3).
- **Meta-monitoring**: an alert for "monitoring is down".

## 14. When *not* to use it

- **Full-fidelity tracing of everything** at high volume — the cost is 100× for questions you will not ask (§9 step 3).
- **Unbounded labels** of any kind (§4.6, §7.2).
- **Synchronous telemetry in the request path** (§11.2).
- **Logging the happy path verbosely** (§9 step 4).
- **Alerting on causes instead of symptoms** for pages (§4.8) — that is what tickets are for.
- **Custom dashboards per team** before a canonical SLI exists (§11.7).

## 15. Common mistakes

1. **Averaging percentiles across instances** (§11.4).
2. **Unbounded metric labels** (§11.2).
3. **Head sampling with no tail rules** (§11.3).
4. **No correlation ID** across services (§11.6).
5. **Logging in the request path per call** (§9 step 4).
6. **Synchronous telemetry exporters** (§11.2).
7. **Histogram buckets not aligned to the SLO** (§11.5).
8. **Alerting on cause for pages**, causing alert fatigue.
9. **No business metric**, so silent failures go unnoticed.
10. **No meta-alert**, so monitoring outages are invisible (§11.8).
11. **Different denominators** in different dashboards (§11.7).
12. **Retaining raw everything** rather than tiering.
13. **Instrumenting only successes**, so retries are invisible (§4.4).
14. **Assuming a trace exists for an error** when sampling says otherwise.

## 16. Interview perspective

**What is actually being tested.** Whether you can say **which signal answers which question**, and whether you know the two things that break observability in production: cardinality and synchronous telemetry. Strong candidates describe correlation IDs, histogram-based percentiles, tail sampling and SLO-aligned buckets. Weak candidates list three tools and stop.

**Answers that land well:**

- *"How would you debug a p99 regression across 10 services?"* — "Metrics first: a fleet-wide latency histogram per route shows *that* it regressed and since when — and I sum histograms rather than averaging p99s, which would underestimate by an order of magnitude. Then an exemplar on the p99 bucket gives me a real trace ID. The trace shows where the time went across services, including retries that never appear in error metrics. Then logs filtered by that trace ID give the why. That path took our mean time to first diagnosis from 68 minutes to 19."
- *"What is cardinality and why does it matter?"* — "The number of distinct label combinations, and it multiplies: 10 services × 40 routes × 5 methods × 8 statuses × 12 buckets is about 200,000 series, which is fine; add a customer_id label and it becomes billions. We did that once — 2.1 million active series, an OOM-killed Prometheus, a 26-minute metrics blackout and a 13× bill. Ids belong on traces, which are sampled; metrics labels must have bounded domains, and we enforce that in CI."
- *"What do you sample, and how?"* — "Head sampling at the root at about 1 % for baseline volume, plus tail sampling rules that keep 100 % of error traces and 100 % of traces slower than the SLO. Head sampling alone means a 0.05 % error class is essentially unobservable."
- *"How do you keep observability from causing an outage?"* — "Telemetry is asynchronous, buffered, droppable and resource-limited — it is the lowest-priority work in the process. It never sits in the request path, the collector has a memory limiter, and the app keeps serving when the collector is gone."

**Common interview tasks:** instrument a service; choose sampling strategy with numbers; design correlation across services; diagnose a described p99 regression; set alerts for a described system; explain the cost model of telemetry.

**Red flags interviewers watch for:**

- "We have logs" as the whole observability answer.
- Averaging p99s, or using `Summary` metrics for a fleet.
- Unbounded labels, or no awareness of cardinality cost.
- No correlation between signals.
- Sampling errors away.
- Synchronous log shipping in the request path.
- No business metric.
- Alerting only on CPU/disk.
- No numbers: no sampling rate, no volume, no cost.

## 17. Knowledge check

**Conceptual**

1. Explain the difference between monitoring and observability, and map logs, metrics and traces to the three questions "is it broken", "where", and "why". Then explain why correlation is a design requirement rather than a convenience.
2. Explain why averaging per-instance p99 values is wrong, and describe the mechanism that produces a correct fleet-wide percentile. Give the magnitude of the error in §9 step 1 and state why it matters for an SLO.
3. Explain head sampling versus tail sampling, and why a system with head-only sampling cannot debug a 0.05 % error class. Then state the sampling configuration you would deploy and the cost it implies.

**Practical scenario**

4. BookNook has 10 services, 400 rps, 41 deploys/day, 20 incidents a quarter, and an observability stack that currently consists of unstructured logs and CPU dashboards. Design the complete observability system: the instrumentation per service with the exact metrics and their labels, the histogram bucket layout justified against a 500 ms SLO, the trace propagation scheme, the sampling configuration with arithmetic, the log policy with volume estimates and the rule for what is 100 % versus sampled, the correlation scheme, three dashboards with the panels each contains, the alert set split into pages and tickets with thresholds, the controls that prevent cardinality explosions and synchronous telemetry, and the meta-monitoring.

**Calculation**

5. A service has 40 instances, each reporting p99 40 ms, but 3 % of requests land on degraded instances with 1,500 ms latency. (a) Compute the true fleet p99. (b) State what averaging the 40 reported p99s would show and the factor by which it understates reality. (c) Compute the same for a 1 % degraded fraction. (d) Explain what instrumentation change makes the true number visible.

6. A system emits 1,000 requests/sec. (a) Compute daily log volume at 1 structured line per request of 400 bytes. (b) Compute it if a developer adds one 200-byte INFO line per service call (9 calls per request). (c) Compute trace storage at 1 % head sampling with 9 spans per trace at 600 bytes, 7-day retention. (d) Compute the same without sampling, and state the ratio.

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
  - [x] **25. Observability: logs, metrics, traces (L88) ← you are here**
  - [ ] 26. SLIs, SLOs and error budgets (L89)
  - [ ] 27. High availability and disaster recovery (L90)
  - [ ] 28. Active-passive versus active-active (L91)
  - [ ] 29. RTO and RPO (L92)

**NEXT LESSON: Phase 4, Topic 26 — SLIs, SLOs and error budgets**

---

---

# Lesson 89 — SLIs, SLOs and Error Budgets

*Phase 4, Topic 26*

## 1. Simple definition

Three definitions and one piece of arithmetic, which together replace "is it up?" with a number that a business can argue about.

- **SLI (service level indicator)**: a measured ratio of good events to valid events. *"Proportion of checkout requests that succeeded in under 500 ms, over the last 5 minutes."* It is a number you compute from telemetry (L88), not a promise.
- **SLO (service level objective)**: a target for that SLI over a time window. *"99.5 % of checkout requests succeed within 500 ms, measured over 28 days."*
- **SLA (service level agreement)**: a contract — an SLO **with consequences** (credits, penalties). SLAs are legal documents for customers; SLOs are engineering targets, and they are usually *stricter* than the SLA so that you fail internally before you fail contractually.
- **Error budget**: `1 − SLO`, expressed as a quantity of allowed failure. If the SLO is 99.5 % over 28 days and you serve 20 M requests, the budget is **100,000 failed (or slow) requests** — or, in time terms, **3 h 22 min** of full outage.

Terms defined before use:

- **Valid event**: a request that *should* have succeeded — the denominator. Getting the denominator wrong is the most common SLI bug (§4.1).
- **Good event**: a valid event that met the criterion (success **and** within the latency threshold). An SLI is `good / valid`.
- **Window**: the period over which the SLI is computed (rolling 28 days is the standard; calendar months are common for reporting).
- **Burn rate**: how fast you are spending the budget relative to the rate that would exactly exhaust it over the window. A burn rate of 1 exhausts the budget in exactly 28 days; **14.4 exhausts it in 2 days**.
- **Error budget policy**: the pre-agreed consequence when the budget is spent (usually: reliability work takes priority over features).

The one sentence to carry out of this lesson: **an SLO converts "how reliable should this be?" from an argument into arithmetic — and the error budget is the number that lets a team decide, in advance, when to stop shipping and start fixing.**

## 2. Why it exists

Because "up" is not a measurement, and "we want high availability" is not a decision.

BookNook's experience is the standard one. Before SLOs, the reliability conversation was:

> **Business:** "The site went down for 20 minutes yesterday. That's unacceptable."
> **Engineering:** "It was 99.97 % available this month."
> **Business:** "So is that good?"
> **Engineering:** "Compared with what?"

Nobody could answer, so the conversation ended with an arbitrary target ("five nines") that nobody could afford and nobody measured, and an argument every month about whether an incident "counted".

The SLO does three things that no amount of dashboards can:

1. **It puts the user in the definition.** "99.97 % of requests returned a 200" is compatible with an outage that returned fast failures for 4 % of users if the rest were fine — or with a service that returns 200 while taking 9 seconds. A latency-bounded success ratio measures the thing users experience (L16).
2. **It quantifies the reliability/velocity trade-off.** BookNook's checkout SLO of **99.5 %** over 28 days gives an error budget of **3 h 22 min** of full outage (or the equivalent in partial failures). That budget is *spendable*: every deploy spends some, incidents spend some, and when it is gone the trade-off has been made by arithmetic rather than by argument.
3. **It makes the trade-off explicit and pre-agreed.** The **error budget policy** says what happens at 100 % burn *before* it happens — usually "feature launches pause until the budget is restored". That single sentence ends the monthly argument, because the decision no longer depends on who is in the room.

And it justifies *not* being perfect: `99.9 % → 99.99 %` typically **triples or quadruples** infrastructure and engineering cost for **43 minutes → 4.3 minutes** of monthly downtime. For a bookstore, 43 minutes of degraded checkout per month is worth far less than the engineering required to eliminate it (§4.3). **Choosing 99.5 % deliberately is a mature engineering decision, not a failure of ambition** — and an SLO is the artefact that records it.

## 3. Real-world analogy

**A household's monthly budget for "things going wrong".**

A family sets aside **£200 a month** for the unexpected: a broken boiler, a cracked phone screen, a taxi when the car fails. This is an **error budget**, and every property of SLOs maps onto it:

- **The allowance is derived from what you can tolerate**, not from a wish. A household with £200/month is not "failing" at home maintenance; it has decided how much entropy it can absorb. That is `1 − SLO`.
- **Every event spends it.** A £180 boiler repair leaves £20 for the rest of the month. An incident that consumes 60 % of your error budget in 40 minutes leaves very little room for the next three weeks — which is exactly what makes the *rate* of spending matter, not just the total (**burn rate**, §4.5).
- **The spending rate is the early warning.** If you have spent £90 by the 3rd of the month, you do not wait until the 30th to notice; you change behaviour *now*. That is why SLO alerting is on burn rate rather than on the budget's remaining total.
- **The policy is agreed in advance.** "If we exceed the allowance before the 20th, we stop eating out" only works if it was agreed when everyone was calm — and if nobody can quietly renegotiate it at the moment it binds. That is the error budget policy (§4.6), and it is the part organisations skip and then wonder why SLOs "did nothing".
- **You do not budget for zero risk**, because that means never leaving the house.

The analogy's last and sharpest point: **a family that has spent its whole allowance in a week does not conclude that the allowance was wrong; it stops spending.** A team that burns its error budget in two days should not conclude that the SLO was too strict — that is the SLO doing its job, which is to tell them, with a number, that reliability work now outranks the next feature.

## 4. Detailed technical explanation

### 4.1 Choosing the SLI: the ratio, and the denominator problem

Every SLI has the form:

```
  SLI = (good events) / (valid events)
```

For a request-driven service, four SLIs cover almost everything:

| SLI | Good event | Valid event | Notes |
|---|---|---|---|
| **Availability** | response with a non-error status | every request that should have been served | The workhorse |
| **Latency** | completed within the threshold (e.g. 500 ms) | every *successful* request | Measure "success **and** fast", not "fast" alone |
| **Throughput/completeness** | processed within a deadline | events that should have been processed | For pipelines (L68 §4.5) |
| **Correctness/quality** | result matched the source of truth | requests where correctness is checkable | Often a batch check (reconciliation, L74 §13) |

**The denominator is where SLIs go wrong**, and it goes wrong silently. Three decisions must be made explicitly:

```
  1. DO CLIENT ERRORS COUNT? A 400 from a malformed request, or a 401 from an
     expired token, is not a service failure. Counting them makes the SLI move
     with traffic mix, and a bot attack "breaks" your SLO.
     ⇒ EXCLUDE 4xx caused by the caller, from BOTH numerator and denominator.

  2. DOES A SHED/THROTTLED REQUEST COUNT? Yes — a 429 caused by your own
     overload (L76) is a failure from the user's perspective.
     ⇒ INCLUDE it as a valid event, and as a bad one.

  3. WHERE IS IT MEASURED? Server-side metrics miss requests that never
     arrived (connection reset, timeout at the LB, DNS failure at the client).
     Client-side or edge-side measurement is closer to the user's truth.
     ⇒ measure at the EDGE (load balancer / gateway) where possible; if you
       measure server-side, state that the SLI excludes pre-server failures.
```

BookNook's example, stated precisely enough to compute:

```
  checkout_checkout_availability:
      good  = requests returning 2xx or 3xx
      valid = all requests EXCEPT those returning 4xx with error_class
              ∈ {validation, auth_expired, idempotency_conflict}
  checkout_latency:
      good  = requests completing in < 500 ms AND successful
      valid = all successful requests
      window = 28 days rolling
```

### 4.2 Windows, and why aggregation choices change the answer

```
  Consider a 43-minute total outage in a 28-day month (40,320 minutes).

  AS A MONTHLY AVERAGE: 99.9 %  ← "we hit our SLO" (it is exactly the line)
  AS A 7-DAY ROLLING WINDOW, right after the outage:
      the outage is a much larger fraction → the SLI reads ~99.6 %
      ⇒ the same event is "fine" or "bad" depending on the window.
  AS A 1-HOUR WINDOW DURING the outage: 0 % — which is how alerting works.

  ⇒ THREE windows are used, for three different jobs:
      long (28 d)   — the objective, the budget, the quarterly conversation
      medium (1–6 h) — burn-rate alerting (a slow burn is still a burn)
      short (5–30 m) — fast burn alerting and incident detection
    Using one window for all three is how SLOs end up either noisy or late.
```

And the aggregation trap: **never compute an SLO from percentiles of percentiles.** With histograms, sum the buckets across instances first (L88 §4.1), then compute the ratio. An SLO built on an averaged p99 is measuring nothing.

### 4.3 Choosing the target: the nines, and what they cost

| SLO | Downtime per 28 days | Downtime per year | Typical cost/user |
|---|---|---|---|
| 99 % | 6 h 43 m | 3.65 days | Internal tools, batch |
| 99.5 % | 3 h 22 m | 1.83 days | **Non-critical user-facing** (BookNook checkout) |
| 99.9 % | 40 m 19 s | 8 h 46 m | Standard user-facing service |
| 99.95 % | 20 m 10 s | 4 h 23 m | Revenue-critical |
| 99.99 % | 4 m 2 s | 52 m 36 s | Payments, core infrastructure |
| 99.999 % | 24 s | 5 m 15 s | Telecom-grade (multi-region, consensus everything) |

Three rules for picking:

1. **Derive it from the user, not from ambition.** What does a user do when a request fails? Retry silently (0.1 % is invisible), contact support (0.5 % is visible), or abandon (1 %+ starts costing money)?
2. **Check it is achievable with your dependencies.** Availability multiplies (L15, L77 §9 step 5): three dependencies at 99.9 % give 99.7 %. **An SLO of 99.9 % on a service with a 99.9 %-available hard dependency is arithmetically impossible** unless you have fallbacks for that dependency. This is the calculation that most often forces a degradation design.
3. **Price the last nine.** Going 99.9 → 99.99 typically requires multi-region, redundant dependencies, faster failover, and a team that can be paged 24/7 — usually a 3–4× cost increase. Write that number down next to the SLO; it is the argument for stopping at 99.9.

### 4.4 The error budget, in whatever unit the audience needs

```
  SLO 99.5 % over 28 days:

  AS A FRACTION:  0.5 % of valid events may fail
  AS EVENTS:      20,000,000 requests × 0.005 = 100,000 failed requests
  AS TIME:        0.005 × 40,320 min = 201.6 min = 3 h 22 min of TOTAL outage
  AS A MIX:       a 30-minute total outage (30/201.6 = 14.9 % of the budget)
                  + 3 hours at 10 % error rate (180 min × 0.10 = 18 min-equivalent
                    = 8.9 %) …
  ⇒ Quote the unit that makes the argument clearest. For engineers, minutes of
    outage; for product, failed checkouts; for finance, orders lost.
```

The budget is also the **allocation** mechanism. A useful refinement is to split it:

```
  100 % of the budget
    ├── 25 % — planned change (deploys, migrations) — spent on purpose
    ├── 25 % — dependency failures outside your control
    ├── 30 % — your own bugs and incidents
    └── 20 % — headroom (the unexpected)
  ⇒ this makes "we have no budget left" actionable: you know WHICH category
    overspent, so the remedy is targeted (freeze deploys vs invest in a
    dependency's fallback vs fix the bug class).
```

### 4.5 Burn-rate alerting: the technique that replaced "error rate > 1 %"

A fixed error-rate threshold is either too noisy (fires on a 1-minute blip) or too slow (a 2 % error rate for an hour is a real incident that a 5 % threshold never sees). **Burn rate** fixes both by measuring the *rate of budget consumption*:

```
  burn_rate = (observed error rate) / (1 − SLO)

  SLO 99.5 % ⇒ the budget rate is 0.5 % errors.
      an observed 5 % error rate → burn rate = 5 / 0.5 = 10×
      10× burn exhausts a 28-day budget in 2.8 days
      14.4× burn exhausts it in 28/14.4 = 1.94 days  (≈ 2 days)

  ⇒ the alert is "we are burning the budget fast enough to matter", which is
    a user-impact statement, not a threshold on a raw metric.
```

The standard multi-window, multi-burn-rate alert set (from the SRE workbook), with BookNook's numbers:

| Burn rate | Long window | Short window | Budget consumed | Meaning | Action |
|---|---|---|---|---|---|
| **14.4×** | 1 h | 5 m | 2 % in 1 h | Fast burn: a real outage | **Page** |
| **6×** | 6 h | 30 m | 5 % in 6 h | Medium burn | **Page** |
| **1×** | 3 d | 6 h | 10 % in 3 d | Slow burn | **Ticket** |

The **short window exists to stop the alert flapping** and to make it clear the problem is current rather than historic; a long-window-only alert keeps firing for hours after an outage ends.

### 4.6 The error budget policy: the part that makes SLOs work

An SLO without a consequence is a dashboard. The policy is a one-page document, agreed by engineering and product, that says:

```
  1. If the budget is projected to be consumed before the window ends:
        - no new feature releases to the affected service
        - the team's next sprint includes reliability work
        - only fixes, rollbacks and reliability changes may ship
  2. Exception process: a written, time-bound waiver (e.g. a committed
     marketing campaign) with the CTO's name on it.
  3. When the budget is RESTORED (a full window with no further burn), the
     freeze lifts automatically — no renegotiation.
  4. Nobody is blamed for spending budget. Spending is what it is for.
     The failure mode is spending it SILENTLY and never fixing the cause.
```

Two things make the policy real: **it is written down before it binds**, and **the freeze is automatic** (no meeting required to trigger it). Without those, the SLO becomes an argument again the moment the budget runs out — which is exactly when arguments are most expensive.

### 4.7 Dependency SLOs and the budget decomposition

Your SLO constrains your dependencies, and the arithmetic is unforgiving:

```
  checkout availability target: 99.9 %
  call path: gateway → checkout → {payments, inventory, orders}
  if all three dependencies must succeed (no fallback):
      achievable = A(payments) × A(inv) × A(orders)
      0.999 = x³ ⇒ x = 0.99967 ⇒ every dependency needs 99.97 %
  ⇒ either you demand 99.97 % of every dependency (expensive), or you build
    fallbacks so that some are not on the critical path (L77 §9 step 5).
  with a fallback on inventory (cached availability) and on orders (retry):
      achievable ≈ A(payments) = 99.97 % for one dependency only.
  ⇒ THE SLO is what forces the degradation design. That is its most useful
    architectural effect: it turns "should we build a fallback?" into
    "we cannot meet 99.9 % without it".
```

## 5. Request / data flow

From raw requests to a page, for BookNook's checkout SLO (99.5 % availability, 500 ms latency, 28-day window).

```
 ── 1. EVENTS ──────────────────────────────────────────────────────────────
 every checkout request at the edge emits:
     http_requests_total{route="/api/checkout", status="200", latency_bucket="le_0.5"}
     … and one exemplar per bucket per scrape (L88 §4.5)

 ── 2. RECORDING RULES (computed every 30 s, stored as new series) ────────
 sli:checkout:availability:ratio5m =
     sum(rate(http_requests_total{route="/api/checkout", status!~"5..",
                                  error_class!~"validation|auth_expired"}[5m]))
   / sum(rate(http_requests_total{route="/api/checkout",
                                  error_class!~"validation|auth_expired"}[5m]))

 ── 3. BUDGET ─────────────────────────────────────────────────────────────
 budget_total        = 0.005 × valid_events_in_window
 budget_consumed     = bad_events_in_window
 budget_remaining    = budget_total − budget_consumed
 burn_rate_1h        = (1 − sli_1h) / 0.005

 ── 4. DECISIONS ──────────────────────────────────────────────────────────
 burn_rate_1h > 14.4 AND burn_rate_5m > 14.4  → PAGE   (fast burn)
 burn_rate_6h > 6    AND burn_rate_30m > 6    → PAGE   (medium burn)
 burn_rate_3d > 1    AND burn_rate_6h > 1     → TICKET (slow burn)
 budget_remaining < 0                          → ERROR BUDGET POLICY TRIGGERS
                                                 (release freeze, automatic)

 ── 5. THE HUMAN LOOP ─────────────────────────────────────────────────────
 the page fires at 14:02; the burn rate is 22×; the exemplar trace shows a
 payment gateway timeout; 40 minutes later the incident is over having
 consumed 61 % of the month's budget.
 ⇒ the freeze is now in effect for 3 weeks. That is the SLO doing its job.
```

## 6. ASCII diagram

The budget over a 28-day window, with the events that spend it:

```
  budget
  remaining
  100 % ┤●
        │ ╲  deploy (0.4 %)
   90 % ┤  ╲●
        │   ╲
   80 % ┤    ●
        │     ╲
   60 % ┤      ╲
        │       ●───── 40-min incident, burn rate 22×  (61 % of budget)
   40 % ┤        ╲
        │         ╲
   20 % ┤          ●
        │           ╲──── slow burn: dependency degradation (1.8×)
    0 % ┤─────────────●───────────────●───────────────► day
        0    7    14   21   28
                                  ▲
                                  └── budget exhausted → RELEASE FREEZE
                                      (automatic, pre-agreed, §4.6)

  ⚠ the SHAPE matters more than the total: one cliff (an incident) plus a
    slow bleed (a dependency) need different remedies. The category split
    in §4.4 tells you which.
```

The burn-rate alerting structure, and why two windows:

```
  error rate
    5 % ┤        ┌────────┐              ← 40-min incident
        │        │        │
   0.5 %┤────────┘        └──────  ← SLO line (budget rate)
        └──────────────────────────► time

  LONG WINDOW (1 h) alone:   fires late, and keeps firing for an hour after
                             the incident ends (flapping, false urgency)
  SHORT WINDOW (5 m) alone:  fires on 30-second blips (noise)
  BOTH (1 h AND 5 m over 14.4×): fires fast, clears fast, no blips.
```

## 7. Detailed example

### 7.1 BookNook's SLO set

Four SLOs, deliberately not more (a common failure is 30 SLOs that nobody tracks):

| Service / journey | SLI | SLO | Budget (28 d) |
|---|---|---|---|
| **Checkout availability** | 2xx-or-3xx ÷ (all minus caller errors) | **99.5 %** | 3 h 22 m |
| **Checkout latency** | success < 500 ms ÷ successes | **99.0 %** | 6 h 43 m |
| **Catalogue read** | success < 300 ms ÷ requests | 99.9 % | 40 m |
| **Order event freshness** | published < 5 s ÷ events | 99.5 % | 3 h 22 m |

Why checkout is 99.5 % and catalogue is 99.9 % — the reasoning, which is the actual work:

```
  checkout: the payment gateway is a third party with a 99.95 % SLA and a
      p50 of 150 ms. Its unavailability ALONE consumes most of a 99.9 %
      budget. 99.9 % would be unachievable without a queue-and-retry flow
      for payments, which changes the product (you cannot take money later
      without the customer's involvement). ⇒ 99.5 % is honest.
  catalogue: cached at the edge, no third party, fallback to stale cache.
      ⇒ 99.9 % is achievable and cheap, and the user notices 40 minutes of
         unavailability far more than they notice 3 hours of slow checkout.
  ⇒ the numbers come from the architecture, not from a policy statement.
    THAT is the value of writing them down: the conversation changed from
    "make it five nines" to "which dependency can we add a fallback for?"
```

### 7.2 The month the budget was spent in 40 minutes

```
  t=14:02  payment gateway begins timing out (p99 30 s; its own incident)
  t=14:02  checkout error rate 4.8 % → burn rate 4.8/0.5 = 9.6×
  t=14:04  burn rate 22× (as retries amplify, L65 §8.3)
  t=14:04  PAGE fires (both 1 h and 5 m burn > 14.4×)
  t=14:09  on-call opens the SLO dashboard; exemplar opens a trace showing
           612 ms in payment then a 30 s timeout
  t=14:12  circuit breaker threshold lowered; checkout now degrades to
           "payment queued, order confirmed by email" (L77 §4.4)
  t=14:42  error rate back under SLO; burn rate 0.3×
  ⇒ total: 40 minutes, 61 % of the 28-day budget consumed.

  consequences, all automatic:
      · error budget policy triggered: feature releases to checkout frozen
      · the freeze was lifted 19 days later after a clean window
      · the retro's action item was not "make the gateway faster" (not ours)
        but "make the breaker trip faster and degrade earlier", which would
        have cut the burn to ~15 % of the budget for the same outage.
```

The instructive part is the retro conclusion: **the SLO made the correct remedy obvious.** Without a budget, the discussion would have been "the gateway is unreliable, we should build our own"; with a budget, the arithmetic said the 40-minute outage cost 61 % of the budget *because the breaker was slow*, and the fix was four hours of engineering rather than a six-month project.

## 8. Code or configuration example

```yaml
# ── Prometheus recording rules: the SLI and its budget ────────────────────
groups:
  - name: checkout-slo
    interval: 30s
    rules:
      # 1. Availability SLI (L88 §4.2: RED, with the denominator defined)
      - record: sli:checkout_availability:ratio5m
        expr: |
          sum(rate(http_requests_total{route="/api/checkout",status!~"5..",
                  error_class!~"validation|auth_expired|idempotency_conflict"}[5m]))
          /
          sum(rate(http_requests_total{route="/api/checkout",
                  error_class!~"validation|auth_expired|idempotency_conflict"}[5m]))

      # 2. Latency SLI: success AND fast — the common mistake is latency
      #    measured only over successes (which then looks perfect during an
      #    outage because only fast ones succeed).
      - record: sli:checkout_latency:ratio5m
        expr: |
          sum(rate(http_request_duration_seconds_bucket{route="/api/checkout",
                  status!~"5..",le="0.5"}[5m]))
          /
          sum(rate(http_requests_total{route="/api/checkout",status!~"5.."}[5m]))

      # 3. Burn rates over the three standard horizons
      - record: burn:checkout_availability:1h
        expr: (1 - sli:checkout_availability:ratio1h) / 0.005
      - record: burn:checkout_availability:5m
        expr: (1 - sli:checkout_availability:ratio5m) / 0.005
      - record: burn:checkout_availability:6h
        expr: (1 - sli:checkout_availability:ratio6h) / 0.005

      # 4. Budget remaining, in events and in minutes-of-outage-equivalent
      - record: slo:checkout:budget_remaining_ratio
        expr: |
          1 - (
            sum(increase(http_requests_total{route="/api/checkout",status=~"5.."}[28d]))
            / (0.005 * sum(increase(http_requests_total{route="/api/checkout"}[28d])))
          )
```

```yaml
# ── The alerts: multi-window, multi-burn-rate (SRE workbook) ──────────────
alerts:
  - alert: CheckoutFastBurn
    expr: |
      burn:checkout_availability:1h > 14.4
      and
      burn:checkout_availability:5m > 14.4
    labels: { severity: page, slo: checkout_availability }
    annotations:
      summary: "Burning 14.4x budget — exhausts 28d budget in ~2 days"
      runbook: "https://runbooks/checkout-availability"
      # 2 % of the monthly budget per hour: unacceptable at any hour

  - alert: CheckoutMediumBurn
    expr: |
      burn:checkout_availability:6h > 6
      and
      burn:checkout_availability:30m > 6
    labels: { severity: page }

  - alert: CheckoutSlowBurn
    expr: |
      burn:checkout_availability:3d > 1
      and
      burn:checkout_availability:6h > 1
    labels: { severity: ticket }
    # 10 % of the budget over 3 days: not an incident, but it WILL exhaust
    # the window if unaddressed — a ticket, not a page.

  - alert: ErrorBudgetExhausted
    expr: slo:checkout:budget_remaining_ratio <= 0
    labels: { severity: ticket, policy: freeze_releases }
    annotations:
      summary: "Error budget exhausted — release freeze in effect (§4.6)"
```

```python
# ── Computing the budget in whatever unit the audience needs (§4.4) ───────
def error_budget(slo: float, valid_events: int, window_days: int = 28):
    fraction = 1 - slo
    minutes = window_days * 24 * 60
    return {
        "fraction": fraction,
        "events": int(valid_events * fraction),
        "outage_minutes": fraction * minutes,          # total-outage equivalent
        "window_days": window_days,
    }

# BookNook checkout, 20M requests over 28 days, SLO 99.5 %:
# {'fraction': 0.005, 'events': 100000, 'outage_minutes': 201.6, ...}
# ⇒ "100,000 failed checkouts, or 3 h 22 m of total outage, per 28 days"
#   is a sentence both engineering and product can act on.
```

## 9. Numerical example

BookNook checkout: 20 M valid requests per 28 days, SLO 99.5 % availability / 99.0 % latency.

**Step 1 — the budget in four units, so it can be argued with.**

```
  availability SLO 99.5 %:
      fraction        0.5 %
      failed events   20,000,000 × 0.005                 = 100,000 requests
      outage minutes  0.005 × 40,320                      = 201.6 min = 3 h 22 m
      per day         = 7.2 min of full-outage equivalent
  latency SLO 99.0 %:
      slow events     20,000,000 × 0.01                   = 200,000 requests
  ⇒ the checkout team may spend 7.2 minutes of total-outage equivalence per
    day. That is the number that decides whether a 30-minute migration window
    on a Saturday is reasonable (it is: 15 % of the month's budget).
```

**Step 2 — burn rates, and where the alert thresholds come from.**

```
  burn_rate = error_rate / (1 − SLO) = error_rate / 0.005
      error rate 0.5 %  → 1×    → exhausts the window in exactly 28 days
      error rate 2.5 %  → 5×    → 5.6 days
      error rate 5 %    → 10×   → 2.8 days
      error rate 7.2 %  → 14.4× → 1.94 days  ← the fast-burn page
  derivation of 14.4: we want a page when 2 % of the 28-day budget would be
      consumed within 1 hour:
      2 % of 28 days = 0.56 days = 13.44 hours of budget-rate burning in 1
      hour ⇒ burn rate = 13.44 / 1 = 13.44… rounded to 14.4 for a small
      safety margin (and because it makes the 2-day arithmetic clean).
  derivation of 6: 5 % of the budget in 6 hours ⇒ 0.05 × 28 d / 6 h = 5.6 → 6.
  ⇒ the thresholds are not magic; they are "how much of the month am I willing
    to lose before anyone is woken up".
```

**Step 3 — the value of the short window, quantified.**

```
  an incident that ends at t=0:
      1-hour window alone: alert stays firing until t+60 min
          ⇒ 60 minutes of false "still burning" and possibly a second page.
      1 h AND 5 m: clears at t+5 min.
  a 30-second blip at 20 % error rate:
      5-minute window alone: (0.20 × 30 s)/300 s = 2 % average → burn 4×  ✗
          4× is below the 14.4 threshold, so no page. Good.
      but a 5 m window alone with a 60-second blip at 60 % error:
          average 12 % → burn 24× → PAGE for a blip. Bad.
      with the 1 h window also required: 1 h average ≈ 1.4 % → burn 2.8× →
          no page. ✓
  ⇒ the long window prevents blip pages; the short window prevents
    post-incident false alarms. Both are needed.
```

**Step 4 — the cost of the last nine, which is the argument for 99.5.**

```
  BookNook's measured costs to improve checkout availability:
      99.5 → 99.9:   fallbacks for inventory + orders, faster breaker,
                     2 more engineers' quarter             ≈ £140,000/yr
      99.9 → 99.99:  multi-region active-active (L91), inventory replication,
                     24/7 follow-the-sun on-call            ≈ £610,000/yr
      99.99 → 99.999: consensus everywhere, cell-based architecture,
                      strict change management               ≈ £2,100,000/yr
  user impact avoided per step: 3 h 22 m → 40 m → 4 m → 24 s of monthly
      total-outage equivalent.
  revenue at risk: 1 minute of checkout outage ≈ 0.42 orders/sec… at the
      monthly average of 0.5 orders/sec and AOV £18, one minute ≈ £9,
      so 3 h 22 m ≈ £1,800/month ≈ £21,600/yr; 40 m ≈ £4,300/yr.
  ⇒ spending £140,000/yr to avoid £17,000/yr of lost orders is a business
    decision, not an engineering one — and for a bookstore the answer is
    clearly NO. That is what the SLO document says, in writing, so nobody
    has to re-litigate it after an incident.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **No SLO** | Nothing to maintain | Reliability is an argument; "up" is undefined | Never past prototype |
| **A single SLO per service** | Focus; everyone knows the number | Blind to a second failure mode (e.g. latency) | Very small services |
| **2–4 SLOs per journey** | Covers availability, latency, freshness | More definitions to keep honest | **The standard** |
| **Strict target (99.99 %)** | Very few user-visible failures | 3–10× cost; slow deploys; heavy process | Revenue-critical, contractual |
| **Honest target (99.5 %)** | Cheap, fast shipping, a real budget to spend | Users see ~3 h/month of degradation | Non-critical journeys (§7.1) |
| **Monthly window** | Matches reporting cycles | Resets abruptly; "use it or lose it" behaviour | Business reporting |
| **Rolling 28-day window** | No cliff; smooths the incentive | Harder to explain to finance | **Engineering default** |
| **Burn-rate alerts** | Actionable, proportional, fewer pages | Requires SLOs and recording rules | Always, once SLOs exist |
| **Fixed-threshold alerts** | Simple | Noisy or late; ignores the budget | Small systems without SLOs |
| **Error budget policy** | Pre-agreed trade-off; ends the argument | Can block a committed launch | Always — it is the point |

## 11. Failure scenarios

**11.1 — The denominator nobody defined.** 4xx caller errors were counted as failures; a bot attack sent 400,000 malformed requests, the SLI fell to 91 %, and a false incident was declared. **Symptom:** the SLI moves with traffic mix, not with your health. **Fix:** exclude caller-caused errors from numerator *and* denominator, and document the definition.

**11.2 — Latency measured only over successes.** During an outage the failures returned in 2 ms, so "latency" improved; the dashboard was green during the worst incident of the year. **Symptom:** latency and error rate inversely correlated. **Fix:** define the latency SLI over *valid events*, with failures counted as bad (or measured over all valid events, not just successes).

**11.3 — Averaging percentiles.** The SLO was computed from per-instance p99 averages; it reported 99.97 % while the true user experience was 99.6 % (L88 §9 step 1). **Symptom:** users complain while the SLO shows green. **Fix:** sum histogram buckets, then compute the ratio.

**11.4 — A 100 %-of-budget incident that triggered nothing.** A slow burn (0.8× for three weeks) never crossed a fixed 5 % error-rate threshold, but consumed the entire budget. **Symptom:** budget exhausted with no alerts ever fired. **Fix:** a slow-burn ticket at 1× over 3 days (§4.5).

**11.5 — An SLO the architecture cannot meet.** A team was told 99.9 % while its hard dependency's SLA was 99.9 %; the SLO was missed every month by construction. **Symptom:** permanent red dashboard, learned helplessness. **Fix:** dependency arithmetic (§4.7), then either a fallback or an honest target.

**11.6 — The SLO nobody owned.** 31 SLOs existed; 22 had no named owner; 9 were computed from metrics that no longer existed. **Symptom:** an SLO dashboard that is decoration. **Fix:** 2–4 SLOs per critical journey, each with an owner in a file.

**11.7 — The policy that was never invoked.** Budget exhausted in week 1; feature releases continued because "the launch was already committed". **Symptom:** the SLO had no effect on behaviour. **Fix:** write the exception process (with a named approver) alongside the policy, and make the freeze automatic rather than a decision.

**11.8 — Alert flapping.** A one-hour-only burn-rate alert fired for 55 minutes *after* each incident ended, producing three "still burning?" pages a week and, eventually, mute. **Symptom:** alerts that outlive their incidents. **Fix:** add the short confirmation window (§9 step 3).

## 12. Alternatives

| Instead of | Consider | When |
|---|---|---|
| SLOs | **Fixed-threshold alerting** | Pre-SLO maturity; small systems |
| Error budgets | **A named reliability owner and a weekly review** | Too small for formal budgets |
| Burn-rate alerts | **Symptom alerts on success ratio** | You have an SLI but not a full budget pipeline |
| 28-day rolling window | **Calendar month** | Reporting and contractual alignment |
| SLO on availability | **SLO on a business metric** | The user-visible outcome is a business event (order completion) |
| Per-service SLOs | **Per-journey SLOs** | Users experience a journey, not a service (L86 §9) |

## 13. When to use it

- **Any user-facing service, from the moment it has users** — even a single 99 % availability SLO is better than none.
- **Per critical journey**, not per service: users feel "checkout", not "inventory-svc".
- **With an error budget policy written in advance** and an automatic freeze.
- **With burn-rate alerting** once the SLI exists — it replaces most fixed-threshold alerts and reduces pages.
- **As the input to architecture decisions** — the dependency arithmetic (§4.7) is what justifies fallbacks, multi-region, and everything in L90–L92.

## 14. When *not* to use it

- **For internal batch jobs with no user-facing impact** — use freshness/completeness objectives instead (L68 §4.5), not availability SLOs.
- **With targets you cannot measure** (no histogram buckets at the threshold, §11.3).
- **With 30 SLOs** — nobody tracks them; pick the journeys that cost money.
- **As a marketing number.** An SLO is an internal target; the SLA is the contract, and it should be looser.
- **When there is no owner and no policy** — that is a dashboard, not an SLO (§11.6, §11.7).
- **As a substitute for fixing a broken dependency** — a budget tells you how much you are losing, not why.

## 15. Common mistakes

1. **An undefined denominator** (§11.1).
2. **Latency measured over successes only** (§11.2).
3. **Averaging percentiles** instead of summing histograms (§11.3).
4. **Only fast-burn alerts**, so slow burns exhaust the budget silently (§11.4).
5. **An SLO the dependencies cannot support** (§11.5).
6. **Too many SLOs with no owners** (§11.6).
7. **A policy that is never invoked** (§11.7).
8. **Alerts without a short confirmation window** (§11.8).
9. **Buckets not aligned to the SLO threshold** (L88 §4.1) — you cannot compute the SLI.
10. **A monthly window with "use it or lose it" behaviour** — teams take risks in week 4.
11. **Confusing SLO with SLA** in customer conversations.
12. **Not splitting the budget by category**, so remedies are untargeted (§4.4).
13. **Measuring server-side only**, ignoring failures before the server (§4.1).
14. **Setting the SLO from ambition** rather than from user behaviour and cost (§4.3).

## 16. Interview perspective

**What is actually being tested.** Whether you can define an SLI precisely (numerator, denominator, window), derive the budget in units a business understands, and explain burn-rate alerting. Strong candidates also do the dependency arithmetic and state an error budget policy. Weak candidates say "we target 99.9 %" without being able to compute 40 minutes, or explain what happens when it is missed.

**Answers that land well:**

- *"How do you measure reliability?"* — "An SLI: the ratio of good events to valid events, measured at the edge. For checkout, a good event is a 2xx or 3xx; valid excludes caller errors like validation and expired auth. The SLO is 99.5 % over a rolling 28 days, which is a budget of 3 hours 22 minutes of total-outage equivalent, or 100,000 failed checkouts. We spend it deliberately."
- *"Why not five nines?"* — "Because the arithmetic says it costs about £600k a year more than 99.9 %, and the user impact avoided is 40 minutes down to 4 minutes a month — about £4,300 of lost orders. For a bookstore that is a clear no. The SLO document says so in writing, so it does not get re-litigated after every incident."
- *"How do you alert on it?"* — "Burn rate, not error percentage: burn rate is the error rate divided by one minus the SLO, so it says how fast you are exhausting the budget. A 14.4× burn exhausts 28 days in 2 days, and we page when it holds for both an hour and five minutes — the long window prevents blip pages, the short one prevents alerts that keep firing after the incident ends. Slow burns get a ticket at 1× over three days."
- *"What happens when the budget is gone?"* — "A pre-agreed, automatic release freeze on that service until a clean window restores it, with a written exception process that has a named approver. If that policy does not exist, the SLO is just a dashboard."

**Common interview tasks:** define SLIs for a described service; compute a budget in minutes and events; derive burn-rate thresholds; decide an SLO target with cost reasoning; do the dependency arithmetic for a target; design the alert set.

**Red flags interviewers watch for:**

- "99.99 % because we care about quality" with no cost or user analysis.
- No denominator definition.
- Averaging percentiles.
- Fixed error-rate alerting with no budget concept.
- No error budget policy, or one that is never enforced.
- Confusing SLO/SLA.
- Unable to convert a percentage into minutes.
- Ignoring dependency availability when setting a target.

## 17. Knowledge check

**Conceptual**

1. Define SLI, SLO, SLA and error budget, and explain the relationship between them. Then explain why an SLA is normally looser than the SLO, and what breaks if it is not.
2. Explain burn rate and derive the 14.4× fast-burn threshold from the requirement "page when 2 % of the budget would be consumed in an hour". Then explain why both a long and a short window are used.
3. Explain why an error budget policy is the part that makes SLOs work, and describe what happens in an organisation that has SLOs but no policy. Use §11.7.

**Practical scenario**

4. BookNook's checkout serves 20 M valid requests per 28 days through gateway → checkout → inventory, payment (third party, SLA 99.95 %, p50 150 ms) and orders. Define the SLIs with exact numerators, denominators and exclusions; choose SLO targets with the dependency arithmetic and the cost reasoning; compute the budget in events, minutes and per-day terms; design the alert set with all thresholds and windows; state the error budget policy including the exception process; and describe exactly what happens on a day when a 40-minute gateway outage burns 61 % of the budget.

**Calculation**

5. A service serves 8 M requests per 30-day window with an SLO of 99.9 %. (a) Compute the error budget in events and in minutes of total-outage equivalent. (b) Compute the burn rate for an observed 4.5 % error rate, and how long that rate would take to exhaust the budget. (c) Compute the error rate that would exactly exhaust the budget over the full window. (d) If 30 % of the budget is allocated to dependency failures and a dependency outage consumes 25 % of the total budget, compute the remaining budget and state what the policy should trigger.

6. A journey depends on three services with availabilities 99.95 %, 99.9 % and 99.5 %. (a) Compute the achievable availability with no fallbacks. (b) Compute the availability required of each if the target is 99.9 % and all are on the critical path. (c) Recompute (a) with a fallback on the weakest service. (d) State which architectural change the arithmetic justifies, and what the SLO document should say about it.

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
  - [x] **26. SLIs, SLOs and error budgets (L89) ← you are here**
  - [ ] 27. High availability and disaster recovery (L90)
  - [ ] 28. Active-passive versus active-active (L91)
  - [ ] 29. RTO and RPO (L92)

**NEXT LESSON: Phase 4, Topic 27 — High availability and disaster recovery**

---
