# Lesson 27 — Browser, Application, Database, and Distributed Caches

*Phase 2, Topic 10*

## 1. Simple definition

Lesson 26 defined *what* a cache is and gave you the two formulas you will use for the rest of this course:

```
hit ratio      h = hits / (hits + misses)
effective latency  L_eff = h × L_hit + (1 − h) × L_miss
```

This lesson answers the next question: **where do you put the cache?**

A **cache layer** is one physical place along the path between a user's eyeball and the authoritative copy of the data where a result can be kept and re-served. There are many such places — roughly eight of them between a CPU register and a browser — and they differ by four properties only:

| Property | What it means |
|---|---|
| **Latency** | How long a hit takes to serve |
| **Capacity** | How much can be held before something is evicted |
| **Sharing** | How many consumers see the *same* cached copy |
| **Control** | Whether you can invalidate an entry on demand, or only wait for it to expire |

Terms defined before use:
- **Authoritative copy / source of truth**: the one place the data is definitively correct. For BookNook that is PostgreSQL.
- **Hop**: one network round trip. A hop within an availability zone is ~0.25–0.5 ms; a cross-region hop is ~70–150 ms.
- **Eviction**: removing an entry to make room for another (LRU, LFU, random — Lesson 26).
- **Invalidation**: removing or updating an entry because the underlying data changed. Full treatment is **Phase 2, Topic 12 — Cache invalidation and TTL** (Lesson 29).
- **Coherence**: whether two copies of the same logical item, held in two different caches, agree with each other.

The single idea to carry out of this lesson: **as you move a cache closer to the user it gets faster and less controllable, and as you move it closer to the database it gets more controllable and slower.** Every layer choice is a position on that one axis.

## 2. Why it exists

Lesson 26 showed that caching works. It did not say where. Beginners therefore do one of two things, and both are wrong:

**Failure mode 1 — "we have Redis, so we're cached."** BookNook puts every lookup in Redis. A book detail page makes 28 backend data fetches. Each Redis `GET` is ~0.5 ms including the network hop. That is 14 ms of pure cache access per page render, before any application logic, and Redis is now handling 28 × 3,000 RPS = 84,000 operations per second for data that changes once a month. The data is cached, but at the wrong layer: a value read 84,000 times per second and written once a month should not be crossing a network at all.

**Failure mode 2 — "cache everything locally, memory is cheap."** BookNook caches the book catalogue in each app server's heap. It is now 0.0004 ms per read instead of 0.5 ms — a 1,250× improvement. Then a price changes. Server A picks up the new price in 30 seconds; server B still has the old one for another 4 minutes because its TTL started at a different instant. A customer refreshes and watches the price flicker between £12.99 and £14.99 depending on which server the load balancer picks (Lesson 23). Support calls it "the haunted price bug". The data is fast, but there are now **N divergent copies** and no way to reconcile them.

Caching layers exist because these two failure modes have different fixes, and the fix is chosen by **access pattern**, not by preference:

| Access pattern | Right layer | Why |
|---|---|---|
| Read 10,000×/s, written once a day, small, staleness of 60 s acceptable | In-process | Network hop is pure waste; divergence window is bounded and harmless |
| Read 5,000×/s, written 50×/s, must be consistent across servers | Distributed (Redis) | One shared copy; a write is visible to all servers at once |
| Read once per user per session, large, user-specific | Browser | Zero server cost; never shared, so coherence is irrelevant |
| Static bytes, read globally, changes on deploy | CDN | Physics: a user in Sydney cannot reach London in under ~140 ms |
| Arbitrary query over 40 GB of rows, unpredictable predicates | Database buffer pool | The DB already does this, better than you will |

So the layer question is not stylistic. Putting the catalogue in Redis costs BookNook 14 ms per page and 84,000 ops/s. Putting a price in an in-process cache costs correctness. Both are the same mistake — the layer did not match the access pattern.

## 3. Real-world analogy

**A professional kitchen during a dinner service.**

- **The chef's cutting board** (CPU L1/L2 cache): the three things being touched *right now*. Grabbing something takes no appreciable time. Holds almost nothing.
- **The counter beside the chef** (CPU L3 / RAM): salt, oil, the mise en place for tonight's menu. One reach away. Holds a service's worth of items.
- **The kitchen's own fridge** (in-process application cache): each chef has their own. Fast, private — and if the head chef changes the recipe, *each* chef's fridge has to be updated separately, and for a while they will be cooking different dishes.
- **The shared walk-in cold room** (distributed cache, Redis/Memcached): one room, all chefs. Thirty seconds of walking, but what one chef puts in, every chef sees. Change the recipe once and everyone is corrected at once.
- **The supplier's regional depot** (database buffer pool): big, organised, authoritative-ish, same-day delivery.
- **The farm** (disk / source of truth): everything exists here eventually, but the lead time is long.
- **The chain's pre-positioned city warehouses** (CDN): identical stock placed in twelve cities so that no restaurant is ever more than 20 minutes from supply.

**Where the analogy breaks down:**

| Professional kitchen | Cache hierarchy |
|---|---|
| Taking an ingredient from the cold room *removes* it; two chefs cannot take the same onion | Reading from a cache **copies**, it does not consume. This is why read-heavy caching scales infinitely and write caching does not |
| A chef can see that the milk has gone off | A cached value gives no signal that it is stale. Staleness is invisible at read time — the entry looks exactly as valid as a fresh one. This is the defining hazard of the whole topic |
| Restocking the cold room is a deliberate human act | Cache population is usually a *side effect* of a miss, so a cold cache causes a load spike on the database at exactly the worst moment (the thundering herd, Lesson 26) |
| Distance is measured in seconds of walking and is roughly linear | Latency between layers spans **eight orders of magnitude** (1 ns to 100 ms). Intuition built at one layer is worthless at another |
| A chef's fridge going missing is obvious | An in-process cache simply vanishes on every deploy, silently, and the database absorbs the entire difference |
| The farm never runs out | The database absolutely does run out of capacity, and the cache is usually the only thing standing between it and collapse |

That fifth row is the one engineers under-rate. In-process caches are destroyed on every restart. If a deploy rolls 20 servers in 4 minutes and each was serving a 95 % hit ratio, the database briefly sees roughly 20× its normal read load. We quantify this in Section 9.

## 4. Detailed technical explanation

### 4.1 The full hierarchy

Every number below is **typical/approximate** for 2024-era commodity hardware and cloud networking. Order of magnitude is what matters; the exact figure varies by vendor.

| # | Layer | Typical hit latency | Typical size | Shared by | Can you invalidate it? |
|---|---|---|---|---|---|
| 1 | CPU register | ~0.3 ns | ~1 KB | One core | No (hardware) |
| 2 | CPU L1 cache | ~1 ns | 32–64 KB per core | One core | No (hardware) |
| 3 | CPU L2 cache | ~4 ns | 256 KB–2 MB per core | One core | No (hardware) |
| 4 | CPU L3 cache | ~15–40 ns | 8–64 MB | All cores on a socket | No (hardware) |
| 5 | Main memory (RAM) | ~80–100 ns | 16 GB–1 TB | One machine | n/a |
| 6 | **In-process cache** (heap map) | ~0.1–1 µs (100–1,000 ns) | 50 MB–4 GB | **One process** | Yes, but only locally — N copies |
| 7 | Local NVMe SSD | ~50–150 µs | 0.5–8 TB | One machine | Yes |
| 8 | **Distributed cache** (Redis/Memcached, same AZ) | **~0.3–1 ms** | 1 GB–1 TB (clustered) | **All app servers** | **Yes, globally, instantly** |
| 9 | **Database buffer pool** | ~0.5–2 ms (as seen by the app, including network + parse) | 25–75 % of DB RAM | All DB clients | Managed by the DB |
| 10 | Database on SSD | ~5–30 ms per query | Unbounded | All DB clients | n/a (source of truth) |
| 11 | **CDN edge** | ~10–40 ms from the user | Effectively unbounded | **Every user in that region** | Yes, via purge — but slowly (seconds to minutes) |
| 12 | **Browser cache** | ~0–10 ms | 50 MB–several GB | **One user** | **No — you cannot reach it** |
| — | Cross-region origin fetch | ~70–150 ms RTT | — | — | — |

Two structural observations:

1. **The jump from layer 6 to layer 8 is ~1,000×.** In-process is a pointer dereference; Redis is a network round trip. Every discussion in this lesson about "is a local cache worth the divergence risk?" is really a discussion about whether a 1,000× latency difference justifies accepting N inconsistent copies.
2. **Controllability inverts at layer 11.** From layer 1 to layer 9 you can (in principle) force an update. At the CDN you can request a purge and wait. At the browser you cannot do anything at all — which is why cache headers must be right *before* you ship.

### 4.2 Browser cache

The browser is the only cache where the storage is on hardware you do not own and cannot reach. Everything is governed by **HTTP response headers** you set at the origin or CDN.

**The headers that matter:**

- `Cache-Control: max-age=N` — the response may be reused without asking the server for N seconds. This is the **freshness lifetime**.
- `Cache-Control: s-maxage=N` — same, but applies only to *shared* caches (CDN, proxies), overriding `max-age` for them. Lets you say "browsers 60 s, CDN 1 day".
- `Cache-Control: no-cache` — **this does not mean "do not cache"**. It means "you may store it, but you must revalidate with the origin before every reuse." It is the most misread directive in HTTP.
- `Cache-Control: no-store` — *this* is "do not write it to disk or memory at all". Use for personal or financial data.
- `Cache-Control: private` — only the browser may cache it; shared caches (CDN) must not. Essential for per-user responses, because a CDN caching a logged-in page serves one user's data to another. This is a real and severe class of incident.
- `Cache-Control: immutable` — promises the body will never change at this URL, so the browser must not revalidate even on a reload. Only safe with content-hashed filenames.
- `ETag: "abc123"` — an opaque version token. The browser sends `If-None-Match: "abc123"`; the server replies `304 Not Modified` with an empty body if unchanged.
- `Last-Modified` / `If-Modified-Since` — the same idea with one-second resolution; weaker than `ETag`.
- `Vary: Accept-Encoding, Accept-Language` — tells caches which request headers change the response. **Omitting `Vary` is how a gzip-encoded body gets served to a client that cannot decompress it**, and how an English page gets served to a German user.

**Revalidation** is the `304` path: a round trip happens, but no body is transferred. For a 180 KB JS bundle on a 10 Mbps connection, that is the difference between ~150 ms and ~40 ms. Still worse than a `max-age` hit, which is ~0 ms.

**Memory cache vs disk cache.** Browsers maintain both:

| | Memory cache | Disk cache |
|---|---|---|
| Latency | ~0 ms (sub-millisecond) | ~2–10 ms (plus decompression) |
| Lifetime | Current tab/session only | Survives browser restart |
| Size | Tens of MB | Hundreds of MB to several GB |
| Populated by | Resources used on the current page | Everything cacheable |

A resource used twice on one page is served from memory cache. The same resource tomorrow comes from disk cache. Both are invisible to your server — **you get no request, no log line, no metric**. This is why browser-cache hit ratio must be inferred from Real User Monitoring, not from server logs.

**Back/forward cache (bfcache).** A separate mechanism: when the user navigates away, the browser freezes the *entire page* — DOM, JavaScript heap, scroll position — and on Back restores it in ~10 ms with no requests at all. Pages are made bfcache-**ineligible** by: an open WebSocket in some browsers, a `unload` event handler, or `Cache-Control: no-store` on the document. Removing a legacy `unload` handler is frequently the single largest navigation-latency win available on an old codebase, and it costs one line.

### 4.3 In-process application cache

A plain map in the application's own memory: Caffeine (Java), `lru_cache` (Python), `sync.Map` plus an eviction policy (Go), or a Node `Map`.

**Why it is unbeatable on latency:** a hit is a hash lookup plus a pointer dereference — ~100–500 ns. No serialisation, no socket, no syscall. You get the *object*, already deserialised. That last part is routinely forgotten: a Redis hit returns bytes you must still parse. Deserialising a 4 KB JSON document costs ~20–50 µs, which is comparable to the Redis network hop itself. Your real Redis read cost is therefore often close to double the latency `SLOWLOG` reports, because `SLOWLOG` measures only the time inside Redis.

**The consistency problem, stated precisely.** With N app servers, an in-process cache is N independent caches. There is no mechanism by which writing to one updates the others. Therefore:

1. A write to PostgreSQL makes **all N copies stale simultaneously**.
2. Each copy becomes correct again only when its own TTL expires.
3. TTLs started at different wall-clock instants (whenever each server first missed on that key).
4. Therefore the **divergence window is up to one full TTL**, and during it, different servers return different answers for the same key.
5. Because the load balancer spreads a user's requests across servers (Lesson 20 — statelessness means no affinity), **a single user can observe the value oscillate**. This is strictly worse than consistent staleness: a user who sees £12.99 twice assumes it is the price; a user who sees £12.99, £14.99, £12.99 assumes the site is broken.

This is **not** a bug to be fixed with a cleverer eviction policy. It is structural: there is no shared state, so there can be no shared invalidation — unless you add one (pub/sub broadcast, below).

**Why in-process caches are still right for small, hot configuration data.** The divergence problem is bounded by three factors you control:

- **Write rate.** If the data changes 3 times a day, the expected number of seconds per day during which any divergence exists is `3 × TTL`. At TTL = 30 s, that is 90 seconds out of 86,400 — 0.1 % of the day.
- **Consequence of staleness.** A feature flag read 30 seconds late is harmless. A stock level read 30 seconds late oversells a book. **Choose the layer by the cost of being wrong, not by the probability of being wrong.**
- **Size.** Feature flags, currency rates, shipping-zone tables, category trees, A/B assignments, JWT signing keys — all are kilobytes to a few megabytes. They fit in every process with no capacity planning.

And the benefit is large precisely because these items are read on *every* request. A feature-flag lookup at 0.0003 ms × 28 flags per request is free; the same 28 lookups over Redis is 14 ms — often more than the rest of the request.

**Mitigations when you want local speed with better coherence:**

| Mitigation | How it works | Residual risk |
|---|---|---|
| Short TTL (5–30 s) | Bounds the divergence window | Still up to one TTL; raises miss traffic |
| TTL jitter | Each server picks TTL × (1 ± 0.2) so refreshes desynchronise | Does not reduce the window, only the synchronised stampede |
| Redis pub/sub invalidation broadcast | On write, publish `invalidate:key`; every server deletes its local copy | **Fire-and-forget** — a server that was reconnecting misses the message and stays stale forever |
| Version counter in Redis, polled every 1 s | Servers poll a single integer; on change, drop the local cache | Adds 1 req/s/server; bounded 1 s window; robust to missed messages |
| Redis client-side caching (`CLIENT TRACKING`, Redis 6+) | Redis itself tracks which clients hold which keys and pushes invalidations | Best available, but tied to Redis and adds server-side tracking memory |

The version-counter poll is the pattern most teams end up at, because it fails safe: a missed poll just means you retry in one second, whereas a missed pub/sub message means permanent staleness.

### 4.4 Distributed cache

A separate service — Redis or Memcached — holding one shared copy, reachable by every app server over the network.

**What you buy:** one copy, so a write invalidates it for everyone at once; capacity far beyond one process's heap; survival across deploys; and the ability to scale cache capacity independently of app capacity.

**What you pay:** one network hop (~0.3–1 ms same-AZ, 10–40× a cross-AZ penalty if you get the placement wrong), serialisation on both sides, a new service to operate, and a new failure domain — if the cache dies, the database receives 100 % of read traffic instantly.

**Redis vs Memcached, properly compared:**

| Dimension | Redis | Memcached |
|---|---|---|
| Data model | Strings, hashes, lists, sets, sorted sets, bitmaps, HyperLogLog, streams, geospatial | **Opaque strings only** |
| Threading | Single-threaded command execution (I/O threads since 6.0); one core does the work | **Truly multi-threaded**; scales vertically across cores |
| Peak throughput, 1 node | ~100k–200k ops/s typical | ~500k–1M+ ops/s typical on many cores |
| Memory efficiency for plain blobs | Higher per-key overhead (~50–100 B) | **Lower overhead; slab allocator; less fragmentation at scale** |
| Persistence | RDB snapshots + AOF log — can survive restart | **None. Restart = empty cache** |
| Replication | Built-in async primary/replica | None (client-side sharding only) |
| High availability | Redis Sentinel or Redis Cluster with automatic failover | None built in |
| Clustering | Redis Cluster: 16,384 hash slots, server-side sharding | Client-side consistent hashing only (Lesson 38) |
| Eviction policies | 8 policies (`allkeys-lru`, `volatile-ttl`, `allkeys-lfu`, …) | LRU per slab class |
| Atomic operations | Rich: `INCR`, `SETNX`, Lua scripts, transactions, `MULTI/EXEC` | `incr`, `decr`, `add`, `cas` |
| Pub/Sub, streams, locks | Yes | No |
| Operational complexity | Higher (persistence tuning, failover, cluster topology) | **Very low — it is one binary with a memory limit** |
| Multi-get efficiency | `MGET`, pipelining | `get_multi`, very efficient |

**How to choose — concretely, not "it depends":**

- Choose **Memcached** when *all four* of these hold: your values are opaque blobs; you need more than ~200k ops/s from a single node; you do not need the cache to survive restart; and you do not need rate limiters, locks, leaderboards or queues. In practice this is large, simple, read-heavy HTML/fragment caching.
- Choose **Redis** when *any one* of these holds: you need a data structure (a sorted set for a leaderboard, a hash for a session, `INCR` for a rate limiter); you need the cache warm after a restart; you need replication or automatic failover; or you want one piece of infrastructure to serve caching, locking and pub/sub.

For BookNook, Redis is correct and it is not close: BookNook needs `INCR` for per-IP rate limiting at the API gateway (Lesson 25), a sorted set for "trending books", and a warm cache after maintenance. Those requirements are unmet by Memcached at any throughput.

### 4.5 Database-level caches

The database is already a cache. Three distinct mechanisms, routinely confused:

**(a) The buffer pool** (`innodb_buffer_pool_size` in MySQL, `shared_buffers` plus the OS page cache in PostgreSQL). The DB keeps recently used **pages** — not query results — in RAM. A query whose pages are all resident does no disk I/O at all.

This is the highest-leverage and least-glamorous cache in your system. Typical sizing: **InnoDB buffer pool ≈ 70–75 % of machine RAM**; **PostgreSQL `shared_buffers` ≈ 25 % of RAM**, deliberately lower because PostgreSQL also relies on the OS page cache and double-buffers. A buffer-pool hit is ~1 µs of memory access inside the DB; a miss is ~100 µs on NVMe or ~5–10 ms on spinning disk. Monitor `Innodb_buffer_pool_read_requests` vs `..._reads` (MySQL), or `pg_statio_user_tables` hit ratios (PostgreSQL). **Below ~99 % buffer-pool hit ratio on an OLTP workload, buy RAM before you build an application cache** — it is cheaper, requires no code, and introduces no coherence problem.

**(b) The query cache — and why MySQL removed it.** MySQL once cached whole result sets keyed by the exact query text. It was deprecated in 5.7 and **removed entirely in MySQL 8.0**. The reasons are a case study in why result caching belongs in the application, not the database:

1. **Invalidation was table-granular.** *Any* write to a table flushed *every* cached result referencing that table. On a write-heavy table the cache was permanently empty.
2. **It was protected by a single global mutex.** Every query — hit or miss — contended on it. Above ~8 cores the query cache made MySQL *slower*, and the standard production advice became "set `query_cache_size=0`".
3. **Matching was textual.** `SELECT * FROM books WHERE id=1` and `select * from books where id = 1` were different entries.
4. **It could not know what the application could tolerate.** The DB must be conservative and invalidate on any possible change. Your application knows a book description may safely be 60 seconds stale. That knowledge only exists at the application layer — which is exactly why cache-aside (Lesson 28) lives there.

PostgreSQL has never had a query result cache, for the same reasons.

**(c) Materialised views.** A **materialised view** is a query whose result is physically stored as a table and refreshed on command or on a schedule — as opposed to a plain view, which is just a stored query re-executed every time.

```sql
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_bestsellers;
```

They are the right tool when a query is expensive (aggregations over millions of rows), the result is small, and staleness of minutes is acceptable — dashboards, bestseller lists, daily reports. They are the wrong tool for per-entity lookups, because refreshing recomputes the whole view, not the one row you changed. `CONCURRENTLY` avoids locking readers during the refresh but requires a unique index and is slower.

### 4.6 CDN (preview)

A **CDN** (Content Delivery Network) is a distributed cache at layer 11: hundreds of edge locations, each caching origin responses close to users. It is the only layer that can beat the speed of light, because it moves the data rather than the request. Full treatment is **Phase 3, Topic 9 — CDNs and edge caching**. For this lesson, know two things: it caches by URL plus `Vary`, and `Cache-Control: private` is what stops it caching a logged-in user's page and serving it to a stranger.

### 4.7 Choosing a layer from the access pattern

Work through these in order and stop at the first match:

1. **Is it a static asset with a content-hashed URL?** → CDN + `Cache-Control: public, max-age=31536000, immutable`. Done.
2. **Is it personal to one user and re-read within a session?** → Browser, with `Cache-Control: private, max-age=...`. Never CDN.
3. **Is it small (< 10 MB), read on nearly every request, changed rarely, and is up-to-60-seconds staleness harmless?** → In-process cache, short TTL, plus a version-counter poll.
4. **Must every server agree on the value immediately after a write?** → Distributed cache. In-process is disqualified outright, regardless of latency benefit.
5. **Is the value large, or is the working set bigger than a process heap?** → Distributed cache.
6. **Is the query an arbitrary predicate over a large table, with unpredictable parameters?** → Do not application-cache it; the hit ratio will be near zero. Fix the index and size the buffer pool.
7. **Is it an expensive aggregation whose result is small and may be minutes stale?** → Materialised view.
8. **Does it change on every request (a balance, a cart total at checkout, an auth decision)?** → Do not cache it. Lesson 26's formula shows that at `h ≈ 0`, `L_eff ≈ L_miss` plus the cache lookup — you have added latency and a correctness risk for nothing.

Layers combine. BookNook's book detail page in its final form uses **five** simultaneously: CDN for the cover image, browser for the JS bundle, in-process for feature flags, Redis for the book JSON, and the PostgreSQL buffer pool underneath all of it.

## 5. Request / data flow

A first-time visitor requests `GET /books/9780141036144` on BookNook. Every layer is consulted in order.

