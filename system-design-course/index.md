# System Design: Beginner → Advanced

A structured, practical course. Every lesson uses one continuing example — **BookNook**, an online bookstore — plus a second contrasting example where it helps understanding.

## How to use this course

- Lessons are numbered continuously (`Lesson 1`, `Lesson 2`, …) across all phases.
- Each lesson is self-contained but assumes everything before it.
- Every lesson follows the same 17-section structure: definition → why it exists → analogy → technical detail → data flow → ASCII diagram → BookNook example → code → worked numbers → trade-offs → failure scenarios → alternatives → when to use → when not to → common mistakes → interview perspective → knowledge check.
- Commands you can give at any time:

| Command | Effect |
|---|---|
| `continue` | Resume at the continuation marker at the end of the last response |
| `go deeper` | Internal implementation details and hard edge cases for the last topic |
| `simplify` | Same concept, less jargon, smaller example |
| `production example` | Adds monitoring, deployment, failure recovery, security, cost |
| `interview mode` | You lead the design; I ask requirements questions only |
| `revision mode` | Comparison table + flashcards + quiz |

## Course roadmap

| Phase | Topics | Lessons | Status |
|---|---|---|---|
| 1 — Internet & system-design foundations | 17 | 1–17 | ✅ Complete |
| 2 — Scaling | 22 | 18–39 | ✅ Complete |
| 3 — Data & storage | 24 | 40–63 | ✅ **Complete (L55, L59, L63 added; wrap-up done)** |
| 4 — Distributed systems | 29 | 64–92 | ✅ **Complete (L64–92 + wrap-up)** |
| 5 — Complete system-design exercises | 14 designs | 93–106 | ✅ **Complete (L93–106 + wrap-up)** |

## Files

| File | Lessons | Contents |
|---|---|---|
| [phase-1-part-1.md](./phase-1-part-1.md) | 1–4 | Client-server, stateful vs stateless, IP/ports/sockets, DNS |
| [phase-1-part-2.md](./phase-1-part-2.md) | 5–8 | HTTP requests/responses, methods & status codes, HTTPS/TLS, TCP vs UDP |
| [phase-1-part-3.md](./phase-1-part-3.md) | 9–12 | HTTP/1.1 vs 2 vs 3, REST, RPC & gRPC, GraphQL |
| [phase-1-part-4.md](./phase-1-part-4.md) | 13–15 | Sync vs async, latency/throughput/Little's Law, availability maths |
| [phase-1-part-5.md](./phase-1-part-5.md) | 16–17 + wrap-up | Percentiles & tail latency, idempotency/retries/backoff, Phase 1 wrap-up |
| [phase-2-part-1.md](./phase-2-part-1.md) | 18–22 | Vertical & horizontal scaling, stateless servers, load balancers, L4 vs L7 |
| [phase-2-part-2.md](./phase-2-part-2.md) | 23–26 | LB strategies, health checks & failover, reverse proxies & gateways, caching fundamentals |
| [phase-2-part-3.md](./phase-2-part-3.md) | 27–30 | Cache hierarchy, caching patterns, invalidation & TTL, stampedes/hot keys/eviction |
| [phase-2-part-4.md](./phase-2-part-4.md) | 31–34 | CDNs & edge caching, replication, sync vs async replication, replication lag |
| [phase-2-part-5.md](./phase-2-part-5.md) | 35–39 + wrap-up | Sharding, sharding strategies, resharding & cross-shard queries, consistent hashing, autoscaling & multi-region, Phase 2 wrap-up |
| [phase-3-part-1.md](./phase-3-part-1.md) | 40–43 | Relational databases, tables/keys/joins/constraints, SQL vs NoSQL, key-value stores |
| [phase-3-part-2.md](./phase-3-part-2.md) | 44–47 | Document stores, wide-column stores, graph databases, search engines |
| [phase-3-part-3.md](./phase-3-part-3.md) | 48–51 | Selecting a database, ACID, isolation levels, read anomalies |
| [phase-3-part-4.md](./phase-3-part-4.md) | 52–55 | Optimistic vs pessimistic concurrency, indexes, index types, **query plans & index trade-offs** |
| [phase-3-part-5.md](./phase-3-part-5.md) | 56–63 + wrap-up | Normalisation, OLTP vs OLAP, warehouses/lakes/lakehouses, **CAP**, PACELC, consistency models, storage types, **backup & DR**, Phase 3 wrap-up |
| [phase-4-part-1.md](./phase-4-part-1.md) | 64–66 | Phase 4 intro, why distribution is hard, partial failures & partitions, clocks & ordering |
| [phase-4-part-2.md](./phase-4-part-2.md) | 67–69 | Sync vs async workflows, message queues, publish-subscribe |
| [phase-4-part-3.md](./phase-4-part-3.md) | 70–72 | Event streams, queues vs logs, consumer groups/partitions/offsets |
| [phase-4-part-4.md](./phase-4-part-4.md) | 73–75 | Delivery semantics, idempotent consumers & dedup, DLQs and retry queues |
| [phase-4-part-5.md](./phase-4-part-5.md) | 76–79 | Backpressure & load shedding, circuit breakers/bulkheads, locks & leases, leader election |
| [phase-4-part-6.md](./phase-4-part-6.md) | 80–85 | Quorums, consensus & Raft, distributed transactions, 2PC, sagas, outbox & CDC |
| [phase-4-part-7.md](./phase-4-part-7.md) | 86–89 | Monoliths vs microservices, discovery & config, observability, SLIs/SLOs |
| [phase-5-part-1.md](./phase-5-part-1.md) | 93–95 | Design Pastebin, URL shortener, rate limiter |
| [phase-5-part-2.md](./phase-5-part-2.md) | 96–98 | Design notification service, search autocomplete, file storage |
| [phase-5-part-3.md](./phase-5-part-3.md) | 99–101 | Design web crawler, real-time chat, news feed |
| [phase-5-part-4.md](./phase-5-part-4.md) | 102–104 | Design video streaming, ride sharing, e-commerce & inventory |
| [phase-5-part-5.md](./phase-5-part-5.md) | 105–106 + wrap-up | Design payment processing, metrics & logging platform, **Phase 5 revision sheet, glossary, 20 quiz questions, exercises, mini design** |
| [phase-4-part-8.md](./phase-4-part-8.md) | 90–92 + wrap-up | HA & DR, active-passive vs active-active, RTO/RPO, **Phase 4 revision sheet, glossary, 20 quiz questions, exercises** |


