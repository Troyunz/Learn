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
| 2 — Scaling | 22 | 18–39 | ✅ Lessons complete, wrap-up pending |
| 3 — Data & storage | 24 | 40–63 | ⏳ In progress |
| 4 — Distributed systems | 29 | 64–92 | ⏳ Not started |
| 5 — Complete system-design exercises | 14 designs | 93–106 | ⏳ Not started |

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
| [phase-2-part-5.md](./phase-2-part-5.md) | 35–39 | Sharding, sharding strategies, resharding & cross-shard queries, consistent hashing, autoscaling & multi-region |

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

### Phase 2 — Scaling
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
- [ ] Phase 2 wrap-up

### Phase 3 — Data and storage
- [ ] 1. Relational databases — L40
- [ ] 2. Tables, keys, relationships, joins, constraints — L41
- [ ] 3. SQL versus NoSQL — L42
- [ ] 4. Key-value databases — L43
- [ ] 5. Document databases — L44
- [ ] 6. Wide-column databases — L45
- [ ] 7. Graph databases — L46
- [ ] 8. Search engines and full-text indexes — L47
- [ ] 9. Selecting a database from access patterns — L48
- [ ] 10. ACID transactions — L49
- [ ] 11. Isolation levels — L50
- [ ] 12. Dirty reads, non-repeatable reads, phantom reads, lost updates — L51
- [ ] 13. Optimistic versus pessimistic concurrency control — L52
- [ ] 14. Database indexes — L53
- [ ] 15. B-tree, hash, composite, covering, secondary indexes — L54
- [ ] 16. Query plans and index trade-offs — L55
- [ ] 17. Normalisation and denormalisation — L56
- [ ] 18. OLTP versus OLAP — L57
- [ ] 19. Data warehouses, data lakes, lakehouses — L58
- [ ] 20. CAP theorem — L59
- [ ] 21. PACELC — L60
- [ ] 22. Strong, eventual, causal, read-your-writes, monotonic consistency — L61
- [ ] 23. Object, block, and file storage — L62
- [ ] 24. Data retention, archival, backups, disaster recovery — L63
- [ ] Phase 3 wrap-up

### Phase 4 — Distributed systems (L64–L92)
- [ ] 1–29 — not started
- [ ] Phase 4 wrap-up

### Phase 5 — Complete system-design exercises (L93–L106)
- [ ] 1. Pastebin · 2. URL shortener · 3. Rate limiter · 4. Notification service
- [ ] 5. Search autocomplete · 6. File storage · 7. Web crawler · 8. Real-time chat
- [ ] 9. News feed · 10. Video streaming · 11. Ride sharing · 12. E-commerce/inventory
- [ ] 13. Payment processing · 14. Metrics and logging platform

---

**NEXT: Phase 2 wrap-up, then Phase 3, Topic 1 — Relational databases**