1. Browser checks its **memory cache** for the HTML document. Miss — first visit this session.
2. Browser checks its **disk cache**. Miss — first visit ever.
3. Browser resolves DNS (Lesson 4), opens TLS (Lesson 7), and issues the request to the CDN edge, ~15 ms away.
4. **CDN edge** looks up the URL plus its `Vary` dimensions. The HTML is `Cache-Control: private`, so the CDN does not cache it and forwards to the origin, 40 ms away.
5. Origin **load balancer** picks app server B by least connections (Lesson 23) and forwards the request.
6. App server B reads 12 **feature flags** from its **in-process cache**. 12 hits, ~0.004 ms total. No network.
7. App server B needs the book record. It builds the key `book:v3:9780141036144` and issues `GET` to **Redis**.
8. Redis returns `nil` — a **miss**. Elapsed: 0.4 ms (the hop was paid even though nothing was returned; a miss is never free).
9. App server B queries **PostgreSQL**: `SELECT ... FROM books WHERE isbn = $1`.
10. PostgreSQL parses and plans, then reads the index and heap pages. The index root is in the **buffer pool** (hit, ~1 µs); the heap page is not (miss) and is read from NVMe (~120 µs).
11. PostgreSQL returns the row. Total as observed by the app: 6 ms, dominated by the connection-pool wait, network and row materialisation — not the disk read.
12. App server B serialises the book to JSON (~30 µs) and issues `SET book:v3:9780141036144 <json> EX 300` to Redis — a **300-second TTL with jitter**, so that thousands of keys populated during the same spike do not all expire in the same second (Lesson 26's thundering herd).
13. App server B renders HTML and responds with `Cache-Control: private, max-age=0, must-revalidate` on the document and absolute, hashed URLs for assets.
14. The **CDN** forwards the HTML without storing it (it is `private`) but *does* cache the referenced cover image, `/img/cover-9780141036144.webp`, with `public, max-age=86400`.
15. Browser parses the HTML, requests the JS bundle `/static/app.7f3a91c2.js`. CDN hit, ~16 ms.
16. Browser stores the JS bundle in **disk cache** with `max-age=31536000, immutable`.
17. Page renders. Total ~310 ms, of which 6 ms was the database.

Now the **same user reloads 10 seconds later**:

18. HTML document: `must-revalidate`, so a conditional request goes out; origin returns `304`, no body. ~55 ms.
19. App server **A** (not B — the LB chose differently) handles the revalidation; its own in-process flag cache hits.
20. `GET book:v3:9780141036144` → **Redis hit**, 0.4 ms. PostgreSQL is not touched at all.
21. JS bundle: served from **browser disk cache**, `immutable`, so **no request is made** — 0 ms, no log line on your servers.
22. Cover image: served from browser **memory cache** this time — it was used on the previous render.
23. Page renders in ~70 ms instead of 310 ms, and the only server work was one `304` and one Redis `GET`.

Then the user clicks a book, then presses **Back**:

24. **bfcache** restores the frozen page in ~10 ms. Zero requests, zero Redis operations, zero database queries. The fastest possible navigation is the one that reaches no cache at all because nothing was discarded.

## 6. ASCII diagram

```
THE FULL HIERARCHY — latency and sharing, same data at every level

  USER                                                          ORIGIN
   │                                                               │
 ┌─┴──────────┐  ┌──────────┐  ┌───────────┐  ┌──────────┐  ┌─────┴─────┐
 │ BROWSER    │  │   CDN    │  │ APP TIER  │  │  REDIS   │  │POSTGRESQL │
 │ mem  ~0 ms │  │ ~15 ms   │  │in-proc    │  │ ~0.4 ms  │  │buffer pool│
 │ disk ~5 ms │  │ edge hit │  │ ~0.0004ms │  │ shared   │  │ ~1 µs     │
 │ bfcache    │  │          │  │ N COPIES  │  │ 1 COPY   │  │ disk ~6ms │
 └────────────┘  └──────────┘  └───────────┘  └──────────┘  └───────────┘
   1 user         1 region       1 process     all servers    truth
   UNREACHABLE    purge: slow    local only    instant        —
                                                 invalidation

  FASTER / LESS CONTROLLABLE  ◄───────────────►  SLOWER / MORE CONTROLLABLE


THE N-COPY DIVERGENCE PROBLEM  (in-process cache, price change at t=0)

  t=0   UPDATE books SET price=14.99  ──▶ PostgreSQL  ✔ correct
        app-A local: 12.99 (TTL ends t=22s)   ← STALE
        app-B local: 12.99 (TTL ends t=47s)   ← STALE
        app-C local: 12.99 (TTL ends t=58s)   ← STALE

  t=25  LB sends user to A → 14.99   ✔
  t=26  LB sends user to B → 12.99   ✘   SAME USER, SAME SECOND
  t=27  LB sends user to C → 12.99   ✘
  t=59  all converged.
        Divergence window = one full TTL. No bug. Structural.


THE SAME WRITE WITH A DISTRIBUTED CACHE

  t=0   UPDATE books SET price=14.99 ──▶ PostgreSQL
        DEL book:9780141036144       ──▶ Redis   (ONE copy, ONE delete)
  t=0+  app-A, app-B, app-C all miss → all read 14.99 → all agree.
        Divergence window ≈ in-flight requests only (~ milliseconds).


WHY "JUST USE REDIS FOR EVERYTHING" IS WRONG
  One page render = 28 lookups

  all in Redis:      28 × 0.5 ms = 14.0 ms   and 84,000 ops/s at 3k RPS
  flags in-process,  12 × 0.0004 +
  entities in Redis: 16 × 0.5 ms =  8.0 ms   and 48,000 ops/s
  + request-scoped
  dedup (16→6):       6 × 0.5 ms =  3.0 ms   and 18,000 ops/s
                                   ───────
                     4.7× faster, 4.7× less Redis load, same correctness.
```
## 7. Detailed example

### BookNook — placing one piece of data, badly and then well

BookNook's book detail page is the most requested page on the site: 3,000 RPS at peak. The page needs the book record (title, author, price, stock, description — ~4 KB of JSON), 12 feature flags, the shipping-zone table, the cover image, and the JS bundle.

**Stage 1 — everything from PostgreSQL.** Every render runs 28 queries. At 3,000 RPS that is 84,000 queries/second against a database comfortable at about 12,000. PostgreSQL saturates at roughly 430 RPS of page traffic; above that, connection-pool waits dominate and p99 goes past 4 seconds. The site falls over at 15 % of target load.

**Stage 2 — "put it all in Redis".** Every one of the 28 lookups becomes a Redis `GET`. Database load drops ~95 %, which is a real win. But:
- 84,000 Redis ops/second against a single-threaded Redis primary comfortable at ~120,000 — so the cache is now at 70 % of its own ceiling, with no headroom for a traffic spike.
- 28 × 0.5 ms = **14 ms of serial network time per render**, which is now the largest single component of server-side latency.
- The feature flags — 12 of the 28 lookups, totalling 900 bytes, changed about twice a week — are responsible for 43 % of that cost.

**Stage 3 — match each item to its access pattern.**

| Item | Read rate | Write rate | Size | Staleness tolerance | Layer chosen | Why |
|---|---|---|---|---|---|---|
| 12 feature flags | 36,000/s | ~2/week | 900 B | 30 s | **In-process**, TTL 30 s + version poll | Tiny, read constantly, harmless if 30 s late |
| Shipping-zone table | 3,000/s | ~1/month | 40 KB | 5 min | **In-process**, TTL 300 s | Same reasoning; rebuilt on deploy |
| Book JSON | 3,000/s | ~40/day | 4 KB | 0 s after a price change | **Redis**, TTL 300 s, deleted on write | Price must be identical on every server — this alone disqualifies in-process |
| Stock level | 3,000/s | ~900/day | 8 B | 0 s | **Redis**, deleted on write, and re-checked at checkout | Overselling is a business error, not a UX annoyance |
| Cover image | 3,000/s | on upload | 60 KB | 1 day | **CDN** + browser, `public, max-age=86400` | Static bytes; physics favours the edge |
| JS bundle | 1 per new visitor | per deploy | 180 KB | never (hashed URL) | **Browser**, `immutable, max-age=31536000` | Content-hashed filename makes invalidation free |
| "Customers also bought" | 3,000/s | nightly | 2 KB | 24 h | **Materialised view** + Redis | Expensive aggregation, small result, staleness irrelevant |
| Cart total at checkout | 40/s | every change | — | **none** | **Not cached** | Changes on every read; `h ≈ 0`, so caching only adds latency and risk |

Result: Redis drops from 84,000 to about 18,000 ops/s (after request-scoped deduplication of repeated lookups within one render), per-render cache access drops from 14 ms to 3 ms, database load stays ~95 % reduced, and crucially the **price can no longer differ between servers**, because the only cached copy is the shared one.

**Stage 4 — the deploy that proved the point.** Six weeks later BookNook moves the book JSON into the in-process cache "for the last 3 ms". A marketing price change goes live. For the next 300 seconds customers see the price flicker between the old and new value depending on which server serves them, and three customers complete checkout at the old price, which the order service then rejects. The change is reverted the same day. The stock level and the price are the two items in the table whose staleness tolerance is 0 across servers — and that column, not the latency column, is what decides the layer.

### Second example for contrast — a real-time trading dashboard

Same menu of layers, almost opposite choices, because the access pattern inverts.

- **Browser cache: disabled entirely** (`no-store`) for all market data. A stale price here is a financial loss, and a cached response shown after a reload is actively dangerous.
- **CDN: static shell only.** The HTML/JS shell is heavily cached at the edge; every quote arrives over a WebSocket that no cache can touch.
- **In-process cache: the primary data store, deliberately.** Each server holds the full order book in its heap and is updated by a subscription feed, not by TTL expiry. Here the N-copy problem is solved by *push*, not by expiry — each copy is continuously corrected in under a millisecond, so divergence is measured in microseconds rather than TTLs. This is the one configuration in which an in-process cache is legitimate for fast-changing, consistency-sensitive data: **when invalidation is pushed rather than waited for.**
- **Redis: not on the read path at all.** A 0.4 ms hop is unacceptable when the budget for the whole update is under 1 ms. Redis is used only for session state and rate limits.
- **Database: not on the read path either.** It records what happened; it does not serve the live view.

The contrast is instructive. BookNook rejected the in-process cache for prices because invalidation was *pull-based* (TTL). The trading system embraced it for prices because invalidation is *push-based* (a streaming feed). Same layer, same data sensitivity, opposite verdicts — decided entirely by how invalidation reaches the copies.

## 8. Code or configuration example

> **Illustrative only — not production-ready.** Error handling, authentication, TLS, connection-pool tuning, metrics, circuit breakers (Lesson 17) and cluster topology are omitted or simplified for clarity. Do not paste this into a service.

### (a) HTTP caching headers at the edge — nginx

```nginx
# Content-hashed assets: the filename changes whenever the bytes change,
# so the URL is permanently safe to cache. 31536000 s = 1 year, the max
# value any cache is required to honour.
location ~* ^/static/.+\.[0-9a-f]{8}\.(js|css|woff2)$ {
    # public    : shared caches (CDN, corporate proxies) may store it
    # immutable : do NOT revalidate, even when the user presses reload.
    #             Only ever safe with a content hash in the filename.
    add_header Cache-Control "public, max-age=31536000, immutable";
}

# Images: shared, but we may want to replace one without renaming it.
location /img/ {
    # max-age   : browsers reuse for 1 day without asking
    # s-maxage  : the CDN keeps it for 30 days (overrides max-age for
    #             shared caches only) so a purge is our control point
    # stale-while-revalidate: serve the stale copy for up to 60 s while
    #             fetching a fresh one in the background -- the user never
    #             waits for the refresh.
    add_header Cache-Control "public, max-age=86400, s-maxage=2592000, stale-while-revalidate=60";
}

# Logged-in HTML. The single most important directive on this whole page.
location / {
    # private        : the CDN MUST NOT store this. Without it, one user's
    #                  account page can be served to a different user --
    #                  a severe and entirely preventable data-leak class.
    # max-age=0 +
    # must-revalidate: the browser may store it but must check with the
    #                  origin before reuse, giving us cheap 304 responses.
    add_header Cache-Control "private, max-age=0, must-revalidate";

    # Tell every cache which request headers change the response. Omitting
    # Vary is how a gzipped body reaches a client that cannot decompress it.
    add_header Vary "Accept-Encoding, Accept-Language";
    proxy_pass http://booknook_app;
}

# Anything with card or token data must never touch any disk anywhere.
location /checkout/payment {
    # no-store is the ONLY directive that means "do not persist this".
    # no-cache does NOT mean that -- it means "store, but revalidate".
    add_header Cache-Control "no-store";
    proxy_pass http://booknook_app;
}
```

### (b) In-process cache with TTL, jitter, and a version poll — Python

```python
import random, threading, time

class LocalCache:
    """Per-process cache for SMALL, HOT, rarely-changed data only.
    Remember: with N app servers there are N of these and nothing
    keeps them in agreement except time."""

    def __init__(self, ttl_seconds, jitter=0.2):
        self._data = {}                     # key -> (value, expires_at)
        self._ttl = ttl_seconds
        self._jitter = jitter               # ±20% so N servers do not all
                                            # expire the same key in the
                                            # same second (thundering herd)
        self._lock = threading.Lock()
        self._version = 0                   # last known global version

    def _expiry(self):
        # Each entry gets its own randomised lifetime. This does NOT shrink
        # the divergence window -- it only desynchronises refreshes.
        return time.monotonic() + self._ttl * (1 + random.uniform(-self._jitter, self._jitter))

    def get(self, key, loader):
        with self._lock:
            entry = self._data.get(key)
            # A hit is a dict lookup: ~100 ns, and the value is ALREADY a
            # Python object -- no deserialisation, unlike a Redis hit.
            if entry and entry[1] > time.monotonic():
                return entry[0]
        value = loader()                    # miss: call the real source
        with self._lock:
            self._data[key] = (value, self._expiry())
        return value

    def poll_version(self, redis_client):
        """Run this on a 1-second timer in a background thread.

        We poll a single integer rather than subscribing to pub/sub because
        polling FAILS SAFE: a dropped poll is retried one second later,
        whereas a missed pub/sub message leaves this process permanently
        stale with no signal that anything is wrong."""
        current = int(redis_client.get("config:version") or 0)
        if current != self._version:        # someone changed the config
            with self._lock:
                self._data.clear()          # drop everything; cheap, it is tiny
            self._version = current
            # Worst-case staleness is now bounded by the poll interval (1 s),
            # not by the TTL (30 s).

FLAGS = LocalCache(ttl_seconds=30)          # flags: 900 bytes, changes twice a week
ZONES = LocalCache(ttl_seconds=300)         # shipping zones: 40 KB, changes monthly
# NOTE: book prices are deliberately NOT here. Their cross-server staleness
# tolerance is zero, which disqualifies this layer no matter how fast it is.
```

### (c) Distributed cache usage — Redis

```python
import json, random, redis

r = redis.Redis(host="booknook-cache.internal", port=6379,
                socket_timeout=0.05,        # 50 ms: a cache must FAIL FAST.
                                            # Waiting 2 s for a cache is worse
                                            # than skipping it (Lesson 17).
                socket_connect_timeout=0.05)

BOOK_TTL = 300

def cache_key(isbn):
    # Version prefix 'v3' lets us invalidate EVERY book at once by bumping
    # to v4 -- no scanning, no DEL storm. The old keys expire on their own.
    return f"book:v3:{isbn}"

def get_book(isbn):
    key = cache_key(isbn)
    try:
        raw = r.get(key)                    # ~0.4 ms same-AZ; a MISS costs
                                            # this too -- misses are not free
        if raw:
            return json.loads(raw)          # ~30 µs to deserialise 4 KB
    except redis.RedisError:
        pass                                # cache down != site down.
                                            # Fall through to the database.

    book = db.query_one("SELECT * FROM books WHERE isbn = $1", isbn)

    try:
        # Jittered TTL: without this, every key populated during a cold-start
        # spike expires in the same second and the stampede repeats forever.
        ttl = int(BOOK_TTL * random.uniform(0.9, 1.1))
        r.set(key, json.dumps(book), ex=ttl)
    except redis.RedisError:
        pass                                # failing to populate is survivable
    return book
```

```ini
# redis.conf -- the three settings that matter most for a cache role
maxmemory 8gb
# allkeys-lru: when full, evict the least recently used key REGARDLESS of
# whether it has a TTL. This is the correct policy for a pure cache.
# The default, noeviction, makes writes FAIL once memory is full -- which
# turns a full cache into an outage. Changing this one line is the most
# common Redis-as-a-cache fix there is.
maxmemory-policy allkeys-lru
# Persistence off for a pure cache: snapshots cost fork() pauses and the
# data is reconstructible. Turn it ON if a cold restart would stampede
# your database (see Section 9, Part D).
save ""
```

### (d) Database-level caching

```sql
-- PostgreSQL: how much of your read traffic the buffer pool is absorbing.
-- Below ~0.99 on an OLTP workload, add RAM before building an app cache:
-- it is cheaper, needs no code, and creates no coherence problem.
SELECT relname,
       heap_blks_hit,                                   -- pages served from RAM
       heap_blks_read,                                  -- pages read from disk
       round(heap_blks_hit::numeric
             / nullif(heap_blks_hit + heap_blks_read, 0), 4) AS hit_ratio
FROM pg_statio_user_tables
ORDER BY heap_blks_read DESC
LIMIT 10;

-- A materialised view: expensive aggregation, small result, staleness of
-- hours is fine. This is a CACHE that lives inside the database.
CREATE MATERIALIZED VIEW mv_bestsellers AS
SELECT b.isbn, b.title, count(*) AS sales_30d
FROM order_items oi
JOIN books b ON b.isbn = oi.isbn
WHERE oi.created_at > now() - interval '30 days'
GROUP BY b.isbn, b.title
ORDER BY sales_30d DESC
LIMIT 100;

-- CONCURRENTLY avoids taking an exclusive lock, so readers are never
-- blocked -- but it requires a UNIQUE index and is noticeably slower.
CREATE UNIQUE INDEX ON mv_bestsellers (isbn);
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_bestsellers;   -- run nightly
```

```ini
# MySQL 8.0: there is no query_cache_size any more. It was removed because
# invalidation was table-granular and a single global mutex made it a
# scalability bottleneck above ~8 cores. Size the BUFFER POOL instead --
# this is the setting that actually determines read performance.
innodb_buffer_pool_size = 24G     # ~70-75% of a 32 GB machine's RAM
innodb_buffer_pool_instances = 8  # reduces mutex contention on the pool
```

## 9. Numerical example

### Part A — The same data at each layer, and the resulting page latency

**Assumptions, stated explicitly:**
- One book detail page render requires the book JSON **once** (request-scoped deduplication is already in place).
- Client-to-origin network RTT: **40 ms**. Client-to-CDN-edge RTT: **15 ms**.
- Fixed server-side work unrelated to this datum (routing, templating, serialisation): **12 ms**.
- Per-layer hit costs, all typical/approximate: in-process **0.0004 ms**; Redis same-AZ **0.4 ms**; PostgreSQL buffer-pool hit as seen by the app **1.5 ms**; PostgreSQL disk read **6.0 ms**; browser disk cache **5 ms** (and then *no* network at all); CDN edge hit **15 ms** total.
- Deserialisation of the 4 KB JSON after a Redis hit: **0.03 ms**.

```
Page latency = client RTT + server work + datum access cost
               (except where the layer removes the network entirely)

Layer                         datum cost      page latency
──────────────────────────────────────────────────────────────────
PostgreSQL, disk read          6.0000 ms      40 + 12 + 6.00   = 58.00 ms
PostgreSQL, buffer-pool hit    1.5000 ms      40 + 12 + 1.50   = 53.50 ms
Redis (shared, one hop)        0.4300 ms      40 + 12 + 0.43   = 52.43 ms
In-process cache               0.0004 ms      40 + 12 + 0.0004 = 52.00 ms
CDN edge hit (whole page)           —         15 + 0 + 0       = 15.00 ms
Browser disk cache (whole page)     —          0 + 0 + 5       =  5.00 ms
Browser memory cache / bfcache      —          0 + 0 + ~0      = ~0.3 ms
```

**The conclusion most engineers get wrong.** Moving the datum from PostgreSQL-on-disk to Redis saves **5.57 ms** — a 10 % page improvement. Moving it from Redis to an in-process cache saves a further **0.43 ms**, which is **0.8 %** of the page. Moving the *whole page* from origin to CDN saves **37 ms**, or 71 %.

```
Redis → in-process   : 52.43 → 52.00  =  0.8% page improvement
origin → CDN edge    : 52.43 → 15.00  = 71.4% page improvement
ratio of benefit     : 37.43 / 0.43   = 87×
```

So the in-process cache buys 87× less page-level benefit than the CDN, while being the only option that introduces the N-copy divergence problem. **When one datum is fetched once per page, the layer below Redis is not worth a correctness risk.** The in-process cache earns its place only in the multiplied case:

```
12 feature flags, fetched per request, NOT deduplicable:
  via Redis      : 12 × 0.43 ms  = 5.16 ms per render
  in-process     : 12 × 0.0004   = 0.0048 ms per render
  saving         : 5.155 ms      = 9.9% of the page -- 12× the benefit
                                   of caching the book locally.
```

The deciding factor is **lookups per render**, not latency per lookup. One lookup → Redis. Twelve or more → in-process becomes defensible, *if* staleness tolerance allows it.

### Part B — Effective latency with the Lesson 26 formula

**Assumptions:** Redis hit ratio `h = 0.94`; Redis hit 0.43 ms; miss costs the Redis round trip (0.40 ms, paid even on a miss) **plus** the PostgreSQL read (6.0 ms) **plus** the repopulating `SET` (0.40 ms).

```
L_hit  = 0.43 ms
L_miss = 0.40 + 6.00 + 0.40 = 6.80 ms

L_eff  = h × L_hit + (1 − h) × L_miss
       = 0.94 × 0.43 + 0.06 × 6.80
       = 0.4042 + 0.4080
       = 0.812 ms
```

Note what that split says: **at a 94 % hit ratio, the 6 % of misses contribute slightly more total latency than all 94 % of hits combined.** Now the sensitivity to hit ratio:

```
h = 0.99 : 0.99 × 0.43 + 0.01 × 6.80 = 0.4257 + 0.0680 = 0.494 ms
h = 0.94 : 0.94 × 0.43 + 0.06 × 6.80 = 0.4042 + 0.4080 = 0.812 ms
h = 0.80 : 0.80 × 0.43 + 0.20 × 6.80 = 0.3440 + 1.3600 = 1.704 ms
h = 0.50 : 0.50 × 0.43 + 0.50 × 6.80 = 0.2150 + 3.4000 = 3.615 ms
h = 0.00 : 0.00 × 0.43 + 1.00 × 6.80 =                    6.800 ms

0.94 → 0.99 improves effective latency by 39%.
0.94 → 0.80 degrades it by 110%.
```

A five-point hit-ratio gain is worth more than any layer change below Redis. **Raising the hit ratio beats moving the cache closer**, which is why Lesson 26 came first.

### Part C — Database load, the real reason to cache

**Assumptions:** 3,000 page renders/s; 1 book query per render before caching; PostgreSQL comfortable to ~12,000 simple queries/s.

```
Uncached            : 3,000 q/s                          = 25% of DB capacity
h = 0.94            : 3,000 × 0.06 = 180 q/s             = 1.5% of capacity
h = 0.99            : 3,000 × 0.01 =  30 q/s             = 0.25% of capacity

Load reduction at h = 0.94 : 3,000 / 180 = 16.7×
Load reduction at h = 0.99 : 3,000 /  30 = 100×
```

The latency gain from 0.94 → 0.99 was 39 %. The **database load** gain over the same interval is 6×. This asymmetry is the main argument for caching at scale: you cache to protect the database far more than to speed up the user.

### Part D — The cold-cache deploy stampede

**Assumptions:** 20 app servers, each with an in-process cache at 95 % hit ratio for the book JSON; rolling deploy replaces all 20 in 4 minutes (one every 12 s); 3,000 RPS spread evenly, so each server handles 150 RPS; a restarted server's cache is empty, so its hit ratio starts at 0 and recovers over ~60 s as the working set repopulates.

```
Steady state DB load  = 3,000 × (1 − 0.95)        = 150 q/s

A freshly restarted server, first second:
  its own DB load     = 150 × (1 − 0.00)          = 150 q/s   (vs 7.5 normally)
  extra load          = 150 − 7.5                 = 142.5 q/s

Servers simultaneously cold (60 s recovery, one restart every 12 s):
  60 / 12 = 5 servers are cold at any moment during the roll.

Peak DB load during deploy
  = 15 warm servers × 7.5 q/s  +  5 cold servers × ~75 q/s (average over
    their recovery curve)
  = 112.5 + 375
  = 487.5 q/s   →  3.25× the steady-state load, for 4 minutes.
```

With a **shared Redis** cache instead, the deploy changes nothing: Redis is a separate process and is not restarted, so DB load stays at 150 q/s throughout. That survival-across-deploys property is a concrete, quantifiable advantage of layer 8 over layer 6 — and it is also why `save ""` in Section 8(c) deserves a second thought: if BookNook's Redis restart would expose the database to the full 3,000 q/s, persistence is worth the fork cost.

```
If Redis restarts cold with no persistence:
  DB load spike = 3,000 q/s = 25% of a 12,000 q/s database, instantly.
  Recovery ≈ time to repopulate the hot set ≈ 30-120 s.
  Survivable here. At 30,000 RPS it would not be -- the database would
  exceed capacity 2.5× and the site would be down until the cache warmed.
```

### Part E — Buffer pool sizing beats application caching

**Assumptions:** 40 GB hot working set; 32 GB machine; `shared_buffers` at 8 GB plus OS page cache.

```
Working set 40 GB, RAM available for caching ~24 GB
  → ~60% of pages resident → ~40% of reads hit disk
  Average read = 0.60 × 0.001 ms + 0.40 × 0.12 ms = 0.0486 ms per page
  A 50-page query: 50 × 0.0486 = 2.43 ms

Upgrade to a 64 GB machine (entire working set resident):
  Average read = 1.00 × 0.001 ms = 0.001 ms per page
  A 50-page query: 50 × 0.001 = 0.05 ms

Improvement: 2.43 / 0.05 = 48.6×, for the price of RAM,
with ZERO code, ZERO new failure modes and ZERO coherence problems.
```

A 48× database improvement from a hardware line item will usually beat an application cache you have to build, operate and invalidate. Check the buffer pool first; it is step 6 of the decision list in Section 4.7 for a reason.

## 10. Trade-offs

**Browser cache**
- *Advantages:* the only layer with zero server cost — a hit generates no request, no bandwidth, no log line. Fastest possible response. Works offline.
- *Disadvantages:* **completely uncontrollable once served.** If you ship `max-age=31536000` on a non-hashed URL, some users keep the wrong file for a year and you have no remedy except changing the URL.
- *Operational cost:* near zero to run, high to get right the first time.
- *Unnecessary when:* responses are user-specific and change on every request; then `no-store` is the correct configuration and there is nothing to tune.

**In-process cache**
- *Advantages:* 1,000× faster than any network cache; no serialisation; no extra service; no new failure domain.
- *Disadvantages:* N divergent copies; lost on every restart (causing the Part D stampede); consumes application heap and can trigger GC pauses; capacity is bounded by the smallest instance.
- *Operational cost:* low to add, high to reason about during an incident — "which server did that user hit?" is a question you will ask at 3 a.m.
- *Unnecessary complexity when:* the item is fetched once per render; Part A showed that saves 0.8 % of page latency in exchange for a correctness hazard.

**Distributed cache (Redis/Memcached)**
- *Advantages:* one shared copy, so invalidation is global and immediate; survives deploys; scales independently; capacity far beyond one heap; Redis adds locks, counters and pub/sub you will want anyway.
- *Disadvantages:* a network hop on every lookup, which multiplies by lookups-per-render; a new service to operate, monitor, secure and fail over; **a new failure domain that can take the database down with it** if the fallback path is not capacity-planned.
- *Operational cost:* moderate-to-high. Eviction policy, memory limits, failover, hot keys, and client timeouts all need deliberate configuration.
- *Unnecessary when:* the dataset is small enough to fit in-process **and** staleness tolerance is non-zero.

**Database buffer pool**
- *Advantages:* free, already running, perfectly coherent, needs no invalidation logic, benefits every query including ones you have not written yet.
- *Disadvantages:* caches pages, not results, so an expensive aggregation is still expensive; capped by machine RAM; scales only vertically (Lesson 18).
- *Operational cost:* one configuration value and a monitoring dashboard.
- *Unnecessary complexity when:* it is never unnecessary. This one is always on; the only question is whether it is sized correctly.

**Materialised view**
- *Advantages:* turns a 30-second aggregation into a 2 ms lookup; lives inside the database so it is transactionally consistent at refresh time; queryable with normal SQL and indexes.
- *Disadvantages:* refresh recomputes everything; staleness equals the refresh interval; refresh consumes DB capacity at exactly the time the DB is busy unless scheduled carefully.
- *Unnecessary when:* the query is a per-entity lookup rather than an aggregation.

**CDN**
- *Advantages:* the only layer that defeats geography; absorbs enormous traffic; provides DDoS protection as a side effect.
- *Disadvantages:* purges take seconds to minutes; a `Vary` or `private` mistake becomes a cross-user data leak; debugging is harder because the response may never reach your logs.
- *Operational cost:* moderate, and mostly concentrated in getting cache keys and headers right.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| In-process caches diverge after a write | The same value flickers between two numbers on successive reloads; occasionally an action is rejected because the server that validated it disagreed | User reports of "the price changed"; a canary job reading the same key from every server and comparing; divergence is **invisible** to per-server metrics | Wait one TTL, or deploy the version-counter poll; permanently, move the item to Redis |
| Redis primary fails | All reads fall through to PostgreSQL; latency rises from 52 ms to 58 ms *if* the DB has headroom, or the site collapses if it does not | Redis connection errors; a vertical cliff in cache hit rate; DB QPS jumps by `1/(1−h)` | Sentinel/Cluster failover (~10–30 s); client timeouts of 50 ms keep requests moving meanwhile |
| Redis restarts cold with `save ""` | A 30–120 s period of elevated latency and, above a traffic threshold, timeouts | DB QPS spike with no corresponding traffic spike; cache hit ratio at 0 climbing slowly | Enable RDB persistence, or pre-warm the hot set before admitting traffic |
| Rolling deploy empties every in-process cache | Elevated p99 for the duration of the roll, every single deploy | DB QPS correlating exactly with deploy windows (Part D: 3.25×) | Shared cache layer; or stagger restarts further apart than the warm-up time |
| `Cache-Control: private` omitted on a logged-in page | **One user sees another user's account page** | CDN cache-hit metrics on a URL that should never be cacheable; user reports; this is a security incident | Emergency CDN purge; add `private`; add a CI test asserting the header on every authenticated route |
| `Vary: Accept-Encoding` omitted | Garbled page for a minority of clients that cannot decompress | Low-volume, browser-specific error reports that are hard to reproduce | Add `Vary`; purge the CDN |
| `max-age=31536000` on a non-hashed URL | Some users keep a broken JS bundle for up to a year, unreachable by any purge | A long tail of errors from an old asset version in RUM data | **You cannot fix the served copies.** Change the URL (add a hash) and ship a new one |
| Buffer pool undersized | Steadily worsening latency as data grows, with no deploy to blame | `heap_blks_read` climbing; buffer-pool hit ratio dropping below 0.99 | Add RAM; archive cold rows; partition (Phase 3) |
| Hot key on a single Redis node | One node at 100 % CPU while the rest of the cluster idles; timeouts on that key's traffic | Per-node CPU skew; `redis-cli --hotkeys` | Replicate the key under N suffixed names and read a random one; or promote it to the in-process cache |
| `maxmemory-policy noeviction` with a full Redis | **Writes to the cache fail**, so nothing is ever cached again; the database silently absorbs 100 % of reads | `OOM command not allowed` errors; hit ratio decaying toward zero | Set `allkeys-lru`; raise `maxmemory`; this single line is the most common Redis-as-cache misconfiguration |
| Pub/sub invalidation message missed during a reconnect | One server serves a stale value **indefinitely** — long past any TTL, because the entry was refreshed from its own stale state | Almost undetectable without a cross-server canary comparison | Replace fire-and-forget pub/sub with a polled version counter, which fails safe |
| bfcache disabled by a legacy `unload` handler | Pressing Back takes 300 ms and loses scroll position, instead of 10 ms | Lighthouse/RUM bfcache-eligibility reports | Remove the `unload` handler; use `pagehide` instead |

## 12. Alternatives

| Approach | Where the copy lives | Pros | Cons | Choose it when |
|---|---|---|---|---|
| **Browser cache** | User's device | Zero server cost; fastest possible; works offline | Unreachable; per-user only; wrong headers are permanent | Static assets and per-user responses re-read within a session |
| **CDN edge cache** | ~hundreds of POPs | Defeats geography; absorbs huge traffic; DDoS protection | Purge latency; `private`/`Vary` mistakes leak data | Public, static or semi-static content read globally |
| **Reverse-proxy cache** (nginx `proxy_cache`, Varnish) | Your own edge | No third party; full control; can cache HTML fragments | Only caches what passes through it; one more tier to scale | You need edge-style caching inside your own perimeter (Lesson 25) |
| **In-process cache** (Caffeine, `lru_cache`) | App heap | ~1,000× faster than any network cache; no extra service | N divergent copies; lost on restart; heap pressure | Small, hot, rarely-changed data with non-zero staleness tolerance |
| **Distributed cache** (Redis, Memcached) | Separate service | One shared copy; global invalidation; survives deploys | Network hop; new failure domain; operational cost | The default for shared application data |
| **Near cache** (in-process + distributed, two tiers) | Both | Local speed with a shared fallback and a push invalidation channel | The most complex option; two coherence problems instead of one | Extreme read rates where both the hop and the divergence matter |
| **Database buffer pool** | Inside the DB | Free; perfectly coherent; helps every query | Caches pages not results; vertical scaling only | Always — size it before building anything else |
| **Materialised view** | Inside the DB | Expensive aggregation becomes a cheap lookup; full SQL | Whole-view refresh; staleness equals refresh interval | Small results from expensive aggregations |
| **Read replicas** | A second database | Scales reads with **no staleness-tolerance decision at the app layer**, just replication lag | Replication lag is still staleness, just measured differently; costs a full DB | Read scaling where you want SQL flexibility — **Phase 3, Topic 2 — Read replicas** |
| **Precomputation / denormalisation** | The primary table itself | No cache, no invalidation, no coherence problem at all | Write amplification; schema coupling | A value is read far more than written and can be maintained transactionally |
| **No cache** | — | Always correct; nothing to invalidate | Full cost on every read | `h ≈ 0` workloads, or anything where stale is unacceptable and writes are frequent |

The last row is a real option and is under-chosen. Lesson 26's formula makes it quantitative: if `h` is near zero, `L_eff ≈ L_miss + lookup cost`, so the cache is pure overhead plus a new way to be wrong.

## 13. When to use it

1. **Use the browser cache for every static asset you ship.** Content-hash the filename, set `public, max-age=31536000, immutable`, and the asset is fetched exactly once per user per deploy. This is the cheapest performance win in web engineering and it costs one build-tool setting.
2. **Use an in-process cache for small, hot, rarely-changed data** — feature flags, config tables, shipping zones, currency rates, JWT public keys, category trees. The qualifying test is all four of: under ~10 MB, read multiple times per request, changed less than once per minute, and tolerant of up to one TTL of staleness.
3. **Use a distributed cache as the default for shared application data** — entity records, rendered fragments, session data, computed results. Anything where two app servers disagreeing would be a bug belongs here and not in-process.
4. **Use the database buffer pool first, always.** Before building any application cache, confirm the buffer-pool hit ratio is above ~0.99. If it is not, adding RAM is cheaper and safer than adding a cache layer (Part E: 48× for a hardware line item).
5. **Use a materialised view for expensive aggregations with small results** — bestseller lists, dashboards, daily rollups — where minutes or hours of staleness is acceptable.
6. **Use a CDN for anything public, static and globally read.** Part A showed 71 % page improvement from this layer alone; nothing below it comes close.
7. **Use multiple layers together, deliberately.** BookNook's final design uses five. Each one is justified by a different access pattern, not by enthusiasm.

## 14. When *not* to use it

1. **Do not cache data that changes on every read.** A cart total during checkout, an account balance, an authorisation decision. `h ≈ 0` means `L_eff ≈ L_miss` plus lookup overhead — you added latency and a correctness risk and gained nothing.
2. **Do not use an in-process cache for anything where two servers disagreeing is a user-visible or financial error** — prices, stock levels, permissions, quotas. The divergence window is up to one full TTL and the user will observe it, because the load balancer moves them between servers (Section 6 diagram).
3. **Do not cache per-user responses in a shared cache** (CDN or any proxy) without `private`. This is the mechanism by which one user's data reaches another, and it is a security incident, not a performance bug.
4. **Do not add a distributed cache to fix a missing index.** If a query is slow because it scans 2 million rows, caching hides it until the first cache miss during peak traffic brings the database down. Fix the query; then cache if the read rate still justifies it.
5. **Do not cache to mask a database you have never sized.** Section 9 Part E: check `shared_buffers`/`innodb_buffer_pool_size` before you write cache code.
6. **Do not add an in-process tier on top of Redis for a datum fetched once per render.** Part A quantified the benefit at 0.8 % of page latency against a structural correctness hazard. Only the multiplied case (12+ lookups per render) pays.

## 15. Common mistakes

**Beginner mistakes**
- Thinking `Cache-Control: no-cache` means "do not cache". It means "store it, but revalidate before reuse." `no-store` is the one that means do not store.
- Putting everything in Redis because "Redis is the cache", then being surprised that 28 round trips per render cost 14 ms.
- Putting prices or stock in an in-process cache because it benchmarked faster, without asking what happens when two servers disagree.
- Setting a long `max-age` on a non-content-hashed URL and discovering there is no way to recall it.
- Forgetting that a cache **miss still costs the round trip**. A 40 % hit ratio Redis layer adds latency to 60 % of requests.
- Measuring cache effectiveness by hit count rather than hit *ratio*, and never computing `L_eff`.
- Leaving `maxmemory-policy` at `noeviction`, so a full Redis stops accepting writes and the cache silently dies.
- Expecting server logs to reveal browser cache behaviour. A browser-cache hit produces no request at all; only RUM can see it.

**Experienced-engineer mistakes**
- Building a near-cache (in-process over distributed) with pub/sub invalidation, and not handling the reconnect gap — a server that misses one message serves a stale value indefinitely, long past the TTL, because it refreshes from its own stale state.
- Disabling Redis persistence on a cache whose cold-start would exceed the database's capacity. Persistence is not about durability here; it is about **not stampeding the database on restart**.
- Sizing Redis memory for the working set but not for fragmentation and replication buffers, then hitting `maxmemory` during a resync.
- Caching at the wrong granularity: caching a whole rendered page keyed by user ID gives a near-zero hit ratio, while caching the five shared fragments inside it gives 95 %.
- Not jittering TTLs, so a cold-start spike populates a million keys in one second and they all expire together, forever, in a synchronised wave.
- Treating the CDN as purgeable in real time. Purges propagate over seconds to minutes across hundreds of POPs; design for `s-maxage` plus versioned URLs instead of relying on purge latency.
- Building an application cache while the buffer-pool hit ratio sits at 0.82, where RAM would have been 48× better for less work.
- Allowing cache reads to inherit the default client timeout of seconds. A cache must fail fast (50 ms) — a slow cache is worse than no cache, because you pay the cache latency *and* the database latency (Lesson 17).
- Ignoring that an in-process cache lives in the GC heap; a 2 GB local cache in a JVM can add 200 ms pause times that dwarf every millisecond it saved.

## 16. Interview perspective

Caching layers are where interviewers separate people who have read about caching from people who have operated it. Almost every candidate says "add Redis". Very few say *where*, and fewer still say *why not somewhere else*.

**How to use it:**

1. **Name the layer and the access pattern together.** *"Feature flags go in-process with a 30-second TTL — they're a kilobyte, read twelve times per request, and changed twice a week. The book price goes in Redis, because two servers showing different prices is a business error."* That sentence demonstrates the whole lesson.
2. **Volunteer the N-copy problem before you are asked.** *"The trade-off with a local cache is that there are N of them and nothing reconciles them, so the divergence window is one TTL — which is fine for flags and unacceptable for stock."*
3. **Quantify the hop.** *"Redis is about 0.4 ms. That's irrelevant for one lookup but it's 14 ms if the page makes 28 of them, so I'd deduplicate per request first."*
4. **Mention the buffer pool.** Most candidates never do. *"Before adding a cache I'd check the buffer-pool hit ratio — if it's below 99 %, RAM is cheaper than a cache layer and has no coherence problem."*
5. **Know why MySQL removed the query cache.** It is a precise, memorable answer: table-granular invalidation plus a global mutex, and the application knows the staleness tolerance that the database cannot.
6. **Bring up the cold-start stampede.** *"A rolling deploy wipes every in-process cache; with 20 servers that's roughly 3× database load for the duration of the roll. A shared cache survives deploys, which is a correctness-adjacent reason to prefer it."*
7. **Get `private` right on logged-in pages.** If a design includes a CDN and authenticated pages, say the word `private` out loud. Interviewers at companies that have had this incident will notice.
8. **Choose Redis vs Memcached on requirements, not taste.** *"Memcached if it's opaque blobs and I need a million ops/s per node; Redis if I need data structures, persistence or failover — and I do, for rate limiting and leaderboards."*

**Red flags interviewers watch for:**
- Saying "we'll cache it" without naming the layer, the key, the TTL, or the invalidation trigger.
- Proposing a local in-process cache for prices, inventory or permissions, and not noticing the divergence problem when prompted.
- Believing a cache miss is free.
- Not knowing that `no-cache` and `no-store` are different.
- Caching authenticated responses in a shared cache with no mention of `private`.
- Never mentioning the database's own caching, implying the DB is treated as a black box.
- Claiming the CDN can be purged instantly and designing around that.
- Treating the cache as infallible: no fallback path, no timeout, no answer to "what happens when Redis is down?"
- Adding a cache before adding an index.

## 17. Knowledge check

Write your answers down before continuing.

**Conceptual**

1. BookNook's book price is read 3,000 times per second and written about 40 times per day. On pure latency grounds an in-process cache is 1,000× faster than Redis. Explain precisely why the price still belongs in Redis, using the concept of the divergence window, and state the exact property of the *data* — not the traffic — that makes the decision. Then describe one realistic modification to the system that would make an in-process price cache acceptable.

2. Explain why MySQL removed the query cache in 8.0, giving both the invalidation reason and the concurrency reason. Then explain why application-level cache-aside does not suffer from the first of those problems, and what the application knows that the database fundamentally cannot.

3. A cache miss on Redis costs the full network round trip even though no data is returned. Using the effective-latency formula from Lesson 26, explain at what hit ratio a Redis layer in front of a PostgreSQL buffer-pool hit (1.5 ms) stops being worth having, assuming a Redis hit is 0.43 ms and a miss costs 0.40 ms of Redis plus the 1.5 ms database read plus a 0.40 ms repopulating write. Show the break-even point and say what that implies about caching queries with unpredictable parameters.

**Practical scenario**

4. BookNook adds a "near cache": each app server keeps a local copy of hot book records, invalidated by a Redis pub/sub `invalidate:<isbn>` message published on every write, with no TTL at all because "pub/sub makes TTLs unnecessary". During a 20-second network partition, app server C loses its Redis connection and reconnects. Describe exactly what state server C is in afterwards, why the problem is **permanent rather than temporary**, why neither per-server latency dashboards nor the Redis hit-ratio metric will reveal it, and what a user would experience. Then give two independent changes — one that bounds the damage and one that removes the failure mode — and explain the trade-off each introduces.

**Calculation**

5. BookNook's search-results page renders 40 items. Each item needs a book record (4 KB) and a stock level (8 bytes), and the page reads 12 feature flags. Assumptions: 800 page renders/second; Redis hit 0.40 ms plus 0.03 ms deserialisation for a book record and 0.005 ms for a stock level; in-process hit 0.0004 ms; PostgreSQL batch read of 40 book records 9 ms; Redis hit ratio 0.92 for books and 0.70 for stock; fixed server work 20 ms; client RTT 45 ms.
   (a) If every lookup goes individually to Redis, how many Redis operations per second does the fleet generate at peak, and how much serial cache latency does one render incur?
   (b) Recompute (a) assuming the 40 book records are fetched with a single `MGET`, the 40 stock levels with a single `MGET`, and the 12 flags come from the in-process cache. State the new ops/second and the new serial cache latency.
   (c) Using `L_eff = h × L_hit + (1 − h) × L_miss` with the batched design, compute the effective latency of the book-record fetch, where a miss costs the `MGET` round trip plus the 9 ms PostgreSQL batch read plus a 0.4 ms repopulating `MSET`.
   (d) What is the total page latency in design (a) and design (b), and what percentage improvement does batching alone deliver?
   Show every step and state any additional assumptions you make.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 28.

---

**Progress checklist**

Phase 1 — Foundations: **complete** (Lessons 1–17)

Phase 2 — Scaling:
`[x] 1. Vertical scaling` · `[x] 2. Horizontal scaling` · `[x] 3. Stateless application servers` · `[x] 4. Load balancers` · `[x] 5. Layer 4 versus Layer 7` · `[x] 6. Load-balancing strategies` · `[x] 7. Health checks and failover` · `[x] 8. Reverse proxies and API gateways` · `[x] 9. Caching fundamentals` · `[x] 10. Browser, application, database, and distributed caches`
`[ ] 11. Cache-aside, read-through, write-through, and write-behind` ← next
`[ ] 12–22` not started

Phases 3–5: not started.

**NEXT LESSON: Phase 2, Topic 11 — Cache-aside, read-through, write-through, and write-behind**

---

# Lesson 28 — Cache-Aside, Read-Through, Write-Through, and Write-Behind

*Phase 2, Topic 11*

## 1. Simple definition

Lesson 27 answered *where* the cache goes. This lesson answers *who talks to it, and in what order*.

A **caching pattern** is the contract that answers two questions:

> *On a read: who is responsible for noticing a miss and loading the data?*
> *On a write: in what order are the cache and the database updated, and who waits for whom?*

There are five patterns in common use. Three are read patterns, two are write patterns, and real systems combine one of each.

| Pattern | Read or write | Who loads the data on a miss | One-line summary |
|---|---|---|---|
| **Cache-aside** (lazy loading) | Read | **The application** | App checks cache, misses, reads DB, puts it back |
| **Read-through** | Read | **The cache library/service** | App asks the cache; the cache fetches on miss |
| **Write-through** | Write | — | App writes cache and DB synchronously before returning |
| **Write-behind** (write-back) | Write | — | App writes the cache, returns immediately, DB is written later |
| **Refresh-ahead** | Read (proactive) | The cache, before expiry | Popular entries are refreshed before anyone misses them |

Terms defined before use:
- **Miss penalty**: the extra cost paid when the cache does not have the item — the wasted cache round trip, plus the database read, plus the repopulating write.
- **Stale entry**: a cached value that no longer matches the database.
- **Staleness window**: how long a stale entry can persist before being corrected.
- **Data-loss window**: for write-behind only, the span of acknowledged writes that exist **only** in the cache and would be lost if it died right now.
- **Write amplification**: how many physical writes one logical write causes.
- **Read-your-writes consistency**: the guarantee that after you write a value, your own next read returns it. Users notice the absence of this immediately — they edit something, the page reloads, and the edit appears to have been discarded.

The one sentence to carry out of this lesson: **cache-aside is the default, and the price of that default is one specific race condition that you must mitigate deliberately.**

## 2. Why it exists

Lesson 27's examples all quietly used one pattern — check the cache, miss, read the database, write it back. That is cache-aside, and it looks so obvious that it is rarely examined. Examining it reveals three separate problems that the other patterns exist to solve.

**Problem 1 — the miss path is written by hand, everywhere.** With cache-aside, every call site contains the same six lines: build key, `GET`, check for nil, query the database, `SET`, return. BookNook has 60 such call sites. Four of them use a 300-second TTL, one uses 3,000 by typo, two forget to `SET` at all (so they never cache anything and nobody notices because the code *works*), and one serialises with a different JSON field order so its entries never match the others. None of this is a design failure — it is the predictable result of duplicating a protocol 60 times. **Read-through** exists to move that protocol into one place.

**Problem 2 — writes and reads are updated by different code paths, so they drift.** Cache-aside says nothing about writes. Each write site must remember to invalidate. BookNook's admin price-update endpoint invalidates `book:v3:{isbn}`. The bulk-import job, written a year later by a different team, does not. Prices imported in bulk stay stale for a full TTL, and nobody connects the two facts. **Write-through** exists to make the cache update an inseparable part of the write.

**Problem 3 — some writes are too frequent to send to the database one at a time.** BookNook records a view counter per book. At 3,000 page renders/second that is 3,000 `UPDATE books SET views = views + 1` statements per second, each one taking a row lock, generating a WAL record, and contending with every other writer on popular rows. The database can serve 12,000 reads/second but it cannot sustain 3,000 hot-row updates/second. **Write-behind** exists to absorb these: increment in the cache at 200,000 ops/second, flush an aggregate to the database every few seconds, and accept a quantified, bounded risk of losing some counts.

And underneath all three sits the race condition that motivates most of Section 4.6: with cache-aside, a concurrent read-miss and write can interleave so that the cache ends up holding a value the database never had *after* the write — and because nothing will correct it until the TTL expires, that entry is stale for its entire lifetime. It is not a rare theoretical concern; Section 9 computes a rate of roughly 400 occurrences per hour on one hot key under realistic traffic.

## 3. Real-world analogy

**A research library with a reading desk.**

- **Cache-aside**: you check your desk for the book. Not there, so you walk to the stacks, fetch it, read it, and leave your copy on the desk for next time. **You** did all the work and **you** decided to keep the copy.
- **Read-through**: you hand a request slip to the librarian. If the book is on the reserve shelf you get it instantly; if not, the librarian walks to the stacks on your behalf and puts it on the reserve shelf afterwards. You never learn whether it was a hit or a miss — and you never write the fetching procedure.
- **Write-through**: you annotate the book. Before you are allowed to leave, the annotation is copied onto both the reserve-shelf copy and the archived master. Slower to finish, but the two copies can never disagree.
- **Write-behind**: you annotate the reserve-shelf copy and leave immediately. A clerk collects annotations from an out-tray every ten minutes and files them. Fast for you. If the building floods before the clerk's round, those annotations never existed.
- **Refresh-ahead**: the librarian knows the journal on the reserve shelf expires on Friday and that six people read it weekly, so a replacement is ordered on Thursday. Nobody ever waits for the stacks.

**Where the analogy breaks down:**

| Research library | Caching patterns |
|---|---|
| The librarian can see that the desk copy is an old edition | A stale cache entry is **indistinguishable from a fresh one at read time**. There is no signal. This is the defining hazard of the entire topic |
| Only one person is at the desk | Thousands of concurrent requests interleave. The cache-aside race in Section 4.6 is invisible in the single-user story and unavoidable in the concurrent one |
| The clerk's out-tray is visible, so you can see unfiled work | Write-behind's pending buffer is in volatile memory. Its size is your data-loss window and it is only visible if you explicitly export it as a metric |
| Fetching from the stacks takes the same time for everyone | A miss under load is far more expensive than a miss when idle — a thundering herd means 5,000 people walk to the stacks for the same book simultaneously (Lesson 26) |
| The archive is always reachable | The database may be down, up but slow, or up and rejecting writes. Each pattern behaves differently in each case, and those differences are the whole of Section 11 |
| A copy left on the desk stays until removed | Entries are evicted under memory pressure at unpredictable times, so "I wrote it to the cache" never means "it will be there later" |

## 4. Detailed technical explanation

### 4.1 Cache-aside (lazy loading)

The application owns the protocol. The cache is a dumb key-value store that knows nothing about the database.

```
READ                                   WRITE
  app ──GET──▶ cache                     app ──UPDATE──▶ database
  hit?  ──yes──▶ return                  app ──DEL─────▶ cache
  no  ──SELECT──▶ database               (delete, do NOT update — see 4.6)
      ──SET────▶ cache
      ──────────▶ return
```

**Properties:**
- Only requested data is ever cached. The cache contains exactly the working set, with no configuration describing what to cache.
- The cache and the database are **decoupled**. If the cache is empty, unavailable, or returns garbage, the application still works — slower, but correct. This is the single most important property in the whole lesson.
- The data model in the cache is free to differ from the database. You can cache a fully assembled `BookView` object composed from four tables; a read-through loader tied to a table could not.
- Every miss costs the full penalty: wasted cache round trip + database read + repopulating write.
- The first request for every key is always a miss. A cold cache has a 0 % hit ratio by construction.

**Why it is the default — four concrete reasons, not preference:**
1. **Failure isolation.** Every other pattern puts the cache *in the path of correctness*. Cache-aside puts it beside the path. A dead Redis degrades latency; it does not produce wrong answers or failed writes.
2. **No wasted memory.** Write-through caches every written item whether or not anyone reads it. For BookNook's order table — written once, read almost never — that is pure waste.
3. **Arbitrary value shapes.** The cached object can be any composite the application finds useful.
4. **It is the only pattern that works with any cache and any database**, with no integration layer, no cache-provider feature, and no vendor lock-in.

**The cost of the default** is that invalidation is the application's job at every write site (Problem 2 above), and that the race in Section 4.6 is possible.

### 4.2 Read-through

The application asks the cache for a key and gets a value. The cache (a library like Caffeine with a `CacheLoader`, or a service like Ehcache/Hazelcast/DynamoDB Accelerator) owns the miss path.

```
  app ──get(key)──▶ cache
                     │ hit  ──────────────▶ value
                     │ miss ──loader()──▶ database
                     │      ──store────▶ itself
                     └────────────────────▶ value
```

**Properties:**
- The miss protocol exists **once**, in the loader. BookNook's 60 divergent call sites become 60 identical `cache.get(key)` calls and one loader definition.
- Good implementations give you **miss coalescing for free**: if 5,000 threads request the same missing key simultaneously, exactly one loader call runs and the other 4,999 wait for its result. With cache-aside you must build this yourself with a lock or a single-flight primitive. This is the strongest practical argument for read-through, and it directly solves Lesson 26's thundering herd.
- The cached shape is constrained by what the loader produces, which is usually one entity type per cache.
- The cache is now **in the correctness path**: if the loader throws, the `get` throws. You must decide whether an exception propagates to the user or falls back to a direct database read — and that decision is easy to forget.
- Read-through on its own says nothing about writes. It is always paired with write-through, write-behind, or explicit invalidation.

### 4.3 Write-through

Every write goes to the cache and the database, synchronously, before the caller gets an acknowledgement.

```
  app ──write──▶ cache ──write──▶ database
                              ◀──ack──
       ◀──ack──
  Caller waits for BOTH.
```

**Properties:**
- The cache is **never stale** with respect to committed data, which eliminates the Section 4.6 race entirely for keys written through it.
- **Read-your-writes is guaranteed**: the value you just wrote is in the cache when your next read arrives.
- Write latency is the **sum** of both writes, not the maximum, because they are sequential. Section 9 Part A quantifies this.
- **Write amplification of 2×**, and the cache fills with data nobody reads. For a write-heavy, read-light table this is strictly worse than not caching at all.
- **The atomicity problem is unavoidable.** The cache and the database are two systems with no shared transaction. If the database write succeeds and the cache write fails, you have a stale cache. If the cache write succeeds and the database write fails, you have a **cached value that was never committed** — which is far worse, because a subsequent read returns data the database does not have. The standard mitigation is ordering: **write the database first, then the cache**; on cache failure, delete the key rather than leaving it, and let the next read repopulate.
- The combination that actually ships is **write-through for correctness-critical keys, cache-aside everywhere else**.

### 4.4 Write-behind (write-back)

The write goes to the cache; the caller is acknowledged immediately; the database is written asynchronously by a background flusher.

```
  app ──write──▶ cache ──ack──▶ app      (caller is done in ~0.4 ms)
                   │
                   └── buffer ──(every N ms or M items)──▶ database
```

**Properties:**
- **Lowest possible write latency** — the caller waits only for the cache.
- **Write coalescing**: 500 increments to the same counter in one flush window become **one** database `UPDATE`. This is the real reason to use write-behind, and it is a throughput argument, not a latency argument. Section 9 Part C shows a 1,900× reduction in database writes for BookNook's view counters.
- **Ordering and batching** let the flusher write sequentially and in bulk, which is dramatically kinder to the database than random single-row updates.
- **The data-loss window.** Acknowledged writes live only in cache memory until flushed. If the cache process dies, every unflushed write is gone — and the user was already told it succeeded. The window is `flush_interval × write_rate` items, or `buffer_size` items, whichever is smaller. **You must compute this number and decide whether the business can absorb it.** Section 9 Part C computes 600 lost view-counts per year for BookNook, which is fine, and shows the same arithmetic producing 600 lost *orders* per year, which is not.
- **The database is no longer the source of truth in real time.** Any consumer reading the database directly — a reporting job, a replica, a CDC stream — sees data that lags by up to one flush interval. This surprises people badly.
- **Failed flushes have nowhere to go.** If the database rejects a write (constraint violation, deadlock, down), the caller left minutes ago and cannot be told. You need a dead-letter path and an alert, or the write vanishes silently.

Write-behind is correct for **high-frequency, low-value, aggregatable, idempotent-ish writes**: view counts, like counts, last-seen timestamps, telemetry, non-critical audit trails. It is wrong for anything a user would file a support ticket about losing.

### 4.5 Refresh-ahead

The cache proactively reloads an entry before it expires, so that popular keys are never fetched on the user's critical path.

```
  TTL = 300 s, refresh threshold = 80%
  t=0    entry stored
  t=240  a read arrives → return the CURRENT value immediately (fast)
         AND trigger a background reload
  t=241  entry replaced with fresh data, TTL reset
  t=300  the original expiry never arrives -- nobody ever missed
```

**Properties:**
- Hot keys approach a **100 % hit ratio** with no user-visible miss penalty. The miss cost is moved off the request path entirely.
- It is **prediction**, so it is wasteful by definition: a key refreshed at t=240 and never read again cost a database query for nothing. On a long tail of unpopular keys this is a large amount of pointless load.
- It does not eliminate the first miss, only subsequent ones.
- It slightly *increases* average staleness relative to strict expiry, because an entry refreshed at 80 % of its TTL is being served while a newer value may exist. It is trading a little more staleness for a lot less tail latency.
- The HTTP header `stale-while-revalidate` (Lesson 27, Section 8a) is exactly this pattern expressed at the browser/CDN layer.

Use it only for a **small, identifiable set of hot keys** — the homepage carousel, the top 500 books, the feature-flag bundle — never for the whole key space.

### 4.6 The cache-aside race condition

This is the most important thing in the lesson, and the thing most candidates have never thought about.

**Setup.** Two concurrent operations on the key `book:9780141036144`, whose database value is currently £12.99:
- **Reader R** issues a read, misses the cache, and begins a database query.
- **Writer W** updates the price to £14.99 and invalidates the cache.

**The timeline that breaks.** Times are illustrative but realistic:

```
t = 0.0 ms   R: GET book:123          → MISS
t = 0.4 ms   R: SELECT ... FROM books → sends query to PostgreSQL
t = 2.0 ms   W: UPDATE books SET price = 14.99 → COMMITTED
t = 2.4 ms   W: DEL book:123          → deletes... nothing. The key is
                                        already absent; R has not written yet.
t = 6.0 ms   R: database returns £12.99   ← the value read BEFORE the update
t = 6.4 ms   R: SET book:123 = £12.99 EX 300
                 ▲
                 R has just written a value that the database has NOT held
                 since t = 2.0 ms, and nothing will correct it.

RESULT: the cache serves £12.99 for the next 300 seconds. The database says
        £14.99. No error was raised. No retry will help. The invalidation
        ran and did exactly what it was told -- it simply ran too early.
```

**Why this is not fixable by reordering the writer.** Suppose W deletes the key *before* updating the database instead. Then a reader that misses between the delete and the commit reads the old value and caches it — the same outcome, through a shorter window. Suppose W updates the cache instead of deleting it: now two concurrent writers can interleave their database commits and their cache writes in opposite orders, leaving the cache holding the *older* writer's value permanently. Every single-step ordering has a losing interleaving. The race is inherent to performing two un-coordinated operations on two systems that share no transaction.

**The vulnerable window** is the interval between the reader's database read returning a value and the reader's `SET` landing. Section 9 Part B computes its probability and its rate for a realistic hot key.

**Mitigation 1 — delete, do not update (mandatory baseline).**
On a write, `DEL` the key; never `SET` the new value. Two reasons:
- Two concurrent writers that `SET` can land in the opposite order to their database commits, leaving the cache permanently holding the older value. Two concurrent writers that `DEL` both leave the key absent, and the next read repopulates from the committed state. **`DEL` is idempotent and converges; `SET` is order-dependent and does not.**
- Writes that no one reads do not consume cache memory.
This does not fix the read/write race above, but it removes the much more common writer/writer race. It is the baseline, not the solution.

**Mitigation 2 — delayed double delete.**
Delete the key, update the database, wait longer than the longest plausible read path, and delete again.

```
DEL book:123
UPDATE books SET price = 14.99          -- commit
sleep(500 ms)  (asynchronously, not blocking the caller)
DEL book:123                            -- kills any entry a racing reader
                                           repopulated with the pre-update value
```
- *Strength*: simple, needs no new infrastructure, and collapses the staleness window from one TTL (300 s) to the delay (0.5 s).
- *Weakness*: it is **probabilistic**. If a reader's database query was slower than the delay — a lock wait, a GC pause, a replica lag spike — the stale entry survives. It converts a certainty into a small probability; it does not provide a guarantee. Anyone who claims otherwise has not considered a 2-second GC pause.
- *Cost*: a second delete per write, and a scheduled task per write.

**Mitigation 3 — versioned keys.**
Include a monotonically increasing version in the key, so stale writes land on keys nobody will ever read.

```
key = f"book:{isbn}:v{row_version}"     -- row_version from an integer
                                           column bumped in the same
                                           transaction as the UPDATE
```
- A racing reader that read version 7 writes `book:123:v7`. After the commit, every reader computes `book:123:v8` and misses, reading fresh data. The poisoned entry is **unreachable** and expires harmlessly.
- *Strength*: this actually **eliminates** the race rather than narrowing it. It is the only mitigation here that is not probabilistic.
- *Weakness*: you must know the version before you can build the key, which usually costs a lookup — so it works naturally when the version travels with the request (an `ETag`, a session, a parent object you already loaded) and awkwardly otherwise. It also strands orphaned entries in memory until they expire, so it needs an eviction policy that reclaims them (`allkeys-lru`, Lesson 27).
- A coarse variant is the global version prefix from Lesson 27 (`book:v3:`), which invalidates everything at once; the per-row version is the precise form.

**Mitigation 4 — CDC-driven invalidation.**
Stop invalidating from application code entirely. Tail the database's own replication log (PostgreSQL logical replication / MySQL binlog) with a connector such as Debezium, and have a consumer delete cache keys for every committed row change.

```
PostgreSQL WAL ──▶ logical decoding ──▶ connector ──▶ DEL book:123
```
- *Strength*: the invalidation is triggered by the **commit itself**, so it cannot be forgotten by a new code path, a bulk import, a manual `psql` session, or a migration. It fixes Problem 2 from Section 2 permanently and structurally.
- *Strength*: the delete happens strictly **after** the commit is durable, so a reader that repopulates from a pre-commit read is corrected by the subsequent CDC delete. The window shrinks to the CDC pipeline's lag (typically 10–500 ms) instead of one TTL.
- *Weakness*: a whole pipeline to run — connector, topic, consumer, lag monitoring, replay-on-restart. It also only knows about row changes, so a cached object composed from four tables needs mapping logic from rows to cache keys.
- *Weakness*: CDC lag is a real staleness window and it grows under load, exactly when you least want it to.
- This is built properly in **Phase 4, Topic 22 — Transactional outbox and change data capture**. For now, know that it exists, that it is the structurally correct answer to "who invalidates?", and that it is usually adopted after an incident caused by a write path that forgot to invalidate.

**Mitigation 5 — short TTL as a backstop.**
Whatever else you do, give every entry a TTL. It does not prevent the race; it **bounds the blast radius** of every mitigation's residual failure to one TTL. A system whose correctness depends on every invalidation succeeding will eventually be wrong forever. TTL is the floor under all of it, and it is why Lesson 29 — **Phase 2, Topic 12 — Cache invalidation and TTL** — is the next lesson.

**Combining them.** Production BookNook: `DEL` not `SET` (always), TTL of 300 s with jitter (always), delayed double delete on price and stock writes (the two fields where staleness is a business error), and CDC invalidation added later to catch the write paths nobody remembered.

### 4.7 Choosing a pattern from the workload

| Workload characteristic | Read pattern | Write pattern | Reason |
|---|---|---|---|
| Read-heavy, unpredictable key access, staleness tolerable | **Cache-aside** | Invalidate on write (`DEL`) | Caches only the working set; survives cache failure |
| Read-heavy, many call sites, thundering herd on hot keys | **Read-through** | Invalidate on write | Loader is centralised and coalesces concurrent misses |
| Read-after-write is frequent and immediate (a user edits then views) | Cache-aside or read-through | **Write-through** | Guarantees read-your-writes; no invalidation race on that key |
| Write-heavy, written data rarely read | Cache-aside | **No cache write at all** — just `DEL` | Write-through would fill the cache with unread data at 2× write cost |
| Very high-frequency, aggregatable, low-value writes (counters, last-seen) | Cache-aside | **Write-behind** | 1,000:1 write coalescing; loss window is acceptable for this data |
| Financial, inventory, or anything a user would file a ticket about losing | Cache-aside | **Write-through, or no cache on the write path** | Write-behind's loss window is unacceptable at any size |
| Small set of extremely hot keys with predictable access | **Refresh-ahead** on those keys | As appropriate | Removes the miss penalty from the critical path for the keys that matter |
| Keys cached as composites of several tables | **Cache-aside** | Invalidate on write, ideally via CDC | A read-through loader is awkward across tables; CDC maps rows to keys |
| Cache unavailability must not cause errors | **Cache-aside** | Invalidate on write | The only pattern in which the cache is not in the correctness path |

## 5. Request / data flow

Two flows: a cache-aside read with a concurrent write (showing the race and its mitigation), then a write-behind counter flush.

**Flow A — BookNook price update with delayed double delete, concurrent with a read**

1. Admin submits `PUT /admin/books/9780141036144 {"price": 14.99}`. The request reaches app server A via the load balancer (Lesson 23).
2. App server A issues `DEL book:v3:9780141036144` to Redis **before** touching the database. Redis removes the entry. Any read arriving now will miss and go to the database.
3. Simultaneously, a customer request reaches app server B. B issues `GET book:v3:9780141036144` → **miss** (step 2 just removed it).
4. B issues `SELECT * FROM books WHERE isbn = $1`. The query reaches PostgreSQL and begins executing. **The value it will return is £12.99**, because A has not committed yet.
5. A executes `UPDATE books SET price = 14.99, row_version = row_version + 1 WHERE isbn = $1` and commits. The database now says £14.99.
6. A schedules an asynchronous task to run in 500 ms, then returns `200 OK` to the admin. The admin's request did **not** wait for the delayed delete.
7. B's query returns — £12.99, the pre-update value. B is not at fault; its read snapshot predates the commit.
8. B issues `SET book:v3:9780141036144 <£12.99 json> EX 300`. **The cache is now poisoned.** Without mitigation it serves £12.99 for 300 seconds.
9. At t + 500 ms, A's scheduled task runs `DEL book:v3:9780141036144`. The poisoned entry is removed.
10. The next read misses, queries the database, gets £14.99, and caches it. The system has converged.
11. **Total staleness window: ~500 ms instead of ~300 s — a 600× reduction.** The residual risk: if B's query had taken longer than 500 ms (a lock wait, a GC pause), step 9 would have run before step 8 and the poisoned entry would survive the full TTL. This is why the TTL backstop in 4.6 Mitigation 5 is not optional.
12. In the versioned-key variant, step 8 writes `book:9780141036144:v7` while every post-commit reader computes `book:9780141036144:v8`. The poisoned entry is written to a key nobody will request, and the race is eliminated rather than narrowed.

**Flow B — write-behind view counter**

1. A customer loads a book page. App server C increments the view counter: `HINCRBY views:pending 9780141036144 1` in Redis — ~0.4 ms.
2. C returns the page. **No database write occurred.** The user's latency included 0.4 ms of cache, not 4 ms of database.
3. Steps 1–2 repeat 3,000 times per second across the fleet, hitting Redis at 3,000 ops/s — about 2.5 % of a single Redis node's capacity.
4. Every 5 seconds a dedicated flusher process runs `HGETALL views:pending` followed by `DEL views:pending` in a `MULTI/EXEC` transaction, so no increments are lost between the read and the clear.
5. The flusher has roughly 15,000 increments spread over perhaps 8,000 distinct books.
6. It issues **one** batched `UPDATE ... FROM (VALUES ...)` statement applying all 8,000 deltas. The database performs 8,000 row updates in one transaction every 5 seconds — 1,600 writes/second of sequential, batched work instead of 3,000/second of random, contended single-row updates.
7. If the flusher crashes between steps 4 and 6, the increments have been deleted from Redis and not yet written: **up to 15,000 view counts are lost.** Mitigations: move the data to a staging key instead of deleting it, and delete only after the database commit succeeds; or accept the loss, which for view counts is the usual and correct decision.
8. If Redis itself dies, up to 5 seconds of increments are lost — the data-loss window, quantified in Section 9 Part C.
9. Note what step 8 means for anyone reading `books.views` directly: a reporting query sees a number up to 5 seconds stale. The database is no longer the real-time source of truth for that column. **Write-behind moves the source of truth into the cache for the duration of the flush window**, and every downstream consumer inherits that.

## 6. ASCII diagram

```
CACHE-ASIDE (lazy loading) — the application owns the protocol
  READ                                    WRITE
   app                                     app
    │ 1. GET key                            │ 1. UPDATE db  (commit)
    ▼                                       ▼
  [cache] ──hit──▶ return (0.4 ms)        [database]
    │ miss                                  │ 2. DEL key  (NOT SET)
    ▼ 2. SELECT                             ▼
  [database] ──▶ 3. SET key ──▶ [cache]   [cache]  (now absent)
    │                                       next read repopulates
    └──▶ return (6.8 ms)
  Cache is BESIDE the correctness path: cache down ⇒ slow, still correct.


READ-THROUGH — the cache owns the miss path
   app ──get(key)──▶ [cache] ──hit──▶ value
                        │ miss
                        ▼  loader()
                    [database]
                        │
                        └─▶ cache stores it ─▶ value
  5,000 concurrent misses on one key ⇒ ONE loader call (coalescing).
  Cache is IN the correctness path: loader throws ⇒ get throws.


WRITE-THROUGH — both writes before the ack
   app ──write──▶ [cache] ──write──▶ [database]
                                ◀────ack───
        ◀───ack───
   latency = cache_write + db_write   (sum, not max)
   never stale · read-your-writes · 2× write amplification
   DANGER: no shared transaction. Write DB first, then cache;
           on cache failure DEL instead of leaving a stale entry.


WRITE-BEHIND — ack first, persist later
   app ──write──▶ [cache] ──ack──▶ app      (0.4 ms)
                     │
                     │ buffer  ◀── DATA-LOSS WINDOW LIVES HERE
                     │              = flush_interval × write_rate
                     ▼  every 5 s, batched
                 [database]
   3,000 writes/s in  ⇒  1 batched statement per 5 s out
   fastest writes · huge coalescing · acknowledged data can vanish


REFRESH-AHEAD — refill before anyone misses
   TTL 300 s, threshold 80%
   t=0 ────────────────────── t=240 ──── t=300
   store                      read: return current value NOW
                              + background reload, TTL resets
   Hot keys never experience a user-visible miss.
   Cost: keys refreshed and never read again = wasted DB queries.


THE CACHE-ASIDE RACE — why a concurrent read+write poisons the cache
  time   READER R                      WRITER W            CACHE    DB
  ─────────────────────────────────────────────────────────────────────
  0.0ms  GET → MISS                                        (empty) 12.99
  0.4ms  SELECT … ──────▶                                  (empty) 12.99
  2.0ms                                UPDATE → COMMIT     (empty) 14.99
  2.4ms                                DEL  (no-op!)       (empty) 14.99
  6.0ms  ◀── returns 12.99 (pre-commit snapshot)           (empty) 14.99
  6.4ms  SET key = 12.99 EX 300                            12.99 ← 14.99
                                                            ▲▲▲▲▲
         STALE FOR 300 SECONDS. No error. No retry helps.
         W's invalidation ran correctly -- it just ran too early.

  MITIGATED (delayed double delete):
  506 ms                               DEL (2nd)           (empty) 14.99
         ⇒ staleness 500 ms instead of 300 s  (600× better, still
           probabilistic: a reader slower than 500 ms still wins)

  MITIGATED (versioned keys) — deterministic:
  6.4ms  SET book:123:v7 = 12.99   ← written to a key nobody will read
  6.5ms  every reader now computes book:123:v8 → MISS → reads 14.99 ✔
```
## 7. Detailed example

### BookNook — four patterns in one system, each justified separately

BookNook's caching started as "cache-aside everywhere" and ended as a deliberate mix. Here is each decision and what forced it.

**(a) Book records — cache-aside with `DEL` on write, plus a delayed double delete.**
Read 3,000/s, written ~40/day, read from dozens of call sites with unpredictable ISBNs. Cache-aside is right because only the working set (about 4 % of the 1.2 million-title catalogue) is ever requested, and because a Redis outage must degrade latency rather than correctness. The delayed double delete was added after a specific incident: a marketing price change at 09:00 produced 41 customer complaints over the next five minutes about a price that "kept changing". Root cause was exactly the Section 4.6 race on the single hottest ISBN of the campaign. Measured staleness window before the fix: up to 300 s. After: ~500 ms.

**(b) Feature flags — read-through with refresh-ahead.**
Read 36,000/s from the in-process cache (Lesson 27). Read-through because there are 60 call sites and exactly one correct loading procedure; refresh-ahead because the flag bundle is a single, permanently hot key where a miss would stall every request on that server simultaneously. The refresh threshold is 80 % of a 30-second TTL, so the bundle is reloaded at t=24 s in the background and no request ever waits for it.

**(c) Shopping-cart contents — write-through.**
Read-after-write is the dominant access pattern: a customer adds an item and the next page render reads the cart immediately. With cache-aside plus `DEL`, that render is a guaranteed miss, so every "add to cart" costs an extra database round trip at the exact moment the user is most engaged. Worse, under replica lag the read could return a cart without the item just added — a textbook read-your-writes violation, and the single most reliable way to make a user believe your site is broken. Write-through makes the post-write read a guaranteed **hit** and guarantees read-your-writes. The 2× write amplification is irrelevant here because carts are small and written only tens of times per second.

**(d) View counters — write-behind.**
3,000 increments/s. PostgreSQL will not sustain 3,000 hot-row updates/s; the hottest 50 books would serialise on their row locks and every other writer would queue behind them. Write-behind with a 5-second flush collapses 15,000 increments into one batched statement every 5 seconds (Section 9 Part C: a 1,900× reduction). The data-loss window is explicitly accepted and documented: a Redis failure loses up to 5 seconds of view counts, which is a cosmetic number on a product page.

**(e) Orders — no cache on the write path at all.**
This is the decision that matters most, and it is a *refusal*. An engineer proposed write-behind for order creation, reasoning that it would cut checkout latency from 180 ms to 30 ms. The arithmetic (Section 9 Part D) shows the same flush interval that loses 15,000 harmless view counts would lose roughly 150 **orders** per incident. Orders are written once and read rarely, so write-through would also be pure amplification. Orders are therefore written directly to PostgreSQL, synchronously, with the order read path using cache-aside afterwards. **The right pattern for a write is sometimes "no pattern".**

**(f) The incident that bought CDC.** Eight months in, a nightly bulk price importer — a separate job, written by the data team, using `COPY` into a staging table and then a bulk `UPDATE` — updated 14,000 prices without touching Redis, because its author had no reason to know Redis existed. Those 14,000 books served stale prices until their individual TTLs expired, up to 300 seconds each, staggered. No alert fired; every service was healthy. The structural fix was CDC-driven invalidation: PostgreSQL logical replication → connector → a consumer issuing `DEL book:v3:{isbn}`. Now *any* committed change invalidates the cache regardless of which code path, job, migration or human `psql` session made it. This is built fully in **Phase 4, Topic 22 — Transactional outbox and change data capture**.

The through-line: every pattern change at BookNook was forced by a specific, observed failure, and each one traded a precisely identified cost for a precisely identified benefit.

### Second example for contrast — a hospital medication-administration system

Same five patterns, almost entirely opposite selections, because the cost of being wrong changes everything.

- **Cache-aside for patient demographics** (name, date of birth, allergies list revision): read constantly, changed rarely, and a 30-second staleness on a *name* is harmless. Same choice as BookNook, same reasoning.
- **No cache at all for the current medication order.** A nurse scanning a wristband must see the order as of *now*. A 500 ms staleness window — perfectly acceptable for a book price — is a medication error. Here `h` would be high and the cache would be fast, and it is still the wrong answer, because the cost of a single stale read is unbounded. **This is the case where the Lesson 26 formula is irrelevant: no latency improvement justifies the failure mode.**
- **Write-through, never write-behind, for administration records.** When a nurse records that a dose was given, that fact must be durable before the screen confirms it. Write-behind's loss window — the exact mechanism that makes BookNook's counters cheap — would mean a dose recorded on screen but absent from the record, which can cause a double dose. The latency saving is real and is declined.
- **Refresh-ahead for the ward's active-patient list**: a small, bounded, extremely hot key set. Same reasoning as BookNook's feature flags, and the one place the two systems fully agree.
- **CDC invalidation from day one, not after an incident.** In a system where an un-invalidated write is a clinical risk rather than a support ticket, you do not wait for the bulk-importer incident to teach you.

The contrast to internalise: BookNook chose patterns by latency and throughput, with staleness as a secondary cost. The hospital chooses patterns by the cost of a single wrong answer, with latency as a secondary cost. **The pattern menu is identical; the ranking function is not.** When you are asked to choose a pattern, the first question is not "how fast?" but "what happens if this value is wrong, once?"

## 8. Code or configuration example

> **Illustrative only — not production-ready.** Error handling, retries, metrics, tracing, connection-pool management, serialisation versioning and security are simplified or omitted. Do not paste this into a service.

### (a) Cache-aside — the default

```python
import json, random, redis
r = redis.Redis(socket_timeout=0.05)     # fail fast: a slow cache is worse
                                         # than no cache (Lesson 17)
TTL = 300

def get_book(isbn):
    key = f"book:v3:{isbn}"
    try:
        raw = r.get(key)                 # 1. ask the cache
        if raw:
            return json.loads(raw)       #    HIT: ~0.43 ms, done
    except redis.RedisError:
        pass                             # cache down ⇒ degrade to slow,
                                         # NOT to wrong. This single except
                                         # block is why cache-aside is the
                                         # default pattern.

    book = db.query_one("SELECT * FROM books WHERE isbn = $1", isbn)  # 2. DB

    try:
        # 3. repopulate, with jitter so a cold-start wave does not expire
        #    in a single synchronised second (Lesson 26 thundering herd)
        r.set(key, json.dumps(book), ex=int(TTL * random.uniform(0.9, 1.1)))
    except redis.RedisError:
        pass
    return book

def update_book_price(isbn, price):
    key = f"book:v3:{isbn}"

    # DELETE FIRST, then write. Deleting first means a racing reader can only
    # cache the pre-update value (fixed below); deleting only after the commit
    # leaves a window where readers get a cache HIT on the old value.
    r.delete(key)

    db.execute("UPDATE books SET price = $1, row_version = row_version + 1 "
               "WHERE isbn = $2", price, isbn)   # commit

    # DELAYED DOUBLE DELETE. Removes any entry a concurrent reader
    # repopulated with the pre-commit value (Section 4.6 / Section 6).
    # Runs ASYNCHRONOUSLY: the admin's request must not wait 500 ms.
    # It is PROBABILISTIC -- a reader whose query took longer than the
    # delay (lock wait, GC pause) still wins the race. The TTL is the
    # backstop for that residual case.
    schedule_after(0.5, lambda: r.delete(key))

    # NOTE: we DEL rather than SET the new value. DEL is idempotent and
    # convergent; two concurrent writers both deleting leaves a correct
    # (absent) state, whereas two writers SETting can land in the opposite
    # order to their commits and leave the OLDER value cached forever.
```

### (b) Read-through with miss coalescing

```python
import threading

class ReadThroughCache:
    """The miss protocol exists ONCE, here -- not at 60 call sites."""

    def __init__(self, loader, ttl=300):
        self._loader = loader            # the single definition of "how to
                                         # fetch this on a miss"
        self._ttl = ttl
        self._inflight = {}              # key -> Event, for coalescing
        self._lock = threading.Lock()

    def get(self, key):
        raw = r.get(key)
        if raw:
            return json.loads(raw)

        # MISS COALESCING ("single flight"). Without this, 5,000 concurrent
        # requests for one expired hot key issue 5,000 identical database
        # queries in the same millisecond -- the thundering herd of Lesson 26.
        # With it, exactly ONE query runs and the other 4,999 wait for it.
        # Good read-through libraries (e.g. Caffeine) give you this free;
        # with cache-aside you must build it yourself, which is the single
        # strongest practical argument for read-through.
        with self._lock:
            event = self._inflight.get(key)
            leader = event is None
            if leader:
                event = self._inflight[key] = threading.Event()

        if not leader:
            event.wait(timeout=5)        # ALWAYS bound the wait, or a hung
                                         # loader stalls every waiter
            return json.loads(r.get(key) or "null")

        try:
            value = self._loader(key)
            r.set(key, json.dumps(value), ex=self._ttl)
            return value
        finally:
            # MUST run on every path, including exceptions, or waiters
            # block until their timeout and the key is permanently "inflight".
            with self._lock:
                del self._inflight[key]
            event.set()

books = ReadThroughCache(loader=lambda k: db.query_one(
    "SELECT * FROM books WHERE isbn = $1", k.split(":")[-1]))
```

### (c) Write-through

```python
def save_cart(user_id, cart):
    key = f"cart:{user_id}"

    # ORDER MATTERS AND IS NOT NEGOTIABLE: database first, cache second.
    # There is no shared transaction between PostgreSQL and Redis, so one of
    # them can fail after the other succeeded. Writing the DB first means the
    # worst case is a MISSING cache entry (harmless -- the next read
    # repopulates). Writing the cache first means the worst case is a CACHED
    # VALUE THAT WAS NEVER COMMITTED, which is served to users as truth.
    db.execute("INSERT INTO carts (user_id, data) VALUES ($1, $2) "
               "ON CONFLICT (user_id) DO UPDATE SET data = $2", user_id, cart)

    try:
        # Write-through pays off precisely because the very next read -- the
        # page render right after "add to cart" -- is now a guaranteed HIT
        # and a guaranteed read-your-writes.
        r.set(key, json.dumps(cart), ex=3600)
    except redis.RedisError:
        # On cache failure, DELETE rather than leaving whatever was there.
        # An absent entry is always safe; a stale entry is not.
        try:
            r.delete(key)
        except redis.RedisError:
            pass                         # TTL is the final backstop
```

### (d) Write-behind with an explicitly bounded loss window

```python
import time, threading

FLUSH_INTERVAL = 5.0       # seconds. This number IS the data-loss window.
MAX_BUFFER     = 50_000    # hard cap: flush early rather than grow unbounded

def record_view(isbn):
    # 0.4 ms, no database write at all. 3,000/s of these is ~2.5% of one
    # Redis node; the same rate as single-row UPDATEs would deadlock
    # PostgreSQL on the hottest rows.
    r.hincrby("views:pending", isbn, 1)

def flusher():
    while True:
        time.sleep(FLUSH_INTERVAL)

        # Read-and-clear ATOMICALLY. Without MULTI/EXEC, increments arriving
        # between HGETALL and DEL are silently destroyed.
        pipe = r.pipeline()
        pipe.hgetall("views:pending")
        pipe.delete("views:pending")
        pending, _ = pipe.execute()
        if not pending:
            continue

        # COALESCING -- the actual reason write-behind exists. ~15,000
        # increments over ~8,000 books become ONE batched statement.
        deltas = [(k.decode(), int(v)) for k, v in pending.items()]
        try:
            db.execute_batch(
                "UPDATE books SET views = views + d.delta "
                "FROM (VALUES %s) AS d(isbn, delta) WHERE books.isbn = d.isbn",
                deltas)
        except Exception:
            # The caller was acknowledged MINUTES ago and cannot be told.
            # Without a dead-letter path the write vanishes in silence.
            # This is the structural weakness of write-behind, not a bug.
            deadletter.write(deltas)
            alert("view-counter flush failed", count=len(deltas))

threading.Thread(target=flusher, daemon=True).start()

# ACCEPTED RISK, DOCUMENTED IN THE RUNBOOK:
#   loss window = FLUSH_INTERVAL × write_rate = 5 s × 3,000/s = 15,000 views
#   Acceptable because a view count is cosmetic.
#   The identical code with orders in place of views would lose ~150 ORDERS
#   per incident. Same pattern, same arithmetic, unacceptable outcome.
```

### (e) Refresh-ahead and versioned keys

```python
REFRESH_AT = 0.8           # refresh once 80% of the TTL has elapsed

def get_flags():
    key = "flags:bundle"
    raw, ttl_left = r.get(key), r.ttl(key)

    if raw and ttl_left > TTL * (1 - REFRESH_AT):
        return json.loads(raw)                  # comfortably fresh

    if raw:
        # Serve the CURRENT value immediately and refresh in the background.
        # The user never pays the miss penalty. This is exactly what the HTTP
        # header stale-while-revalidate does at the CDN layer (Lesson 27).
        # Cost: keys refreshed and then never read again are wasted DB load,
        # so apply this ONLY to a small, known-hot key set.
        schedule_now(lambda: r.set(key, json.dumps(load_flags()), ex=TTL))
        return json.loads(raw)

    value = load_flags()                        # genuine cold miss
    r.set(key, json.dumps(value), ex=TTL)
    return value


def versioned_key(isbn, row_version):
    # ELIMINATES the cache-aside race rather than narrowing it: a racing
    # reader writes book:123:v7 while every post-commit reader computes
    # book:123:v8, so the poisoned entry is UNREACHABLE and simply expires.
    # Requires: row_version is bumped in the SAME transaction as the update,
    # and the caller already knows the version (from an ETag, a parent object,
    # or the request itself) -- otherwise you pay a lookup to build the key.
    # Requires allkeys-lru eviction, because orphaned versions accumulate.
    return f"book:{isbn}:v{row_version}"
```

## 9. Numerical example

### Part A — Write latency by pattern

**Assumptions, all typical/approximate:** PostgreSQL write (including commit and fsync) **4.0 ms**; Redis write same-AZ **0.4 ms**; application overhead per write **0.1 ms**; the delayed second delete is asynchronous and therefore off the caller's critical path.

```
Pattern               Caller waits for                        Latency
────────────────────────────────────────────────────────────────────────
No cache              DB only                 4.0 + 0.1     = 4.10 ms
Cache-aside (DEL)     DB + one DEL            4.0 + 0.4+0.1 = 4.50 ms
Write-through         DB + cache SET          4.0 + 0.4+0.1 = 4.50 ms
Write-behind          cache only              0.4 + 0.1     = 0.50 ms

Write-behind vs write-through : 4.50 / 0.50 = 9.0× faster
Write-behind vs no cache      : 4.10 / 0.50 = 8.2× faster
Cache-aside vs write-through  : identical latency -- the difference
                                between them is NOT speed, it is whether
                                the next read hits (write-through) or
                                misses (cache-aside).
```

That last line is the one people miss. Write-through is not chosen for write latency; it is chosen because it converts the next read from a guaranteed miss into a guaranteed hit, and guarantees read-your-writes.

**Read-after-write, the cart case.** Assumptions: Redis hit 0.43 ms; cache miss path = 0.40 (wasted `GET`) + 4.0 (DB read) + 0.40 (repopulate) = 4.80 ms; one read immediately follows every write.

```
Cache-aside : write 4.50 + next read 4.80 (guaranteed MISS) = 9.30 ms
Write-through: write 4.50 + next read 0.43 (guaranteed HIT) = 4.93 ms
Improvement : 9.30 / 4.93 = 1.89× for the write-then-read sequence.
```

### Part B — The cache-aside race, quantified

**Assumptions:** one hot key (a book on the homepage carousel); 600 reads/second for that key; TTL 300 s with no other invalidation, so the key expires and is repopulated roughly every 300 s plus whatever eviction causes — assume **2 miss events per second** for this key under eviction pressure and jitter; the key is written **10 times per second** during a flash-sale repricing job; the vulnerable window (from the reader's database read returning to its `SET` landing) is **6 ms**.

```
P(a given read-miss is poisoned)
  = probability a write commits inside that reader's 6 ms window
  = write_rate × window
  = 10 /s × 0.006 s
  = 0.06                                  → 6% of misses are poisoned

Poisoning events per second
  = miss_events/s × P
  = 2 × 0.06
  = 0.12 /s

Poisoning events per hour = 0.12 × 3,600 = 432
```

**Exposure per poisoning, with no mitigation:**

```
Stale lifetime      = full TTL = 300 s   (nothing corrects it)
Reads served stale  = 600 /s × 300 s = 180,000 stale reads per event
```

**With a delayed double delete at 500 ms:**

```
Stale lifetime      ≈ 0.5 s
Reads served stale  = 600 × 0.5 = 300 per event
Reduction           = 180,000 / 300 = 600×

Residual failures: readers whose DB query exceeds 500 ms. Assume 0.1% of
queries do (p99.9 lock waits and GC pauses):
  Residual poisonings/hour = 432 × 0.001 = 0.43/hour
  ≈ 10 per day that still last the full 300 s.
```

**With versioned keys:**

```
Poisonings that are ever READ = 0.
The poisoned entry is written to book:123:v7; every post-commit reader
requests book:123:v8. The stale entry is unreachable by construction.
Cost: orphaned entries occupy memory until eviction. At 432/hour × 4 KB
= 1.7 MB/hour of orphans on this key's traffic -- trivially reclaimed by
allkeys-lru, but it is why noeviction would eventually be fatal.
```

This is the arithmetic that justifies calling the race "real": 432 occurrences per hour on one key, each one visible to up to 180,000 reads, is not a theoretical concern.

### Part C — Write-behind: coalescing gain and loss window

**Assumptions:** 3,000 view increments/second; flush interval 5 s; views spread over ~8,000 distinct books per window; PostgreSQL sustains ~2,000 single-row random `UPDATE`s/s but ~40,000 rows/s inside batched statements; Redis node capacity ~120,000 ops/s.

```
WITHOUT write-behind
  DB writes            = 3,000 /s
  DB capacity          = 2,000 /s
  Utilisation          = 3,000 / 2,000 = 150%  ⇒ IMPOSSIBLE.
  The hottest 50 books also serialise on their row locks, so the real
  ceiling is well below 2,000/s.

WITH write-behind (5 s flush)
  Increments buffered per window = 3,000 × 5 = 15,000
  Distinct rows per window       = 8,000
  Coalescing ratio               = 15,000 / 8,000 = 1.875× from merging
  DB statements per second       = 1 statement / 5 s = 0.2 statements/s
  DB rows per second             = 8,000 / 5 = 1,600 rows/s, BATCHED
                                   = 1,600 / 40,000 = 4% of batch capacity

  Reduction in DB STATEMENTS     = 3,000 / 0.2 = 15,000×
  Reduction in DB ROW-WRITES     = 3,000 / 1,600 = 1.875×
  Reduction in effective load    ≈ 3,000/2,000 (150% of random capacity)
                                   → 1,600/40,000 (4% of batch capacity)
                                   ≈ 37× more headroom
```

**The data-loss window:**

```
loss_window_items = flush_interval × write_rate
                  = 5 s × 3,000 /s
                  = 15,000 increments

Assume the Redis node suffers an unplanned restart twice a year, and the
flusher process crashes four times a year:
  incidents/year     = 6
  lost increments/yr = 6 × 15,000 = 90,000
  total increments/yr= 3,000 × 31,536,000 = 94.6 billion
  fraction lost      = 90,000 / 94.6e9 = 0.000095%
```

A 0.000095 % error on a cosmetic view counter is clearly acceptable. **State the number, then decide** — that is the discipline. Reducing the flush interval to 1 s cuts the loss window to 3,000 items but raises DB statements 5× and coalescing drops from 1.875× to roughly 1.3×, because fewer repeat views land in a shorter window.

### Part D — The same arithmetic applied to orders

**Assumptions:** 30 orders/second at peak; identical 5 s flush; same 6 incidents/year.

```
loss_window_items = 5 × 30 = 150 orders per incident
lost orders/year  = 6 × 150 = 900 orders
average order     = £28
revenue lost      = 900 × £28 = £25,200/year

But the real cost is not £25,200. It is 900 customers who were shown an
order confirmation, were charged (the payment gateway is a separate
system that DID record it), and whose order does not exist. Each one is a
support ticket, a refund, a chargeback risk, and a trust loss.
```

```
Latency saving offered: 4.10 ms → 0.50 ms = 3.6 ms per order
Total annual latency saved = 30/s × 31.5 M s × 3.6 ms = 3,400 seconds/year
                           = under one hour of aggregate user time.
```

**Trading 900 lost orders for 57 minutes of aggregate saved latency is obviously wrong, and the arithmetic makes it obvious.** The identical pattern that was correct for view counters is indefensible here, and nothing about the pattern changed — only the value of the data. This is why "which pattern?" can never be answered without "what is this data worth?"

### Part E — Read-through miss coalescing during a hot-key expiry

**Assumptions:** one key at 5,000 reads/second; it expires; database query 6 ms; no coalescing versus perfect coalescing.

```
WITHOUT coalescing (naive cache-aside)
  Concurrent requests in the 6 ms miss window = 5,000 × 0.006 = 30
  ... but each of those 30 also misses and issues its own query, and the
  queries themselves now queue. At 5,000/s sustained for a 6 ms window
  that lengthens under load to, say, 60 ms:
  Queries issued = 5,000 × 0.060 = 300 concurrent identical queries
  against a connection pool of 50 ⇒ pool exhausted ⇒ EVERY other query
  in the application also waits. One expired key degrades the whole service.

WITH coalescing (read-through single-flight)
  Queries issued = 1
  Other 299 requests wait on the in-process event and are served from the
  single result.
  Database load from this event = 1 query instead of 300 = 300× reduction,
  and the connection pool is untouched.
```

## 10. Trade-offs

**Cache-aside**
- *Advantages:* the cache sits beside the correctness path, so a cache failure degrades latency only; caches exactly the working set; arbitrary value shapes; works with any cache and any database; no integration layer.
- *Disadvantages:* the miss protocol is duplicated at every call site and drifts; invalidation is the application's responsibility at every write site and gets forgotten; the Section 4.6 race is possible; no built-in miss coalescing, so hot-key expiry causes a stampede you must handle yourself; every key's first read is a miss.
- *Operational cost:* low to start, and it accrues quietly as call sites multiply.
- *Unnecessary complexity when:* there is exactly one call site and one write site, where the duplication argument for read-through does not apply.

**Read-through**
- *Advantages:* one loader instead of N call sites; free miss coalescing (Part E: 300× database reduction on a hot-key expiry); consistent TTLs and serialisation by construction.
- *Disadvantages:* the cache is now in the correctness path — a failing loader fails the request unless you code a fallback; the value shape is tied to the loader; usually requires a specific library or cache product.
- *Operational cost:* moderate; the fallback-on-loader-failure path is the part teams forget.
- *Unnecessary when:* cached values are composites of many tables, where the loader abstraction fights you.

**Write-through**
- *Advantages:* the cache is never stale for keys written through it; read-your-writes is guaranteed; the post-write read is a guaranteed hit (Part A: 1.89× on write-then-read); no invalidation race on those keys.
- *Disadvantages:* 2× write amplification; fills the cache with data that may never be read; write latency is the sum of both writes; **two systems with no shared transaction**, so partial failure must be handled explicitly and in the right order.
- *Operational cost:* moderate; the ordering rule (DB first, then cache, delete on cache failure) must be enforced at every write site or you have reintroduced the problem.
- *Unnecessary when:* written data is rarely read — order rows, audit logs, event records.

**Write-behind**
- *Advantages:* the lowest achievable write latency (Part A: 9× faster); enormous write coalescing (Part C: 37× more database headroom); converts random single-row contention into sequential batched work.
- *Disadvantages:* **acknowledged writes can be lost**; the database stops being the real-time source of truth, which silently affects every downstream reader, replica and CDC consumer; failed flushes cannot be reported to a caller who left minutes ago; ordering guarantees across keys are weak; it is the hardest pattern to reason about during an incident.
- *Operational cost:* high. You need buffer-depth metrics, a dead-letter path, flush-failure alerting, and a documented, agreed loss window.
- *Unnecessary and dangerous when:* the data has any individual value. Part D: the same code that loses 15,000 harmless view counts loses 900 orders a year.

**Refresh-ahead**
- *Advantages:* removes the miss penalty from the critical path for hot keys; approaches a 100 % hit ratio where it matters most; prevents hot-key expiry stampedes before they start.
- *Disadvantages:* it is prediction, so it wastes database queries on keys that are never read again; slightly increases average staleness; needs a maintained list of which keys qualify.
- *Operational cost:* low for a small key set, bad if applied globally — a refresh-ahead policy over a million-key space can generate more database load than having no cache at all.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| Cache-aside race: concurrent read-miss and write poison an entry | A stale price or stock level for a full TTL; with repeated refreshes the value appears to flip between old and new | Extremely hard: nothing errors. A canary comparing cache against the database for hot keys; customer reports clustered around a known write event | Nothing self-heals before the TTL. Prevent with delayed double delete (600× smaller window) or versioned keys (eliminates it) |
| A write path forgets to invalidate (bulk importer, migration, manual `psql`) | Stale data for a full TTL on every row that job touched, with no alert anywhere | Only by comparing cache to database, or by customer reports | Flush the affected key prefix; structurally, move invalidation to CDC (**Phase 4, Topic 22**) so the commit itself triggers it |
| Write-through: database write succeeds, cache write fails | Next read is a miss — slower, but correct | Cache error-rate metric; a dip in hit ratio | Self-heals on the next read. Always `DEL` on cache-write failure rather than leaving the old entry |
| Write-through: cache write succeeds, database write fails | **The cache serves a value that was never committed**, as truth, until TTL | Divergence between cache and database; often found only when a second system disagrees | Enforce DB-first ordering so this cannot happen; if it has, flush the key prefix immediately |
| Write-behind: cache dies with unflushed data | A confirmed action silently never happened. For counters: a number is slightly low. For orders: the customer was charged and has no order | Buffer-depth metric going to zero at the same moment as a restart; downstream reconciliation against the payment provider | **Unrecoverable** — the data never existed outside volatile memory. Only prevention works: bound the window, or do not use the pattern for valuable data |
| Write-behind: flush fails (constraint violation, DB down) | Nothing visible; the write silently never lands | Flush-failure counter and alert; dead-letter queue depth | Replay from the dead-letter queue; requires idempotent application of deltas |
| Write-behind: buffer grows without bound while the database is down | Rising memory until Redis hits `maxmemory` and starts **evicting unflushed writes** | Redis `evicted_keys` rising on keys that hold pending writes — a particularly nasty signal because eviction looks routine | Cap the buffer and shed or block writes when full; never let a cache's eviction policy silently discard durable-looking data |
| Read-through loader throws | The request fails, rather than falling back to the database | Error rate on `cache.get`; the correlation with cache health is obvious once you look | Catch in the loader and fall back to a direct database read; decide this explicitly rather than inheriting the library's default |
| Hot key expires with no coalescing | A latency spike every TTL period, like clockwork; at worst, connection-pool exhaustion takes the whole service down (Part E) | Periodic p99 spikes aligned to TTL boundaries; database query count spiking on one statement | Single-flight/coalescing, refresh-ahead on that key, and TTL jitter |
| Refresh-ahead applied to the whole key space | No user-visible symptom until the database saturates from refreshing keys nobody reads | Database read load far exceeding user traffic; a high ratio of refreshes to reads | Restrict refresh-ahead to an explicit hot-key allowlist |
| Delayed double delete fires before the racing reader's `SET` | The stale entry survives the full TTL despite the mitigation being "in place" | Same as the first row — very hard; this is the residual 0.1 % from Part B | TTL backstop; move to versioned keys for keys where this matters |
| Cache cleared manually during an incident | A large latency spike and a database load surge as everything repopulates at once | Hit ratio at zero; database QPS jumping by `1/(1−h)` | Repopulate progressively, or warm the hot set before admitting traffic. Never flush a whole cache at peak |

## 12. Alternatives

| Approach | Who writes to the cache | Consistency guarantee | Failure behaviour | Choose it when |
|---|---|---|---|---|
| **Cache-aside** | Application, on read | Stale up to one TTL; race possible | Cache down ⇒ slower, still correct | **Default.** Read-heavy, unpredictable keys, staleness tolerable |
| **Read-through** | The cache library, on miss | Same as cache-aside | Loader failure ⇒ request failure unless you add a fallback | Many call sites; hot keys needing miss coalescing |
| **Write-through** | Application, on write | Never stale for written keys; read-your-writes | Partial write ⇒ stale or phantom entry depending on order | Read-after-write dominant (carts, profiles, drafts) |
| **Write-behind** | Application, on write; flusher persists | Database lags by up to one flush interval | **Acknowledged writes can be lost** | High-frequency, aggregatable, low-value writes |
| **Refresh-ahead** | Background refresher, before expiry | Slightly staler on average, far fewer misses | Refresh failure ⇒ a normal miss | A small, known-hot key set |
| **CDC-driven invalidation** | A consumer of the database's replication log | Bounded by pipeline lag (10–500 ms); cannot be forgotten by any write path | Pipeline lag or outage ⇒ staleness grows; TTL is the backstop | Many independent write paths, including jobs you do not own (**Phase 4, Topic 22**) |
| **Write-around** | Nobody — writes bypass the cache entirely, which is invalidated | Same as cache-aside | Cache down ⇒ slower, still correct | Write-heavy data that is rarely read afterwards (logs, events, orders) |
| **TTL only, no invalidation** | Application, on read | Stale up to one TTL, always | Nothing to fail | Staleness is genuinely harmless and simplicity is worth more than freshness |
| **Materialised view** | The database, on refresh | Stale up to the refresh interval; transactionally consistent at refresh | Refresh failure ⇒ growing staleness, visible in SQL | Expensive aggregations with small results (Lesson 27) |
| **Read replicas** | Nobody — no cache at all | Stale by replication lag (typically 10–500 ms) | Replica down ⇒ failover; no invalidation logic to get wrong | You want read scaling with full SQL and no invalidation code — **Phase 3, Topic 2 — Read replicas** |
| **No cache** | — | Always correct | Nothing to be wrong | Writes dominate reads, or a single stale read is unacceptable (the hospital example) |

**Write-around** deserves a note because it is frequently the right answer and is rarely named. It is cache-aside's write side taken literally: writes go straight to the database and simply invalidate the cache, never populating it. BookNook's orders use exactly this. It avoids write-through's amplification *and* avoids caching data nobody will read, at the cost of making the first read after a write a guaranteed miss — which is fine precisely when that read usually never comes.

## 13. When to use it

1. **Use cache-aside as the default for read-heavy data.** It is the only pattern in which the cache is not in the correctness path, so a Redis outage costs latency rather than correctness. Pair it with `DEL`-on-write, a jittered TTL, and a deliberate choice of race mitigation.
2. **Use read-through when the same loading logic appears at many call sites, or when a hot key's expiry causes a stampede.** Part E measured a 300× database reduction from coalescing alone on a single hot-key expiry, and 60 duplicated miss protocols is a guaranteed source of drift.
3. **Use write-through when a read reliably follows the write** — shopping carts, user profiles, draft documents, anything a user edits and then immediately views. It guarantees read-your-writes and converts the next read from a guaranteed miss into a guaranteed hit (Part A: 1.89×).
4. **Use write-behind for high-frequency, aggregatable, low-value writes** — view counts, like counts, last-seen timestamps, telemetry — *after* you have computed the loss window and written it down. Part C: 37× more database headroom for a 0.000095 % loss rate.
5. **Use refresh-ahead on a small, explicit allowlist of extremely hot keys** — the feature-flag bundle, the homepage carousel, the top-N list. Never globally.
6. **Use CDC-driven invalidation once more than one codebase writes to the same tables.** The bulk-importer incident in Section 7(f) is near-inevitable at that point, and application-level invalidation cannot prevent it structurally (**Phase 4, Topic 22**).
7. **Use write-around for data that is written often and read rarely** — orders, audit records, event logs. It avoids write-through's amplification and avoids filling the cache with data nobody will request.

## 14. When *not* to use it

1. **Do not use write-behind for anything a user would file a support ticket about losing.** Orders, payments, medication administrations, booking confirmations. Part D: 900 lost orders per year to save 57 minutes of aggregate latency. The pattern is not "risky" in the abstract — it has a specific, computable loss window, and for this class of data no window is small enough.
2. **Do not use write-through for data that is written far more often than it is read.** You pay 2× write amplification to populate entries nobody will request. Use write-around and let reads populate the cache lazily.
3. **Do not use read-through without an explicit decision about loader failure.** The default in most libraries is to propagate the exception, which converts a cache problem into a user-facing error — losing cache-aside's single most valuable property.
4. **Do not apply refresh-ahead to a large key space.** Refreshing a million keys that are read once a day generates more database load than having no cache, with no user-visible benefit.
5. **Do not rely on a delayed double delete as a guarantee.** It is probabilistic. Part B: roughly 10 residual full-TTL staleness events per day remain. If an entry being stale is a business or safety error, use versioned keys or do not cache the value at all.
6. **Do not cache on the write path at all when a single stale read is unbounded in cost** — the hospital medication order in Section 7. Here the Lesson 26 latency formula is simply not the relevant criterion.

## 15. Common mistakes

**Beginner mistakes**
- Updating the cache with the new value on a write (`SET`) instead of deleting it. Two concurrent writers can then land in the opposite order to their commits, leaving the older value cached permanently.
- Writing the cache before the database in write-through, producing a cached value that was never committed — the worst of the two partial-failure outcomes.
- Believing a cache invalidation "ran, so the cache is correct". The Section 4.6 timeline shows an invalidation that ran correctly and left the cache stale anyway, because it ran too early.
- Omitting the TTL because "we invalidate on write". Any missed invalidation then means permanent staleness with no backstop.
- Not jittering TTLs, so a cold-start wave expires in one synchronised second, every time, forever.
- Assuming a cache miss is free. The wasted round trip is paid on every miss and appears in `L_miss`.
- Using write-behind because it benchmarked fastest, without computing `flush_interval × write_rate`.
- Caching a value and never thinking about who deletes it. Every cache entry needs an owner for its invalidation.
- Flushing the entire cache during an incident at peak traffic, converting a small problem into a database overload.

**Experienced-engineer mistakes**
- Implementing cache-aside without miss coalescing and discovering it only when a hot key expires at peak and exhausts the connection pool (Part E).
- Treating the delayed double delete as a fix rather than a 600× window reduction with a measurable residual (Part B: ~10 full-TTL events per day).
- Adopting versioned keys without `allkeys-lru`, so orphaned versions accumulate and eventually hit `maxmemory` under `noeviction` — turning a correctness improvement into an outage.
- Building write-behind without a dead-letter path, so flush failures are silent; the caller left minutes ago and there is no one to tell.
- Forgetting that write-behind makes the database stale for everyone — reporting jobs, read replicas, CDC consumers and analytics all now read data that lags by a flush interval, and none of them were consulted.
- Letting a write-behind buffer live in a Redis instance with an LRU eviction policy, so unflushed durable-looking writes are silently evicted under memory pressure. Pending writes must not share an eviction domain with disposable cache entries.
- Adding CDC invalidation and not monitoring pipeline lag, so staleness quietly grows under exactly the load where it matters most.
- Mixing patterns per key without documenting which key uses which, so the next engineer debugging a stale value cannot tell whether an invalidation was supposed to happen.
- Choosing write-through for everything "for consistency" and doubling write load on tables whose rows are read once a year.
- Assuming read-through gives you invalidation. It governs the miss path only; writes still need write-through, write-around or explicit invalidation.

## 16. Interview perspective

This topic is where an interviewer finds out whether you have run a cache or only drawn one. Everybody can say "cache-aside". Very few can state its race condition, and almost nobody volunteers the write-behind loss-window arithmetic.

**How to use it:**

1. **Name the pattern and justify the default.** *"Cache-aside, because it's the only pattern where the cache isn't in the correctness path — if Redis dies we get slower, not wrong."* That is the strongest single sentence available on this topic.
2. **Say `DEL`, not `SET`, and explain why.** *"On a write I delete rather than update, because two concurrent writers that both `SET` can land in the opposite order to their commits and leave the old value cached forever. `DEL` is idempotent and convergent."*
3. **Volunteer the race condition unprompted.** Walk the timeline: reader misses, reader queries, writer commits, writer deletes nothing, reader caches the pre-commit value. Then give the mitigation ladder — delete-not-update, delayed double delete, versioned keys, CDC — and be explicit about which ones are probabilistic and which are deterministic.
4. **Quantify the write-behind loss window out loud.** *"Five-second flush at 3,000 writes/second is 15,000 items at risk. For view counts that's fine. For orders that's 150 per incident, so orders go straight to the database."* Showing the same arithmetic reaching opposite conclusions is exactly what senior judgement looks like.
5. **Use write-through where read-after-write is the access pattern.** *"The cart is written then immediately read, so write-through — it makes that read a guaranteed hit and guarantees read-your-writes."*
6. **Mention miss coalescing.** *"A hot key expiring at 5,000 RPS issues hundreds of identical queries and can exhaust the connection pool. Read-through gives me single-flight for free; with cache-aside I'd build it."*
7. **Forward-reference CDC correctly.** *"Long-term I'd invalidate from the replication log, because then a bulk importer or a manual SQL change can't bypass it — that's transactional outbox / change data capture territory."*
8. **Always mention the TTL backstop.** *"Whatever the invalidation strategy, every entry gets a TTL, so a missed invalidation is bounded instead of permanent."*

**Red flags interviewers watch for:**
- Describing cache-aside and showing no awareness that a concurrent read and write can poison the cache.
- Updating the cache with the new value on write and not recognising the writer/writer ordering problem.
- Proposing write-behind for orders, payments or bookings — or using it anywhere without naming the loss window.
- Writing the cache before the database in write-through.
- Saying "we'll invalidate on write" without asking how many write paths exist, or who else writes to that table.
- No TTL, on the grounds that invalidation is handled.
- Not knowing what happens when the cache is down — no fallback, no timeout, no capacity plan for the database absorbing `1/(1−h)` times its normal read load.
- Treating the delayed double delete as a guarantee rather than a window reduction.
- Claiming read-through solves write consistency.
- Being unable to say what the next read after a write does under each pattern (miss under cache-aside, hit under write-through).

## 17. Knowledge check

Write your answers down before continuing.

**Conceptual**

1. Explain, with a step-by-step timeline, how a concurrent read and write under cache-aside can leave the cache holding a value the database has not held since before the write committed — even though the writer's invalidation executed successfully. Then explain why reversing the writer's order (delete first, then update) does **not** fix it, and why updating the cache instead of deleting it makes a *different* race worse.

2. Cache-aside and write-through have identical write latency in Section 9 Part A (4.50 ms each). State precisely what you are buying when you choose write-through, what you pay for it, and name two kinds of data where that trade is clearly worth it and two where it is clearly not. Define read-your-writes consistency in your answer and explain how each pattern treats it.

3. Compare the delayed double delete and versioned keys as mitigations for the cache-aside race. State which one is probabilistic and which is deterministic, explain exactly what residual failure survives the probabilistic one, and describe two practical constraints that make versioned keys harder to adopt than their stronger guarantee suggests.

**Practical scenario**

4. BookNook's engineering team proposes moving order creation to write-behind with a 2-second flush interval, arguing that checkout latency drops from 180 ms to 30 ms and that a 2-second window is "basically nothing". Peak order rate is 30/second; the payment gateway is a separate external system that records the charge independently and synchronously. Describe exactly what a customer experiences if Redis restarts during that 2-second window; explain why the payment gateway being a separate system makes the failure materially worse than simply "losing an order"; quantify the expected annual impact given 6 cache-process incidents per year; state what you would propose instead for the latency problem they are actually trying to solve; and identify one piece of BookNook data for which this same pattern and flush interval *would* be appropriate, with justification.

**Calculation**

5. BookNook caches author pages. Assumptions: one author key receives 1,200 reads/second; TTL 600 s with jitter; eviction and expiry together produce 3 miss events per second for that key; during a "new release" campaign the author row is written 4 times per second by a metadata sync job; the vulnerable window between a reader's database read returning and its `SET` landing is 8 ms; a Redis hit is 0.43 ms; a miss costs 0.40 ms of Redis plus a 7 ms database read plus a 0.40 ms repopulate; 0.2 % of database queries exceed 600 ms.
   (a) What is the probability that any given miss is poisoned by a concurrent write, and how many poisoning events occur per hour?
   (b) With no mitigation, how many stale reads does a single poisoning event produce, and how many stale reads per hour does the key serve in total?
   (c) With a delayed double delete at 600 ms, how many stale reads does a single event produce, what is the reduction factor, and how many poisoning events per hour still survive the mitigation and last a full TTL?
   (d) Using `L_eff = h × L_hit + (1 − h) × L_miss`, compute the effective read latency for this key at the implied hit ratio (derive `h` from 3 misses/second against 1,200 reads/second), and state how much of that effective latency is contributed by misses versus hits.
   Show every step and state any additional assumptions you make.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 29.

---

**Progress checklist**

Phase 1 — Foundations: **complete** (Lessons 1–17)

Phase 2 — Scaling:
`[x] 1. Vertical scaling` · `[x] 2. Horizontal scaling` · `[x] 3. Stateless application servers` · `[x] 4. Load balancers` · `[x] 5. Layer 4 versus Layer 7` · `[x] 6. Load-balancing strategies` · `[x] 7. Health checks and failover` · `[x] 8. Reverse proxies and API gateways` · `[x] 9. Caching fundamentals` · `[x] 10. Browser, application, database, and distributed caches` · `[x] 11. Cache-aside, read-through, write-through, and write-behind`
`[ ] 12. Cache invalidation and TTL` ← next
`[ ] 13–22` not started

Phases 3–5: not started.

**NEXT LESSON: Phase 2, Topic 12 — Cache invalidation and TTL**







# Lesson 29 — Cache Invalidation and TTL

*Phase 2, Topic 12*

## 1. Simple definition

**Cache invalidation** is the act of making a cached copy stop being used, because the underlying source of truth has changed and the copy is now wrong.

**TTL** (*time to live*) is the simplest form of invalidation: when you write an entry into the cache you also stamp it with a lifetime, and when that lifetime elapses the entry is treated as if it were never there.

Terms defined before use:
- **Source of truth**: the one system whose copy is authoritative. For BookNook that is PostgreSQL. Everything else — Redis, the reverse proxy cache (Lesson 25), the browser, a CDN — holds *derived copies*.
- **Stale**: a cached value that no longer matches the source of truth. Staleness is a *property of time*, not of correctness in the abstract — a value is stale from the instant the database changed until the instant the cache is corrected.
- **Staleness window**: the maximum duration a user can observe a stale value. This is the number you actually design against.
- **Invalidate**: remove the entry. The next read misses and repopulates.
- **Update in place** (also called *refresh* or *write-through*, Lesson 28): overwrite the entry with the new value so the next read hits.
- **Expiry**: invalidation caused by the passage of time rather than by an event.

Lesson 27 covered *where* caches live; Lesson 28 covered *how data gets into them* (cache-aside, read-through, write-through, write-behind). This lesson covers the harder question: **how data gets out of them at the right moment.**

## 2. Why it exists

Phil Karlton's line — *"There are only two hard things in Computer Science: cache invalidation and naming things"* — is quoted constantly and explained rarely. It is worth being precise about *why* invalidation is hard, because the reason is structural, not a matter of effort.

A cache is, by definition, a **second copy of data that is not updated by the same transaction that updates the first copy.** That single sentence contains the whole problem:

1. **There is no transaction spanning both stores.** PostgreSQL gives you atomicity for the row. Redis is a different process on a different machine with its own failure modes. You cannot commit to both atomically without a distributed transaction protocol (two-phase commit, which we cover in **Phase 3, Topic 14 — Distributed transactions and sagas**), and nobody runs 2PC between a database and a cache because it would erase the entire latency benefit that justified the cache.
2. **The cache does not know when the database changed.** A row can be updated by your API, by a background job, by a data-migration script, by a DBA running `UPDATE`, by a replication stream from another region, or by a trigger. Each of those paths must *remember* to tell the cache. Every forgotten path is a permanently wrong cache entry.
3. **There are many caches, not one.** BookNook's price lives simultaneously in PostgreSQL, Redis, the nginx proxy cache, a CDN edge in Frankfurt, and 40,000 browsers. You control the first three. You do not control the last two in any immediate sense. "Invalidate the cache" is therefore never a single action.
4. **Invalidation is not idempotent in time.** Deleting a key and then having a slow in-flight read write the *old* value back afterwards is a real, common race. The delete "succeeded" and the cache is still wrong.
5. **You cannot test your way out of it.** Staleness bugs are timing-dependent, usually invisible in development (one process, empty cache), and surface as rare customer complaints: "it showed me the old price."

So invalidation exists because caches exist, and it is hard because **correctness now depends on coordinating two systems that were deliberately decoupled for performance.** TTL exists because it is the one invalidation mechanism that requires *no* coordination at all: the cache expires the entry by itself, using only a local clock. That is why TTL is the default everywhere, and why understanding how to choose a TTL is the most immediately useful skill in this lesson.

## 3. Real-world analogy

**A printed supermarket shelf label.**

The till (the database) knows the real price. The shelf label (the cache) is a copy printed at some earlier moment. The store has four ways to keep labels honest, and they map exactly onto the four strategies in Section 4:

- **TTL**: every label is reprinted every Monday regardless of whether anything changed. Cheap, requires no communication, and guarantees a label is at most seven days wrong.
- **Explicit invalidation**: when head office changes a price, they phone the store and a staff member walks to that shelf and pulls the label. Fast and precise — if the phone call happens, and if the staff member finds every shelf that carries that product.
- **Event-driven invalidation**: the till system is wired to a printer that automatically reprints a label the moment a price changes in the central system. No human has to remember. But now you depend on the wiring.
- **Versioned keys / namespace bump**: instead of removing labels, the store changes the shelf-edge colour from yellow to green overnight and prints all new labels in green. Staff are told "ignore yellow labels". Every old label is invalidated instantly, with no walking at all — but the yellow labels physically remain on the shelves taking up space until someone clears them.

And **negative caching** is the "we do not stock this" sign: it saves a staff member from walking to the stockroom every time a customer asks for an item the store has never carried.

**Where the analogy breaks down:**

| Supermarket shelf label | Cache entry |
|---|---|
| One label per product, in one known place | The same value is cached in Redis, in nginx, at the CDN edge, and in thousands of browsers simultaneously. There is no single label to pull. |
| A customer who reads a stale label and is charged the real price at the till notices immediately | A user served a stale API response has no way to know it is stale; there is no "till" that corrects it |
| Pulling a label takes a staff member 30 seconds and the job is definitively done | A cache delete takes 0.3 ms but may be silently undone milliseconds later by a slow in-flight read writing the old value back |
| Head office knows every store | Your application does not know every cache. Browsers hold copies for as long as `Cache-Control: max-age` said, and you cannot reach them. |
| Reprinting a label costs nothing to other customers | Mass expiry at one instant sends every request to the database at once — the *act of invalidating* can take the system down (Lesson 30) |
| If a label is missing, the customer asks staff | If a cache entry is missing, 4,000 concurrent requests all ask the database in the same 300 ms |

The last two rows are the bridge into Lesson 30. Invalidation is not free: **every invalidation is a scheduled future load spike on the source of truth**, and sizing that spike is part of choosing an invalidation strategy.

## 4. Detailed technical explanation

### 4.1 The four mechanisms

| Mechanism | Trigger | Staleness window | Coordination required |
|---|---|---|---|
| **TTL expiry** | Clock | Up to the full TTL | None |
| **Explicit invalidation** | Your write path calls `DEL` | ~1 ms, *if the path is covered* | Every writer must remember |
| **Event-driven invalidation** | A change feed / CDC stream | ~10 ms – 2 s (pipeline lag) | A streaming pipeline |
| **Versioned keys** | Version counter bump | ~1 ms, for a whole class of keys | A version read per request |

#### TTL-based expiry

You attach a lifetime to the entry at write time. Redis: `SET key value EX 300`. The cache enforces it; your application contributes nothing beyond choosing the number.

Two implementation details matter in production:

- **Lazy versus active expiry.** Redis does *not* scan all keys continuously looking for expired ones — that would be O(N) forever. It uses **lazy expiry** (when a key is read, check its TTL; if expired, delete and report a miss) plus **active expiry** (a background cycle samples 20 random keys with TTLs set, deletes the expired ones, and repeats if more than 25 % were expired). The consequence: **an expired key can continue to occupy memory for some time after it logically expired.** Your memory accounting must assume expired-but-not-yet-reclaimed entries exist. It never returns a stale value, but it may hold stale bytes.
- **TTL is wall-clock based and therefore clock-sensitive.** Expiry across a replicated Redis setup is driven by the primary, which sends explicit `DEL` to replicas, precisely so that clock skew between nodes cannot cause a replica to serve or expire differently.

#### Explicit invalidation (delete-on-write)

Your write path does: update the database, then delete the cache key.

```
UPDATE books SET price = 12.99 WHERE isbn = '978...';   -- source of truth
DEL book:978...                                          -- cache correction
```

Note it is **delete**, not **update**. Section 4.4 explains why that choice is load-bearing.

#### Event-driven invalidation (change data capture)

Instead of trusting each writer to remember, you read the database's own replication log — PostgreSQL's **write-ahead log** (WAL) via logical decoding, MySQL's binlog — with a tool such as Debezium, and emit an invalidation for every row that changes, from any source whatsoever.

This is the only mechanism that is **structurally complete**: a DBA's manual `UPDATE` produces a WAL record just like your API's does, so it invalidates correctly. The price is an extra always-on pipeline (Kafka or similar — **Phase 4, Topic 5 — Message queues and brokers**) and an invalidation lag of typically 10 ms–2 s.

#### Versioned keys and namespace bumping

Instead of deleting entries, make the *key* contain a version, and change the version:

```
key = "v7:book:9780143127741"
```

To invalidate every book at once, bump a counter: `v7 → v8`. All `v7:` keys instantly become unreachable; nothing reads them again. This is **O(1) mass invalidation** — one write, regardless of whether you have 50 keys or 50 million.

Two things to be clear about, because this is a favourite interview topic:

- The old entries are **not deleted**. They sit in memory until their TTL expires or the eviction policy reclaims them (Lesson 30, Section 4.6). So a version bump temporarily roughly *doubles* memory for that namespace. **Versioned keys always need a TTL as well**, otherwise you leak memory permanently.
- Reading the version adds a round trip unless you cache it. The standard pattern is to cache the version number in process memory for a few seconds (Lesson 27's L1/L2 hierarchy), which means your bump takes effect after that short local TTL, not instantly.

The same trick at a sub-tree level is **namespace bumping**: `book:9780143127741:reviews:v{n}` where `n` lives at `book:9780143127741:reviews_version`. Bumping it invalidates all pages, sort orders and filters of that book's reviews — potentially hundreds of derived keys — with one `INCR`.

A related variant is **content-addressed keys**, where the key includes a hash or a timestamp of the source data (`book:978...:price:1739214000`). Nothing ever needs invalidating because a changed value produces a different key. This is exactly how static asset fingerprinting works (`app.9f3c2a.js`), and it is why you can safely serve those files with `Cache-Control: max-age=31536000, immutable`.

### 4.2 Choosing a TTL from staleness tolerance

The only correct way to choose a TTL is to answer a business question first:

> **"For how long may a user see the old value before someone is harmed, loses money, or loses trust?"**

That number is your **staleness tolerance**. The TTL must be less than or equal to it. Then you check what that TTL costs the database (Section 4.3) and, if it is too expensive, you add explicit invalidation so the TTL only has to act as a *safety net* rather than the primary mechanism.

Applied to BookNook:

| Data | Staleness tolerance — and the business reason | TTL | Additional mechanism |
|---|---|---|---|
| **Book price** | Near zero *at checkout* — charging a price different from the one displayed is a consumer-law problem and a chargeback risk. But on a browse page, a few seconds is harmless. | 300 s on the browse path | **Delete-on-write** on every price change, plus a **price re-read from PostgreSQL inside the checkout transaction**. The cache is never trusted for money. |
| **Stock count** | Seconds. Overselling the last copy creates a cancellation email, a refund, and a support ticket. Under-selling (showing "out of stock" when one exists) loses a sale. | 10 s, and display it as a band ("In stock", "Only a few left") rather than an exact number | Authoritative decrement via a conditional `UPDATE ... WHERE stock > 0` at order time. The cache informs; it does not reserve. |
| **Reviews list** | Minutes. A reviewer not seeing their own review immediately is the only real complaint, and that is solved differently. | 600 s | **Namespace bump** on the book's review version when a review is posted or deleted; plus read-your-own-writes by reading from the primary for 5 s after the author posts (Lesson 28). |
| **Homepage / bestsellers** | Tens of minutes. The list is editorially curated and recomputed by a batch job hourly; nobody can tell if it is 12 minutes old. | 900 s | `stale-while-revalidate` so a user never waits for the recompute, and the list is explicitly purged when merchandising publishes a campaign. |
| **User profile (name, address book)** | Near zero *for the owning user* — a user who edits their shipping address and sees the old one assumes the save failed and does it again, generating duplicates. Other users never see it. | 1800 s | **Delete-on-write keyed by user ID.** Single-writer data is the easiest case in all of caching: exactly one actor can change it, and that actor is in your request path. |
| **Non-existent ISBN** (negative entry) | Minutes — but bounded, because a book genuinely can be added. | 60 s | Deleted explicitly when a book with that ISBN is created. |

Three principles are visible in that table and are worth stating explicitly:

1. **Staleness tolerance is per-field, not per-entity.** A book's price and its cover-image URL live in the same row and have tolerances three orders of magnitude apart. If you cache the whole book object under one key with one TTL, you are forced to use the *strictest* field's TTL for all of it. Splitting hot-and-volatile fields into their own keys is often the single highest-leverage change available.
2. **Money and inventory are never served from cache at the decision point.** Cache them for *display*; re-read them inside the transaction that commits. This is not a caching rule, it is a correctness rule, and interviewers specifically listen for it.
3. **TTL is the backstop, not the plan.** Where you have explicit invalidation, the TTL exists to limit the damage when invalidation is missed — a dropped message, a crashed worker, a code path someone forgot. Setting an infinite TTL because "we always invalidate explicitly" means a single missed invalidation is wrong *forever*.

### 4.3 The TTL-versus-database-load trade-off

This is a calculation, not a judgement call. For a set of keys that are read frequently enough that each one is read at least once per TTL period:

```
expiry-driven misses per second  ≈  number of distinct cached keys / TTL in seconds
```

Why: each key must be refetched once per TTL, and refetches spread out over time. This *only* holds for keys hot enough to be re-read before they expire; cold keys miss on access frequency instead, not on TTL.

Doubling the TTL halves the expiry-driven database load. Halving the TTL doubles it. That is the entire trade-off, and it is linear and predictable, which is why it belongs in a capacity plan. Section 9 works it through with BookNook's real numbers.

The important and non-obvious consequence: **beyond a certain point, raising the TTL buys almost nothing.** Going from 60 s to 600 s removes 90 % of expiry misses. Going from 600 s to 6,000 s removes 90 % of the remaining 10 % — that is, 9 % more — while making your data ten times staler. The curve is `1/TTL`; the returns collapse fast. Most systems are correctly tuned somewhere in the 30 s–900 s range, and TTLs of hours are usually a sign that someone is using TTL where they should be using explicit invalidation.

### 4.4 Delete-on-write versus write-through

Lesson 28 introduced write-through (update the database and the cache together). On a write you have exactly three options:

| Option | Action on write | Next read | Risk |
|---|---|---|---|
| **Delete-on-write** (invalidate) | `UPDATE db` → `DEL key` | Miss → repopulate from DB | One extra miss per write; stampede risk on hot keys |
| **Write-through** (update) | `UPDATE db` → `SET key = new` | Hit | **Writes a value the cache may not be entitled to write** (see below) |
| **Write-around** | `UPDATE db` only | Stale until TTL | Simple; staleness bounded only by TTL |

**Delete is the safer default, and here is the precise reason.** With concurrent writers, `SET` races are *lost-update* races on the cache:

```
t0  Writer A: UPDATE db SET price = 10      (db = 10)
t1  Writer B: UPDATE db SET price = 12      (db = 12)
t2  Writer B: SET cache price = 12          (cache = 12  ✓)
t3  Writer A: SET cache price = 10          (cache = 10  ✗ WRONG, and permanent)
```

Writer A was slower between its two steps and overwrote a newer value. The cache now disagrees with the database until the TTL expires — and if there is no TTL, forever. With delete-on-write the same interleaving produces two `DEL`s, and the worst outcome is an extra miss:

```
t2  Writer B: DEL key     (empty)
t3  Writer A: DEL key     (empty — harmless)
t4  Reader:   miss → SELECT → price = 12 ✓
```

Deletes are **idempotent and order-insensitive**; writes are neither. That is the whole argument, and it generalises far beyond caching.

Write-through is still correct and valuable when (a) there is effectively a single writer per key, (b) the key is hot enough that the post-invalidation miss is expensive, and (c) the value you write is exactly what a read would have produced. Condition (c) fails more often than people expect: if the cached object is a *composed* view (book + author + average rating), the writer of the price does not have the other fields, and write-through would require it to recompose the whole object — effectively doing the read anyway.

### 4.5 The dual-write problem

Any time you write to two stores without a shared transaction, you have a **dual write**, and dual writes have no fully correct ordering. Both orders fail, differently:

**Order A — database first, then cache delete:**
```
1. UPDATE db           ✓ committed
2. DEL cache           ✗ Redis timeout / process crash between the lines
⇒ Cache holds the old value until TTL. Bounded staleness. Reads are wrong, writes are safe.
```

**Order B — cache delete first, then database:**
```
1. DEL cache           ✓
2. (concurrent reader) MISS → SELECT (old value) → SET cache = old
3. UPDATE db           ✓ committed
⇒ Cache holds the old value until TTL, and this race needs NO failure to occur. It is
  a normal-operation race, and it is more likely than Order A's failure.
```

So **database first, then delete** is the standard, and the residual exposure is bounded by the TTL. That is one more reason every key gets a TTL.

The remaining race in Order A is the **read-repopulate race**, which exists even with no failures at all:

```
t0  Reader R:  MISS on book:978 → issues SELECT
t1  Writer W:  UPDATE db SET price = 12.99
t2  Writer W:  DEL book:978            (key already absent — no effect)
t3  Reader R:  SELECT returns 9.99     (it read before t1, or from a replica)
t4  Reader R:  SET book:978 = 9.99     ← stale value written AFTER the delete
⇒ Cache is wrong until TTL. Nothing failed. No code is "buggy".
```

This is the race people mean when they say invalidation is hard. The mitigations, in increasing order of cost:

1. **Short TTL.** Accept the race; bound it. Correct for the overwhelming majority of systems, and the right first answer in an interview.
2. **Delayed double delete.** After the write, delete the key, wait ~500 ms (longer than a typical read), delete again. This kills a straggler repopulation. It is a heuristic, not a proof, and the delay must exceed your p99 read latency plus replication lag.
3. **Set-if-not-modified using a version.** Store `(value, db_version)` and have the reader write back only if the version it read is ≥ the cached one, using a Lua script for atomicity. Correct, but every cached object now needs a monotonic version column.
4. **CDC-driven invalidation.** The invalidation is derived from the committed WAL, so it is necessarily *after* the commit and carries the commit's ordering. This is the structurally sound fix, and the reason large systems eventually adopt it.
5. **Do not cache it.** If a field cannot tolerate any staleness and cannot tolerate a version column, the honest answer is that it is not a caching candidate. Say this out loud in interviews; it reads as judgement, not evasion.

### 4.6 Negative caching

A **negative cache entry** records that something *does not exist*, so repeated lookups for it do not reach the database.

```
GET book:9999999999999   →  miss
SELECT ... → 0 rows
SET book:9999999999999 "__NULL__" EX 60     ← cache the absence
```

Without this, every request for a non-existent key is a guaranteed database query — the cache provides *zero* protection for exactly the traffic pattern an attacker would choose. That attack is **cache penetration**, and it is Lesson 30's third topic.

Three rules:
- **Use a distinguishable sentinel**, not an empty string or `nil`. Your code must be able to tell "cached absence" from "not cached". A missing `nil` check that treats both the same reintroduces the full database load silently.
- **Give negative entries a much shorter TTL than positive ones** — typically 30–120 s against 300–900 s. The cost of a stale negative (a newly created book appearing 60 s late) is real but small; the cost of a long one is that creating a record appears broken.
- **Delete the negative entry on creation.** When BookNook adds ISBN `978…`, the write path must `DEL` the negative key, or the "not found" persists for its full TTL.

### 4.7 HTTP-level invalidation

Everything above concerns caches you operate. HTTP gives you a standard protocol for caches you *don't* fully control — browsers, proxies, CDNs (**Phase 2, Topic 14 — CDNs and edge caching**).

**Freshness directives** (`Cache-Control`) tell the client how long it may use a copy *without asking*:

```
Cache-Control: public, max-age=300, s-maxage=600, stale-while-revalidate=60, stale-if-error=86400
```

- `public` — shared caches (CDN, proxy) may store it. `private` — only the browser may.
- `max-age=300` — the browser may reuse it for 300 s with **no network request at all**.
- `s-maxage=600` — *shared* caches may use 600 s, overriding `max-age` for them. This lets you keep browsers short-lived (because you cannot purge them) while letting the CDN hold longer (because you can).
- `stale-while-revalidate=60` — for 60 s past expiry, **serve the stale copy immediately and refresh in the background**. The user never waits. This is the single most effective latency directive in HTTP caching, and it is also a stampede mitigation (Lesson 30).
- `stale-if-error=86400` — if the origin returns 5xx or is unreachable, keep serving the stale copy for up to a day. Your cache becomes a **static fallback during an outage**. BookNook's book pages stay browsable while PostgreSQL is failing over.
- `no-store` — never write it to disk or memory anywhere. The correct directive for checkout pages and anything with personal data. `no-cache` does **not** mean "do not cache"; it means "you may store it, but you must revalidate before each use." Confusing these two is the single most common HTTP caching mistake.
- `must-revalidate` — forbids serving stale on error; the opposite of `stale-if-error`.
- `immutable` — promises the body will never change at this URL; the browser skips revalidation even on reload. Only safe with fingerprinted filenames.

**Validators** let a client revalidate cheaply instead of re-downloading:

```
Response:  ETag: "a3f91c2"            ← opaque version token for this representation
           Last-Modified: Tue, 10 Feb 2026 11:04:00 GMT

Request:   If-None-Match: "a3f91c2"   ← "only send a body if the ETag changed"
           If-Modified-Since: Tue, 10 Feb 2026 11:04:00 GMT

Response:  304 Not Modified           ← ~200 bytes of headers, no body
```

A `304` on a 180 KB page costs about 200 bytes. `ETag` is exact and is preferred; `Last-Modified` has 1-second granularity and cannot represent two changes in the same second. Prefer a **strong ETag** derived from content (a hash of the serialised body) over a weak one (`W/"..."`), which only promises semantic equivalence. A detail that bites people: generating an ETag by hashing the response body means you still rendered the response, so you saved bandwidth but not CPU. Deriving the ETag from a cheap version column (`W/"book-978-v42"`) saves both.

`ETag` also gives you **conditional writes** — `If-Match: "a3f91c2"` on a `PUT` makes the update fail with `412 Precondition Failed` if someone else changed the resource first. That is optimistic concurrency control, revisited in **Phase 3, Topic 9 — Optimistic and pessimistic locking**.

**The practical pattern for BookNook:**

```
GET /books/978…           Cache-Control: public, max-age=60, s-maxage=600,
                                         stale-while-revalidate=120, stale-if-error=86400
                          ETag: "bk-978-v42"
GET /static/app.9f3c2a.js Cache-Control: public, max-age=31536000, immutable
GET /api/me               Cache-Control: private, no-store
POST /orders              Cache-Control: no-store
```

Short browser TTL (you cannot purge browsers), long CDN TTL (you *can* purge the CDN), content-fingerprinted assets cached for a year.

### 4.8 CDN purge APIs and propagation latency

A **purge** (or *invalidation*) is an API call asking a CDN to drop cached copies at its edge locations.

```
POST https://api.cdn.example/v1/purge
{ "urls": ["https://booknook.example/books/9780143127741"] }
```

Three purge types, and the differences matter:

| Purge type | What it does | Typical propagation |
|---|---|---|
| **Single URL** | Drops one object at every POP | ~1–10 s |
| **Surrogate key / cache tag** | Drops every object tagged `book-978…` — page, API response, OG image, in one call | ~1–10 s |
| **Wildcard / purge-everything** | Drops an entire path prefix or the whole property | 30 s – several minutes |

Realistic expectations, labelled **approximate and vendor-dependent**: modern CDNs advertise single-URL purges completing globally in roughly **150 ms to 5 s**, with tag-based purges similar and full purges taking **tens of seconds to minutes**. You must design for the upper end, not the advertised median, because a purge is a broadcast to hundreds of points of presence and any one of them can be slow.

Three consequences that get missed:

1. **Purge is not transactional with your database commit.** Between `COMMIT` and purge completion, edges serve the old object. If that window matters, do not rely on the CDN for that object (`s-maxage=0` or `private`).
2. **Purge-everything is a self-inflicted stampede.** Dropping a whole property sends every subsequent request to origin simultaneously — exactly Lesson 30's cache avalanche, at CDN scale. BookNook's origin sees its full uncached load. Use surrogate keys so you purge tens of objects, not millions.
3. **Browsers are not purgeable at all.** Nothing you send to the CDN reaches a copy already sitting in a user's browser with 10 minutes of `max-age` remaining. This is why browser `max-age` should be small (30–120 s) for anything that can change, with `s-maxage` carrying the long life at the shared tier. The alternative — changing the URL — is why asset fingerprinting exists.

**Soft purge** is worth knowing by name: it marks objects stale rather than deleting them, so they are served with `stale-while-revalidate` semantics while the edge refetches. It converts a purge from "instant load spike plus latency cliff" into "gradual background refresh", and it is almost always the one you want for high-traffic objects.

## 5. Request / data flow

A merchandiser changes the price of *The Overstory* from £9.99 to £12.99 in BookNook's admin tool. Here is the full path, end to end:

1. `PUT /admin/books/9780143127741 {"price": 12.99}` reaches the load balancer and is routed to app server B (Lesson 23).
2. App server B opens a PostgreSQL transaction and executes `UPDATE books SET price = 12.99, version = version + 1 WHERE isbn = '9780143127741'`.
3. The transaction **commits**. PostgreSQL is now the authoritative holder of £12.99, and the WAL record is durable. *Everything before this point is reversible; everything after it is cache correction.*
4. App server B issues `DEL v7:book:9780143127741` to Redis. Delete, not set — deletes are idempotent (Section 4.4). Elapsed: ~0.4 ms.
5. App server B issues `INCR book:9780143127741:reviews_version`? **No** — reviews did not change. Only keys whose *content* changed are touched. (A common bug is over-invalidating and destroying the hit ratio.)
6. App server B publishes a `book.updated` event carrying the ISBN and the surrogate key `book-9780143127741` to the internal event bus.
7. A purge worker consumes the event and calls the CDN purge API with the surrogate key, dropping the cached HTML page, the JSON API response, and the Open Graph image in one call.
8. A second consumer calls the nginx proxy cache's purge endpoint on each proxy node (`PURGE /books/9780143127741`), because the reverse-proxy cache (Lesson 25) is a separate cache that the CDN purge does not reach.
9. Meanwhile, a user in Berlin requests `/books/9780143127741`. Their **browser** has a copy with `max-age=60` and 22 seconds remaining, so it is served locally with **no network request**. This user sees £9.99. *Nothing in your infrastructure can prevent this* — it is why `max-age` is short.
10. 22 seconds later, the browser's copy expires. It revalidates with `If-None-Match: "bk-978-v42"`.
11. The CDN edge has been purged, so it forwards to origin. nginx has been purged, so it forwards to app server A.
12. App server A executes cache-aside (Lesson 28): `GET v7:book:9780143127741` → **miss** (deleted at step 4).
13. App server A executes `SELECT * FROM books WHERE isbn = '9780143127741'` → £12.99, `version = 43`.
14. App server A writes `SET v7:book:9780143127741 '{...12.99...}' EX 300` — repopulating with a 300-second TTL as the safety net.
15. App server A returns `200 OK` with `ETag: "bk-978-v43"` and the standard `Cache-Control` header. The ETag changed, so the conditional request from step 10 gets a full body rather than a `304`.
16. nginx stores the response; the CDN edge stores it with `s-maxage=600`; the browser stores it with `max-age=60`.
17. Total time from commit to that user seeing £12.99: ~22 s, dominated entirely by the browser's remaining `max-age`, not by any of your infrastructure.
18. Separately, had step 4 failed (Redis timeout), the entry would have survived until its TTL expired — at most 300 s. That bounded exposure is exactly what the TTL is for.

The number to take away from step 17: **the slowest cache in the chain determines your real staleness window**, and it is usually the one you control least.

## 6. ASCII diagram

```
INVALIDATION REACHES EACH TIER DIFFERENTLY
────────────────────────────────────────────────────────────────────────
                                        can you      how fast        what
                                        purge it?    after commit?   bounds it
  ┌──────────┐
  │ Browser  │  max-age=60              ✗ NEVER      up to 60 s      max-age
  └────┬─────┘
       │
  ┌────▼─────┐
  │   CDN    │  s-maxage=600            ✓ purge API  ~0.15–5 s       purge+TTL
  └────┬─────┘
       │
  ┌────▼─────┐
  │  nginx   │  proxy_cache_valid 60s   ✓ PURGE req  ~10 ms          purge+TTL
  └────┬─────┘
       │
  ┌────▼─────┐
  │  Redis   │  EX 300                  ✓ DEL        ~0.4 ms         TTL
  └────┬─────┘
       │
  ┌────▼─────┐
  │ Postgres │  SOURCE OF TRUTH         —            0 ms            —
  └──────────┘
  Staleness window = the SLOWEST tier a given user hits, not the fastest.


THE READ-REPOPULATE RACE  (no failure required)
────────────────────────────────────────────────────────────────────────
  time ──────────────────────────────────────────────────────────▶
  Reader  │ MISS ├──── SELECT (slow, 40 ms) ────┤ SET cache=9.99 ✗
          │                                      ▲
  Writer  │        │UPDATE db=12.99│ DEL key│    │
          │                                 ▲    │
          │                      key already gone │
          └───────────────────────────────────────┘
  Result: cache = 9.99, database = 12.99. Wrong until TTL expires.
  FIX (cheap): short TTL.  FIX (sound): CDC-driven invalidation.


VERSIONED KEYS — O(1) MASS INVALIDATION
────────────────────────────────────────────────────────────────────────
  BEFORE                              AFTER one INCR
  global_ver = 7                      global_ver = 8
  ┌────────────────────────┐          ┌────────────────────────┐
  │ v7:book:A  ← read      │          │ v7:book:A  (orphan)    │  still
  │ v7:book:B  ← read      │  INCR    │ v7:book:B  (orphan)    │  in RAM
  │ v7:book:C  ← read      │ ───────▶ │ v7:book:C  (orphan)    │  until TTL
  │ ... 4,999,997 more     │  O(1)    │ v8:*  ← all reads MISS │
  └────────────────────────┘          └────────────────────────┘
  COST: ~0 to invalidate · 2× memory until TTL · 100% miss spike next second
  ⇒ Never bump a namespace without a TTL and without expecting the miss storm.


TTL vs DATABASE LOAD  (50,000 hot keys)
────────────────────────────────────────────────────────────────────────
  misses/s = keys / TTL
  TTL    10 s │████████████████████████████████████████ 5,000/s  ✗ DB dies
  TTL    60 s │███████                                    833/s
  TTL   300 s │█▌                                         167/s  ← sweet spot
  TTL   900 s │▌                                           56/s
  TTL  3600 s │▏                                           14/s  ✗ too stale
  Each 10× of TTL removes 90% of what is left. Returns collapse fast.
```

## 7. Detailed example

### BookNook — the invalidation audit

BookNook's cache has been running since Lesson 28 with a uniform `EX 600` on everything and no explicit invalidation at all. Hit ratio is 96 %, which looks excellent. Then three incidents arrive in one month.

**Incident 1 — "I was charged more than the price shown."** A merchandiser raised prices at 09:00 for a publisher deal. For the next ten minutes the book pages showed the old price while checkout charged the new one, because checkout re-reads from PostgreSQL. 180 customers were affected; 23 opened support tickets and 6 filed chargebacks. Direct cost including staff time: roughly £1,400.

The fix was not a shorter TTL — a 10-second TTL on 50,000 keys would have meant 5,000 extra database queries per second (Section 9). The fix was **delete-on-write**: the admin write path now issues `DEL v7:book:{isbn}` immediately after commit. TTL stays at 300 s as a safety net. Staleness went from 600 s to under 1 s for the path that matters, while database load went *down*, because 300 s is still a long TTL and a price change happens a few hundred times a day rather than thousands of times a second.

**Incident 2 — "I posted a review and it vanished."** Reviews were cached under `book:{isbn}:reviews:page1`, `:page2`, `:sort=helpful`, `:sort=recent`, and a `rating_histogram` key — five to twelve derived keys per book. The write path deleted only `page1`. Everything else stayed stale for 600 s, so a reviewer saw their review on the first page but the rating average did not move and the "most helpful" tab did not include it.

The fix was **namespace bumping**. Every reviews key becomes `book:{isbn}:reviews:v{n}:{variant}`, where `n` is read from `book:{isbn}:reviews_version`. Posting a review is now a single `INCR`, which invalidates all twelve derived keys atomically and in O(1). The reads cost one extra Redis `GET` for the version, which is pipelined with the data fetch and adds ~0.1 ms. The orphaned `v{n−1}` keys expire on their own 600-second TTL.

**Incident 3 — "The scraper."** A competitor's crawler requested 40,000 randomly generated ISBNs over 20 minutes. None existed. Every request missed the cache (nothing to cache a miss as), hit PostgreSQL, and returned 404. That is 33 queries/second of pure waste on top of normal load, and because the ISBN column lookup is an index scan returning zero rows, each one still cost a round trip and a connection-pool slot. Database p99 rose from 11 ms to 95 ms and the site felt slow for forty minutes.

The fix was **negative caching** with a 60-second TTL and a `__NULL__` sentinel, plus deletion of the negative key in the book-creation path. Repeat lookups for a non-existent ISBN now cost 0.3 ms in Redis. The deeper fix — a Bloom filter in front of the cache so even the *first* lookup never reaches the database — is Lesson 30.

**Where BookNook ended up:**

| Data | TTL | Primary mechanism |
|---|---|---|
| Book detail (price, title, cover) | 300 s | Delete-on-write |
| Stock band | 10 s | TTL only; authoritative check at order time |
| Reviews (all variants) | 600 s | Namespace bump |
| Homepage / bestsellers | 900 s | `stale-while-revalidate` + explicit purge on campaign publish |
| User profile | 1800 s | Delete-on-write by user ID |
| Negative (missing ISBN) | 60 s | TTL + delete on creation |
| Search results | 120 s | TTL only — results are inherently approximate |

Hit ratio fell from 96 % to 94.1 % (deletes cause extra misses). Database read QPS rose by 4 %. Correctness complaints went to zero. **That is the trade you are making: a measurable, affordable amount of database load in exchange for a bounded, explainable staleness window.**

### Second example for contrast — a stock-trading price feed

Now take a system where every assumption above inverts: a brokerage showing live quotes.

- **Staleness tolerance is essentially zero**, and it is a *regulatory* constraint, not a UX preference. Showing a 3-second-old quote on which a user trades is a compliance incident.
- **Invalidation by deletion is meaningless.** The value changes continuously — thousands of times per second for a liquid instrument. A cache entry for `AAPL` would be deleted and repopulated faster than it could be read.
- Therefore the architecture inverts: instead of **pull-with-TTL**, it is **push-on-change**. A market-data feed streams updates into an in-memory store; subscribers receive pushes over WebSocket (Lesson 10). The cache is never "invalidated" because it is never allowed to go stale — it is *overwritten* by the authoritative feed at the feed's own rate.
- TTL still exists, but it means something completely different: it is a **liveness check**. If no tick for a symbol has arrived in 5 seconds, the UI greys the price out and labels it "delayed". The TTL protects against a *dead feed*, not against stale data.
- Negative caching is inverted too: an unknown ticker is a hard error, not a cacheable absence, because the instrument universe is small, fixed and locally known.

Same vocabulary, opposite design, because the write rate exceeded the read rate. **The entire TTL framework assumes reads vastly outnumber writes.** When they do not, caching with invalidation is the wrong tool and streaming replication of state is the right one. Recognising which regime you are in — before choosing a mechanism — is the actual skill.

## 8. Code or configuration example

> **Illustrative only — not production-ready.** Error handling, retries, circuit breakers, connection pooling, serialisation details, metrics, tracing, and security hardening are omitted or simplified for clarity. Do not paste this into a production service.

### Application-level invalidation patterns

```python
import json, random, time
import redis, psycopg2

r = redis.Redis(host="cache.booknook.internal", socket_timeout=0.05)  # 50 ms: NEVER
                                                                      # let the cache
                                                                      # stall a request
NULL_SENTINEL = "__NULL__"      # distinguishable from "key absent" and from an
                                # empty body. A bare "" would be ambiguous.

BOOK_TTL      = 300             # safety net; real freshness comes from delete-on-write
NEGATIVE_TTL  = 60              # much shorter: a book genuinely may be created
JITTER        = 0.10            # ±10% so 50k keys don't expire in the same second
                                # (cache avalanche — Lesson 30)

def ttl_with_jitter(base):
    # Spreading expiry over a window converts one big miss spike into a flat trickle.
    return int(base * (1 + random.uniform(-JITTER, JITTER)))

def book_key(isbn):
    # "v7" is the GLOBAL NAMESPACE VERSION. Bumping it invalidates every book key
    # in O(1) -- see mass_invalidate_all_books() below.
    return f"v7:book:{isbn}"

# ---------------------------------------------------------------- READ (cache-aside)
def get_book(isbn):
    key = book_key(isbn)
    cached = r.get(key)

    if cached is not None:
        if cached == NULL_SENTINEL.encode():        # NEGATIVE CACHE HIT:
            return None                             # we already know it doesn't exist,
                                                    # so the database is never touched
        return json.loads(cached)                   # positive hit

    row = db_select_book(isbn)                      # MISS -> source of truth

    if row is None:
        # Cache the ABSENCE. Without this line every lookup for a bogus ISBN is a
        # guaranteed database query -- that is cache penetration (Lesson 30).
        r.set(key, NULL_SENTINEL, ex=ttl_with_jitter(NEGATIVE_TTL))
        return None

    r.set(key, json.dumps(row), ex=ttl_with_jitter(BOOK_TTL))
    return row

# ---------------------------------------------------------------- WRITE
def update_book_price(isbn, new_price):
    # ORDER MATTERS. Database FIRST, cache SECOND.
    # Reversing this creates a race that needs no failure at all: a concurrent reader
    # repopulates the cache with the OLD value in the gap before the commit.
    with db_transaction() as tx:
        tx.execute(
            "UPDATE books SET price = %s, version = version + 1 WHERE isbn = %s",
            (new_price, isbn),
        )
    # commit has happened here -- the database is now authoritative

    # DELETE, do not SET. Deletes are idempotent and order-insensitive; two concurrent
    # writers issuing SET can leave the OLDER value in the cache permanently.
    r.delete(book_key(isbn))

    # Optional "delayed double delete": kills a straggler read that was mid-SELECT
    # when we deleted above and is about to write a stale value back. The delay must
    # exceed p99 read latency + replica lag. This is a HEURISTIC, not a guarantee.
    schedule_after(0.5, lambda: r.delete(book_key(isbn)))

# ---------------------------------------- NAMESPACE BUMP: invalidate a related family
def reviews_key(isbn, variant):
    # Version is stored per book. One INCR invalidates page1..pageN, every sort order,
    # and the rating histogram -- all derived keys at once, in O(1).
    version = r.get(f"book:{isbn}:reviews_version") or b"1"
    return f"book:{isbn}:reviews:v{version.decode()}:{variant}"

def post_review(isbn, review):
    db_insert_review(isbn, review)
    r.incr(f"book:{isbn}:reviews_version")   # O(1) mass invalidation of ~12 keys
    # The old v{n-1} keys are NOT deleted. They occupy memory until their TTL expires.
    # This is why versioned keys MUST still carry a TTL, or you leak memory forever.

def create_book(isbn, data):
    db_insert_book(isbn, data)
    # CRITICAL and frequently forgotten: clear the NEGATIVE entry, or the book stays
    # "not found" for up to NEGATIVE_TTL seconds after it was successfully created.
    r.delete(book_key(isbn))

# ------------------------------------------------- GLOBAL O(1) MASS INVALIDATION
def mass_invalidate_all_books():
    # Bumping the global version makes every "v7:*" key unreachable instantly.
    # NEVER use KEYS or FLUSHALL for this: KEYS is O(N) and blocks Redis entirely
    # (single-threaded command loop), and FLUSHALL destroys unrelated namespaces.
    r.incr("global:book_namespace_version")
    # WARNING: the next second is a 100% miss rate on every book. Only do this
    # behind a stampede guard (Lesson 30), or during low traffic, or with a warmer.
```

### Atomic version-checked write-back (closing the read-repopulate race)

```lua
-- Redis Lua script: runs atomically on the server, so no other command interleaves.
-- Writes the value ONLY if it is newer than what is already cached.
-- KEYS[1] = cache key, ARGV[1] = JSON value, ARGV[2] = db row version, ARGV[3] = TTL
local existing = redis.call('HGET', KEYS[1], 'version')
if existing and tonumber(existing) >= tonumber(ARGV[2]) then
  return 0                                   -- a newer value is already cached; the
                                             -- straggler read is REJECTED, race closed
end
redis.call('HSET', KEYS[1], 'value', ARGV[1], 'version', ARGV[2])
redis.call('EXPIRE', KEYS[1], ARGV[3])       -- HSET does not set a TTL; do it explicitly
return 1
```

### HTTP caching headers in nginx

```nginx
# Fingerprinted static assets: the filename changes when the content changes, so the
# URL itself is the version. Safe to cache for a year and skip revalidation entirely.
location ~* \.(js|css|woff2)$ {
    add_header Cache-Control "public, max-age=31536000, immutable";
}

# Book pages: short BROWSER life (you can never purge a browser),
# long SHARED life (you CAN purge the CDN and this proxy).
location /books/ {
    proxy_pass http://booknook_app;
    proxy_cache booknook_zone;

    add_header Cache-Control "public, max-age=60, s-maxage=600, stale-while-revalidate=120, stale-if-error=86400";
    #                                   ^^^^^^^ browser: 60 s of zero-request reuse
    #                                            ^^^^^^^^^^^ CDN/proxy: 600 s
    #                        stale-while-revalidate: serve stale INSTANTLY and refresh
    #                        in the background -- the user never waits for the origin
    #                        stale-if-error: keep serving stale for 24 h if the origin
    #                        5xxs. The cache becomes an outage fallback.

    proxy_cache_valid 200 60s;
    proxy_cache_valid 404 10s;              # negative caching at the proxy tier too
    proxy_cache_use_stale error timeout updating http_500 http_502 http_503;
                                            # "updating" = while one request refreshes,
                                            # everyone else gets stale instead of
                                            # piling onto the origin (Lesson 30)
    proxy_cache_background_update on;       # refresh out-of-band, not in the user's path
    proxy_cache_lock on;                    # only ONE request per key may go to origin
    proxy_cache_lock_timeout 5s;            # the others wait up to 5 s for it
}

# Anything personal or transactional must never be stored by a shared cache.
location /api/me   { add_header Cache-Control "private, no-store"; }
location /checkout { add_header Cache-Control "no-store"; }
# NOTE: "no-cache" does NOT mean "do not cache" -- it means "store it, but revalidate
# before every use". "no-store" is the one that means do not keep a copy. Confusing
# these two is the most common HTTP caching mistake in production.
```

### CDN purge by surrogate key

```bash
# Tag responses at origin so one purge call can drop every representation of a book:
#   the HTML page, the JSON API response, the OG image, the sitemap fragment.
#   Surrogate-Key: book-9780143127741 author-powers category-fiction

curl -X POST https://api.cdn.example/service/SVCID/purge/book-9780143127741 \
     -H "Fastly-Key: $CDN_API_TOKEN" \
     -H "fastly-soft-purge: 1"
#    ^ SOFT purge: marks the object stale rather than deleting it, so edges serve the
#      stale copy (per stale-while-revalidate) while refetching in the background.
#      A HARD purge on a hot object sends every subsequent request to origin at once.
#
# Typical global propagation: ~150 ms - 5 s (approximate, vendor-dependent).
# DESIGN FOR THE UPPER BOUND, not the advertised median.
#
# NEVER script "purge everything" on a content change. It is a self-inflicted cache
# avalanche: your origin instantly receives its full uncached load (Lesson 30).
```

## 9. Numerical example

### Part A — TTL versus database load

**Assumptions, stated explicitly:**
- BookNook has 2,000,000 books in PostgreSQL, but traffic is Zipf-distributed and the **hot working set is 50,000 distinct books** (Lesson 27).
- Peak read traffic: **6,000 requests/second** to book detail endpoints.
- Every hot key is read many times per TTL period, so its miss rate is driven by *expiry*, not by access frequency.
- One cache miss costs exactly one PostgreSQL query at **8 ms** of primary CPU-time equivalent.
- PostgreSQL primary can sustain approximately **3,000 simple indexed queries/second** before p99 degrades (Lesson 14's queueing cliff).

The governing relationship:

```
expiry-driven misses/sec ≈ distinct hot keys ÷ TTL(seconds)
```

| TTL | Misses/sec | % of 6,000 req/s | DB utilisation (of 3,000 q/s) | Verdict |
|---|---|---|---|---|
| 10 s | 50,000 / 10 = **5,000** | 83.3 % | 167 % | Database is down |
| 30 s | 50,000 / 30 = **1,667** | 27.8 % | 55.6 % | Survivable, no headroom |
| 60 s | 50,000 / 60 = **833** | 13.9 % | 27.8 % | Workable |
| 300 s | 50,000 / 300 = **167** | 2.8 % | 5.6 % | **Chosen** |
| 900 s | 50,000 / 900 = **56** | 0.9 % | 1.9 % | Fine, but 15 min stale |
| 3600 s | 50,000 / 3600 = **13.9** | 0.2 % | 0.5 % | Too stale for price data |

**Marginal benefit of each step, computed:**

```
10 s  → 30 s : 5,000 − 1,667 = 3,333 queries/s removed   (66.7% of the load)
30 s  → 60 s : 1,667 −   833 =   834 queries/s removed   (16.7%)
60 s  → 300 s:   833 −   167 =   666 queries/s removed   (13.3%)
300 s → 900 s:   167 −    56 =   111 queries/s removed   ( 2.2%)
900 s → 3600s:    56 −    14 =    42 queries/s removed   ( 0.8%)
```

**Interpretation.** Moving from 10 s to 300 s removes 96.7 % of expiry-driven load. Moving from 300 s to 3,600 s removes a further 3.1 % while making the data **twelve times staler**. The `1/TTL` curve means almost all the benefit is captured early. This is why "just raise the TTL" stops being an answer very quickly, and why the real lever for a hot system is *explicit invalidation with a moderate TTL*, not a long TTL.

### Part B — Hit ratio and effective latency at TTL = 300 s

**Additional assumptions:** Redis hit latency 0.5 ms; miss path = 0.5 ms (failed lookup) + 8 ms (query) + 0.4 ms (write-back) = **8.9 ms**.

```
Expiry-driven miss rate  = 167 / 6,000                       = 2.78 %
Delete-on-write misses   = 400 price edits/day ÷ 86,400 s    = 0.0046 misses/s
                         = 0.0046 / 6,000                    = 0.00008 %  (negligible)
Capacity-eviction misses (measured)                          = 1.1 %
Cold/long-tail misses (measured)                             = 2.0 %
─────────────────────────────────────────────────────────────────────────
Total miss rate ≈ 2.78 + 0.00008 + 1.1 + 2.0                 = 5.88 %
Hit ratio                                                     = 94.12 %

Effective latency = 0.9412 × 0.5 ms + 0.0588 × 8.9 ms
                  = 0.4706 + 0.5233
                  = 0.994 ms
Uncached latency  = 8.0 ms
Speed-up          = 8.0 / 0.994                               = 8.05×
```

Note where the time actually goes: **53 % of the effective latency comes from the 5.88 % of requests that miss.** That asymmetry is the whole reason stampede control (Lesson 30) matters — the misses dominate, so protecting the miss path is worth more than optimising the hit path.

### Part C — The cost of getting the TTL wrong on price data

**Assumptions:** 400 price changes/day, each affecting one book; 6,000 req/s spread Zipf-wise so an average *hot* book receives 6,000 / 50,000 = 0.12 req/s; a wrong price shown at checkout costs an average of **£7.80** (support handling + refund + chargeback risk, from Incident 1's £1,400 over 180 customers).

**With TTL = 600 s and no explicit invalidation:**
```
Mean staleness window per change        = TTL / 2 = 300 s
  (a change lands uniformly at random within the key's TTL period)
Requests to that book during the window = 0.12 req/s × 300 s   = 36 requests
But price changes target promoted books, which are ~20× hotter = 720 requests
Checkout conversion on a book page                             = 2.5 %
Affected checkouts per change           = 720 × 0.025          = 18
Affected checkouts per day              = 18 × 400             = 7,200
```
That figure is clearly implausible as a steady state — and that is the point of doing the arithmetic. Real incidents cluster because price changes come in batches (a publisher deal updates 300 books at 09:00, not 400 spread over the day). Recomputing with the realistic batch shape:
```
Batch: 300 books changed at 09:00 (peak hour, ~1.6× average traffic)
Hot-book rate during peak        = 0.12 × 20 × 1.6               = 3.84 req/s
Mean staleness window            = 300 s
Stale page views per book        = 3.84 × 300                    = 1,152
Across the batch                 = 1,152 × 300 books             = 345,600 views
Checkouts at 2.5 %               = 8,640 — but most books in a batch are cold,
  so apply the Zipf correction: only ~6 % of the batch is genuinely hot
Effective affected checkouts     = 8,640 × 0.06                  = 518
Observed in Incident 1                                           = 180
```
The model over-predicts by ~2.9×, which is normal for a first-principles estimate and is still the right order of magnitude. **State this in an interview**: a model that lands within 3× and whose assumptions are explicit is far more valuable than a precise-looking number with hidden assumptions.

**With delete-on-write and TTL = 300 s as a backstop:**
```
Staleness window      = Redis DEL latency + replication = ~1 ms (instead of 300 s)
Reduction factor      = 300,000 ms / 1 ms               = 300,000×
Residual exposure     = only when the DEL itself fails, bounded at 300 s
DEL failure rate (Redis availability ≈ 99.95 %)          = 0.05 %
Expected stale incidents/day = 400 changes × 0.0005      = 0.2 per day
                             ≈ 1 every 5 days, each bounded at 5 minutes
Extra database load from the deletes = 400/day ÷ 86,400  = 0.0046 queries/s
```
**£1,400/month of incident cost removed for 0.0046 additional queries per second.** That ratio — essentially free correctness — is why delete-on-write is the default for any field a human edits.

### Part D — Negative caching against the scraper

**Assumptions:** 40,000 bogus ISBNs requested over 20 minutes (1,200 s); the scraper retries each ISBN an average of 3 times; a zero-row indexed lookup costs 4 ms of database time.

```
Total bogus requests        = 40,000 × 3                       = 120,000
Duration                    = 1,200 s
Request rate                = 120,000 / 1,200                  = 100 req/s

WITHOUT negative caching — every one reaches PostgreSQL:
  Extra DB queries/s        = 100
  Extra DB time/s           = 100 × 4 ms                       = 400 ms/s
  As a fraction of one core                                     = 40 %
  As a fraction of 3,000 q/s capacity = 100 / 3,000             = 3.3 %
  Connection-pool slots held (Little's Law, L = λW):
                            = 100 × 0.004 s                     = 0.4 slots average
  — but the scraper is bursty; at a 10× burst it holds 4 of a 20-slot pool = 20 %

WITH negative caching (60 s TTL, sentinel value):
  First request per ISBN still hits the DB   = 40,000 over 1,200 s = 33.3 q/s
  Retries (2 of every 3) are served by Redis = 66.7 req/s at 0.3 ms
  Extra DB queries/s                                              = 33.3
  Reduction                                  = 1 − 33.3/100       = 66.7 %

WITH a Bloom filter in front (Lesson 30), 1 % false-positive rate:
  Requests reaching the DB   = 120,000 × 0.01                     = 1,200
  Rate                       = 1,200 / 1,200 s                    = 1 q/s
  Reduction vs baseline      = 99 %
```

The staged result — 100 → 33.3 → 1 queries/second — shows the layering clearly. **Negative caching fixes repeats; only a membership filter fixes first-time lookups of keys that do not exist.** If the attacker never repeats an ISBN, negative caching saves nothing at all, which is precisely why Lesson 30 needs Bloom filters.

### Part E — Namespace bump memory cost

**Assumptions:** 50,000 cached books averaging 2.4 KB serialised, plus ~90 bytes of Redis overhead per key.

```
Per entry           = 2,400 + 90                   = 2,490 bytes
Namespace total     = 50,000 × 2,490               = 124,500,000 B = 118.7 MB
After a version bump, old + new coexist until the old TTL (300 s) drains:
  Peak memory       ≈ 2 × 118.7 MB                 = 237.4 MB
  Duration of peak  ≈ one full TTL                 = 300 s
On a 4 GB Redis instance: peak = 237.4 / 4,096     = 5.8 %   → safe

BUT the miss storm:
  Every one of 6,000 req/s misses until keys refill
  DB capacity                                       = 3,000 q/s
  Deficit                                           = 3,000 q/s  → 200 % overload
  ⇒ A namespace bump at peak traffic TAKES THE DATABASE DOWN.
  Time to refill 50,000 keys at 3,000 q/s           = 16.7 s of total saturation
```

The memory cost is trivial; the **miss cost is the real cost**, and it is the thing nobody computes before shipping a "clear the cache" button. A namespace bump is safe only with request coalescing, a pre-warm, or an off-peak window — all of which are Lesson 30.

## 10. Trade-offs

| Decision | Gain | Cost | Choose it when |
|---|---|---|---|
| **Longer TTL** | Fewer DB queries (`keys/TTL`), higher hit ratio | Larger staleness window; more memory held by cold entries | Data is slow-changing or staleness is genuinely harmless |
| **Shorter TTL** | Fresher data, less memory retention | Load grows as `1/TTL`; can exceed DB capacity outright | Volatile data *and* a small key set, or a DB with headroom |
| **Explicit invalidation** | Near-zero staleness at ~zero steady load | Every write path must remember; missed paths are wrong until TTL | A known, small set of write paths you control |
| **Event-driven (CDC)** | Structurally complete — catches DBA edits, migrations, replication | An always-on pipeline to build, run and monitor; 10 ms–2 s lag | Many writers, or writers outside your codebase |
| **Versioned keys** | O(1) mass invalidation, no scan, no `KEYS` | Temporary 2× memory; a version read per request; miss storm on bump | Large families of derived keys that change together |
| **Delete-on-write** | Idempotent, race-tolerant, always correct-by-refetch | One guaranteed miss per write; stampede risk on hot keys | Default choice; especially with concurrent writers |
| **Write-through** | No post-write miss; hot keys stay warm | Lost-update races between writers; writer must know the full object | Single-writer-per-key and hot keys |
| **Negative caching** | Removes repeated lookups of absent keys | Newly created items appear late; sentinel bugs cause silent passthrough | Any public endpoint keyed by user-supplied identifiers |
| **`stale-while-revalidate`** | User never waits for a refresh; natural stampede damping | Users knowingly served stale data for the SWR window | Read-heavy content where "a little old" beats "a little slow" |
| **`stale-if-error`** | Site stays browsable during an origin outage | Can serve very old data during a long outage | Catalogue and content pages; never for prices at checkout |
| **CDN purge on write** | Global freshness within seconds | API quotas, cost per purge, seconds of propagation, stampede risk | Content with a long `s-maxage` that must change on demand |
| **Short browser `max-age`** | Keeps the unpurgeable tier small | More revalidation requests (though `304`s are ~200 bytes) | Anything mutable; pair with long `s-maxage` |

Two trade-offs deserve naming because they are the ones candidates miss:

- **Hit ratio versus correctness is not symmetric.** Going from 96 % to 94 % hit ratio cost BookNook 4 % more database queries — a rounding error against capacity. Going from "sometimes wrong" to "never wrong" removed chargebacks. Hit ratio is an *optimisation* metric; staleness is a *correctness* metric. Never trade the second for the first.
- **Every invalidation is a future load spike.** The cheaper an invalidation is to issue (a namespace bump is one `INCR`), the more expensive it can be to absorb. Invalidation cost must be budgeted on the *refill* side, not the *issue* side.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| `DEL` after commit fails (Redis timeout) | Old price shown for up to the full TTL; checkout charges the new price — visible mismatch | `cache_invalidation_failures` counter; mismatch alerts comparing cache value to DB for sampled keys | TTL expires and the key repopulates. Bounded by TTL — which is exactly why no key should be TTL-less |
| Read-repopulate race (reader writes stale value after the delete) | A single key is wrong until TTL, with no error anywhere; support ticket says "it showed the old price" | Near-undetectable by monitoring; found by a periodic sampling job that compares cache against DB | TTL expiry; or delayed double delete; or version-checked write-back (§8 Lua); or CDC invalidation |
| Developer adds a new write path and forgets to invalidate | A specific field is permanently stale after a specific action; reproducible but obscure | Cache/DB consistency sampling; a lint rule requiring every `UPDATE` to declare the keys it touches | Add the invalidation; the structural fix is CDC so writers cannot forget |
| TTL set too short on a large key set | Site feels slow; p99 climbs; DB CPU pinned; occasional 503s | DB QPS ≈ keys/TTL; cache miss rate rises with no change in traffic; correlate with the config change | Raise the TTL, add delete-on-write, enable request coalescing (Lesson 30) |
| No TTL at all on a key (`SET` without `EX`) | A value that is wrong *forever*; survives restarts if persistence is on; nobody can explain it | Audit for keys with `TTL == -1`; Redis `INFO keyspace` shows `expires` far below `keys` | `EXPIRE` the key; fix the write path; add a CI check banning `SET` without a TTL |
| Namespace version bumped at peak | Site-wide slowdown for 10–30 s, possibly a full outage; looks exactly like a database failure | DB QPS spike to saturation with a 100 % cache miss rate; correlates with an admin action or deploy | Request coalescing so one request per key reaches the DB; pre-warm; restrict bumps to off-peak |
| Negative entry never cleared on creation | A newly added book returns 404 for up to `NEGATIVE_TTL` after a successful create; the admin re-creates it, producing duplicates | 404 rate on freshly created IDs; admin complaints; duplicate-row alerts | `DEL` the negative key in the create path; keep negative TTLs short (30–120 s) |
| `Cache-Control: no-cache` used where `no-store` was meant | Personal data stored on disk by a shared proxy or a corporate cache; potentially served to the wrong user | Security review; penetration test; a cache-key audit | Change to `no-store`; purge the shared cache; treat as a data-exposure incident |
| CDN purge API returns 200 but propagation is slow at some POPs | Users in one region see old content for seconds to minutes while others see new | Per-POP synthetic checks after each purge; `Age` header inspection | Wait for propagation; design for the upper bound; use soft purge; keep `s-maxage` modest for volatile objects |
| "Purge everything" run during a content release | Global latency spike; origin saturates; possible full outage | Origin request rate jumps by 10–100×; CDN offload ratio collapses to ~0 | Never script it; use surrogate keys; if it has happened, shed load and let the cache refill gradually |
| Clock skew or an unexpected `maxmemory` eviction removes entries early | Elevated misses with no config change; DB load higher than the `keys/TTL` model predicts | `evicted_keys` metric > 0 in Redis `INFO`; `used_memory` at `maxmemory` | Raise memory, shrink values, or choose the right eviction policy (Lesson 30) |
| Browser holds a stale copy with `max-age` remaining | One user sees old content after everything server-side is correct; a hard refresh "fixes" it | Irreproducible reports; `Age` and `Date` headers in a HAR file | Nothing you can do remotely. Prevent it: short `max-age`, fingerprinted URLs, long `s-maxage` only on the shared tier |

## 12. Alternatives

| Approach | How freshness is achieved | Staleness window | Operational cost | Best for |
|---|---|---|---|---|
| **TTL only** | Clock expiry | Up to full TTL (mean TTL/2) | Near zero | Slow-changing data; a default safety net on everything |
| **Delete-on-write** | Writer deletes the key after commit | ~1 ms when it works; TTL when it fails | Low — one line per write path | The common case; human-edited data |
| **Write-through** | Writer overwrites the key | ~1 ms, but lost-update races possible | Low, plus composition cost | Single-writer, very hot keys |
| **CDC / event-driven** | WAL or binlog stream drives invalidation | 10 ms – 2 s | High — Kafka/Debezium to run and monitor | Many writers, legacy writers, cross-service caches |
| **Versioned / content-addressed keys** | A changed value yields a different key | Zero, by construction | Low for assets; key design discipline needed | Static assets; immutable derived artefacts |
| **Namespace bumping** | One counter invalidates a whole family | ~1 ms | Low to issue, high to absorb | Large families of derived keys |
| **Polling for a version** | Client re-checks a cheap version endpoint | Polling interval | Medium — extra request volume | Config, feature flags, small hot metadata |
| **Push / subscribe (pub-sub)** | Server notifies caches of changes | ~10–100 ms | Medium — delivery is at-most-once unless you add durability | Multi-node L1 caches that must agree |
| **Redis client-side caching (RESP3 tracking)** | Redis itself invalidates the client's local copy | ~1 ms | Medium — requires RESP3 and client support | L1 in-process caches in front of Redis |
| **No cache; read the source** | Freshness is inherent | Zero | Zero complexity, high latency and DB load | Money, inventory decisions, auth decisions |
| **Materialised view / read model** | A denormalised table maintained by the DB or a stream processor | Refresh interval or stream lag | Medium–high | Expensive aggregates; **Phase 3, Topic 17 — CQRS and materialised views** |
| **Streaming state (push-on-change)** | Authoritative feed overwrites continuously | Milliseconds | High | Write-rate ≥ read-rate workloads such as market data |

The decision procedure, in order:

1. **Can the data tolerate any staleness?** If no (money, stock decisions, authorisation), do not cache it at the decision point. Cache it for display only.
2. **Do you control every writer?** If yes, delete-on-write plus a TTL backstop. If no, CDC.
3. **Does one change invalidate many keys?** If yes, namespace bumping or surrogate keys.
4. **Can the value change identity instead of content?** If yes, content-addressed keys and you never invalidate at all. This is strictly the best option when it is available.
5. **Whatever you choose, still set a TTL.** It is the only mechanism that works when everything else has failed.

## 13. When to use it

1. **Always set a TTL on every cache entry, without exception.** Even with perfect explicit invalidation, the TTL is the bound on how long a bug, a dropped message, or a Redis timeout can keep a value wrong. A key with `TTL == -1` is a permanent-correctness bet you did not intend to make.
2. **Use delete-on-write for anything a human edits** — prices, product copy, profiles, addresses, settings. These are low-frequency writes with high visibility, so the invalidation is nearly free and the correctness gain is large (Section 9, Part C: £1,400/month for 0.0046 extra queries/second).
3. **Use namespace bumping when one change invalidates a family of derived keys** — review pages across sorts and filters, search facets, a user's permission-derived views. One `INCR` beats enumerating keys, and crucially it beats `KEYS` or `SCAN`, which are O(N) and can block Redis.
4. **Use negative caching on any endpoint keyed by a user-supplied identifier** — ISBN, SKU, username, short-link code. Without it, an attacker chooses your database's load for you.
5. **Use `stale-while-revalidate` on read-heavy content pages.** It converts the worst-case user experience of a cache miss (waiting for the origin) into the best case (instant stale response, background refresh), and it damps stampedes as a side effect.
6. **Use `stale-if-error` on anything that is better served slightly old than not at all** — catalogue pages, documentation, marketing content. It turns your CDN into a read-only fallback during a database outage.
7. **Use CDC-driven invalidation once more than one service, or any human, writes the same table.** The moment a data migration or a DBA can change a row your application caches, application-level invalidation is structurally incomplete.
8. **Use short browser `max-age` with long `s-maxage` for anything mutable.** You can purge shared caches; you can never purge a browser.

## 14. When *not* to use it

1. **Do not cache the value a money or inventory decision is made on.** Display a cached price; re-read it inside the checkout transaction. Display a cached stock band; decrement with a conditional `UPDATE ... WHERE stock > 0`. Caching for *display* and caching for *decisions* are different activities, and conflating them is how you oversell stock and mis-charge customers.
2. **Do not use a long TTL as a substitute for invalidation.** If you find yourself choosing between "3,600 s and stale" and "10 s and the database dies", you have the wrong mechanism, not the wrong number. Add delete-on-write and set the TTL to a moderate value.
3. **Do not cache write-heavy data where writes outnumber reads.** Below a read-to-write ratio of roughly 5:1 the cache is invalidated faster than it is used; you pay the memory, the complexity, and the risk for a hit ratio near zero. Use a read replica or a materialised view instead.
4. **Do not use `KEYS`, `SCAN` loops, or `FLUSHALL` to invalidate.** `KEYS` is O(N) and blocks Redis's single command loop — on a 10 M-key instance it is a multi-second total stall, which is an outage. Use versioned keys.
5. **Do not bump a namespace or purge everything during peak traffic.** Section 9 Part E showed a 200 % database overload lasting ~17 seconds. Mass invalidation is a capacity event, not a maintenance action.
6. **Do not use `no-cache` when you mean `no-store`.** The first permits storage; only the second forbids it. For personal data on a shared cache, that difference is a data-exposure incident.

## 15. Common mistakes

**Beginner mistakes**
- Calling `SET` without `EX`, creating entries that are wrong forever and survive restarts if persistence is enabled.
- Updating the cache *before* the database, which produces a stale cache with no failure required at all — a concurrent reader repopulates in the gap.
- Using `SET` instead of `DEL` on write, so two concurrent writers can leave the older value cached permanently.
- Forgetting to invalidate the **negative** entry when a record is created, so a successful create appears to have failed.
- Caching a whole entity under one key with one TTL, forcing the volatile price field and the immutable cover URL to share a freshness policy.
- Using an empty string or `nil` as the negative sentinel, making "cached absence" indistinguishable from "not cached" and silently restoring full database load.
- Treating the cache as a database — putting data in Redis that exists nowhere else, then wondering what an eviction means.
- Assuming "I cleared the cache" means all users now see fresh data, having forgotten browsers entirely.
- Confusing `no-cache` with `no-store`.
- Measuring only hit ratio and never measuring staleness, so correctness problems are invisible until a customer reports one.

**Experienced-engineer mistakes**
- Building explicit invalidation for every write path in the application and overlooking migrations, batch jobs, replication, and DBA edits — each of which silently produces permanently wrong entries. This is the argument for CDC.
- Shipping a "Clear cache" admin button with no stampede protection, which is a one-click outage (Section 9 Part E).
- Using versioned keys without a TTL, so orphaned versions accumulate until `maxmemory` is reached and the eviction policy starts making decisions for you.
- Setting `s-maxage` to hours for correctness-sensitive content and relying entirely on purge APIs, then discovering that purges are asynchronous, rate-limited, and occasionally fail.
- Generating ETags by hashing the rendered body, saving bandwidth but none of the CPU that actually constrained the service.
- Enabling `stale-if-error` on an endpoint that returns prices, so a database outage results in users being quoted day-old prices rather than seeing an honest error.
- Implementing delayed double delete and believing it is a correctness guarantee rather than a probability reduction whose delay must exceed p99 read latency plus replication lag.
- Invalidating more keys than changed ("delete the whole book namespace when any field changes") and destroying the hit ratio to avoid thinking about key design.
- Caching per-user data under a key that omits the user ID, or varying on a header the CDN does not include in its cache key — the classic cache-poisoning path where one user's data is served to another.
- Not accounting for the fact that Redis expiry is lazy plus sampled, so expired entries still occupy memory and capacity planning based on logical TTLs under-counts.

## 16. Interview perspective

Invalidation is where interviewers find out whether you have *operated* a cache or only drawn one on a whiteboard. Almost every candidate says "we'll add Redis". Very few can say what happens when the data changes.

**How to use it:**

1. **Lead with staleness tolerance, not with a TTL number.** *"Before picking a TTL I'd ask how long a user can see the old value before it costs us money. For a book price on a browse page that's a few minutes; at checkout it's zero, so checkout re-reads from the primary inside the transaction."* This reframes the question from a guess into a requirement, and it is the single highest-signal sentence in this lesson.
2. **Quantify the TTL/load trade-off on the spot.** *"With 50,000 hot keys, expiry-driven misses are keys over TTL — 167 per second at a 5-minute TTL, 5,000 per second at 10 seconds. The database handles 3,000, so 10 seconds is an outage and 300 seconds is 5 % utilisation."* Interviewers remember candidates who can produce that in one breath.
3. **Say "delete, don't update", and give the reason.** *"Deletes are idempotent and order-insensitive. Two concurrent writers using SET can leave the older value cached permanently; two concurrent DELs just cause an extra miss."*
4. **Volunteer the dual-write problem before you are asked.** *"There's no transaction across Postgres and Redis, so I write to the database first and delete second. That leaves a read-repopulate race where a slow reader writes a stale value back after the delete — I'd bound it with a TTL, and if the data warranted it, close it properly with CDC-driven invalidation."* Naming that race unprompted is a strong senior signal.
5. **Offer versioned keys when asked about mass invalidation.** *"I would not use KEYS — it's O(N) and blocks Redis. I'd put a version in the key prefix and bump a counter: O(1) invalidation. The costs are temporary double memory and a 100 % miss spike, so I'd pair it with request coalescing."*
6. **Separate the tiers explicitly.** *"Browsers can't be purged, so max-age stays at 60 seconds. The CDN can be purged, so s-maxage is 600 with surrogate keys. Redis is deleted on write. Postgres is the source of truth."* Four sentences that show you know a cache is a chain, not a box.
7. **Mention `stale-while-revalidate` and `stale-if-error` by name.** They are cheap to say, rarely mentioned, and demonstrate real HTTP fluency.
8. **Know when the answer is "don't cache this".** Saying *"inventory at the decision point shouldn't come from cache"* is a correctness answer, not a cop-out, and strong interviewers specifically listen for it.

**Red flags interviewers watch for:**
- Saying "we'll cache it" with no mention of how it gets invalidated.
- Picking a TTL with no stated staleness requirement, or picking "1 hour" by habit.
- Updating the cache before the database, or not noticing that the order matters.
- Using `SET` on write and not recognising the lost-update race between concurrent writers.
- Proposing `KEYS pattern*` or `FLUSHALL` to invalidate a group of entries.
- Not knowing that a browser cache cannot be purged, and claiming a CDN purge makes everything fresh.
- Treating cache and database as if a single transaction covered both.
- Serving prices or stock from cache at the point of purchase.
- Having no answer for "what happens if the invalidation message is lost?" — the expected answer is "the TTL bounds it".
- Never mentioning negative caching when the design has a user-supplied identifier in the URL.
- Claiming a 100 % hit ratio is the goal, which implies either an infinite TTL or no writes.

## 17. Knowledge check

Write your answers down before continuing.

**Conceptual**

1. Explain precisely why cache invalidation is structurally hard rather than merely fiddly. Your answer must name the absence of a shared transaction, state why nobody solves it with two-phase commit, and describe the read-repopulate race in enough detail that someone could reproduce it. Then explain why "delete the key" is safer than "update the key" when two writers are concurrent, and why that argument generalises beyond caching.

2. BookNook caches a book's price, its cover-image URL, its stock count, and its review list. Explain why putting all four in one cached object under one TTL is a design error, what the correct decomposition is, and what each component's TTL should be — justifying every number by a business consequence rather than by intuition. Then state which of the four should never be read from cache at the moment of a purchase decision, and why.

3. Explain cache key versioning and namespace bumping: what problem they solve, why the invalidation is O(1), what happens to the old entries, why a TTL is still mandatory, and what the second-order cost is in the seconds immediately after a bump. Then explain why `KEYS prefix*` is not an acceptable alternative in Redis, referring to Redis's execution model.

**Practical scenario**

4. BookNook is adding a wholesale channel. A partner's pricing system writes directly into the `books` table through a nightly ETL job that your application code never sees, and a data analyst occasionally runs manual `UPDATE` statements to fix bad rows. The existing cache uses application-level delete-on-write with a 300-second TTL. Describe exactly what goes wrong, why neither a shorter TTL nor more careful code review fixes it properly, what mechanism does fix it, what that mechanism costs to build and operate, and what the invalidation lag would realistically be. Then describe the interim mitigation you would ship this week, before the proper fix is ready.

**Calculation**

5. BookNook is launching in a second country. The new catalogue has 300,000 books, of which 24,000 are hot. Peak traffic to book endpoints is 9,000 requests/second. The PostgreSQL primary sustains 3,500 simple queries/second before p99 degrades. A cache hit costs 0.5 ms; a miss costs 0.5 ms + 11 ms + 0.4 ms. Capacity-eviction and cold-start misses together account for a measured 2.4 % of requests, independent of TTL. Cached entries average 3.1 KB plus 90 bytes of Redis overhead.
   (a) Compute expiry-driven misses per second at TTLs of 30 s, 120 s, 300 s, and 900 s, and express each as a percentage of total request volume and of database capacity. State which TTLs are viable and why.
   (b) For the lowest viable TTL from (a), compute the total miss rate, the hit ratio, the effective average latency, and the speed-up versus an uncached read. State how much of the effective latency is contributed by misses.
   (c) Compute the memory required to hold all 24,000 hot entries, and then the peak memory during a global namespace version bump. If the Redis instance has 2 GB of usable memory, state whether the bump is safe on memory grounds.
   (d) Compute what happens to the database during that namespace bump at peak traffic: the instantaneous miss rate, the resulting query rate, the overload factor against capacity, and how long full saturation would last while the cache refills. State the single mitigation that changes this outcome most, and estimate the refill time with that mitigation in place assuming it reduces per-key origin requests to one.
   (e) The product team proposes raising the TTL from your chosen value to 1,800 s to "reduce database load". Compute exactly how many queries per second that saves, express it as a percentage of database capacity, and state the new worst-case staleness window. Then give a one-sentence recommendation with the number that justifies it.
   Show every step and state any additional assumptions you make.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 30.

---

**Progress checklist**

Phase 1 — Foundations: **complete** (Lessons 1–17)

Phase 2 — Scaling:
`[x] 1. Vertical scaling` · `[x] 2. Horizontal scaling` · `[x] 3. Stateless application servers` · `[x] 4. Load balancers` · `[x] 5. Layer 4 versus Layer 7` · `[x] 6. Load-balancing strategies` · `[x] 7. Health checks and failover` · `[x] 8. Reverse proxies and API gateways` · `[x] 9. Caching fundamentals` · `[x] 10. Browser, application, database, and distributed caches` · `[x] 11. Cache-aside, read-through, write-through, write-behind` · `[x] 12. Cache invalidation and TTL`
`[ ] 13. Cache stampedes, hot keys, cache penetration, and eviction` ← next
`[ ] 14–22` not started

Phases 3–5: not started.

**NEXT LESSON: Phase 2, Topic 13 — Cache stampedes, hot keys, cache penetration, and eviction**

---

# Lesson 30 — Cache Stampedes, Hot Keys, Cache Penetration, and Eviction

*Phase 2, Topic 13*

## 1. Simple definition

This lesson is about the four ways a cache that is working correctly can still take your system down.

- A **cache stampede** (also called a **thundering herd** or **dog-piling**) is what happens when one popular entry expires and every concurrent request for it misses simultaneously, so instead of one database query you get thousands.
- A **hot key** is a single cache entry receiving a disproportionate share of all traffic — enough that the one shard holding it saturates while the rest of the cluster idles.
- **Cache penetration** is traffic for keys that **do not exist**, which therefore can never be a cache hit and reaches the database every single time.
- **Eviction** is the cache removing an entry *you did not ask it to remove*, because memory is full. The **eviction policy** is the rule deciding which entry dies.

Terms defined before use:
- **Herd**: the set of concurrent requests that all miss on the same key in the same instant.
- **Coalescing** (or **single-flight**): making N concurrent misses for one key produce exactly one backend request, with the other N−1 waiting for its result.
- **Cache avalanche**: the plural of stampede — many *different* keys expiring at the same instant, usually because they were all written at the same instant with the same TTL.
- **Working set**: the set of keys actually being accessed in a given period (Lesson 27). If it does not fit in memory, eviction is continuous.
- **Capacity miss** versus **expiry miss**: a capacity miss is caused by eviction (not enough memory); an expiry miss is caused by TTL (Lesson 29). They look identical in a hit-ratio graph and have completely different fixes.

Lesson 29 was about keeping the cache *correct*. This lesson is about keeping it, and the database behind it, *alive*.

## 2. Why it exists

Lesson 27 established the number that makes every one of these problems dangerous: BookNook's cache absorbs roughly **94 % of read load**, and the database has been sized accordingly. That is the normal, desirable state of a cached system — and it means the database is running at about 6 % of what the application actually demands.

The implication is uncomfortable and is the single most important idea in this lesson:

> **A cached system is a system whose database cannot survive its own traffic.**

The cache is not an optimisation bolted on the side. It is load-bearing structure. The moment a meaningful fraction of the cache stops absorbing load — because a hot key expired, because a shard went down, because every key expired at once, because the requests are for keys that cannot be cached — the database receives a multiple of what it was provisioned for, and multiples of capacity do not degrade gracefully. They hit the queueing cliff from Lesson 14: utilisation above ~90 % produces unbounded queue growth, connection pools exhaust, timeouts cascade, retries amplify the load further, and the system is down.

Concretely, from Lesson 27's incident: a Redis failover during evening peak took database QPS from 420 to 7,100 in under two seconds and the site was effectively down for six minutes.

Each section of this lesson is one path to that same outcome:

| Problem | Mechanism | Trigger |
|---|---|---|
| Stampede | One key, N concurrent misses | A hot key's TTL expires |
| Avalanche | N keys, all expiring together | Bulk warm-up, deploy, flush, or a uniform TTL |
| Hot key | One key, one shard, all the traffic | A celebrity product, a viral post, a default value |
| Penetration | Keys that cannot be cached | Non-existent IDs, often adversarial |
| Eviction pressure | Working set > memory | Growth, or large values, or no TTLs |

They are grouped in one lesson because the mitigations overlap heavily — coalescing, jitter, stale-serving and local replicas each fix more than one of them — and because in a real incident you must be able to tell them apart quickly from metrics that look superficially identical.

## 3. Real-world analogy

**A motorway service station with one coffee machine.**

- **Cache hit** = there is already a fresh pot of coffee; you are served instantly.
- **Cache stampede** = the pot runs dry at the exact moment a coach unloads 60 people. All 60 ask the barista to brew. A sensible barista brews **one** pot and tells everyone to wait two minutes — that is **request coalescing**. A panicking barista starts 60 separate brews, melts the machine, and nobody gets coffee — that is a stampede reaching the database.
- **Probabilistic early expiry** = the barista starts the next pot when the current one is three-quarters gone, chosen slightly randomly, so the pot is never actually empty when someone arrives.
- **Mutex lock key** = a sign on the machine reading "brewing in progress — back in 2 minutes" so only the first person starts a brew.
- **`stale-while-revalidate`** = serving the last cup from the slightly-old pot immediately while the fresh one brews. Nobody waits; one person gets slightly-less-fresh coffee.
- **Cache avalanche** = every pot in the building was brewed at 06:00 with the same two-hour life, so at 08:00 all twelve run dry simultaneously.
- **TTL jitter** = deliberately brewing each pot on a slightly different schedule so they never all run out together.
- **Hot key** = everyone wants the single oat-milk flat white while four other machines sit idle. Buying a fifth machine does not help; you need the oat milk duplicated at every machine — that is **key splitting** / **local replica caching**.
- **Cache penetration** = a queue of people repeatedly asking for a drink the station has never sold. Each request sends a staff member to the stockroom to check. The fix is a laminated menu listing what exists — that is a **Bloom filter**.
- **Eviction** = the shelf holds 40 sandwiches; the 41st delivery requires throwing one away. *Which* one you throw away is the eviction policy, and throwing away the one that was about to be bought is the worst outcome.
- **`noeviction`** = the shelf is full and the policy is "refuse all deliveries". Nothing is lost, but the restocking lorry is turned away — in Redis terms, **writes start failing**.

**Where the analogy breaks down:**

| Service station | Cache |
|---|---|
| 60 people arrive over a minute and queue visibly | 4,800 requests arrive in 400 ms, concurrently, across dozens of application processes that cannot see each other — the herd is invisible to any single server |
| The barista naturally brews one pot for everyone | Nothing coalesces by default. Every process independently decides to query the database. You must *build* the coalescing |
| Brewing more coffee costs coffee | A stampede's cost is non-linear: 4,800 queries do not take 4,800× one query's time, they exhaust the connection pool and make *every unrelated query* slow too |
| The machine melting is obvious | A stampede looks like "the database got slow". The cause is upstream and five seconds in the past by the time you see the graph |
| One building, one set of pots | A sharded cache means a hot key is served by exactly one node; the other nodes cannot help no matter how many you add |
| Asking for a non-existent drink is rare and harmless | Penetration can be *adversarial* and generated at thousands of requests per second by a single script |
| Throwing away a sandwich loses one sandwich | Evicting a hot key costs a database query *and* can trigger a stampede for it |
| The lorry being turned away is visible | `noeviction` surfaces as `OOM command not allowed` errors deep inside application code, often in a path with no error handling |

The sixth row is the one candidates miss most often in interviews: **sharding does not fix hot keys.** Adding nodes divides the *key space*, not the traffic to any individual key.

## 4. Detailed technical explanation

### 4.1 The stampede mechanism, precisely

Consider one key, `homepage:bestsellers`, under cache-aside (Lesson 28):

```
if (value = cache.get(key)) == null:      # ← every process evaluates this independently
    value = database.query()               # ← expensive: 400 ms
    cache.set(key, value, ttl)
return value
```

At time T the TTL expires. Between T and T + 400 ms, the key is absent and the first request's refill has not completed. **Every request arriving in that window sees a miss and starts its own database query.**

The size of the herd is given directly by Little's Law:

```
herd size = request rate for the key × time to repopulate
```

At 12,000 requests/second for this key and 400 ms to repopulate: **4,800 concurrent database queries**, for a value that one query could have produced.

Three amplifications make it worse than that number suggests:

1. **Connection-pool exhaustion.** The pool has, say, 200 connections. The other 4,600 requests queue for a connection, time out, and the user sees an error — including users requesting *completely unrelated* pages, because the pool is shared. A single key's stampede causes a site-wide outage.
2. **Retry amplification.** Clients retry on timeout. A 4,800-request herd that times out and retries twice becomes 14,400. Retries must have exponential backoff *with jitter* and a circuit breaker (Lesson 17), or the herd re-forms repeatedly.
3. **The query gets slower under load.** The 400 ms figure was measured on an idle database. At 4,800 concurrent queries it may take 4 seconds — which widens the stampede window tenfold, which admits ten times as many requests into the herd. **The stampede is self-reinforcing.** This positive feedback loop is why stampedes produce outages rather than blips.

### 4.2 Stampede mitigations, compared

#### (a) Request coalescing / single-flight

One process (or one thread per process) is elected to perform the refill; all other concurrent requests for the same key wait for its result.

```
in-flight map: key → shared future
  request 1 : no entry → create future, query the DB
  requests 2..4800 : entry exists → await the same future
```

- **Effect:** N concurrent misses → 1 query, **per process**. With 40 application processes you get 40 queries, not 4,800. A 120× reduction from one data structure.
- **Scope limitation, stated clearly:** Go's `singleflight`, Java's `Caffeine` loading cache, and Python's per-process lock all coalesce *within one process only*. They do not coordinate across servers. For cross-process coalescing you need (b) or (c), or a proxy-level mechanism such as nginx's `proxy_cache_lock`.
- **Cost:** the waiting requests experience the full refill latency. You must cap the wait (a *coalescing timeout*) or a slow refill stalls everyone.

#### (b) Distributed mutex lock key

Use the cache itself as the lock, so coalescing works across all processes:

```
SET lock:homepage:bestsellers <uuid> NX EX 10
   NX  = only set if it does not exist → exactly one winner, cluster-wide
   EX 10 = the lock self-releases after 10 s, so a crashed holder cannot deadlock
```

The winner queries the database and repopulates. Losers either (i) wait briefly and re-read the cache, or (ii) return the stale value, or (iii) return a degraded response.

- **Effect:** exactly **one** database query cluster-wide. The strongest reduction available.
- **Costs and traps:** the lock TTL must exceed the worst-case refill time or two holders can run concurrently; the holder must delete the lock **only if it still owns it** (compare the UUID in a Lua script) or it may delete a successor's lock; and if losers *block*, you have converted a database stampede into a latency spike for everyone. Blocking with no timeout is a common way to turn a 5-second blip into a 60-second outage.
- **A correctness caveat worth saying aloud in interviews:** a single-instance Redis lock is not a *correct* distributed lock under failover — it is a best-effort optimisation. That is fine here, because the worst case of a lock failure is two database queries, not data corruption. Never use this pattern where correctness depends on mutual exclusion; that requires a consensus system (**Phase 3, Topic 12 — Distributed consensus**).

#### (c) Probabilistic early expiration (XFetch)

Rather than waiting for the TTL to expire, each reader independently decides, with a probability that rises as expiry approaches, to refresh *early* while the cached value is still valid and still being served.

Store alongside the value: the time the last recomputation took, `delta`, and the absolute `expiry`. On each read:

```
if now − delta × beta × ln(rand()) ≥ expiry:
        recompute and reset the entry
else:
        return the cached value
```

- `rand()` is uniform on (0, 1), so `ln(rand())` is negative; the term `−delta × beta × ln(rand())` is a positive random lookahead.
- `delta` is the cost of recomputation. **Expensive values are refreshed earlier**, which is exactly right: the more expensive a refill, the more damaging a herd for it.
- `beta` tunes aggressiveness; `beta = 1` is the standard default. Larger values refresh earlier and more often.
- **Effect:** the probability that the key is ever actually *absent* becomes very small, because somebody almost always refreshes it ahead of expiry. The herd never forms because there is never a window with no value.
- **Costs:** a small number of extra recomputations (typically a few percent); the value and its metadata must be stored together; and it only protects keys that are *read frequently* — a key read once every ten minutes gets no early-refresh opportunities.
- Published as "Optimal Probabilistic Cache Stampede Prevention" (Vattani, Chierichetti, Lowenstein, 2015). Knowing the formula and what `delta` means is a strong interview signal.

#### (d) Serve stale while revalidating

Keep two timestamps per entry: a *soft* expiry and a *hard* expiry. Past soft expiry, return the stale value immediately and trigger one background refresh. Past hard expiry, the entry is genuinely gone.

This is `stale-while-revalidate` (Lesson 29) applied inside your own cache. nginx spells it `proxy_cache_use_stale updating` plus `proxy_cache_background_update on`.

- **Effect:** the user-visible miss latency disappears entirely, and the herd collapses to one background refresh.
- **Cost:** users are knowingly served stale data for the revalidation window. Combine with Lesson 29's staleness-tolerance analysis — this is unacceptable for prices at checkout, and ideal for a bestsellers list.
- **This is usually the best first mitigation**, because it improves latency *and* removes the herd, and it requires no locks.

#### (e) Staggered and jittered TTLs

Write each entry with `TTL = base × (1 ± jitter)` — typically ±10 %, sometimes ±50 % for bulk-loaded data.

- **Effect:** this does **not** help a single hot key (one key expires once, whenever it expires). It is the fix for *avalanche* — many keys expiring together — covered in 4.5.
- **Cost:** essentially zero. There is no good reason not to do it, which is why it should be a default in your cache wrapper rather than a per-call decision.

#### Comparison

| Mitigation | DB queries per expiry event | Scope | User-visible latency | Complexity | Main risk |
|---|---|---|---|---|---|
| Nothing | `rate × refill_time` (thousands) | — | Catastrophic | None | Outage |
| In-process coalescing | 1 per process (tens) | Single process | One refill | Low | Doesn't cross processes |
| Distributed mutex | **1 cluster-wide** | Cluster | One refill, or stale | Medium | Lock TTL and ownership bugs; blocking losers |
| Probabilistic early expiry | ~1, before expiry | Cluster (statistical) | **None** | Medium | Needs frequent reads; metadata per entry |
| Serve-stale + background refresh | 1 background | Cluster | **None** | Low–medium | Serves stale data by design |
| TTL jitter | Unchanged for one key | Cluster | Unchanged | Trivial | Fixes avalanche, not stampede |

**Production systems combine these**: jitter on every write, serve-stale for read-heavy content, and a mutex or single-flight on the few genuinely expensive keys. They are complementary, not alternatives.

### 4.3 Hot keys

A **hot key** is an entry whose request rate is a large fraction of total traffic. Causes are mundane: a celebrity product, a front-page feature, a viral post, a default configuration value read on every request, a feature-flag blob, or a single tenant that is 40× larger than the rest.

**Why hot keys break *sharded* caches specifically.** In a sharded cache (Redis Cluster, or client-side consistent hashing — **Phase 2, Topic 21 — Consistent hashing and virtual nodes**), a key's location is `shard = hash(key) → slot → node`. That mapping is deterministic and *per key*. Therefore:

- All traffic for one key lands on exactly **one** node.
- That node's single-threaded command loop (Redis executes commands on one thread) is a hard per-key ceiling — roughly **80,000–150,000 simple operations/second** on typical hardware, and far less if the value is large.
- **Adding shards does not help at all.** Ten more nodes divide the key *space*; the hot key still has one home. The cluster can be at 8 % average CPU while one node is at 100 % and timing out.
- Network bandwidth binds before CPU for large values: a 400 KB value at 20,000 requests/second is `400 KB × 20,000 = 8 GB/s`, which exceeds a 10 Gbit/s NIC by about 6×. The node is bandwidth-dead long before it is CPU-dead.

**Mitigations:**

1. **Local (L1) replica caching.** Each application process keeps the hot value in its own memory with a very short TTL (1–5 s). With 40 processes and a 2-second local TTL, Redis sees `40 / 2 = 20` requests/second for that key instead of 20,000 — a **1,000× reduction**. The cost is bounded staleness equal to the local TTL, and inconsistency *between* processes during that window. This is Lesson 27's L1/L2 hierarchy applied as an availability measure rather than a latency one, and it is overwhelmingly the most effective hot-key fix.
2. **Key splitting / fanout replication.** Store N copies under derived keys `hot:key#0 … hot:key#9`. Readers pick one at random; writers must update all N. Consistent hashing places the copies on different nodes, so load divides by N.
   - Cost: writes become N× more expensive, and the copies can diverge briefly. Use for read-mostly values.
   - Choosing N: `N ≈ ceil(peak_key_rate / safe_per_node_rate)`, with a safety factor of about 2.
3. **Client-side caching with server invalidation.** Redis 6's RESP3 *client-side caching* has the server track which keys each client has cached and push an invalidation when one changes. This is local caching without the staleness window — the best of both — at the cost of requiring RESP3 support and server-side tracking memory.
4. **Request coalescing at the application tier**, which also flattens read bursts for a hot key.
5. **Detection, which must come first.** `redis-cli --hotkeys` (needs an LFU policy), `MONITOR` sampling (expensive — never leave it running), proxy-level key-frequency counters, or probabilistic counters such as Count-Min Sketch in your client. You cannot mitigate a hot key you have not identified, and hot keys are frequently *not* the ones anyone predicted.

### 4.4 Cache penetration

**Cache penetration** is traffic for keys that do not exist in the source of truth. A read-through or cache-aside flow with no negative caching handles it as: miss → query → zero rows → return 404 → **cache nothing**. Every repeat is another database query. The cache's hit ratio for this traffic is structurally 0 %.

This is distinct from the other failures: a stampede is *too many requests for one real key*; penetration is *requests for keys that can never be cached*. It is also the only one of the four that an attacker can trigger deliberately and cheaply, by generating random IDs.

**Fix 1 — negative caching (Lesson 29, §4.6).** Cache the absence with a sentinel and a short TTL. This eliminates *repeats*. It does **not** help if every request uses a fresh random ID, and it consumes memory proportional to the number of distinct bogus keys — which an attacker controls. Cap it: either a bounded local negative cache, or rely on eviction, and never give negative entries a long TTL.

**Fix 2 — Bloom filters.**

A **Bloom filter** is a probabilistic set-membership structure. It answers "is X in the set?" with either **"definitely not"** or **"possibly yes"**. It never produces a false negative; it can produce a false positive.

How it works:
- A bit array of `m` bits, all initially 0, and `k` independent hash functions.
- **Insert `x`:** set the bits at positions `h₁(x) mod m … h_k(x) mod m` to 1.
- **Query `x`:** if *any* of those `k` bits is 0, `x` was definitely never inserted. If *all* are 1, `x` is *probably* present — or the bits were set by other elements, which is a false positive.
- **No deletions** (clearing a bit could erase another element). Variants that support deletion — counting Bloom filters, cuckoo filters — exist and cost more space.

The false-positive probability after inserting `n` elements:

```
p = (1 − e^(−k·n/m))^k
```

Minimised when `k = (m/n)·ln2`, which gives the two sizing formulas you should memorise:

```
m = −(n · ln p) / (ln 2)²        bits required
k = (m/n) · ln 2                 optimal number of hash functions
```

A useful rule of thumb that follows: **about 9.6 bits per element for a 1 % false-positive rate**, and each additional ~4.8 bits per element divides the false-positive rate by ten — *regardless of element size*. A Bloom filter holding two million 13-character ISBNs needs about 2.4 MB; storing the ISBNs themselves in a hash set would need roughly 2 million × (13 + ~50 bytes overhead) ≈ 126 MB. That ~50× saving, with no false negatives, is why Bloom filters are the standard answer here. Section 9 Part C works the sizing fully.

Deployment shape for BookNook: build the filter from `SELECT isbn FROM books` at startup, keep it in each application process's memory, add every newly created ISBN to it, and rebuild it nightly (because you cannot delete). Check the filter *before* touching Redis. A "definitely not present" answer returns 404 in microseconds with zero network calls.

**The false positive is harmless here** — it means a non-existent ISBN slips through to the normal cache-aside path, which then negative-caches it. The two techniques compose: the Bloom filter stops ~99 % of first-time bogus lookups, and negative caching stops the repeats among the 1 % that got through.

**Fix 3 — input validation.** An ISBN-13 has a check digit. Validating it rejects the overwhelming majority of randomly generated identifiers for free, before any lookup at all. Use opaque, validatable identifiers (UUIDs, HMAC-signed IDs) for public endpoints and a large class of enumeration attacks disappears. The cheapest fix is often not a cache fix.

**Fix 4 — rate limiting per client** on 404-heavy traffic (**Phase 4, Topic 9 — Rate limiting**).

### 4.5 Cache avalanche

An **avalanche** is mass simultaneous expiry: thousands of *different* keys becoming invalid in the same instant, so the miss rate jumps to near 100 % and the database receives full uncached load.

Causes, all of which are routine operations:
- A cache warm-up script loading 50,000 keys in two minutes, all with `EX 600`. Ten minutes later they expire in the same two-minute window — and then again every ten minutes, because the refills are themselves synchronised. **Synchronised expiry is self-perpetuating.**
- A deploy that changes the key prefix, or a namespace version bump (Lesson 29 §9E).
- A `FLUSHALL`, or a Redis restart without persistence, or a failover to an empty replica.
- A scheduled batch job that rewrites a large set of keys at the same minute each hour.

**Why TTL jitter fixes it.** Write `TTL = base × (1 + U(−j, +j))`. The expiries of a bulk-loaded set then spread uniformly across a window of `2 × j × base` seconds instead of landing in one instant:

```
50,000 keys, base TTL 600 s, jitter ±10 %
Expiry window  = 2 × 0.10 × 600            = 120 s
Refill rate    = 50,000 / 120              = 417 queries/s     ← absorbable
Without jitter = 50,000 in ~1 s            = 50,000 queries/s  ← outage
Improvement factor                          = 120×
```

Two further points:

- Jitter also **de-synchronises future cycles**: because each key's next expiry is independently randomised again, the population stays spread rather than re-clustering. Without jitter, a single synchronised event echoes forever at the TTL period.
- Jitter does **nothing** for a Redis restart or a flush, where keys vanish regardless of TTL. For those you need a **warm-up** (populate the top-N keys before accepting traffic), a **gradual traffic ramp** on a cold cache, and a database sized to survive a meaningful fraction of uncached load — Lesson 27 suggested ~40 % as a reasonable target. A cold-cache event is a capacity problem, not a TTL problem, and no amount of jitter will save you from it.

### 4.6 Eviction policies

When a cache reaches its memory limit, it must either remove something or refuse writes. **Eviction is distinct from expiry**: expiry is "this entry's time was up"; eviction is "I needed the space". They produce identical-looking misses and have completely different remedies, so the first diagnostic question in any hit-ratio regression is *which one is it* — in Redis, `INFO stats` separates `expired_keys` from `evicted_keys`.

| Policy | Rule | Strength | Weakness | Correct when |
|---|---|---|---|---|
| **LRU** (least recently used) | Evict the entry unused for the longest | Matches temporal locality; cheap approximations exist | A single large scan (a crawler, an analytics job) touches everything once and flushes the whole working set | **Default for most workloads**; recency predicts reuse |
| **LFU** (least frequently used) | Evict the entry with the lowest access count | Immune to scan pollution; keeps genuinely popular items | Needs ageing, or yesterday's hit stays forever; slightly more metadata | Stable popularity distributions; catalogues; protection against scans |
| **FIFO** | Evict the oldest inserted, regardless of use | Trivial, O(1), no per-access bookkeeping | Evicts hot entries just because they are old | Streams, logs, append-only data where age is the right signal |
| **Random** | Evict a uniformly random entry | O(1), no metadata, surprisingly decent | Can evict the hottest key | Huge caches where metadata cost dominates; near-uniform access |
| **TTL-based** (shortest remaining first) | Evict whatever expires soonest | Respects explicit freshness intent | Ignores popularity entirely | Mixed-TTL workloads where freshness ranks above hit ratio |
| **Segmented LRU / 2Q / W-TinyLFU** | Admission control plus multiple LRU segments | Near-optimal in practice; resists scans and one-hit wonders | More complex; implementation-specific | Modern library caches (Caffeine) and CDNs |

**Redis's `maxmemory-policy` values**, which are a standard interview question:

| Policy | Candidate set | Behaviour |
|---|---|---|
| `noeviction` | — | **Refuses writes** once `maxmemory` is reached (see below). Reads still work. |
| `allkeys-lru` | Every key | Approximated LRU across everything. **The usual choice for a pure cache.** |
| `allkeys-lfu` | Every key | Approximated LFU with ageing. Better under scans and skewed popularity. |
| `allkeys-random` | Every key | Random. Cheapest; use when access is near-uniform. |
| `volatile-lru` | Only keys **with a TTL set** | LRU among expirable keys; keys without a TTL are never evicted |
| `volatile-lfu` | Only keys with a TTL | LFU among expirable keys |
| `volatile-random` | Only keys with a TTL | Random among expirable keys |
| `volatile-ttl` | Only keys with a TTL | Evicts the shortest remaining TTL first |

**The `allkeys-*` versus `volatile-*` decision is the one that causes outages.** The `volatile-*` policies only consider keys that have a TTL. If your instance mixes cache entries (with TTLs) and persistent data (sessions, queues, counters — without TTLs), `volatile-lru` protects the persistent data, which is exactly what you want. But there is a trap: **if no key has a TTL, `volatile-*` has nothing to evict and behaves exactly like `noeviction`** — writes start failing while memory sits full of untouchable keys. This is a genuinely common production failure, and it is a favourite interview follow-up.

**What `noeviction` does when memory is full**, precisely:

- Commands that *add* data — `SET`, `LPUSH`, `HSET`, `INCR` on a new key, `SADD` — return the error `OOM command not allowed when used memory > 'maxmemory'`.
- Read commands (`GET`, `EXISTS`) and most deletes (`DEL`, `EXPIRE`) still succeed, so the instance looks "up" to a health check that only reads.
- In a cache-aside application this is uniquely nasty: reads miss, the refill `SET` fails with an OOM error, and *every subsequent read for that key misses again*. The cache stops absorbing load entirely while still appearing healthy. **You get the full uncached database load plus a flood of errors.**
- Therefore: `noeviction` is correct **only** when Redis is being used as a durable store or queue where silently discarding data is unacceptable, and you have alerting on `used_memory` with real headroom. For a cache, `noeviction` converts a graceful degradation (slightly lower hit ratio) into an outage.

**Redis's LRU is approximate, deliberately.** Exact LRU requires a linked list touched on every access, costing memory and time. Redis instead samples `maxmemory-samples` keys (default 5) and evicts the best candidate among them. At 5 samples the choice is close to true LRU; at 10 it is very close, at roughly 2× the eviction CPU. This is the same "power of sampling" idea as power-of-two-choices in Lesson 23: a tiny random sample captures nearly all the benefit of a full scan at a fraction of the cost.

**The metric that tells you which problem you have:**

```
evicted_keys > 0 and rising  →  CAPACITY problem   → more memory, smaller values, shorter TTLs
expired_keys dominant        →  TTL problem        → longer TTLs, or explicit invalidation
keyspace_misses on keys that
  were never written          →  PENETRATION       → negative caching, Bloom filter
one node's CPU ≫ others      →  HOT KEY           → local replica, key splitting
miss rate spikes to ~100 %    →  AVALANCHE / cold  → jitter, warm-up, gradual ramp
```

## 5. Request / data flow

BookNook's `homepage:bestsellers` key expires at 20:00:00 during evening peak. Here is the full sequence **with the mitigations in place**, which is also the order in which each defence engages:

1. A request arrives at app server 7 and calls `get_bestsellers()`.
2. **L1 check:** the process-local cache holds a copy with a 2-second TTL, written 1.4 s ago. Still valid → returned in ~0.01 ms. *Most requests stop here; this alone removes ~99 % of Redis traffic for this key.*
3. 0.6 s later the L1 entry expires. The next request falls through to L2.
4. **Probabilistic early expiration check.** The process reads `(value, delta=400 ms, expiry=20:00:00)` from Redis. It is 19:59:58.2. It evaluates `now − delta × beta × ln(rand())`. With `rand() = 0.31`, `−ln(0.31) = 1.171`, so the lookahead is `0.4 × 1 × 1.171 = 0.468 s`, giving `19:59:58.668 < 20:00:00` → **not yet**; serve the cached value.
5. At 19:59:59.7, another request draws `rand() = 0.08`: `−ln(0.08) = 2.526`, lookahead `1.01 s`, so `20:00:00.71 ≥ 20:00:00` → **this request volunteers to refresh early**, while the old value is still valid and still being served to everyone else.
6. **Single-flight guard.** The volunteer calls `SET lock:bestsellers <uuid> NX EX 10`. It wins (`NX` succeeds), so exactly one refresh runs cluster-wide.
7. Any other process that also volunteers in the same instant finds `NX` fails, so it **does not block** — it simply returns the still-valid cached value. No user waits.
8. The volunteer executes the 400 ms aggregation query against PostgreSQL. **One query, not 4,800.**
9. The volunteer writes `SET bestsellers <json> EX 540` where `540 = 600 × (1 + U(−0.1, +0.1))` evaluated to 0.9 — **jittered**, so this key will not re-synchronise with the other 49,999 keys refreshed around the same time.
10. The volunteer stores the measured `delta = 412 ms` alongside the value for the next XFetch decision.
11. The volunteer releases the lock with a Lua script that deletes it **only if the stored UUID still matches**, so it cannot delete a successor's lock if its own work overran.
12. At no point was the key absent. The TTL at 20:00:00 passes with a fresh value already in place. **The herd never formed.**
13. Had the volunteer crashed at step 8, the lock would self-release after 10 s (`EX 10`) and the key would have expired at 20:00:00, falling back to the next layer: `proxy_cache_use_stale updating` at nginx serves the last good response while one request goes to origin.
14. Had Redis itself failed, the application's circuit breaker (Lesson 17) would open after consecutive timeouts and serve the L1 copy plus a static fallback, admitting only a rate-limited trickle to PostgreSQL rather than the full 12,000 requests/second.

Steps 4–5 are the heart of it: **the refresh happens before the expiry, so there is never a window in which the value is missing.** Every other mitigation in the list is a fallback for when that one fails.

## 6. ASCII diagram

```
STAMPEDE — one key, one expiry, thousands of queries
────────────────────────────────────────────────────────────────────────
  rate = 12,000 req/s for ONE key · refill = 400 ms
  herd = rate × refill = 4,800  (Little's Law)

  t=0.000  TTL expires ─────────────────────────────────────┐
           │                                                 │
  req ───► │ MISS → SELECT (400 ms) ─────────────────────►   │
  req ───► │ MISS → SELECT ──────────────────────────────►   │  4,800
  req ───► │ MISS → SELECT ──────────────────────────────►   │  concurrent
     ...   │          (4,797 more)                           │  queries
  t=0.400  │ first SELECT returns, SET key ◄─────────────────┘
           ▼
  DB pool = 200 connections ⇒ 4,600 requests QUEUE then TIME OUT
  …including requests for UNRELATED pages. One key took the site down.
  …and the DB is now slow, so refill takes 4 s, so the herd is 10× bigger.
                        ▲                                   │
                        └────── SELF-REINFORCING ───────────┘


MITIGATIONS, STACKED  (each line is what reaches PostgreSQL)
────────────────────────────────────────────────────────────────────────
  nothing                 ████████████████████████████████ 4,800 queries
  in-process single-flight ██                                  40  (1/process)
  distributed mutex        ▏                                    1
  XFetch (early refresh)   ▏                                    1  (before expiry)
  serve-stale + bg refresh ▏                                    1  (user waits 0 ms)


HOT KEY — why adding shards changes NOTHING
────────────────────────────────────────────────────────────────────────
  20,000 req/s for "book:celebrity-memoir"
          │
          ▼ hash(key) → slot 7341 → ALWAYS node 2
  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐
  │node 1│ │node 2│ │node 3│ │node 4│ │node 5│ │node 6│
  │  6%  │ │ 100% │ │  7%  │ │  5%  │ │  6%  │ │  7%  │
  └──────┘ └──▲───┘ └──────┘ └──────┘ └──────┘ └──────┘
              │  single-threaded ceiling ≈ 80–150k ops/s
              │  400 KB value × 20k/s = 8 GB/s ≫ 10 Gbit NIC
       ALL the traffic.  Adding node 7 does not move this key.

  FIX A — L1 local replica (2 s TTL, 40 processes):
     Redis sees 40 / 2 = 20 req/s   ⇒ 1,000× reduction
  FIX B — key splitting into 10 copies:
     hot#0 hot#1 hot#2 … hot#9  → spread across nodes → 2,000 req/s each


PENETRATION vs BLOOM FILTER
────────────────────────────────────────────────────────────────────────
  WITHOUT                          WITH BLOOM FILTER (2.4 MB, p=1%)
  req(bogus ISBN)                  req(bogus ISBN)
      │ cache MISS (always)            │
      ▼                                ▼ bloom.check() → "DEFINITELY NOT"
  ┌─────────┐                      ┌────────┐
  │ Postgres│ ← 100% of bogus      │ 404 in │ ← 99% stop here, 0 network calls
  └─────────┘   traffic            │  µs    │
                                   └────────┘
                                        │ 1% false positives fall through
                                        ▼ normal path + negative cache


AVALANCHE — and why jitter fixes it
────────────────────────────────────────────────────────────────────────
  NO JITTER: 50,000 keys written at 10:00:00, all EX 600
    queries/s
    50,000 ┤                    █                    █
           │                    █                    █     ← 50,000 q/s spikes
     3,000 ┤─ DB capacity ──────█────────────────────█────
         0 ┤█████████████████████████████████████████████
            10:00   10:05   10:10   10:15   10:20
                            ▲ OUTAGE      ▲ OUTAGE AGAIN (self-perpetuating)

  ±10% JITTER: expiry spread over 2 × 0.1 × 600 = 120 s
    queries/s
     3,000 ┤─ DB capacity ───────────────────────────────
       417 ┤              ▁▂▃▄▅▅▄▃▂▁        ▁▂▃▄▅▄▃▂▁
         0 ┤██████████████          ████████
            10:00   10:05   10:10   10:15   10:20
            Peak 417 q/s = 14% of capacity. 120× improvement. One line of code.


EVICTION — what `noeviction` does to a cache-aside app
────────────────────────────────────────────────────────────────────────
  used_memory == maxmemory
       GET key   → (nil)  MISS              ✓ command allowed
       SELECT …  → 40 ms                    ✓ database hit
       SET key … → OOM command not allowed  ✗ REFILL FAILS
       next GET  → (nil)  MISS              ✗ …forever
  ⇒ hit ratio → 0%, full uncached load on the DB, and Redis still passes
    a read-only health check. Looks healthy. Is an outage.
  FIX: allkeys-lru (or allkeys-lfu). Degrade the hit ratio, not availability.
```

## 7. Detailed example

### BookNook — the Black Friday outage, and the four fixes

BookNook enters Black Friday with the Lesson 29 configuration: delete-on-write, sensible TTLs, 94 % hit ratio, PostgreSQL sized for about 3,000 queries/second. Traffic is 4× normal. Four separate incidents occur in six hours, one for each failure mode in this lesson.

**19:14 — the stampede.** The marketing team features one title, *The Midnight Library*, on the homepage, in an email to 900,000 subscribers, and in a push notification, all firing at 19:00. By 19:14 that single book's detail endpoint is receiving **12,000 requests/second**, and the composed cached object (book + author + reviews summary + "customers also bought") takes **400 ms** to build because the recommendation join is expensive.

At 19:14:32 the key's 300-second TTL expires. Little's Law: `12,000 × 0.4 = 4,800` concurrent misses. The connection pool (200 connections) exhausts in approximately 17 milliseconds. Requests for *every other page* begin failing because they cannot obtain a connection. Database CPU goes to 100 %, so the aggregation query slows from 400 ms to 3.8 s, widening the window and admitting ~45,600 requests into the herd. p99 goes to 30 s; the checkout flow times out. **Revenue stops for 4 minutes 20 seconds.**

The fix shipped at 19:31 was two lines: a distributed mutex lock key around the refill, and `stale-while-revalidate` semantics so that losers return the previous value instead of waiting. Database queries for that key dropped from 45,600 per expiry to **1**. The deeper fix, shipped the following week, was probabilistic early expiration so the value is refreshed *before* it is ever absent.

**20:47 — the hot key.** With the stampede fixed, the same book now produces a different failure. All 12,000 requests/second for `v7:book:9780525559474` hash to Redis Cluster node 4. Node 4's CPU is at 100 % while nodes 1–3 and 5–6 sit at 6 %. The cached object is 38 KB, so node 4 is pushing `38 KB × 12,000 = 456 MB/s ≈ 3.6 Gbit/s` of a shared 10 Gbit link — and it is serving every *other* key assigned to it badly as a result. Redis `GET` latency on node 4 goes from 0.4 ms to 90 ms, and the application's 50 ms Redis timeout starts firing, which turns cache hits into cache misses, which sends the traffic to PostgreSQL anyway.

Adding two more Redis nodes — the instinctive response — changed nothing, because the key's hash slot did not move to a less busy node; it is one key with one home. The fix was **L1 local caching**: each of the 40 application processes keeps hot books in an in-process LRU cache of 500 entries with a 2-second TTL. Redis traffic for that key fell to `40 processes / 2 s = 20 requests/second`, a **600× reduction**. The cost is up to 2 seconds of staleness and brief disagreement between processes — acceptable for a book page, and explicitly *not* applied to stock or checkout (Lesson 29's rule).

**22:10 — penetration.** A price-comparison bot begins probing 140,000 ISBNs scraped from a publisher catalogue, of which about 96,000 are titles BookNook does not stock. Negative caching (added in Lesson 29) handles the repeats, but the bot does not repeat — it walks the list once. So 96,000 first-time lookups at ~130 requests/second each cost a database query. On a database already at 85 % utilisation from Black Friday traffic, that was enough to push p99 over the timeout threshold.

The fix was a **Bloom filter** built from all 2 million stocked ISBNs: 2.4 MB in each process's memory, 1 % false-positive rate, 7 hash functions. 99 % of the bogus lookups now return 404 in microseconds without touching Redis or PostgreSQL. ISBN check-digit validation was added at the same time, which rejected a further portion for free.

**02:30 — the avalanche.** Overnight, the team runs a cache warm-up script to pre-populate the top 50,000 books ahead of the morning rush. It writes them all in 90 seconds with a uniform `EX 600`. Ten minutes later, all 50,000 expire inside the same 90-second window. Morning traffic is low, so the database absorbs the `50,000 / 90 = 556` queries/second — but the refills are *also* synchronised, so the pattern repeats every 10 minutes, and by 08:00 the 10-minute spike collides with peak traffic and the database saturates for 40 seconds.

The fix was **±10 % TTL jitter** applied centrally in the cache wrapper, so every future write is automatically de-synchronised. Expiry now spreads over a 120-second window and, crucially, the randomisation reapplies on every refill, so the population never re-clusters.

**Where BookNook ended up:**

| Defence | Mechanism | Effect |
|---|---|---|
| Jitter on every write | ±10 % in the cache wrapper | Avalanche spike 50,000 → 417 q/s |
| L1 process cache, 2 s TTL | 500-entry LRU per process | Hot-key load on Redis ÷ 600 |
| XFetch early refresh | `beta=1`, `delta` stored per key | Hot keys are never absent |
| Distributed mutex | `SET lock NX EX 10` | One origin query per expiry, cluster-wide |
| Serve-stale on lock loss | Return previous value | No user waits on a refill |
| Bloom filter + negative cache | 2.4 MB, p = 1 %, 60 s negative TTL | 99 % of penetration stopped |
| `maxmemory-policy allkeys-lru` | Changed from `volatile-lru` | Writes never fail; hit ratio degrades gracefully |

Total code: roughly 200 lines in a shared cache wrapper. Total database load removed on a bad day: between one and two orders of magnitude.

### Second example for contrast — a ticketing system at on-sale

Now a system where the same four problems appear but **three of the four standard mitigations are forbidden**: a concert ticketing platform at the instant tickets go on sale.

- **The stampede is intentional and scheduled.** 400,000 people press buy at 10:00:00 exactly. There is no TTL expiry to jitter and no gradual ramp — the herd is the product.
- **Everything is one hot key.** A single event's seat inventory is *the* contended resource. It cannot be split by key, because the copies would disagree about whether seat 14B is sold, and overselling a seat is a legal and reputational failure, not a cosmetic one.
- **Serve-stale is forbidden.** Showing a stale "available" count is precisely the harm you are trying to prevent. Lesson 29's rule — never serve a purchase decision from cache — binds absolutely here.
- **Local L1 caching is forbidden** for the same reason: 40 processes each holding a 2-second-stale seat map would sell the same seat 40 times.

So the architecture changes shape entirely rather than adding cache defences:
- A **virtual waiting room** admits users at a controlled rate (perhaps 2,000/second) — load *shedding* and *shaping* rather than load absorption (**Phase 4, Topic 9 — Rate limiting and load shedding**).
- Seat inventory moves to a **single-writer in-memory authority per event**, with atomic decrement, rather than a cache in front of a database. Redis is used here as the *source of truth* for the on-sale window, with a durable log behind it, not as a cache at all.
- Writes are **queued**, not synchronous: `POST /reserve` returns `202 Accepted` with a position, and the result is pushed over WebSocket (Lesson 10, Lesson 13).
- Static content — the event page, the seat-map image, the artist description — *is* aggressively cached and CDN-served with long TTLs, because it is read-only. The caching strategy is split by **mutability**, not by endpoint.

The contrast to internalise: BookNook's answer to a hot key is *make more copies*. Ticketing's answer is *make exactly one copy and control the arrival rate at it*. **Replication is the mitigation for read contention; serialisation plus admission control is the mitigation for write contention.** Choosing the wrong one gives you either a melted shard or 400 duplicate tickets.

## 8. Code or configuration example

> **Illustrative only — not production-ready.** Error handling, retries, metrics, tracing, connection pooling, lock-renewal, and clock-skew handling are simplified or omitted. The Redis lock shown is a best-effort optimisation, **not** a correct distributed lock; never use it where correctness depends on mutual exclusion.

### Stampede protection: jitter + single-flight + mutex + XFetch

```python
import json, math, random, threading, time, uuid
import redis

r = redis.Redis(host="cache.booknook.internal", socket_timeout=0.05)

# ---------------------------------------------------------------- 1. TTL JITTER
def jittered(base_ttl, jitter=0.10):
    # Spreads the expiry of a bulk-written key set over 2*jitter*base seconds.
    # Fixes AVALANCHE (many keys together). Does NOT fix a single hot key.
    # Costs nothing. Should be the default in every cache wrapper, not opt-in.
    return int(base_ttl * (1 + random.uniform(-jitter, jitter)))

# ------------------------------------------- 2. IN-PROCESS SINGLE-FLIGHT (per process)
_inflight = {}                       # key -> threading.Event shared by waiters
_inflight_lock = threading.Lock()
_results = {}

def single_flight(key, loader):
    """N concurrent misses in THIS process produce ONE loader call.
       Scope limitation: this does NOT coordinate across servers.
       With 40 processes you still get 40 DB queries, not 1 -- see the mutex below."""
    with _inflight_lock:
        event = _inflight.get(key)
        if event is None:                       # we are the leader
            event = threading.Event()
            _inflight[key] = event
            leader = True
        else:
            leader = False                      # we are a follower

    if not leader:
        # Followers MUST have a timeout. Waiting forever on a slow leader turns a
        # database problem into a total application stall.
        if not event.wait(timeout=2.0):
            raise TimeoutError("coalesced load timed out")
        return _results[key]

    try:
        value = loader()
        _results[key] = value
        return value
    finally:
        event.set()                             # release every follower
        with _inflight_lock:
            _inflight.pop(key, None)            # must run on EVERY path, including
                                                # exceptions, or the key is permanently
                                                # "in flight" and all readers hang

# -------------------------------------- 3. CLUSTER-WIDE MUTEX + 4. XFETCH EARLY REFRESH
BETA = 1.0          # XFetch aggressiveness; 1.0 is the standard default
LOCK_TTL = 10       # MUST exceed worst-case refill time, or two holders run at once

def get_with_stampede_protection(key, loader, base_ttl=300):
    raw = r.hgetall(key)

    if raw:
        value  = json.loads(raw[b"value"])
        delta  = float(raw[b"delta"])      # how long the LAST recomputation took
        expiry = float(raw[b"expiry"])     # absolute unix time of hard expiry
        now    = time.time()

        # XFetch: "Optimal Probabilistic Cache Stampede Prevention" (Vattani et al. 2015)
        #   refresh early if:  now - delta * beta * ln(rand()) >= expiry
        # ln(rand()) is negative, so the term is a POSITIVE random lookahead.
        # Expensive values (large delta) volunteer earlier -- exactly right, because
        # an expensive refill is the one whose herd hurts most.
        if now - delta * BETA * math.log(random.random()) < expiry:
            return value                   # still fresh enough; serve it

        # We volunteered to refresh EARLY, while the value is still valid.
        # If we lose the lock race we simply serve the existing value: NOBODY WAITS.
        token = str(uuid.uuid4())
        if not r.set(f"lock:{key}", token, nx=True, ex=LOCK_TTL):
            return value                   # another process is refreshing; serve stale
        try:
            return _recompute(key, loader, base_ttl)
        finally:
            _release_lock(key, token)

    # Hard miss: nothing cached at all (cold start, eviction, or flush).
    # Here somebody MUST wait, so take the lock and coalesce hard.
    token = str(uuid.uuid4())
    if r.set(f"lock:{key}", token, nx=True, ex=LOCK_TTL):
        try:
            return _recompute(key, loader, base_ttl)
        finally:
            _release_lock(key, token)

    # Lost the race on a hard miss. Do NOT query the database -- that is the stampede.
    # Poll briefly for the winner's result, then fail fast rather than piling on.
    deadline = time.time() + 2.0
    while time.time() < deadline:
        time.sleep(0.02)
        raw = r.hgetall(key)
        if raw:
            return json.loads(raw[b"value"])
    raise TimeoutError("cache fill timed out; shed this request instead of hammering DB")

def _recompute(key, loader, base_ttl):
    started = time.time()
    value = loader()                       # the expensive database call
    delta = time.time() - started          # measured cost, stored for the next XFetch
    ttl = jittered(base_ttl)
    pipe = r.pipeline()
    pipe.hset(key, mapping={
        "value":  json.dumps(value),
        "delta":  delta,
        "expiry": time.time() + ttl,
    })
    pipe.expire(key, ttl)                  # HSET does NOT set a TTL; always do this,
                                           # or the key lives forever (Lesson 29)
    pipe.execute()
    return value

_UNLOCK = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""
def _release_lock(key, token):
    # Delete ONLY if we still own it. Without this check, a holder whose work overran
    # its LOCK_TTL would delete a SUCCESSOR's lock, allowing two concurrent refills.
    r.eval(_UNLOCK, 1, f"lock:{key}", token)
```

### Hot keys: L1 local replica and key splitting

```python
from cachetools import TTLCache       # bounded, TTL-aware, per-process

# L1: 500 hottest entries, 2-second TTL. This is the single most effective hot-key fix.
# With 40 processes: Redis sees 40/2 = 20 req/s for a key receiving 12,000 req/s.
# COST: up to 2 s of staleness, and processes may briefly disagree.
# NEVER apply to stock counts, prices at checkout, or authorisation data (Lesson 29).
_l1 = TTLCache(maxsize=500, ttl=2)

def get_book_hot_safe(isbn):
    key = f"v7:book:{isbn}"
    if key in _l1:
        return _l1[key]                                  # ~0.01 ms, zero network
    value = get_with_stampede_protection(key, lambda: db_select_book(isbn))
    _l1[key] = value
    return value

# KEY SPLITTING: N physical copies of one logical key, landing on different shards.
# Use when L1 is not enough or not permitted.
HOT_FANOUT = 10        # N ≈ ceil(peak_key_rate / safe_per_node_rate) × 2 safety factor

def read_split(logical_key):
    # Readers pick a random replica, so traffic divides by HOT_FANOUT across nodes.
    return r.get(f"{logical_key}#{random.randrange(HOT_FANOUT)}")

def write_split(logical_key, value, ttl):
    # Writers must update EVERY copy -- writes become N× more expensive, and the
    # copies can disagree for a few milliseconds. Read-mostly values only.
    pipe = r.pipeline()
    for i in range(HOT_FANOUT):
        pipe.set(f"{logical_key}#{i}", value, ex=jittered(ttl))
    pipe.execute()
```

### Penetration: Bloom filter sizing and use

```python
from pybloom_live import BloomFilter

# m = -(n * ln p) / (ln 2)^2      bits required
# k = (m / n) * ln 2              optimal hash function count
# Rule of thumb: ~9.6 bits per element for p = 1%; each extra ~4.8 bits/element
# divides the false-positive rate by 10, REGARDLESS of element size.
#
# n = 2,000,000 ISBNs, p = 0.01  ->  m ≈ 19.17 Mbit ≈ 2.4 MB, k ≈ 7
# A hash set of the same ISBNs would need ≈ 126 MB. ~50× saving.
isbn_filter = BloomFilter(capacity=2_000_000, error_rate=0.01)

def warm_bloom_filter():
    for (isbn,) in db_query("SELECT isbn FROM books"):
        isbn_filter.add(isbn)
    # Bloom filters CANNOT delete (clearing a bit would erase other elements),
    # so rebuild nightly to reclaim space from removed titles. Counting Bloom
    # filters and cuckoo filters support deletion at a higher space cost.

def get_book_penetration_safe(isbn):
    if not valid_isbn13_checksum(isbn):
        return None            # CHEAPEST fix of all: a check digit rejects most
                               # randomly generated IDs with zero lookups

    if isbn not in isbn_filter:
        return None            # "DEFINITELY NOT PRESENT" -- no false negatives are
                               # possible, so this 404 is SAFE. Microseconds, no I/O.

    # "Possibly present" (includes ~1% false positives). Fall through to the normal
    # path; negative caching absorbs the false positives on repeat.
    return get_book(isbn)
```

### Redis eviction configuration

```ini
maxmemory 4gb
# Reserve headroom: replication buffers, client output buffers, and fragmentation
# are NOT counted the same way, so never set maxmemory to the machine's full RAM.
# ~70-75% of instance RAM is a typical safe ceiling.

maxmemory-policy allkeys-lru
# allkeys-lru  : evict least-recently-used across ALL keys. The right default for a
#                PURE cache -- degrades hit ratio gracefully instead of failing writes.
# allkeys-lfu  : better when a scan or crawler would otherwise flush the working set,
#                because frequency survives a one-pass sweep that recency does not.
# volatile-lru : evicts ONLY keys that have a TTL. Correct when this instance ALSO
#                holds persistent data (sessions, queues) that must never be evicted.
#                TRAP: if NO key has a TTL, volatile-* has nothing to evict and
#                behaves EXACTLY like noeviction -- writes start failing.
# noeviction   : refuses writes with "OOM command not allowed" once full.
#                Reads still succeed, so a read-only health check still passes.
#                In a cache-aside app this is the WORST outcome: every refill SET
#                fails, hit ratio collapses to 0, and the database takes full load
#                while Redis looks healthy. Use ONLY for durable-store use cases.

maxmemory-samples 5
# Redis LRU/LFU is APPROXIMATE by design: it samples N keys and evicts the best
# candidate rather than maintaining an exact global ordering. 5 is near-LRU;
# 10 is very close to exact at roughly double the eviction CPU. Same
# "power of sampling" principle as power-of-two-choices (Lesson 23).

lfu-log-factor 10        # how quickly the LFU counter saturates
lfu-decay-time 1         # minutes before counters halve -- WITHOUT ageing, an item
                         # popular yesterday would never be evicted

# Metrics that distinguish the failure modes (INFO stats):
#   evicted_keys  > 0 and rising -> CAPACITY problem (more memory / smaller values)
#   expired_keys  dominant       -> TTL problem      (longer TTL / explicit invalidation)
#   keyspace_misses on never-written keys -> PENETRATION (Bloom filter / negative cache)
```

## 9. Numerical example

### Part A — The stampede that caused the outage

**Assumptions, stated explicitly:**
- One hot key, `v7:book:9780525559474`, receiving **12,000 requests/second** at peak.
- Repopulating it requires an aggregation query taking **400 ms** on an unloaded database.
- PostgreSQL connection pool: **200** connections, shared by all queries.
- Database capacity: **3,000** simple queries/second before p99 degrades.
- Cache-aside with no stampede protection; TTL 300 s with no jitter.
- Clients retry twice on timeout with no backoff.

**Step 1 — herd size, by Little's Law (`L = λW`):**
```
L = 12,000 req/s × 0.400 s = 4,800 concurrent requests in the miss window
Each one issues its own query ⇒ 4,800 concurrent database queries
Useful queries                                               = 1
Wasted queries                 = 4,800 − 1                   = 4,799
Waste ratio                    = 4,799 / 4,800               = 99.98 %
```

**Step 2 — how fast the connection pool dies:**
```
Pool size                                                     = 200 connections
Arrival rate of misses                                        = 12,000 /s
Time to consume the pool       = 200 / 12,000                 = 0.0167 s = 16.7 ms
Requests left without a connection = 4,800 − 200              = 4,600
```
Those 4,600 include requests for *unrelated* pages, because the pool is shared. **A single key's expiry becomes a site-wide outage in under 17 milliseconds.**

**Step 3 — the self-reinforcing loop:**
```
Queries submitted              = 4,800  vs capacity 3,000     = 160 % overload
Query time under that load (measured)                         = 3.8 s  (9.5× slower)
New miss window                                               = 3.8 s
New herd size                  = 12,000 × 3.8                 = 45,600
Amplification                  = 45,600 / 4,800               = 9.5×
```

**Step 4 — retry amplification:**
```
Timed-out requests             ≈ 4,600 (pool) + queued        ≈ 45,400
With 2 retries each            = 45,400 × 3                   = 136,200 attempts
Effective offered load         = 136,200 / 3.8 s              = 35,842 req/s
Against DB capacity            = 35,842 / 3,000               = 1,195 % of capacity
```
Recovery is impossible while retries continue. This is why exponential backoff **with jitter** and a circuit breaker (Lesson 17) are not optional on a cached read path.

**Step 5 — business cost:**
```
Outage duration (observed)                                    = 260 s
Peak-hour order rate                                          = 42 orders/s
Average order value                                           = £31.40
Lost orders                    = 42 × 260                     = 10,920
Direct lost revenue            = 10,920 × £31.40              = £342,888
  (an upper bound: some customers return later — a 40 % recovery
   assumption gives a realistic net of ≈ £205,700)
```

**Step 6 — the mitigations, quantified:**
```
No protection                                   4,800 (→45,600) DB queries
In-process single-flight, 40 processes          40 queries      (120× better)
Distributed mutex (SET NX)                      1 query         (4,800× better)
XFetch early refresh                            1 query, BEFORE expiry — and the
                                                user-visible miss latency is ZERO
```
**One `SET ... NX EX 10` prevented a £206,000 outage.** That is the number worth carrying into an interview.

### Part B — Cache avalanche and the value of jitter

**Assumptions:** a warm-up script writes 50,000 keys in 90 seconds, all with `EX 600`; refill query 8 ms; database capacity 3,000 queries/second.

**Without jitter:**
```
All 50,000 expire within the same 90 s window as they were written.
Worst realistic case: a 1 s burst (keys written in a tight loop)
  Refill rate required          = 50,000 / 1 s        = 50,000 q/s
  Overload factor               = 50,000 / 3,000      = 16.7×
Spread over the full 90 s window:
  Refill rate                   = 50,000 / 90         = 556 q/s
  Utilisation                   = 556 / 3,000         = 18.5 %
  — survivable alone, but it recurs every 600 s and eventually collides with peak:
  At 4× peak traffic the baseline is already 2,550 q/s (85 % utilisation)
  2,550 + 556                   = 3,106 q/s           = 103.5 % of capacity → saturation
```

**With ±10 % jitter:**
```
Expiry window      = 2 × 0.10 × 600                    = 120 s
Refill rate        = 50,000 / 120                      = 417 q/s
Utilisation        = 417 / 3,000                       = 13.9 %
At 4× peak: 2,550 + 417                                = 2,967 q/s = 98.9 % — marginal
```

**With ±25 % jitter:**
```
Expiry window      = 2 × 0.25 × 600                    = 300 s
Refill rate        = 50,000 / 300                      = 167 q/s
Utilisation        = 167 / 3,000                       = 5.6 %
At 4× peak: 2,550 + 167                                = 2,717 q/s = 90.6 % — safe
Improvement vs the 1 s burst case = 50,000 / 167       = 299×
```

**Interpretation.** Jitter width is a direct, linear lever on refill rate: `refill_rate = keys / (2 × jitter × base_TTL)`. For bulk-loaded key sets, ±10 % is often not enough; ±25 % to ±50 % is appropriate, and the staleness cost is trivial because the base TTL already encodes your staleness tolerance (Lesson 29). The second-order benefit matters as much: because each refill re-randomises, the population **never re-synchronises**, whereas without jitter one synchronised event echoes at the TTL period indefinitely.

### Part C — Bloom filter sizing, worked fully

**Assumptions:** 2,000,000 stocked ISBNs; target false-positive rate 1 %; 13-character ISBN strings.

**Step 1 — bits required:**
```
m = −(n · ln p) / (ln 2)²

ln(0.01)   = −4.60517
(ln 2)²    = (0.693147)² = 0.480453

m = −(2,000,000 × −4.60517) / 0.480453
  = 9,210,340 / 0.480453
  = 19,170,116 bits
  = 19,170,116 / 8      = 2,396,265 bytes
  = 2,396,265 / 1,048,576 = 2.29 MiB  (≈ 2.4 MB)
```

**Step 2 — bits per element:**
```
19,170,116 / 2,000,000 = 9.585 bits per element
```
This is the rule of thumb: **~9.6 bits per element for p = 1 %**, independent of how long the ISBNs are.

**Step 3 — optimal hash functions:**
```
k = (m/n) · ln 2 = 9.585 × 0.693147 = 6.643  →  round to k = 7
```

**Step 4 — verify the achieved false-positive rate with k = 7:**
```
p = (1 − e^(−k·n/m))^k
k·n/m    = 7 × 2,000,000 / 19,170,116 = 0.73031
e^(−0.73031)                           = 0.48177
1 − 0.48177                            = 0.51823
p = 0.51823^7
  0.51823² = 0.26856
  0.51823⁴ = 0.26856² = 0.07212
  0.51823⁷ = 0.07212 × 0.26856 × 0.51823 = 0.01004
p ≈ 1.004 %   ✓ matches the target
```

**Step 5 — compare with alternatives:**
```
Bloom filter (p=1%)                                     = 2.29 MiB
Python set of 2M 13-char strings ≈ 2M × 63 B            ≈ 120 MiB   (52× larger)
Redis SET of 2M members ≈ 2M × 70 B                     ≈ 133 MiB   (58× larger)
Bloom filter at p = 0.1 % : 14.38 bits/elem             = 3.43 MiB  (+1.14 MiB for 10× fewer FPs)
Bloom filter at p = 10 %  :  4.79 bits/elem             = 1.14 MiB
```
Each 10× reduction in false-positive rate costs a further `ln10/(ln2)² ≈ 4.79` bits per element — a **constant** additive cost, which is why very low false-positive rates are affordable.

**Step 6 — effect on the attack from Section 7:**
```
Bogus first-time lookups                                = 96,000
Reaching the database with the filter = 96,000 × 0.01   = 960
Reduction                                               = 99 %
Rate before      = 130 q/s ;  after = 1.3 q/s
Memory cost      = 2.29 MiB per process × 40 processes  = 91.6 MiB total
```
91 MB of RAM across the fleet to remove 99 % of a penetration attack. If the per-process copy were too expensive, the alternative is a shared Redis Bloom filter (RedisBloom module) at one network hop — slower per call, 2.29 MiB total.

### Part D — Hot key capacity

**Assumptions:** 12,000 requests/second for one key; value size 38 KB; Redis node ceiling ~120,000 simple ops/second; NIC 10 Gbit/s shared with other keys; 40 application processes.

**Step 1 — is the node CPU-bound or bandwidth-bound?**
```
CPU headroom       = 12,000 / 120,000                     = 10 %   → fine on ops
Bandwidth          = 38 KB × 12,000 = 456,000 KB/s        = 456 MB/s
                   = 456 × 8                              = 3,648 Mbit/s = 3.65 Gbit/s
Share of a 10 Gbit NIC                                     = 36.5 %
```
**The node is bandwidth-constrained, not CPU-constrained** — and 36.5 % of the NIC for *one key* starves every other key on that node, which is exactly what produced the 90 ms `GET` latency in Section 7. Always check both dimensions; practitioners reflexively check ops/second and miss bandwidth.

**Step 2 — L1 local caching, 2-second TTL, 40 processes:**
```
Redis requests for this key = processes / local_TTL = 40 / 2  = 20 req/s
Reduction                   = 12,000 / 20                     = 600×
Bandwidth at Redis          = 38 KB × 20                      = 760 KB/s = 0.006 Gbit/s
Worst-case staleness                                          = 2 s
Extra memory per process    = 500 entries × 38 KB             = 19 MB
Across 40 processes                                           = 760 MB total
```

**Step 3 — key splitting as an alternative (if L1 staleness were unacceptable):**
```
Safe per-node rate (50 % of ceiling)                          = 60,000 ops/s
By ops:        N = ceil(12,000 / 60,000)                      = 1  (not the constraint)
By bandwidth, target ≤ 10 % of NIC per key per node = 1 Gbit/s = 125 MB/s
               N = ceil(456 MB/s ÷ 125 MB/s)                  = 4
With a 2× safety factor                                        = 8 copies
Per-node load  = 12,000 / 8                                    = 1,500 req/s, 57 MB/s
Write cost     = 8× (every update writes 8 keys)
```

**Step 4 — the fix that beats both:** shrink the value. The 38 KB object includes the "customers also bought" block and full review text.
```
Split the key: book core 3 KB (hot) + recommendations 35 KB (separately cached)
Bandwidth for the hot core = 3 KB × 12,000      = 36 MB/s = 0.29 Gbit/s
Reduction vs original                            = 12.7×
```
**The cheapest hot-key fix is usually to make the value smaller, not to make more copies.** Both halves also then get independent TTLs, which is Lesson 29's per-field-tolerance principle paying off a second time.

### Part E — Eviction and `noeviction`

**Assumptions:** 4 GB `maxmemory`; 1.4 M cached entries averaging 2.4 KB + 90 B overhead; 6,000 read requests/second; 94 % hit ratio; database capacity 3,000 q/s.

**Step 1 — does the working set fit?**
```
Per entry            = 2,400 + 90                        = 2,490 B
1.4 M entries        = 1,400,000 × 2,490                 = 3,486,000,000 B = 3.25 GiB
Usable after ~25 % headroom for replication/client buffers/fragmentation
                     = 4 GiB × 0.75                      = 3.0 GiB
3.25 GiB needed vs 3.0 GiB usable                        → OVER by 0.25 GiB (8.3 %)
⇒ The cache is in continuous eviction. evicted_keys will be non-zero and rising.
```

**Step 2 — with `allkeys-lru`:**
```
~8 % of entries cannot be held. Because access is Zipf-distributed, the evicted 8 %
is the COLDEST 8 %, which carries far less than 8 % of traffic — approximately 2 %.
New miss rate      = 6 % + 2 %                           = 8 %
New hit ratio                                            = 92 %
DB load            = 6,000 × 0.08                        = 480 q/s
Utilisation        = 480 / 3,000                         = 16 %   → SAFE, degraded
```

**Step 3 — with `noeviction`:**
```
Memory is full ⇒ every refill SET returns "OOM command not allowed".
Misses can never be repopulated. Over time, expiry drains the keyspace and
nothing replaces it, so the hit ratio decays toward 0.
Steady-state DB load = 6,000 × 1.00                      = 6,000 q/s
Utilisation          = 6,000 / 3,000                     = 200 %  → OUTAGE
And Redis still answers GET, so a read-only health check reports HEALTHY.
```

**Step 4 — with `volatile-lru` but 30 % of keys having no TTL:**
```
Evictable keys       = 70 % of 1.4 M                     = 980,000
Evictable bytes      = 980,000 × 2,490                   = 2.44 GiB
Non-evictable bytes  = 420,000 × 2,490                   = 1.05 GiB
Usable                                                    = 3.0 GiB
Space left for evictable data = 3.0 − 1.05               = 1.95 GiB
Entries that fit     = 1.95 GiB / 2,490 B                = 841,000 of 980,000
Shortfall            = 139,000 entries (14 % of the evictable set)
⇒ Works, but the TTL-less 1.05 GiB is permanently protected. If that grows past
  3.0 GiB, volatile-lru has NOTHING left to evict and degrades to noeviction:
  the Step 3 outage, arriving silently as data grows.
```

**Step 5 — the decision:**
```
allkeys-lru   : 92 % hit ratio, 480 q/s, graceful           ← correct for a pure cache
allkeys-lfu   : ~93 % (resists scan pollution), 420 q/s     ← better under crawlers
volatile-lru  : works only while TTL-less data stays small  ← mixed-use instances
noeviction    : 200 % DB overload, silent, health-check-green ← never for a cache
Right answer: allkeys-lru now, +2 GiB of memory, and MOVE the TTL-less session data
to its own Redis instance so cache and durable data never compete for memory.
```

## 10. Trade-offs

| Decision | Gain | Cost | Choose it when |
|---|---|---|---|
| **In-process single-flight** | N misses → 1 query per process; trivial to add | Doesn't coordinate across servers; followers wait; a leaked in-flight entry hangs readers | Always — it is nearly free and strictly better than nothing |
| **Distributed mutex lock** | Exactly 1 origin query cluster-wide | Lock-TTL and ownership bugs; blocking losers converts a DB problem into a latency problem | Expensive refills (> ~100 ms) on hot keys |
| **Probabilistic early expiry** | The key is never absent; zero user-visible miss latency | A few percent extra recomputations; metadata per entry; useless for rarely-read keys | Hot, expensive, frequently-read keys |
| **Serve-stale + background refresh** | Best latency *and* herd removal; no locks | Users are knowingly served stale data | Read-heavy content with real staleness tolerance |
| **TTL jitter** | Avalanche spike ÷ (2 × jitter × TTL); de-synchronises future cycles | None worth mentioning | Always, by default, in the cache wrapper |
| **L1 local cache** | Hot-key load ÷ (processes / L1 TTL); sub-microsecond reads | Staleness up to the L1 TTL; processes disagree; memory × process count | Hot keys; small values; staleness-tolerant data |
| **Key splitting** | Divides hot-key load by N across shards | Writes N× costlier; replicas can diverge; more key-space complexity | Read-mostly hot keys where L1 staleness is unacceptable |
| **Client-side caching (RESP3)** | Local speed without the staleness window | Needs RESP3 and server-side tracking memory; client support varies | Modern stacks with a tight consistency need on hot keys |
| **Negative caching** | Removes repeated lookups of absent keys | Memory controlled by the attacker; new records appear late | Any endpoint keyed by a user-supplied identifier |
| **Bloom filter** | 99 % of *first-time* bogus lookups stopped; ~9.6 bits/element | Cannot delete (rebuild periodically); false positives; must be kept in sync with creates | Large, enumerable key spaces facing scanners or attackers |
| **`allkeys-lru`** | Graceful degradation; writes never fail | Evicts anything, including data you assumed persistent | Pure caches — the default |
| **`volatile-lru`** | Protects TTL-less persistent data on a shared instance | **Degrades to `noeviction` if no key has a TTL** | Mixed cache/persistent instances, with monitoring |
| **`noeviction`** | Nothing is silently discarded | Writes fail; cache-aside refills break; hit ratio → 0 while health checks pass | Redis as a durable store/queue, never as a cache |
| **`allkeys-lfu`** | Survives scans and crawlers that would flush an LRU | Slightly more metadata; needs ageing configured | Stable popularity distributions; crawler-heavy traffic |

Three trade-offs that are frequently stated backwards:

- **Coalescing trades database load for user latency.** Every waiter is paying the refill time. If the refill is 4 seconds and 10,000 requests are waiting, you have protected the database and created a 4-second p99. Serve-stale avoids this; blocking coalescing does not. Always cap the wait.
- **L1 caching trades consistency for availability, not for speed.** The speed is a bonus; the reason to deploy it on a hot key is that it removes a hard per-shard ceiling that sharding cannot remove.
- **A higher hit ratio is not always the goal.** `allkeys-lru` at 92 % keeps the service up; `noeviction` protecting a "100 % correct" memory footprint takes it down. Availability outranks hit ratio.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| Stampede on a hot key | Site-wide errors and 30 s p99 for minutes, including on unrelated pages | DB connection-pool exhaustion; QPS spike with a simultaneous miss spike on one key; DB CPU at 100 % | Shed load, let the pool drain, repopulate the key manually; permanently fix with a mutex plus serve-stale and XFetch |
| Retry storm after a stampede | Errors continue long after the original cause has passed | Request rate far exceeding real user traffic; retry counters climbing | Exponential backoff with jitter plus a circuit breaker (Lesson 17); shed at the edge until load subsides |
| Hot key saturating one shard | Intermittent slow pages tied to specific popular items; other items fine | One cluster node at ~100 % CPU or NIC while the rest idle; `redis-cli --hotkeys` | L1 local cache immediately; then key splitting or a smaller value |
| Blocking coalescing with no timeout | Every request for the key hangs; worker threads exhaust; the whole app stalls | Thread-pool saturation with low CPU; many threads parked on one lock | Add a wait cap; serve stale on timeout; never block unbounded |
| Mutex holder crashes mid-refill | A short gap where the key stays missing and the next arrivals queue | Lock key present with no corresponding refill activity | Lock TTL expires (`EX 10`) and a new holder takes over; this is why locks must have TTLs |
| Lock deleted by a slow previous holder | Two or more concurrent refills; a mild stampede; possibly an older value written last | Duplicate expensive queries in DB logs for the same key | Compare-and-delete the lock by UUID in a Lua script |
| Cache avalanche after a warm-up or deploy | Site-wide slowdown for tens of seconds, repeating at the TTL period | Miss rate spiking to ~100 % on a periodic cycle; `expired_keys` arriving in bursts | TTL jitter; stagger the warm-up; refill in waves |
| Cold cache after a Redis failover or restart | Full uncached load instantly; often a total outage during peak | `used_memory` near zero; hit ratio collapses to 0; DB QPS jumps 10–20× | Gradual traffic ramp; pre-warm the top-N keys before accepting traffic; size the DB for ~40 % of uncached load |
| Cache penetration by a scanner | General slowness; elevated 404 rate; the DB is busy with zero-row lookups | High `keyspace_misses` on keys never written; 404 rate up; one client IP dominating | Negative caching plus a Bloom filter; ID validation; rate-limit 404-heavy clients |
| Negative-cache memory exhaustion | Eviction of *real* entries, so the hit ratio drops for legitimate traffic | `evicted_keys` rising together with a flood of distinct bogus keys | Short negative TTLs, a bounded local negative cache, and a Bloom filter in front so bogus keys never get cached at all |
| `noeviction` with full memory | Writes fail deep in the code; hit ratio decays to zero; full load on the DB — while Redis passes its health check | `OOM command not allowed` in application logs; `used_memory == maxmemory`; `evicted_keys == 0` despite memory pressure | Switch to `allkeys-lru`; add memory; alert on `used_memory / maxmemory > 0.8` |
| `volatile-*` with no TTL-bearing keys | Identical to `noeviction` and even more confusing, because a policy *is* configured | `evicted_keys == 0` with memory full; `INFO keyspace` shows `expires` ≈ 0 | Set TTLs on cache keys, or switch to `allkeys-lru`; separate durable data onto its own instance |
| Scan pollution under LRU | Hit ratio drops sharply for hours after a crawler or analytics job | Hit ratio falls with no traffic-mix change; correlates with a batch job window | Switch to `allkeys-lfu`; or route batch jobs to a replica so they never touch the cache |
| L1 caches disagreeing between processes | A user refreshes and sees the value flip back and forth | Hard to detect; surfaces as confusing bug reports | Shorten the L1 TTL; exclude mutable data from L1; use RESP3 client-side invalidation |

## 12. Alternatives

| Approach | What it solves | Mechanism | Cost | Best for |
|---|---|---|---|---|
| **Do nothing** | — | Hope the key is never hot | Outage when it is | Genuinely low-traffic systems only |
| **Request coalescing (single-flight)** | Stampede | One loader per key per process | Followers wait; in-process scope | Everything; the cheapest first step |
| **Distributed mutex** | Stampede | `SET NX EX` elects one refiller | Lock bugs; blocking risk | Expensive refills on hot keys |
| **Probabilistic early expiry** | Stampede | XFetch: random lookahead scaled by refill cost | Extra recomputations; metadata | Hot, expensive, frequently-read keys |
| **Serve-stale / SWR** | Stampede + latency | Soft and hard expiry; background refresh | Serves stale by design | Read-heavy content |
| **TTL jitter** | Avalanche | Randomised TTL per write | None | Always |
| **Pre-warming + gradual ramp** | Cold cache | Populate top-N before taking traffic | Deploy complexity | Any cache that can restart or fail over |
| **Refresh-ahead / scheduled recompute** | Stampede + latency | A background job rewrites hot keys before expiry | Wasted work on keys nobody reads | A small, known set of very hot keys |
| **L1 in-process cache** | Hot key | Local copy, short TTL | Staleness; memory × processes | Hot read-mostly keys |
| **Key splitting / fanout** | Hot key | N replicas across shards | N× write cost; divergence | Hot keys where L1 staleness is unacceptable |
| **Client-side caching (RESP3)** | Hot key | Server pushes invalidations to clients | Server tracking memory; client support | Modern stacks needing local speed and freshness |
| **Negative caching** | Penetration (repeats) | Cache the absence with a sentinel | Attacker-controlled memory; late creates | Any public identifier endpoint |
| **Bloom filter** | Penetration (first-time) | Probabilistic membership, ~9.6 bits/elem at 1 % | No deletes; false positives | Large enumerable key spaces |
| **ID validation / opaque IDs** | Penetration | Check digits, UUIDs, signed IDs | Design-time only | New APIs; the cheapest fix available |
| **Rate limiting** | Penetration + stampede | Cap per-client request rate | Risk of blocking legitimate bursts | Public APIs (Phase 4, Topic 9) |
| **Load shedding / admission control** | All of them | Reject excess work at the edge, fast | Some users get errors by design | Protecting a system already past capacity |
| **Bigger database / read replicas** | All of them, crudely | Raise the ceiling the stampede hits | Expensive; replica lag; doesn't fix the cause | When the uncached floor is genuinely too high (Lesson 27) |
| **Queue-based write path** | Hot-key *writes* | Serialise contention through a queue | Async semantics | Ticketing, inventory, auctions |

The ordering that experienced engineers apply, cheapest and most general first:

1. **Jitter every TTL.** One line; removes avalanche.
2. **Serve stale while revalidating.** Removes both the herd and the latency cliff.
3. **Coalesce in-process.** Nearly free; 100×-scale reduction.
4. **Add a distributed mutex** only on the few genuinely expensive keys.
5. **Add L1 caching** when a hot key appears, which it will.
6. **Add negative caching, then a Bloom filter**, when penetration appears.
7. **Set `allkeys-lru` and alert on memory** before any of the above matters.
8. **Only then** consider more shards or a bigger database — because sharding does not fix hot keys, and a bigger database does not fix a stampede, it just raises the number of wasted queries it can absorb.

## 13. When to use it

1. **Apply TTL jitter to every cache write, unconditionally.** It costs nothing, it is impossible to get meaningfully wrong, and it removes an entire class of outage. Put it in the shared cache wrapper so it cannot be forgotten.
2. **Apply stampede protection to any key whose refill costs more than ~100 ms or whose request rate exceeds ~100/second.** Those two thresholds, multiplied, give a herd of at least 10 — and herds grow super-linearly because of the self-reinforcing loop in Section 9 Part A.
3. **Apply L1 local caching the moment a single Redis node's CPU or NIC utilisation diverges from its peers.** That divergence is the definitive hot-key signature, and L1 is the only mitigation that works without changing key structure or write paths.
4. **Apply negative caching to every endpoint whose key comes from user input** — ISBN, SKU, username, slug, short code. Without it, the request rate against your database is chosen by whoever is calling you.
5. **Add a Bloom filter when the key space is large, enumerable, and publicly addressable**, and when negative caching alone is insufficient because callers do not repeat. ~2.4 MB for two million keys at a 1 % false-positive rate is almost always worth it.
6. **Set `allkeys-lru` (or `allkeys-lfu`) on any instance used purely as a cache**, and alert when `used_memory / maxmemory` exceeds 0.8 and when `evicted_keys` starts rising.
7. **Prefer `allkeys-lfu` when crawlers, analytics jobs, or backfills sweep your key space.** A single sequential scan flushes an LRU working set entirely; LFU survives it, because one access does not outrank thousands.
8. **Pre-warm and ramp traffic gradually after any cache restart, failover, or flush.** A cold cache is a capacity event, and no TTL setting mitigates it.

## 14. When *not* to use it

1. **Do not add a distributed mutex to cheap refills.** If the refill is 3 ms, the lock round trip (~0.4 ms) plus the lock release is a significant fraction of the work, and the herd would have been small anyway. Reserve it for expensive keys.
2. **Do not use L1 local caching for data that must be consistent across processes** — stock counts, prices at checkout, authorisation decisions, feature flags controlling a payment path. Processes will disagree for the L1 TTL duration, which is exactly the failure Lesson 29 told you to avoid at decision points.
3. **Do not use key splitting for write-heavy keys.** Writes become N× more expensive and the copies diverge. A counter being incremented thousands of times per second is a *write* hot key, which needs sharded counters or a queue, not read replication.
4. **Do not use `noeviction` on a cache.** It converts graceful degradation into a silent outage with green health checks (Section 9 Part E).
5. **Do not rely on negative caching against an attacker who never repeats a key.** It consumes memory proportional to attacker-chosen distinct keys and saves nothing. Use a Bloom filter plus ID validation plus rate limiting.
6. **Do not block coalescing waiters without a timeout.** Protecting the database by stalling every application thread is a trade you did not intend to make.
7. **Do not reach for sharding to solve a hot key.** More nodes divide the key space, not the traffic to one key. This is the single most common wrong answer in interviews on this topic.

## 15. Common mistakes

**Beginner mistakes**
- Writing plain cache-aside with no coalescing and assuming "the first request will refill it" — which is true, and irrelevant, because 4,799 others start simultaneously.
- Using identical TTLs for a bulk-loaded key set, creating an avalanche that then repeats forever at the TTL period.
- Believing that adding Redis nodes fixes a hot key.
- Returning early on a cache miss without caching the absence, so non-existent keys hit the database every time.
- Leaving `maxmemory` unset so Redis grows until the OS OOM-killer terminates it — losing the entire cache instantly and triggering a cold-cache event.
- Not knowing the difference between `evicted_keys` and `expired_keys`, and therefore treating a capacity problem as a TTL problem.
- Retrying failed cache-miss requests without backoff, turning a 5-second stampede into a 60-second retry storm.
- Testing with a warm cache only, so the cold-start behaviour is first observed in production during an incident.
- Assuming a 0.4 ms Redis call is free and making dozens of them per request, so a hot key's problem is multiplied by the call count.

**Experienced-engineer mistakes**
- Implementing a distributed mutex without a lock TTL (deadlock when the holder crashes), or without compare-and-delete on release (deleting a successor's lock and permitting two refills).
- Having the lock losers **block indefinitely**, converting a database saturation into an application-wide thread exhaustion — a strictly worse outcome.
- Choosing `volatile-lru` on a mixed instance and never verifying that enough keys actually carry TTLs, so the policy silently behaves as `noeviction` as the TTL-less data grows.
- Measuring hot keys with `MONITOR` in production and leaving it running — it serialises through the same single thread you are trying to protect.
- Sizing a Bloom filter for today's key count with no headroom; once `n` exceeds the design capacity, the false-positive rate rises steeply and the filter quietly stops helping.
- Forgetting to add newly created IDs to the Bloom filter, so a freshly created book returns a confident "definitely does not exist" — the one case where a Bloom filter *appears* to produce a false negative, because the set it models has drifted from reality.
- Applying `stale-if-error` or serve-stale to data where staleness is a correctness violation, such as prices at checkout or stock counts.
- Enabling XFetch on keys that are read rarely, where no reader ever gets an early-refresh opportunity, and assuming the key is protected when it is not.
- Running a cache warm-up that itself stampedes the database, because the warm-up script has no concurrency limit.
- Setting `maxmemory` to the machine's full RAM, ignoring replication buffers, client output buffers, and allocator fragmentation, then being surprised by an OOM kill at ~75 % reported usage.
- Treating a hot key as purely an ops/second problem and never computing bandwidth — the constraint that actually binds for large values (Section 9 Part D).
- Fixing the stampede and declaring victory, then meeting the hot-key problem thirty minutes later because the two have the same trigger and different mechanisms.

## 16. Interview perspective

This topic separates candidates who have *run* a cache from candidates who have *drawn* one. Nearly everyone can say "add Redis". Very few can say what happens at the instant a popular key expires.

**How to use it:**

1. **Compute the herd size out loud using Little's Law.** *"At 12,000 requests per second for that key and a 400 ms refill, the herd is 12,000 × 0.4 = 4,800 concurrent queries for a value one query could produce. The connection pool has 200 slots, so it's gone in 17 milliseconds and unrelated pages start failing too."* That is the single highest-signal thing you can say on this topic.
2. **Mention the self-reinforcing loop.** *"And because the database is now overloaded, the refill takes 3.8 seconds instead of 400 ms, which makes the herd nearly ten times larger. Stampedes don't plateau, they escalate."*
3. **Give the mitigation ladder, not one answer.** Jitter → serve-stale → in-process single-flight → distributed mutex → XFetch, with the scope limitation of each. Explicitly note that single-flight is per-process and a mutex is cluster-wide — that distinction is what demonstrates real experience.
4. **Know XFetch by name and shape.** *"Probabilistic early expiration — each reader refreshes early with probability rising as expiry approaches, scaled by how expensive the last recomputation was, so expensive keys refresh earlier. The key is never actually absent, so the herd never forms."*
5. **State clearly that sharding does not fix hot keys.** *"The key hashes to one slot on one node. Adding nodes divides the key space, not the traffic to that key. The fixes are a local L1 copy, key splitting with fanout, or making the value smaller."*
6. **Check bandwidth, not just ops/second.** *"38 KB at 12,000 requests per second is 3.6 Gbit/s from one node — that's bandwidth-bound well before it's CPU-bound."* Almost no candidate does this arithmetic.
7. **Explain Bloom filters properly if penetration comes up.** No false negatives, possible false positives, `m = −n·ln p / (ln2)²`, about 9.6 bits per element at 1 %, so two million ISBNs fit in 2.4 MB against about 126 MB for a hash set. Then add: *"False positives are harmless here — they just fall through to the normal path, which negative-caches them."*
8. **Be precise about `noeviction`.** *"Reads still work, writes return OOM. In a cache-aside app the refill SET fails, so the hit ratio decays to zero and the database takes full load — while Redis still passes a read-only health check. For a cache I want `allkeys-lru`: degrade the hit ratio, not availability."*
9. **Name the `volatile-*` trap.** *"If no key has a TTL, `volatile-lru` has nothing to evict and behaves exactly like `noeviction`."* This is a classic follow-up and most candidates have never considered it.
10. **Close with the framing sentence.** *"A cached system is a system whose database can't survive its own traffic — so every one of these problems is really the same problem: something stopped the cache absorbing load, and the floor underneath is 15× higher than the database was built for."*

**Red flags interviewers watch for:**
- Not knowing what happens when a hot key expires, or thinking "the first request refills it" is a complete answer.
- Proposing more cache shards as the fix for a hot key.
- Never mentioning coalescing, locks, jitter, or serve-stale anywhere in a caching design.
- Treating a cold cache or a flush as a minor event rather than a full-load capacity event.
- Describing a Bloom filter as something that "might miss items", i.e. getting the false-positive/false-negative direction backwards.
- Not knowing that `noeviction` makes writes fail, or assuming eviction policies only affect the hit ratio.
- Confusing eviction with expiry, or having no idea which metric distinguishes them.
- Retrying on cache-miss failures with no backoff and no circuit breaker.
- Claiming a cache "just makes things faster" with no acknowledgement that it is now load-bearing infrastructure.
- Offering "it depends" without naming the dependency — on this topic the dependencies are always concrete: request rate for the key, refill cost, staleness tolerance, value size, and whether the data drives a decision or only a display.

## 17. Knowledge check

Write your answers down before continuing.

**Conceptual**

1. Explain the full mechanism of a cache stampede, starting from a single key's TTL expiry and ending in a site-wide outage. Your answer must use Little's Law to size the herd, explain why requests for *unrelated* endpoints also fail, and describe the self-reinforcing feedback loop that makes the herd grow after it forms. Then compare in-process single-flight against a distributed mutex lock key: state precisely what each reduces the origin query count to, what the scope limitation of each is, and what new failure mode each introduces.

2. Explain why a hot key cannot be fixed by adding more cache shards, referring to how a key is mapped to a node and to the per-node ceilings on both operations per second and network bandwidth. Then describe three distinct mitigations, state exactly what each one trades away, and explain which of the three you would refuse to use for a stock count and why — connecting your answer to Lesson 29's rule about decision points.

3. Define cache penetration and distinguish it precisely from a cache stampede and from a cache avalanche. Explain why negative caching fully solves one variant of penetration and not another. Then explain a Bloom filter: what the two possible answers are, which error it can make and which it structurally cannot, why it cannot support deletion, and what the practical consequence is of forgetting to add a newly created record to it.

**Practical scenario**

4. BookNook has launched an author-interview video series. One interview goes viral: 40,000 requests/second arrive for a single page whose cached object is 180 KB and takes 900 ms to compose. The Redis cluster has 6 nodes; the application runs 60 processes; the database sustains 3,500 queries/second; the connection pool has 300 connections; the page's TTL is 600 s with no jitter and no stampede protection. Describe, in order, exactly what fails and when, including which failures are stampede failures and which are hot-key failures and how you would tell them apart from metrics alone. Then specify the complete set of changes you would deploy, in the order you would deploy them during the incident, saying for each one what it fixes, what it costs, and what it does *not* fix. Finally, state which single change you would have made in advance that would have prevented most of this, and justify it.

**Calculation**

5. BookNook's new recommendations service caches 180,000 personalised result sets. Each cached object is 14 KB plus 90 bytes of overhead. Peak traffic is 11,000 requests/second. A cache miss requires a 260 ms model inference. The database/inference tier sustains 900 inferences/second. Redis `maxmemory` is 6 GiB, and you should assume 25 % of that is unusable headroom. The hottest single key receives 2,400 requests/second. There are 50 application processes. Separately, a scraper requests 500,000 user IDs that do not exist, 420,000 of them only once.
   (a) Compute the stampede herd size for the hottest key at expiry, the time taken to exhaust a 300-connection pool, and the overload factor against the inference tier. State whether the system survives.
   (b) All 180,000 keys are warmed in 120 seconds with a uniform 900-second TTL. Compute the refill rate at the resulting avalanche, express it as a percentage of inference capacity, and then compute the jitter percentage required to bring the refill rate below 40 % of capacity. Show the formula you use.
   (c) Compute whether the working set fits in the usable memory. If it does not, compute the shortfall in both bytes and number of entries, and state what `allkeys-lru`, `volatile-lru` (assume 20 % of keys have no TTL), and `noeviction` would each do to the miss rate and to the inference tier's utilisation.
   (d) For the hottest key, compute the Redis request rate with an L1 local cache at TTL values of 1 s, 2 s, and 5 s across 50 processes, the reduction factor for each, and the worst-case staleness. Then compute the bandwidth that key consumes at Redis both with and without the 2-second L1 cache, and state whether operations per second or bandwidth is the binding constraint.
   (e) Size a Bloom filter for 3,000,000 existing user IDs at false-positive rates of 1 % and 0.1 %: compute bits required, bytes, bits per element, and the optimal number of hash functions for each. Then compute how many of the scraper's 500,000 requests reach the inference tier with each filter, with negative caching alone (60-second TTL), and with neither. State which combination you would deploy and why.
   Show every step and state any additional assumptions you make.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 31.

---

**Progress checklist**

Phase 1 — Foundations: **complete** (Lessons 1–17)

Phase 2 — Scaling:
`[x] 1. Vertical scaling` · `[x] 2. Horizontal scaling` · `[x] 3. Stateless application servers` · `[x] 4. Load balancers` · `[x] 5. Layer 4 versus Layer 7` · `[x] 6. Load-balancing strategies` · `[x] 7. Health checks and failover` · `[x] 8. Reverse proxies and API gateways` · `[x] 9. Caching fundamentals` · `[x] 10. Browser, application, database, and distributed caches` · `[x] 11. Cache-aside, read-through, write-through, write-behind` · `[x] 12. Cache invalidation and TTL` · `[x] 13. Cache stampedes, hot keys, cache penetration, and eviction`
`[ ] 14. CDNs and edge caching` ← next
`[ ] 15–22` not started

Phases 3–5: not started.

**NEXT LESSON: Phase 2, Topic 14 — CDNs and edge caching**