> **Note on `_chunks/`**: this directory holds raw generation output from the original build. Every Phase 4 draft it contained has now been merged into the `phase-4-part-*` files. It is a working folder, not part of the course; see [../AUDIT.md](../AUDIT.md).

## Progress checklist

Legend: `[x]` complete · `[ ]` not started

### Phase 1 — Internet and system-design foundations ✅
- [x] 1. Client-server architecture — L1
- [x] 2. Stateful versus stateless servers — L2
- [x] 3. IP addresses, ports, sockets, domain names — L3
- [x] 4. DNS resolution and DNS caching — L4
- [x] 5. HTTP requests and responses — L5
- [x] 6. HTTP methods, headers, cookies, sessions, status codes — L6
- [x] 7. HTTPS and TLS (conceptual) — L7
- [x] 8. TCP versus UDP — L8
- [x] 9. HTTP/1.1 vs HTTP/2 vs HTTP/3 — L9
- [x] 10. REST APIs — L10
- [x] 11. RPC and gRPC — L11
- [x] 12. GraphQL — L12
- [x] 13. Synchronous versus asynchronous communication — L13
- [x] 14. Latency, throughput, bandwidth, concurrency — L14
- [x] 15. Availability, reliability, durability, fault tolerance — L15
- [x] 16. Average latency vs p50/p95/p99 and tail latency — L16
- [x] 17. Idempotency, retries, timeouts, exponential backoff — L17
- [x] Phase 1 wrap-up

### Phase 2 — Scaling ✅
- [x] 1. Vertical scaling — L18
- [x] 2. Horizontal scaling — L19
- [x] 3. Stateless application servers — L20
- [x] 4. Load balancers — L21
- [x] 5. Layer 4 versus Layer 7 load balancing — L22
- [x] 6. Load-balancing strategies — L23
- [x] 7. Health checks and failover — L24
- [x] 8. Reverse proxies and API gateways — L25
- [x] 9. Caching fundamentals — L26
- [x] 10. Browser, application, database, distributed caches — L27
- [x] 11. Cache-aside, read-through, write-through, write-behind — L28
- [x] 12. Cache invalidation and TTL — L29
- [x] 13. Cache stampedes, hot keys, penetration, eviction — L30
- [x] 14. CDNs and edge caching — L31
- [x] 15. Database replication — L32
- [x] 16. Synchronous versus asynchronous replication — L33
- [x] 17. Replication lag and read-after-write consistency — L34
- [x] 18. Database partitioning and sharding — L35
- [x] 19. Range, hash, directory, geographic sharding — L36
- [x] 20. Resharding, hot partitions, cross-shard queries — L37
- [x] 21. Consistent hashing and virtual nodes — L38
- [x] 22. Autoscaling and multi-region architecture — L39
- [x] Phase 2 wrap-up

### Phase 3 — Data and storage ✅
- [x] 1. Relational databases — L40
- [x] 2. Tables, keys, relationships, joins, constraints — L41
- [x] 3. SQL versus NoSQL — L42
- [x] 4. Key-value databases — L43
- [x] 5. Document databases — L44
- [x] 6. Wide-column databases — L45
- [x] 7. Graph databases — L46
- [x] 8. Search engines and full-text indexes — L47
- [x] 9. Selecting a database from access patterns — L48
- [x] 10. ACID transactions — L49
- [x] 11. Isolation levels — L50
- [x] 12. Dirty reads, non-repeatable reads, phantom reads, lost updates — L51
- [x] 13. Optimistic versus pessimistic concurrency control — L52
- [x] 14. Database indexes — L53
- [x] 15. B-tree, hash, composite, covering, secondary indexes — L54
- [x] 16. Query plans and index trade-offs — L55 ✳ *(written 2026-10)*
- [x] 17. Normalisation and denormalisation — L56
- [x] 18. OLTP versus OLAP — L57
- [x] 19. Data warehouses, data lakes, lakehouses — L58
- [x] 20. CAP theorem — L59 ✳ *(written 2026-10)*
- [x] 21. PACELC — L60
- [x] 22. Strong, eventual, causal, read-your-writes, monotonic consistency — L61
- [x] 23. Object, block, and file storage — L62
- [x] 24. Data retention, archival, backups, disaster recovery — L63 ✳ *(written 2026-10)*
- [x] Phase 3 wrap-up

### Phase 4 — Distributed systems (L64–L92)
- [x] 1. Why distributed systems are difficult — L64
- [x] 2. Partial failures and network partitions — L65 ✳ *(written 2026-10)*
- [x] 3. Clock differences and ordering events — L66 ✳ *(written 2026-10)*
- [x] 4. Synchronous versus asynchronous workflows — L67
- [x] 5. Message queues — L68 ✳ *(written 2026-10)*
- [x] 6. Publish-subscribe systems — L69 ✳ *(written 2026-10)*
- [x] 7. Event streams — L70
- [x] 8. RabbitMQ-style queues versus Kafka-style logs — L71 ✳ *(written 2026-10)*
- [x] 9. Consumer groups, partitions, offsets and ordering — L72 ✳ *(written 2026-10)*
- [x] 10. At-most-once, at-least-once and exactly-once semantics — L73 ✳ *(written 2026-10)*
- [x] 11. Idempotent consumers and deduplication — L74 ✳ *(written 2026-10)*
- [x] 12. Dead-letter queues and retry queues — L75 ✳ *(written 2026-10)*
- [x] 13. Backpressure and load shedding — L76 ✳ *(written 2026-10)*
- [x] 14. Circuit breakers, bulkheads and graceful degradation — L77 ✳ *(written 2026-10)*
- [x] 15. Distributed locks and leases — L78 ✳ *(written 2026-10)*
- [x] 16. Leader election — L79 ✳ *(written 2026-10)*
- [x] 17. Quorums — L80 ✳ *(written 2026-10)*
- [x] 18. Consensus and Raft — L81 ✳ *(written 2026-10)*
- [x] 19. Distributed transactions — L82 ✳ *(written 2026-10)*
- [x] 20. Two-phase commit — L83 ✳ *(written 2026-10)*
- [x] 21. Saga pattern — L84 ✳ *(written 2026-10)*
- [x] 22. Transactional outbox and change data capture — L85 ✳ *(written 2026-10)*
- [x] 23. Monoliths, modular monoliths and microservices — L86 ✳ *(written 2026-10)*
- [x] 24. Service discovery and configuration management — L87 ✳ *(written 2026-10)*
- [x] 25. Observability: logs, metrics, traces — L88 ✳ *(written 2026-10)*
- [x] 26. SLIs, SLOs and error budgets — L89 ✳ *(written 2026-10)*
- [x] 27. High availability and disaster recovery — L90 ✳ *(written 2026-10)*
- [x] 28. Active-passive versus active-active — L91 ✳ *(written 2026-10)*
- [x] 29. RTO and RPO — L92 ✳ *(written 2026-10)*
- [x] Phase 4 wrap-up ✳ *(revision sheet, glossary, 20 quiz questions, 5 exercises, mini design, interview questions)*

### Phase 5 — Complete system-design exercises (L93–L106)
- [x] 1. Pastebin (L93) ✳ · [x] 2. URL shortener (L94) ✳ · [x] 3. Rate limiter (L95) ✳ · [x] 4. Notification service (L96) ✳
- [x] 5. Search autocomplete (L97) ✳ · [x] 6. File storage (L98) ✳ · [x] 7. Web crawler (L99) ✳ · [x] 8. Real-time chat (L100) ✳
- [x] 9. News feed (L101) ✳ · [x] 10. Video streaming (L102) ✳ · [x] 11. Ride sharing (L103) ✳ · [x] 12. E-commerce/inventory (L104) ✳
- [x] 13. Payment processing (L105) ✳ · [x] 14. Metrics and logging platform (L106) ✳
- [x] Phase 5 wrap-up ✳ *(revision sheet, glossary, 20 quiz questions, 5 exercises, mini design, interview questions)*

---

**🎓 All 106 lessons and all five phase wrap-ups are complete.** Start at [Phase 1, Lesson 1](./phase-1-part-1.md), or use the [Phase 5 wrap-up](./phase-5-part-5.md) as a revision sheet before interviews.

For the precise state of every file — including orphaned drafts, stale inline checklists, and the remaining work plan — see [../AUDIT.md](../AUDIT.md).
