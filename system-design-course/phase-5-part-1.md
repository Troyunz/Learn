# Lesson 93 — Design Pastebin

*Phase 5, Topic 1*

## 1. Simple definition

Phase 5 changes the shape of the lessons. Phases 1–4 taught **components**; Phase 5 builds **systems** from them, in the format an interview uses and a design review demands.

**The problem.** Design a service where a user pastes text and gets back a short URL; anyone with the URL can read the text; pastes can expire; large pastes are read far more often than they are written.

**The method**, applied identically to all 14 designs in this phase:

1. **Requirements** — functional (what it does) and non-functional (how well: availability, latency, scale, durability, cost). Numbers, not adjectives.
2. **Capacity estimation** — writes/s, reads/s, storage/day, bandwidth, cache working set. These numbers drive every later decision.
3. **API** — the smallest surface that satisfies the requirements.
4. **Data model** — what is stored, where, and with what key.
5. **High-level design** — the components and the request paths.
6. **Deep dives** — the two or three decisions that actually decide the design.
7. **Bottlenecks** — what breaks first at 10×, and the plan.
8. **Evolution** — how it changes at 10×, 100×, and with a new requirement.

Terms used in this lesson:

- **Paste**: immutable text content plus metadata, addressed by an identifier.
- **Key / slug**: the short identifier in the URL (`/p/aZ3kP9q`).
- **Content-addressed**: the identifier is derived from the content's hash, so identical content collapses to one object.
- **Cache-aside**: read the cache, fall back to the store on a miss, write the result back.
- **TTL**: time to live — the expiry rule, which for pastes is a **product feature**, not just a memory policy.
- **Lazy expiry vs sweeper**: delete on access (free, leaves garbage) versus a background job (costs a scan, keeps storage honest).
- **Hot key**: one identifier receiving a disproportionate share of reads.

The one sentence to carry out of this lesson: **a paste service is an immutable-blob store with a tiny metadata index, so the design is decided by three things — how the key is generated, where the 10 KB blob lives, and how expiry is enforced — and everything else is caching.**

## 2. Why it exists

It is the smallest system that still forces every Phase 5 decision to be made explicitly, which is why it is the first design in every interview syllabus.

**Because the read/write ratio is extreme, and that ratio is the design.** Pastebin-class services see 10–100 reads per write; a URL in a chat is opened by everyone who sees it. That ratio means the write path can be modest and the read path must be **edge-cached**, which changes the architecture from "a database behind a load balancer" to "a CDN in front of an object store".

**Because content is immutable, which is a gift.** Nothing ever updates. That means: no cache invalidation (L31's hardest problem, absent), no locking, no versioning, and a natural fit for object storage with `Cache-Control: immutable`. Recognising this is most of the design.

**Because the content is large relative to the metadata, and that boundary decides the storage.** A 10 KB average paste belongs in object storage, not in a `TEXT` column: 5 M pastes/day × 10 KB = **50 GB/day**, which in a row-store database would bloat the buffer pool, the WAL and every backup (L63). Metadata at ~400 B goes in a database. The 10 KB boundary is the lesson.

**Because expiry is a correctness requirement with a storage consequence.** "Pastes expire after 30 days" is a product promise and a retention policy. Lazy deletion on read is free but leaves unread pastes forever; a sweeper costs a scan. Both are needed, and the interaction (L85's outbox, L75's DLQ thinking) matters: deleting the blob and the metadata row is a two-system operation, i.e. a distributed transaction you should design away.

**And because abuse is not an afterthought.** Anonymous, free, public URL creation is the canonical abuse surface: malware distribution, phishing, illegal content, and storage exhaustion. A paste service without a moderation and rate-limit design (L95) is a liability, and at BookNook's scale the DMCA queue is a feature.

## 3. Real-world analogy

**A coat check.**

You hand over a coat; the attendant gives you a small numbered ticket. The number is meaningless on its own — it maps to exactly one item in a back room. The item is never modified while stored. Retrieval requires only the ticket, and **anyone holding the ticket can collect the coat**.

The analogy carries four design points precisely:

**The ticket is the whole security model (§4.7).** There is no identity check; possession is authorisation. So the ticket must be **unguessable**: a sequential number (1, 2, 3…) would let anyone collect every coat in the building by counting. That is why the key is random or hash-derived, not a counter in decimal. And it is why the URL is a secret even though it looks like a public link.

**The ticket can be reused, which is a hazard.** Real coat checks reuse numbers night after night. If a paste service reused keys, an old URL would one day return someone else's content — a data leak, not a bug. Keys must therefore be permanently unique, which pushes toward a keyspace far larger than the number of pastes you will ever store (62⁷ = 3.5 trillion for 7 characters).

**Unclaimed coats get cleared out.** The attendant has a policy: items not collected in 30 days are removed. Cheap to run (they sweep the rack), and it bounds storage. This is the sweeper, and it is why expiry is a **process** rather than a column value.

**And the back room is not a filing cabinet.** Coats hang; you find one by its ticket, instantly, in a large room designed only for storage. That is object storage: cheap, huge, no query language, addressed by key. Putting the coats in an alphabetised ledger with indexes (a relational database) would be absurd at this size.

The one place the analogy fails, and it matters: **a coat check holds one coat per ticket, but a paste service may receive many identical pastes** — the same log excerpt posted by five people. The design choice is whether those collapse into one object (content addressing) or stay separate (identity + dedupe), and that is a product decision about analytics and deletion (§4.6).

## 4. Detailed technical explanation

### 4.1 Requirements, with numbers

**Functional.**

| # | Requirement |
|---|---|
| F1 | Create a paste: text content (1 byte – 1 MB), optional title, optional syntax hint, optional expiry (1 h, 1 d, 30 d, never), optional burn-after-read |
| F2 | Read a paste by its short URL; render as plain text or highlighted |
| F3 | Optional custom slug (paid tier) |
| F4 | Optional edit — **deliberately excluded** for v1 (see §4.2) |
| F5 | Delete by owner token (issued at creation) |
| F6 | Expiry enforced according to the requested TTL |
| F7 | Report a paste (abuse), and block it on moderation |

**Non-functional.**

| # | Requirement | Number |
|---|---|---|
| N1 | Read availability | 99.95 % (a dead link is a broken page, not a lost sale) |
| N2 | Read latency | p99 < 80 ms globally (edge-cached) |
| N3 | Write latency | p99 < 300 ms |
| N4 | Durability | Acknowledged paste survives the loss of one AZ; **no** cross-region requirement (L90 §4.5) |
| N5 | Scale | 5 M new pastes/day; 50 M reads/day |
| N6 | Content cap | 1 MB; average **10 KB** |
| N7 | Retention | Per-paste TTL; default 30 days |
| N8 | Privacy | Unlisted-by-default; key entropy ≥ 64 bits |

**Deliberately out of scope:** accounts and editing (v1), real-time collaboration, full-text search across pastes, and public discovery. Each is a different product.

### 4.2 The one requirement we removed, and why

**Editing** looks harmless and destroys the design's best property: immutability (§2). With edits you need versions, cache invalidation, a lock or optimistic concurrency check, and a decision about what an old URL shows. Estimated cost at BookNook: **3–4 weeks** plus an ongoing cache-invalidation surface.

The cheap alternative that satisfies 90 % of users: **create a new paste and get a new URL**, with the old one kept until its TTL. Shipping that in zero days and adding versions only if demand appears is the correct sequencing — and stating it explicitly in a design review is what distinguishes a senior answer from a feature list.

### 4.3 Capacity estimation

```
  WRITES      5,000,000 pastes/day
              5,000,000 / 86,400      = 58 writes/s average
              peak factor 3           = 174 writes/s

  READS       50,000,000 reads/day    (10 : 1 read:write)
              50,000,000 / 86,400     = 579 reads/s average
              peak factor 3           = 1,737 reads/s

  CONTENT     5,000,000 × 10 KB       = 50 GB/day
              50 GB × 365             = 18.3 TB/year (before compression;
                                        text compresses ~3× ⇒ ~6 TB/year)
  METADATA    5,000,000 × 400 B       = 2 GB/day = 730 GB/year
  INDEXES     ~30 % of metadata       = ~220 GB/year

  BANDWIDTH   writes: 174/s × 10 KB   = 1.7 MB/s
              reads:  1,737/s × 10 KB = 17.4 MB/s  (83 % absorbed by CDN in
                                        practice ⇒ ~3 MB/s from origin)

  CACHE       If 20 % of pastes get 80 % of reads (Pareto), the hot working set
              is 20 % of 30 days of pastes = 0.2 × 150 M = 30 M pastes. Too big
              to cache in full (30 M × 10 KB = 300 GB), so cache only the
              genuinely hot: measured tiering (§9 step 4) says 2 % of pastes
              serve 70 % of reads ⇒ 3 M × 10 KB = 30 GB of RAM. Feasible.

  ⇒ the numbers decide three things immediately:
     (1) writes are trivially small ⇒ no sharding of the write path
     (2) content is 25× the metadata ⇒ separate stores
     (3) reads are 10× writes and cacheable ⇒ CDN is the primary read path
```

### 4.4 API

```
POST /v1/pastes
     { "content": "...", "title": "...", "syntax": "python",
       "ttl_seconds": 2592000 }
  →  201 { "id": "aZ3kP9q", "url": "https://bn.link/p/aZ3kP9q",
           "delete_token": "d_9f2c…" }        # returned ONCE, never stored raw
     errors: 413 too large · 429 rate limited · 422 invalid ttl

GET  /p/{id}
  →  200 text/html (rendered) or text/plain (via ?raw=1)
  →  404 unknown · 410 expired or blocked
     headers: Cache-Control: public, max-age=86400, immutable
              ETag: "<content_hash>"        # enables conditional requests

DELETE /v1/pastes/{id}
     Authorization: Bearer <delete_token>
  →  204 · 403 wrong token · 404

POST /v1/pastes/{id}/report                # abuse
     { "reason": "malware" } → 202

GET  /v1/pastes/{id}/meta                  # owner-only, no content
  →  200 { "created_at", "expires_at", "views" }

⇒ design notes: the delete token is a capability, hashed at rest (like a
  password) so a database leak does not grant deletion rights; GET is the only
  hot path and it is cacheable; every write returns an idempotency key option
  (L74) so a client retry does not create a duplicate paste.
```

### 4.5 Data model

**Metadata (relational, one row per paste).**

```sql
CREATE TABLE pastes (
  id             VARCHAR(12) PRIMARY KEY,       -- base62, 7 chars
  content_key    TEXT        NOT NULL,          -- object-store key
  content_sha    BYTEA       NOT NULL,          -- 32 B, for dedupe/GC
  content_bytes  INTEGER     NOT NULL,
  title          TEXT,
  syntax         VARCHAR(24),
  owner_token_hash BYTEA     NOT NULL,          -- delete capability, hashed
  visibility     SMALLINT    NOT NULL DEFAULT 0,-- 0 unlisted, 1 public, 2 blocked
  created_at     TIMESTAMPTZ NOT NULL,
  expires_at     TIMESTAMPTZ,                   -- NULL = never
  burn_after_read BOOLEAN    NOT NULL DEFAULT false,
  view_count     BIGINT      NOT NULL DEFAULT 0
);
CREATE INDEX pastes_expiry ON pastes (expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX pastes_sha    ON pastes (content_sha);
-- partitioned by created_at month: 150 M rows/month is too much for one table
-- to vacuum and back up cheaply, and the range partitions are the retention
-- unit (L54, L63).
```

**Content (object storage).**

```
  s3://booknook-pastes/<sha256[0:2]>/<sha256[2:4]>/<sha256>.zst
  ⇒ content-addressed by SHA-256 with a 2-level prefix. Why prefix? Object
    stores shard by key prefix; a flat namespace of 5 B objects is fine on S3
    but a 2-level prefix keeps listing and lifecycle rules manageable.
  compression: zstd on text ≈ 3× (18 TB/yr → 6 TB/yr, £340/mo → £115/mo)

  ⚠ DEDUPE IS A PRODUCT DECISION, NOT AN OPTIMISATION. Content addressing
    collapses identical pastes into one object. Then:
      · deleting paste A must NOT delete the object if paste B shares it
        ⇒ reference counting, or deferred GC (mark-and-sweep on the sha)
      · burn-after-read cannot share an object with a non-burn paste
      · a moderation block on a sha blocks every paste with that content —
        which is a FEATURE for malware, and a bug for a legitimate log excerpt
        posted 4,000 times.
    ⇒ BookNook dedupes only within (owner, day) to bound the blast radius of
      these interactions, and accepts 12 % storage waste (§9 step 5).
```

### 4.6 Key generation: the three options

```
  OPTION A — base62 of a global counter
      62^7 = 3.5 × 10^12 keys ⇒ 7 chars covers 3,500 billion pastes
      + trivially unique, no collisions, compact
      − a single counter is a SPOF and a coordination point (L79);
        range-allocating blocks of 1 M to each node fixes that, but the IDs
        become SEQUENTIAL AND GUESSABLE: 5 M pastes/day means a scraper can
        enumerate 58 neighbours per second and read them all. That is a
        data leak (§3). Unacceptable for a privacy product.
  OPTION B — hash the content: base62(sha256(content))
      + deterministic: posting the same text twice returns the SAME URL
        (a feature for logs, a leak for anything else — two users' identical
        drafts become one paste)
      + dedupe is free
      − an attacker who guesses the content (a common password, a known file)
        can compute the URL without ever seeing the paste ⇒ NOT SECRET.
        This is the classic mistake; it is why content-addressing is used for
        durable objects and never for capability URLs.
  OPTION C — random, from a CSPRNG (CHOSEN)
      7 chars of base62 ≈ 41.7 bits — too few for an unguessable secret when
      you have 5 B pastes and an attacker can make 1,000 guesses/s:
          3.5e12 / 2 = 1.75e12 average guesses ÷ 1e3/s = 1.75e9 s = 55 YEARS
          for ONE paste — fine in aggregate, BUT the birthday bound matters:
          P(collision) after n inserts ≈ n²/2K; for n = 3.5e10 (a year of
          writes at 5 M/day ≈ 1.8e9) ⇒ 1.8e9² / 2·3.5e12 = 46 %. Too high.
      ⇒ use 9 chars of base62 = 53.6 bits ⇒ K = 1.76e16:
            collisions in a year: 1.8e9² / 3.5e16 = 0.00009 (negligible)
            brute force for one paste at 1e3/s: 2.8e6 years
        and add a UNIQUE constraint with a single retry on collision, because
        the arithmetic is a probability, not a guarantee.
  ⇒ the honest rule: KEY LENGTH = max(brute-force requirement, collision
    requirement), and for a capability URL the brute-force requirement usually
    dominates. 9 characters, not 7.
```

### 4.7 Write path and read path

```
  WRITE  (174/s peak)
   client → API gateway (auth optional, rate limit L95)
          → paste-svc: validate size (reject > 1 MB with 413 before reading
            the body — critical, or you buffer 1 MB per request)
          → abuse pre-check: synchronous scan for known-bad indicators
            (URL blocklist, magic bytes) on the first 8 KB, 15 ms budget;
            async deep scan post-hoc
          → content: PUT to S3 with Content-MD5, zstd compression
          → metadata: INSERT row in Postgres (id, content_key, sha, ttl)
          → outbox: paste.created event (L85) → analytics, modem queue
          → return 201 with id + delete token
  ⇒ ordering note: S3 first, then Postgres. If Postgres fails after the PUT,
    the object is orphaned and the GC job (§4.8) reclaims it. If Postgres were
    first, a failure would leave a row pointing at nothing — a 500 that
    persists forever. ALWAYS WRITE THE BLOB BEFORE THE POINTER.

  READ   (1,737/s peak)
   client → CDN edge (PoP)  ── 83 % hit ──► return cached page
                     │ miss
                     ▼
            paste-svc → Redis (id → content)  ── 70 % hit ──► assemble, cache
                     │ miss
                     ▼
            Postgres metadata (by PK, ~0.3 ms)
                     │ check: expires_at, visibility
                     ▼
            S3 GET (p99 90 ms) → decompress → render → cache at Redis + edge
  ⇒ three cache tiers with the same key: browser (86400 s, immutable), CDN
    (86400 s), Redis (3600 s). Immutability is what allows a day-long TTL
    with no invalidation (L31 §4.7).
```

### 4.8 Expiry, deletion and garbage collection

```
  THREE deletion paths, and they are different problems:

  1. TTL EXPIRY (the product promise)
     · lazy: on read, if expires_at < now → 410 Gone, enqueue a delete
       (free, and the common case: 90 % of pastes are never read again)
     · sweep: hourly job scans pastes_expiry index for expires_at < now,
       batches of 10,000, enqueues deletes
     · the CDN problem: a cached page has max-age 86400, so an expired paste
       can be served by an edge for up to a day after expiry. Two options:
         (a) shorten the CDN TTL for pastes with a near expiry (a per-object
             Cache-Control computed at write time, e.g. min(86400, ttl))
         (b) accept it and adjust the product promise to "within 24 h"
       ⇒ (a) is correct: the header is decided at write time from data you
         already have. This is the kind of detail the interview wants.

  2. USER DELETION
     · mark visibility = 2 (blocked) FIRST so reads fail immediately, then
       delete asynchronously; a hard delete that races a cache miss is how
       deleted content reappears.

  3. GC OF ORPHANS
     · objects written but never referenced (a crash between PUT and INSERT)
     · objects unreferenced after the last paste sharing their sha is deleted
     · implemented as a weekly mark-and-sweep: list S3 keys (paginated),
       subtract the referenced set from pastes.content_key, delete the
       difference older than 24 h (the grace period protects in-flight writes)
  ⇒ NOTE: deleting metadata and blob is a two-store operation — the classic
    partial failure (L64). The rule that makes it safe is ORDERING +
    IDEMPOTENT RETRY: block, delete metadata, enqueue blob delete, and let GC
    catch whatever the queue loses. Never a distributed transaction (L82).
```

### 4.9 Rendering, syntax highlighting and abuse

- **Render type is decided at write time** from the `syntax` hint and stored; highlighting is precomputed into HTML and cached. Rendering 1 MB of Python at 1,737 rps would be a CPU-bound service; precomputation moves it to 174 writes/s.
- **Serve `text/plain` for `?raw=1`**, and set `Content-Disposition: inline` with `X-Content-Type-Options: nosniff` so a pasted HTML file cannot execute on your origin. A paste service that renders raw user HTML is an XSS farm.
- **Serve from a separate origin** (e.g. `usercontent.booknook.example`) so a content-borne bug cannot read your cookies. This is cheap and is the standard mitigation.
- **Abuse**: rate limits per IP and per capability (L95), a synchronous scan of URLs against a blocklist, an async classifier, a report queue with an SLA, and a deny-list of hashes so a known-malware blob is blocked for every paste at once (§4.5).

## 5. Request / data flow

```
 WRITE — POST /v1/pastes, 10 KB body, TTL 30 days
 ─────────────────────────────────────────────────────────────────────────
 1. gateway      auth (optional) · rate limit 60/min/IP · body size check
                                                              [1.2 ms]
 2. paste-svc    validate: size ≤ 1 MB, ttl ∈ {3600, 86400, 2592000, null}
                                                            [0.4 ms]
 3. abuse        first-8 KB scan vs blocklist (Bloom + exact)  [15 ms]
 4. id           CSPRNG 9 chars base62; INSERT retries once on unique
                 violation (§4.6)                             [0.2 ms]
 5. content      zstd compress 10 KB → 3.4 KB                 [0.3 ms]
                 PUT s3://booknook-pastes/3f/a1/<sha>.zst     [22 ms]
 6. metadata     INSERT pastes row                            [1.1 ms]
 7. outbox       INSERT paste_created (same transaction as 6) [0.3 ms]
 8. moderation   enqueue deep scan (async, 30 s SLA)
 9. return       201 {id, url, delete_token}   TOTAL p50 41 ms, p99 190 ms

 READ — GET /p/aZ3kP9q, cache miss at every tier
 ─────────────────────────────────────────────────────────────────────────
 1. edge (CDN)   MISS                                          [0 ms]
 2. paste-svc    Redis GET id                                  [0.6 ms]
 3. Postgres     SELECT ... WHERE id = ? (PK lookup)           [0.4 ms]
 4. validate     expires_at > now? visibility = 0?  ⇒ else 410/404
 5. S3           GET object                                    [19 ms p50,
                                                              90 ms p99]
 6. render       HTML + precomputed highlight                  [0.8 ms]
 7. write-back   Redis SETEX 3600 · CDN cache 86400 (immutable)
 8. return       200 with ETag = content hash                  TOTAL p50 21 ms,
                                                              p99 96 ms
 9. analytics    async (never on the request path): INCR views; the counter
                 is written in batches every 60 s — a synchronous counter
                 update would make the cache-miss path a write path and
                 destroy cacheability.
```

## 6. ASCII diagram

```
                        ┌──────────────┐
   browsers/clients ───►│  CDN edge    │ 83 % of reads served here
                        │ (PoPs)       │ Cache-Control: immutable, max-age 86400
                        └──────┬───────┘
                               │ miss
                        ┌──────▼────────────┐
                        │ API gateway       │ auth · rate limit · size guard
                        └──────┬────────────┘
                  ┌────────────┴─────────────┐
                  ▼                          ▼
        ┌──────────────────┐        ┌──────────────────┐
        │ read path        │        │ write path       │
        │ paste-svc (×6)   │        │ paste-svc (×3)   │
        └───┬────────┬─────┘        └───┬────────┬─────┘
            │        │                  │        │
     ┌──────▼──┐  ┌──▼───────┐   ┌──────▼──┐  ┌──▼──────────┐
     │ Redis   │  │ Postgres │   │ abuse   │  │ moderation  │
     │ 30 GB   │  │ metadata │   │ scanner │  │ queue       │
     │ 70 % hit│  │ (part.)  │   └─────────┘  └─────────────┘
     └─────────┘  └──┬───────┘
                     │ content_key
                ┌────▼──────────────────────────┐
                │ object storage (S3)           │
                │ 6 TB/yr compressed, 18 TB raw │
                │ content-addressed + GC        │
                └───────────────────────────────┘

  KEY FACT: the write path is 30× smaller than the read path, and the read
  path is 3 caches deep. Nothing here is sharded, because 174 writes/s is
  nothing — the scale is in READ DISTRIBUTION and STORAGE, not in QPS.

  EXPIRY, drawn:
     write ──► expires_at = now + ttl
                    │
        ┌───────────┴────────────┐
        ▼                        ▼
   lazy (on read)          sweeper (hourly)
   → 410 Gone              → scan pastes_expiry
   → enqueue delete        → enqueue delete
        └───────────┬────────────┘
                    ▼
            delete queue → (1) block visibility, (2) delete row,
                           (3) enqueue blob delete, (4) weekly GC sweep
```

## 7. Detailed example

**BookNook's "share this excerpt" feature.** Readers highlight a passage in the reader and share a public link; the paste contains the excerpt plus a citation.

```
  requirements, measured:
    · 1.1 M excerpts created/day (not 5 M — BookNook is smaller than Pastebin)
    · 10.4 reads per excerpt within 7 days; then near-zero (median 12 reads)
    · average length 2.1 KB (an excerpt, not a log file)
    · 61 % of users choose "never expire"; 34 % choose 30 days; 5 % choose 1 day
    · 8 % of excerpts are shared in a public review thread and get 400+ reads
    · DMCA reports: 40/week; 96 % are excerpts longer than 4,000 words
```

```
  capacity from those numbers:
    writes     1,100,000 / 86,400 = 12.7/s avg, peak ×5 = 64/s
    reads      12.7 × 10.4        = 132/s avg, peak ×5 = 660/s
    content    1,100,000 × 2.1 KB = 2.3 GB/day = 840 GB/yr  (zstd 3× ⇒ 280 GB)
    metadata   1,100,000 × 400 B  = 440 MB/day = 160 GB/yr
    ⇒ 25× SMALLER than the generic Pastebin arithmetic above, which changes
      the design: a single Postgres instance holds the metadata comfortably
      for 3 years (480 M rows, ~190 GB), and Redis can cache the ENTIRE hot
      working set (2 % × 90 days of pastes × 2.1 KB ≈ 4 GB).
```

**The three decisions that mattered, in order of cost avoided:**

1. **Cache ratio, measured, not assumed.** BookNook measured a **97 %** CDN hit ratio at peak, because one shared excerpt is opened by every reader of the same review thread. That is higher than the 83 % assumed in §4.3, so origin traffic is 3 % of 660/s ≈ 20 rps on the object-store path. **The read path was never a scaling problem; storage and the abuse queue were.** A design that had sharded the read path would have spent six weeks on nothing.

2. **The citation changes the data model.** The paste must show the book, the page, and a verification link. That is a foreign key into the catalogue — which means the render path needs catalogue data, or the paste row must denormalise the title and author (**it does**: the citation is frozen at creation, because a book's title may change but the excerpt's citation should not).

3. **DMCA is a product feature with a schema consequence.** 96 % of reports are over-long excerpts, so moderation needs to know the character count and the source book — which means the paste carries `book_id` and `char_count`, and the report queue is queryable by both. BookNook's takedown flow: **block the sha in the deny-list** (so re-posting the same excerpt fails at write time), set `visibility = 2`, and keep the metadata for the audit trail while deleting the content object.

**What went wrong in the first iteration, and the fix.**

```
  v1 stored content in Postgres (TEXT column, TOAST) "because it was simpler".
  After 4 months: 33 GB of content in a 210 GB database.
    · the base backup grew from 22 min to 3 h 10 m (the RTO floor, L92 §4.4)
    · `pg_dump`-based nightly export exceeded the 4 h window
    · autovacuum churned on TOAST tables, raising write p99 from 4 ms to 31 ms
    · a read replica added for reporting had to carry the whole 33 GB of
      content it never queried
  v2 moved content to S3 with a `content_key`: database back to 40 GB,
      backup 9 min, write p99 6 ms, replica 40 GB.
  ⇒ the 10 KB boundary is not a preference. Above it, the blob's lifecycle
    (backup, vacuum, replication, page cache) contaminates the metadata's.
```

## 8. Code or configuration example

```python
# ── Key generation: brute-force bound first, collision bound second ───────
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
KEY_LEN  = 9                      # 62^9 = 1.76e16; see §4.6 for the arithmetic

def new_key() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(KEY_LEN))

def create_paste(conn, content: bytes, ttl_s: int | None, **meta) -> str:
    if len(content) > 1_048_576:
        raise TooLarge()                       # checked BEFORE reading the body
    sha = hashlib.sha256(content).hexdigest()
    key = f"{sha[0:2]}/{sha[2:4]}/{sha}.zst"
    body = zstd.compress(content, 9)

    # 1) BLOB FIRST — an orphan object is reclaimable; a dangling pointer is not
    s3.put_object(Bucket=BUCKET, Key=key, Body=body,
                  ContentType="text/plain; charset=utf-8",
                  Metadata={"sha256": sha})

    # 2) METADATA + 3) OUTBOX, in ONE local transaction (L85)
    with conn.transaction():
        for attempt in range(2):                        # one retry on collision
            pid = new_key()
            try:
                conn.execute(
                    """INSERT INTO pastes (id, content_key, content_sha,
                           content_bytes, title, syntax, owner_token_hash,
                           created_at, expires_at, book_id, char_count)
                       VALUES (%s,%s,%s,%s,%s,%s,%s, now(),
                               CASE WHEN %s IS NULL THEN NULL
                                    ELSE now() + (%s || ' seconds')::interval END,
                               %s,%s)""",
                    (pid, key, sha, len(content), meta.get("title"),
                     meta.get("syntax"), sha256(meta["delete_token"]),
                     ttl_s, ttl_s, meta.get("book_id"), len(content) // 3))
                conn.execute(                       # same transaction: L85
                    "INSERT INTO outbox (topic, key, payload) "
                    "VALUES ('paste.created', %s, %s)",
                    (pid, json.dumps({"id": pid, "bytes": len(content)})))
                return pid
            except UniqueViolation:
                if attempt == 1:
                    raise
    raise AssertionError("unreachable")
```

```python
# ── Read path: three tiers, immutable content, expiry checked per tier ────
@app.get("/p/{pid}")
async def read_paste(pid: str, request: Request):
    cached = await redis.get(f"p:{pid}")
    if cached:
        row = json.loads(cached)
    else:
        row = await db.fetchrow(
            "SELECT content_key, expires_at, visibility, syntax, title, "
            "       content_sha, created_at "
            "FROM pastes WHERE id = %s", pid)
        if row is None:
            raise HTTPException(404)
        # Do not cache a row that is about to expire; cache a short negative
        ttl = 3600 if row["expires_at"] is None else min(
            3600, max(0, (row["expires_at"] - now()).total_seconds()))
        await redis.setex(f"p:{pid}", ttl, json.dumps(dict(row), default=str))

    if row["visibility"] == 2:
        raise HTTPException(410, "unavailable")
    if row["expires_at"] and row["expires_at"] <= now():
        await delete_queue.enqueue(pid)          # lazy expiry (§4.8)
        raise HTTPException(410, "expired")

    body = await content_store.get(row["content_key"])   # S3, 90 ms p99

    # The header is computed at WRITE time from the TTL, so an expiring paste
    # is never cached at the edge past its expiry (§4.8, item 1).
    edge_ttl = 86400 if row["expires_at"] is None else min(
        86400, int((row["expires_at"] - now()).total_seconds()))
    return HTMLResponse(render(body, row), headers={
        "Cache-Control": f"public, max-age={max(edge_ttl, 0)}, immutable",
        "ETag": f'"{row["content_sha"]}"',
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; style-src 'self'",
    })
```

```sql
-- ── The sweeper: batched, indexed, idempotent, and it never blocks reads ──
WITH expired AS (
  SELECT id FROM pastes
  WHERE  expires_at IS NOT NULL AND expires_at < now() - interval '1 hour'
  ORDER  BY expires_at
  LIMIT  10000
  FOR UPDATE SKIP LOCKED                 -- parallel sweepers, no contention
)
UPDATE pastes p SET visibility = 2        -- block first: reads fail instantly
FROM expired e WHERE p.id = e.id
RETURNING p.id, p.content_key;
-- the application then enqueues blob deletion; a failed enqueue is recovered
-- by the weekly GC sweep rather than by a distributed transaction (L82)
```

```yaml
# ── Object lifecycle as a backstop for the GC bug you will eventually ship ──
LifecycleConfiguration:
  Rules:
    - Id: expire-orphan-content
      Filter: { Prefix: "" }
      Status: Enabled
      Expiration: { Days: 45 }     # pastes default to 30 days; 15-day margin
    # ⚠ This deletes objects whose metadata row says "never expire" too.
    # Mitigation: a separate prefix (e.g. /perm/) for non-expiring content,
    # excluded from this rule, written by the same code path.
```

## 9. Numerical example

BookNook's excerpt feature, using the numbers from §7.

**Step 1 — the storage decision, quantified.**

```
  Option A: content in Postgres
      1.1 M/day × 2.1 KB × 365 = 840 GB/yr of TOAST, 61 % never expiring
      backup: 40 GB base + 840 GB → nightly full restore 2 h 40 m
      ⇒ violates the 30-minute RTO objective for the metadata store (L92 §4.4)
  Option B: content in S3
      280 GB/yr compressed (zstd 3×); database stays at ~40 GB
      backup restore 9 min  ⇒ RTO floor met with room for verification
      S3 cost: 280 GB × £0.021/GB-mo = £5.90/mo in year 1, £29/mo by year 5
      ⇒ Option B is ~£6/month MORE in storage and saves 2 h 31 m of RTO.
```

**Step 2 — cache sizing, from the measured distribution.**

```
  measured read distribution per paste, 90-day window:
      top 2 % of pastes  → 70 % of reads
      next 8 %           → 22 %
      bottom 90 %        →  8 %
  Redis for the top 2 %:
      pastes in 90 days = 1.1 M × 90 = 99 M; 2 % = 1.98 M pastes
      × 2.1 KB = 4.2 GB of content + 0.8 GB of metadata keys = 5 GB
      ⇒ a 16 GB Redis instance (3× headroom) at ~£95/mo
  hit ratio achievable: 70 % at Redis + CDN absorbing a further share
  origin S3 GETs at peak (using the measured 97 % edge offload from §7):
      660/s × (1 − 0.97) × (1 − 0.70) = 660 × 0.03 × 0.30 = 5.9 GETs/s
  ⇒ S3 handles 5,500 GET/s per prefix comfortably, so the origin is at ~0.1 %
    of its limit. THE READ PATH IS NOT THE CONSTRAINT.
```

**Step 3 — where the p99 actually comes from.**

```
  cache-miss read, component latencies (p50 / p99):
      Redis GET        0.4 / 1.2 ms
      Postgres PK      0.3 / 1.1 ms
      S3 GET          19   / 90  ms   ← DOMINATES
      render           0.5 / 2.4 ms
  ⇒ end-to-end p99 96 ms is 94 % S3. Optimising Postgres or Redis is
    pointless; the only levers are (a) a higher cache hit ratio, (b) a
    smaller object (compression already applied), (c) spreading reads across
    prefixes. This is why §9 step 2's hit-ratio work is the real latency work.
```

**Step 4 — the cost of the whole system, per month.**

```
  compute   6 read pods + 3 write pods × 2 vCPU          £410
  Postgres  db.r6g.xlarge + 1 replica + 480 GB gp3       £690
  Redis     16 GB                                        £95
  S3        23 GB new/mo × 12 (steady-state) = 280 GB    £6
  CDN       660/s × 2.1 KB × 2.6 M s/mo ≈ 3.6 TB egress  £310
  queue/obs logs, metrics, traces                        £140
  ─────────────────────────────────────────────────────────────
  total                                                  ≈ £1,651/mo
  ⇒ £0.0000015 per paste created, or £0.00015 per paste read. At a £3.99/mo
    reading subscription, this feature is 0.04 % of revenue. THE DESIGN IS
    NOT COST-CONSTRAINED, so reliability and abuse handling should win every
    trade-off — which is exactly the opposite of the usual instinct.
```

**Step 5 — dedupe: measured, and rejected.**

```
  BookNook tried global content addressing for 30 days:
      unique shas / total pastes = 0.88 ⇒ 12 % storage saved = £0.70/month
      incidents caused: 3
        · deleting one paste 404'd 214 others (shared object, no refcount)
        · a malware takedown blocked a legitimate 2,000-word public-domain
          excerpt because 41 users had pasted the same passage
        · burn-after-read returned a shared object to a second reader
  ⇒ 12 % of £6/month is £0.70. The engineer-hours to make refcounting correct
    were ~6 days. Dedupe was removed; per-owner-per-day dedupe retained.
  ⇒ THE GENERAL LESSON: dedupe is a storage optimisation that creates a
    correctness problem, and it is only worth it when storage is the binding
    cost. Here it was 0.02 % of the bill.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **Content in object storage** | Cheap, unbounded, backup stays small | 20–90 ms GET; no query on content | Content > ~1 KB average (**always** for pastes) |
| **Content in the database** | Transactional with metadata; one system | Bloats backup, WAL, replicas, vacuum | Content is small (< 1 KB) and always read with metadata |
| **Random 9-char keys** | Unguessable capability URLs | 41 % more URL length than 7 chars | Any private-by-default content |
| **Sequential keys** | Compact, trivially unique | **Enumerable — a data leak** | Only for genuinely public content |
| **Content-addressed keys** | Free dedupe | Identical content = identical URL; guessable if content is known | Public, durable, non-secret objects |
| **Custom slugs** | Product feature, SEO | Namespace collision handling; squatting; abuse | Paid tiers, with reservation + moderation |
| **Lazy expiry only** | Zero background cost | Storage grows forever; expired content cached | Never alone |
| **Sweeper only** | Storage honest | Expired pastes readable until the sweep | Never alone |
| **CDN TTL = min(86400, ttl)** | Expiry honoured at the edge | Lower hit ratio for expiring pastes | Correctness over hit rate |
| **Global dedupe** | 12 % storage | Refcounting, block-list blast radius, burn-after-read bugs | Storage is the dominant cost (rarely) |
| **Mutable pastes** | A familiar product model | Loses immutable caching entirely | Only if editing is a stated requirement |

## 11. Failure scenarios

**11.1 — S3 is slow or unavailable.** Reads fall from 21 ms to error. **Fix:** a Redis tier sized to the hot set (§9 step 2) so 70 % of reads never reach S3; a circuit breaker (L77) returning the cached version if any, and a 503 with `Retry-After` otherwise. Blobs are the availability floor of the read path.

**11.2 — Postgres primary fails.** Writes fail for the failover duration (75 s, L90 §4.3); reads continue entirely from CDN and Redis for the hot set. **Fix:** accept it — a 75-second write outage on a paste service is inside the error budget (L89), and the RTO work belongs to the metadata DB, not to a sharded write path.

**11.3 — Cache stampede on a viral paste.** One paste goes viral; 5,000 readers/s hit a cold key simultaneously and all miss to S3. **Fix:** request coalescing / single-flight per key (L29 §4.6), plus edge caching so only the first request per PoP reaches the origin.

**11.4 — The expired paste that stayed up.** A 1-day paste was served for 20 hours past expiry because the CDN had cached it with `max-age=86400`. **Fix:** compute `Cache-Control` at write time as `min(86400, ttl)` (§4.8).

**11.5 — Orphaned objects after a crash.** The PUT succeeded, the INSERT failed, and 4 GB/day of unreferenced objects accumulated. **Fix:** write blob first, GC weekly with a 24-hour grace period (§4.8); measure orphan rate as a metric and alarm above 1 %.

**11.6 — A 1 MB upload buffered by the gateway.** The gateway read the body before checking size; 200 concurrent 1 MB uploads exhausted memory. **Fix:** reject on `Content-Length` first, and stream with a hard cap.

**11.7 — XSS through a rendered paste.** A pasted HTML file executed on the main origin and read session cookies. **Fix:** `text/plain` + `nosniff` + a separate user-content origin + a strict CSP (§4.9).

**11.8 — The enumrable-keys incident.** A v1 used base62 of a sequence; a scraper read 190,000 pastes in a week. **Fix:** random keys with a brute-force bound (§4.6) and a per-IP read-rate limit on 404s (an enumeration attempt shows up as a 404 spike).

**11.9 — Deletion racing a cache miss.** A user deleted a paste; the CDN and Redis kept serving it for an hour. **Fix:** set `visibility = 2` first (the metadata check is on the miss path), purge the CDN key explicitly, and accept up to one Redis TTL of leakage for cached bodies.

## 12. Alternatives

| Instead of | Consider | When |
|---|---|---|
| Building it | A gist-style feature in an existing code platform | You need it for a week, not a decade |
| Object storage + metadata DB | A single `TEXT` column | Content is tiny and always accessed with metadata |
| Random keys | Content hash | Content is public and dedupe matters |
| Own CDN integration | A managed edge with a KV store | You want read-at-edge with no origin at all |
| Sweeper | Database TTL/partition drop | Your store supports native TTL (DynamoDB TTL, partition drop) |
| A moderation queue | A third-party content-safety API | You cannot staff a 24/7 review team |
| A separate service | A signed object URL from the main app | The paste is incidental to the product |

## 13. When to use it

- **Use this design** for any immutable, key-addressed blob service: pastes, gists, logs shared by URL, config snippets, error reports, build artefacts with a small index.
- **Separate content from metadata** whenever the blob's average size crosses a few kilobytes.
- **Three cache tiers** — browser, edge, in-memory — whenever content is immutable, because immutability is what makes long TTLs safe.
- **Random capability keys** for anything private by default.
- **Lazy + sweep + GC** for retention; all three, because each covers a different hole.

## 14. When *not* to use it

- **When the content must be edited** — you lose immutable caching entirely and need versioning (a different design).
- **When reads must be searched** — pastes are addressed by key; full-text search needs a different index (L47) and changes the cost model completely.
- **When the content is one byte** — a database row per paste is cheaper than an object plus metadata plus GC.
- **When the URL must never be guessable *and* the content is shared** — content addressing and secrecy are contradictory (§4.6).
- **When deletion must be immediate and total** — CDN TTLs and cache tiers mean "deleted" is eventually true; if the requirement is seconds, remove the edge cache.

## 15. Common mistakes

1. **Storing blobs in the primary database** and discovering the effect on backups, vacuum and replicas (§7).
2. **Sequential or content-derived keys for private content** (enumerable/guessable, §4.6).
3. **Key length chosen for aesthetics** rather than brute-force and collision arithmetic.
4. **No synchronous size check** before buffering the body (§11.6).
5. **Long CDN TTLs on expiring content** (§11.4).
6. **Lazy expiry only** — storage grows forever and deletion promises are unenforced.
7. **Metadata deleted before the blob** — a dangling pointer is unfixable; an orphan is GC-able (§4.8).
8. **No GC for orphans** (§11.5).
9. **Global dedupe without refcounting** (§9 step 5).
10. **Rendering user HTML on the main origin** (§11.7).
11. **Synchronous view counting** — it writes on every read and destroys cacheability (§5).
12. **Designing for a write-sharding problem that does not exist** at 58–174 writes/s.
13. **No abuse pipeline** — free anonymous URL creation will be used for phishing within weeks.
14. **Optimising the database** when 94 % of miss latency is the object store (§9 step 3).

## 16. Interview perspective

**What is actually being tested.** Whether you can move from requirements to a **quantified** design quickly, and whether you recognise that a paste service is a **storage-and-distribution** problem, not a QPS problem. Strong candidates: derive the 10 KB boundary, justify the key length with arithmetic, put the blob in object storage with the metadata in SQL, design expiry as three mechanisms, and name the abuse surface unprompted.

**Answers that land well:**

- *"What's the hard part of Pastebin?"* — "Not throughput: 5 million pastes a day is 58 writes a second. The hard parts are the key (it is a capability, so it must be unguessable, which sets the length), the storage boundary (10 KB of content against 400 bytes of metadata means two stores), and expiry (a product promise that has to be enforced at the edge, in the database, and in the blob store)."
- *"How do you generate the ID?"* — "Nine random base62 characters: 53 bits. I check two bounds — an attacker guessing one paste at 1,000 tries a second needs millions of years, and collisions in a year's writes are about 1 in 10,000. A counter would be shorter and trivially unique, but it is enumerable, so for private-by-default content it is a data leak."
- *"How long can you cache it?"* — "Forever, because it never changes — `max-age=86400, immutable`. Except when it has a TTL: then the header is computed at write time as the minimum of one day and the remaining lifetime, or the edge serves deleted content for a day."
- *"Where is your p99 coming from?"* — "Ninety-four per cent object-store GET on a cache miss. So the levers are hit ratio and object size, not database tuning — and the measured read distribution says 2 % of pastes take 70 % of reads, which fits in 5 GB of Redis."

**Common interview tasks:** estimate writes/reads/storage; choose and justify an ID scheme; split content from metadata; design the redirect/read path with caching; handle expiry; handle abuse; identify the first bottleneck at 10×.

**Red flags interviewers watch for:**

- Starting with a sharded database before estimating QPS.
- Sequential IDs for private content, with no mention of enumeration.
- Blobs in the database with no discussion of backup or vacuum impact.
- "We'll use a UUID as the primary key" with no consideration of index locality or URL length.
- No expiry mechanism beyond a `WHERE` clause.
- No abuse or moderation mention.
- Cache TTLs that ignore the product's expiry promise.
- No numbers at all.

## 17. Knowledge check

**Conceptual**

1. Explain why a paste service's architecture is dominated by **storage and distribution** rather than by request throughput. Derive the boundary at which content should leave the database for object storage, and name three database symptoms that indicate you crossed it too late.
2. Compare the three key-generation strategies (sequential, content-hash, random) on uniqueness, guessability, dedupe and deletion, and state which bound dominates the choice for a private-by-default paste service.
3. Describe the three deletion mechanisms (lazy, sweeper, GC) and explain why all three are needed. Then explain the ordering rule for deleting a blob and its metadata, and why a distributed transaction is the wrong tool.

**Practical scenario**

4. Design a paste service for BookNook's excerpt feature: 1.1 M creates/day, 2.1 KB average, 61 % of pastes never expire, 97 % CDN hit ratio at peak, a 40/week DMCA queue, and a requirement that expired content be unreachable within one hour. Give the requirements table with numbers, the capacity estimate, the API, the schema (including the citation fields), the write and read paths with component latencies, the key-length arithmetic, the expiry design including the CDN header rule, the moderation flow, and the cost per month. State the first thing that breaks at 10× and the change you would make.

**Calculation**

5. A service stores 5 M pastes/day at 10 KB average, with a 10:1 read:write ratio and a peak factor of 3. (a) Compute writes/s and reads/s at peak. (b) Compute content and metadata growth per year, and the compressed content size at 3× zstd. (c) Compute the Redis memory needed to cache the top 2 % of a 90-day corpus. (d) Compute the origin object-store GET rate at a 70 % Redis hit ratio and an 83 % CDN offload, and state whether it is a concern.

6. You choose random base62 keys. (a) Compute the keyspace for 7 and 9 characters. (b) For 1.8 billion inserts a year, compute the collision probability for each length using `n²/2K`. (c) For an attacker making 1,000 guesses/s against one paste, compute the expected time to guess for each length. (d) State which length you would ship and why, and what operational safeguard accompanies it.

**Progress checklist**

- Phase 1 — Foundations (L1–17): **complete** ✅
- Phase 2 — Scaling (L18–39): **complete** ✅
- Phase 3 — Data and Storage (L40–63): **complete** ✅
- Phase 4 — Distributed Systems (L64–92): **complete** ✅
- Phase 5 — Complete system designs (L93–106):
  - [x] **1. Design Pastebin (L93) ← you are here**
  - [ ] 2. Design a URL shortener (L94)
  - [ ] 3. Design a rate limiter (L95)
  - [ ] 4. Design a notification service (L96)
  - [ ] 5. Design search autocomplete (L97)
  - [ ] 6. Design file storage (L98)
  - [ ] 7. Design a web crawler (L99)
  - [ ] 8. Design real-time chat (L100)
  - [ ] 9. Design a news feed (L101)
  - [ ] 10. Design video streaming (L102)
  - [ ] 11. Design ride sharing (L103)
  - [ ] 12. Design e-commerce and inventory (L104)
  - [ ] 13. Design payment processing (L105)
  - [ ] 14. Design a metrics and logging platform (L106)

**NEXT LESSON: Phase 5, Topic 2 — Design a URL shortener**

---

---

# Lesson 94 — Design a URL Shortener

*Phase 5, Topic 2*

## 1. Simple definition

**The problem.** Given a long URL, produce a short one (`https://bn.link/x7Kp2Qa`). When anyone visits the short URL, redirect them to the original. Support optional custom aliases, optional expiry, and per-link click counts.

**Why it is the second design in the syllabus.** It is the smallest system whose *primary* job is a **read at extreme scale with a tiny payload**, so it isolates three problems that every high-traffic service eventually meets: **key generation without coordination**, **serving a redirect from the edge with almost no origin traffic**, and **the 301-versus-302 decision that silently breaks your analytics**.

Terms used in this lesson:

- **Slug / code**: the short identifier (`x7Kp2Qa`).
- **Redirect**: an HTTP response (`301`, `302`, `307`, `308`) pointing at the target.
- **301 Moved Permanently**: permanently cached by browsers and intermediaries; browsers may stop asking you entirely.
- **302 Found / 307 Temporary Redirect**: not cached by default; the browser asks every time.
- **Edge / PoP**: a CDN point of presence near the user, where the redirect can be answered without touching your origin.
- **Alias**: a user-chosen slug, which introduces a **unique-name registration** problem.
- **Keyspace**: the set of possible slugs, `62^L` for length `L`.
- **Hot link**: a single slug receiving a large share of traffic, typically when a campaign goes viral.

The one sentence to carry out of this lesson: **a URL shortener is a key-value lookup where the value is 200 bytes and the lookup must happen at the edge, so the design is decided by how keys are allocated without coordination, and by whether you are allowed to let the browser cache the answer — which is an analytics decision, not an HTTP decision.**

## 2. Why it exists

**Because the read path is 100× the write path, and that ratio forces the architecture to the edge.** BookNook's numbers: 100,000 new links/day (1.2 writes/s) and 10 M clicks/day (116 reads/s average, ~1,700/s at peak). A single small database handles the writes; nothing about the write path is interesting. The reads must be answered in **under 10 ms at p99 from anywhere in the world**, which means the origin sees 1–5 % of requests (L93's caching lesson, taken further).

**Because key allocation is the only genuine distributed-systems problem in it.** You need globally unique, short, non-enumerable slugs at a rate of ~1.2/s from multiple application instances, and you cannot afford a coordination round trip per write, nor a collision-handling read on the hot path. Every candidate — a central counter, a range-allocated counter, a random key, a hash, a pre-generated pool — is a different point on the spectrum between **coordination cost**, **key length**, **guessability** and **reuse risk**. That trade-off is the lesson.

**Because the redirect status code is an irreversible product decision.** `301` is permanently cached: your servers stop seeing the request, your click counts become wrong forever, and you **cannot change the destination**. That is unacceptable for marketing links (which must be re-targetable and measurable) and desirable for a stable asset URL. Choosing per-link — not globally — is the sophisticated answer.

**Because link shorteners are an abuse surface by construction.** They hide the destination, which is exactly what phishing and malware distribution want. A shortener without a destination scanner, a deny-list, an interstitial for suspicious links, and a takedown process becomes a liability within weeks — and the platform's reputation (blocklisted domain = every link you ever issued is dead) is the risk.

**And because "reuse" turns a bug into a data-leak.** If a slug is recycled after deletion or expiry, an old link in a public forum starts pointing at a stranger's content, or at an attacker's chosen destination. Keys must be **monotonically additive and never reused** — which rules out naive counter recycling and demands a tombstone or an irreversibly large keyspace.

## 3. Real-world analogy

**A licence plate.**

A short registration mark (`X7 KPQ`) identifies a vehicle, and it is *the* compact handle that everyone uses in place of the full identity (a VIN, 17 characters, and the vehicle's entire record). Everything about the analogy matches the design problems:

**Plates are allocated from a finite space by an authority, and never reused carelessly.** A plate belongs to a vehicle *at a time*; when a vehicle is scrapped, the number is normally retired for a period precisely so a stale sighting is never misattributed. A URL shortener that recycles slugs gets exactly the failure a government would: an old citation attached to a new owner. **Never reuse a slug** (§4.7).

**Vanity plates are a separate, scarce allocation.** Somebody pays for `BOOKS1`; that is a *registration* — a uniqueness check against a namespace, with a fee, a reservation, and a revocation policy. That is exactly the custom-alias problem, and it is a different code path from bulk allocation (§4.6).

**You can look a plate up from a camera, at a distance, in milliseconds — without asking the registry.** An automatic plate reader matches against a **local** copy of the hot data, not a round trip to a central database. That is the edge cache, and it is why the central registry's lookup latency does not matter to the reader at the roadside.

**And the plate itself is not the destination: it is a pointer to an authority that can revoke.** The registry can mark a vehicle stolen, and every reader then gets a different answer for the same plate. That is the deny-list check, and it is the only reason `suspended` links return an interstitial instead of a redirect (§4.8).

The analogy's one useful mismatch: **plates must be human-readable and short, so the space is genuinely constrained** (7 characters in the UK ≈ 3×10¹²). A URL slug need only be machine-short, so you can always buy more space by adding a character — but each character costs 1.5 % more characters on every link ever printed, which is why 7–9 is the practical range rather than 12 (§4.5).

## 4. Detailed technical explanation

### 4.1 Requirements

**Functional.**

| # | Requirement |
|---|---|
| F1 | Create a short link for a destination URL, returning the slug |
| F2 | Redirect `GET /{slug}` to the destination |
| F3 | Optional custom alias (paid tier), with a uniqueness guarantee |
| F4 | Optional expiry (default: none for paid, 2 years for anonymous) |
| F5 | Per-link click count and referrer breakdown, visible within 60 s |
| F6 | Delete/disable a link (owner token, or moderation) |
| F7 | Reject unsafe destinations (malware, phishing), with an interstitial |

**Non-functional.**

| # | Requirement | Number |
|---|---|---|
| N1 | Redirect availability | 99.99 % (a dead short link breaks every page that embeds it) |
| N2 | Redirect latency | p99 < 10 ms at the edge; p99 < 40 ms on an origin miss |
| N3 | Write latency | p99 < 250 ms including the destination scan |
| N4 | Scale | 100,000 creates/day; 10,000,000 redirects/day |
| N5 | Slug length | ≤ 8 characters preferred, 9 hard maximum |
| N6 | Permanence | A slug is **never** reissued, even after deletion |
| N7 | Analytics freshness | 60 s (batched, never on the request path) |
| N8 | Redirectability | The destination of a non-301 link must be changeable after creation |

### 4.2 Capacity estimation

```
  WRITES      100,000/day ÷ 86,400     = 1.16/s  · peak × 6 = 7/s
  READS       10,000,000/day ÷ 86,400  = 115.7/s · peak × 15 = 1,736/s
              (the 15× peak factor is real: a campaign launch is a spike,
               not a diurnal curve — this is the defining traffic shape)
  RATIO       100 : 1 reads to writes

  STORAGE     100,000 × 260 B = 26 MB/day  = 9.5 GB/year
              + click events: 10 M/day × 120 B = 1.2 GB/day = 438 GB/year
                (kept in a columnar store, aggregated after 90 days — L57)
              ⇒ the LINK TABLE IS TINY. 5 years of links = 47 GB, which fits
                on one modest server with room for indexes. The design is
                NOT storage-bound.

  KEYS        For 100,000/day over 10 years = 365 M slugs:
              62^6 = 5.7e10   (6 chars: 1,500× headroom on 10-year volume)
              62^7 = 3.5e12   (7 chars: chosen — see §4.5)
  ⇒ capacity conclusion: ONE write database, ONE metadata design, and the
    entire engineering effort goes into the read path's edges. State this
    explicitly; a candidate who shards the write path here has misread the
    problem.

  BANDWIDTH   redirects are 200–400 B each: 1,736/s × 350 B = 0.6 MB/s.
              Nothing.
```

### 4.3 API

```
POST /v1/links
     { "url": "https://booknook.example/books/9780140449136?ref=spring",
       "alias": null,                    # custom, paid tier
       "ttl_seconds": null,
       "redirect_type": 302 }            # default 302; 301 only if requested
  →  201 { "slug": "x7Kp2Qa",
           "short_url": "https://bn.link/x7Kp2Qa",
           "expires_at": null,
           "manage_token": "m_7d1f…" }   # returned once; hashed at rest
     errors: 400 invalid url · 409 alias taken · 403 alias reserved ·
             422 unsafe destination (with a reason code) · 429 rate limited

GET  /{slug}
  →  302 Location: <destination>
        Cache-Control: no-store          # for 302 (see §4.4)
        X-Robots-Tag: noindex
  →  301 Location: <destination>
        Cache-Control: public, max-age=31536000, immutable   # for 301
  →  404 unknown · 410 disabled/expired · 451 blocked (with interstitial HTML)

PATCH /v1/links/{slug}
     Authorization: Bearer <manage_token>
     { "url": "...", "enabled": false }
  →  200 · 403 · 409 (if the link was created as a 301, changing the
     destination is REFUSED — the clients already cached it)

GET  /v1/links/{slug}/stats?from=&to=
  →  200 { "clicks": 41207, "by_day": [...], "top_referrers": [...] }

DELETE /v1/links/{slug}   → 204 (tombstone retained; the slug is never reused)
```

### 4.4 The redirect status code — the decision that cannot be reversed

| Aspect | `301` permanent | `302`/`307` temporary |
|---|---|---|
| Browser caching | Caches indefinitely (often ignoring your `Cache-Control`) | Not cached by default |
| Server sees the click | **No** — after the first click, ever | Yes, every time |
| Destination changeable | **No** — you cannot reach the cached clients | Yes |
| Analytics | Permanently undercounted from the first visit | Accurate |
| Origin load | ~0 after warm-up | Edge or origin per click |
| Correct for | Stable asset URLs (docs, canonical resources) | **Marketing, campaigns, anything measured** |

```
  THE TRAP, precisely: a Chrome user who clicks a 301 link will not ask your
  server again for that slug — even after you delete it, even after you
  disable it, even after a moderation block. Measured on BookNook's first
  iteration:
      · 61 % of clicks were invisible after the first visit per browser
      · a malware link that was blocked still resolved for 8 days in the
        browsers that had cached it
      · a typo'd destination could not be fixed at all
  ⇒ DEFAULT TO 302. Offer 301 only for links the customer declares permanent,
    and refuse to allow the destination to change on a 301 (PATCH returns 409).
  ⇒ AND: because a 302 is not cached by browsers, EDGE CACHING is the only way
    to keep origin load down — hence a 60-second edge TTL with a stale-while-
    revalidate policy: the edge absorbs 98 % of a viral spike while remaining
    changeable within a minute.
```

### 4.5 Key generation — the five options, with the arithmetic

```
  Budget: 100,000/day = 36.5 M/year. Plan for 10 years = 365 M slugs.

  A. RANDOM base62, 7 chars  (RECOMMENDED)
      K = 62^7 = 3.52e12
      collisions over 365 M inserts: n²/2K = (3.65e8)² / (7.04e12) = 18.9
      ⇒ EXPECTED 19 collisions in 10 years. Not zero — so the UNIQUE
        constraint plus one retry is mandatory, and the retry rate is
        365 M/3.5e12 × 100 = 0.01 % — invisible.
      guessability: at 1,000 guesses/s, 1.76e12 expected guesses = 55 years
      ⇒ for a PUBLIC redirect this is more than enough; for an unlisted link
        (a private share) it is adequate but not a secret. State that.

  B. COUNTER + base62 of a sequence
      + zero collisions, shortest possible keys, trivially unique
      − ENUMERABLE: x7Kp2Qa ~ "next link" means anyone can walk your entire
        link table at 1,000/s. For a link shortener that is mostly a privacy
        issue (unlisted links) and an abuse issue (enumerate live phishing
        links) rather than a catastrophe — but it also makes the sequence a
        public business metric (your growth rate is in your URLs).
      − requires a coordination point: a range allocator (blocks of 10,000 per
        instance) or a ticket server. One extra moving part for a system with
        1.16 writes/s — cheap here, and the enumerability is the real cost.

  C. HASH the destination (base62(sha256(url))[:7])
      + identical destinations collapse to one slug (dedupe for free)
      − REQUIRES a collision-handling loop on the write path (different URLs
        can share 7 characters of a hash), and it breaks per-link analytics:
        every campaign URL that differs only by `?ref=` hashes differently
        (fine) but a re-created identical URL returns the OLD slug and joins
        the old click stream (often surprising).
      − reveals equality: two customers shortening the same URL get the same
        slug, so one can observe the other's clicks. For a multi-tenant
        product this is a privacy leak.

  D. PRE-GENERATED KEY POOL (a "key service")
      a background job generates random keys into a table; application nodes
      lease blocks of 10,000 with one UPDATE ... RETURNING
      + zero write-path coordination per request, zero collisions, keys stay
        unguessable (they are random)
      + the lease is ONE query per 10,000 links — at 1.16 writes/s, a node
        leases once every 2.4 hours
      ⇒ this is the best of A and B: random APPEARANCE, sequential
        ALLOCATION. BookNook chose this (§9 step 2).

  E. Snowflake-style (time + node + sequence) encoded in base62
      + unique without coordination, roughly sortable, time-ordered
      − leaks creation time and node identity (a minor privacy issue), and
        the sortability is what makes it enumerable-ish (adjacent IDs are
        adjacent times, so a guess narrows the neighbourhood)
```

**Decision for BookNook: D, with 7-character slugs.** The pool means no coordination on the request path and no collision retries, the random appearance preserves unguessability, and 7 characters carry a 96× headroom over the 10-year volume.

### 4.6 Custom aliases are a different problem

A custom alias (`bn.link/spring-sale`) is a **name registration**, not a key allocation:

```
  · it must be checked for uniqueness against BOTH the alias namespace and the
    generated one (a generator could emit "spring-sale"? — no, because the
    generator's alphabet excludes `-` ⇒ keep the two alphabets DISJOINT and
    the collision problem disappears for free)
  · it is contested: someone will want a name another customer has
    ⇒ reservation rules: paid tier may reserve, free tier cannot; a
      trademark complaint process; a dormant-name reclaim policy after 24
      months of zero clicks
  · it is an abuse carrier: `bn.link/apple-support` is a phishing magnet
    ⇒ aliases get an additional review: no brand names on a deny-list
      (apple, microsoft, paypal, …) unless the account is verified
  · it needs its own race-safe insert: a UNIQUE constraint, and on violation
    return 409 — never a check-then-insert (that races, L51)
```

### 4.7 Never reuse a slug

```
  Deleted link, slug freed, new customer gets it:
      the old link is embedded in 4,000 forum posts and 12 printed flyers.
      It now points at the new customer's destination.
  ⇒ two acceptable policies:
      (a) TOMBSTONE: keep the row with enabled = false; the slug stays
          reserved forever (cost: one row per deleted link, ~260 B)
      (b) NEVER-FREE KEYSPACE: keys are random from a space 100× larger than
          any plausible lifetime volume, so the probability of ever
          re-emitting a retired key is ~0 — and the UNIQUE constraint means
          a re-emission would fail rather than overwrite.
  ⇒ BookNook does BOTH: rows are tombstoned (so the slug is provably
    reserved), AND the keyspace is 62^7. Cost of tombstoning at 100,000
    deletes/day for 10 years = 365 M rows = 47 GB. Acceptable; state it.
```

### 4.8 Abuse: the part that decides whether the product survives

```
  THE THREAT: a link shortener is a legitimate-looking domain that hides its
  destination. That is precisely the requirement for phishing, malware
  distribution, and spam. Every large shortener is on blocklists at some point,
  and once YOUR domain is blocklisted, EVERY link you ever issued is dead —
  including your customers' printed campaigns. The risk is existential.

  THE PIPELINE, at write time (synchronous, 40 ms budget):
    1. scheme + shape validation (http/https only; reject JS, data, file)
    2. deny-list of hosts (exact + suffix, updated hourly)
    3. redirect-chain resolution (max 3 hops, 500 ms budget) — a shortener
       pointing at another shortener is a laundering pattern
    4. reputation lookup (a third-party feed), cached
    5. brand-impersonation heuristic on the alias
    ⇒ verdicts: allow · allow-with-interstitial · block
  POST-WRITE (asynchronous, minutes):
    6. full-page classification (is the destination a credential-harvest form?)
    7. retro-scan every live link weekly; a destination can be sold/hijacked
       after the link was created — this is the common case, not the rare one
    8. report queue: 24/7 SLA, one-click disable, deny-list the destination
  AT READ TIME:
    · edge-cached blocks (a 100-row deny-list replicated to the edge, so a
      blocked link returns 451 immediately even without origin contact)
    · interstitial (an "are you sure?" page) for suspicious-but-allowed links,
      and — importantly — interstitial pages must NOT be 302-cached or the
      user sees it forever
  MEASURED: BookNook's pipeline caught 94 % of blocklisted destinations at
    create time; the remaining 6 % were caught by the weekly retro-scan at a
    median of 3.1 days, which is the residual risk the interstitial exists to
    cover.
```

### 4.9 The read path: three tiers and one invalidation strategy

```
  TIER 1 — browser: 302 ⇒ no caching (analytics win, origin load cost)
  TIER 2 — CDN edge: cache the 302 RESPONSE ITSELF for 60 s with
           stale-while-revalidate, keyed by slug. This is unusual and correct:
           the edge serves a 302 from its own cache without contacting the
           origin, which keeps a viral spike off your servers while letting a
           disable take effect within 60 s.
           ⚠ configured via CDN rules, not via `Cache-Control: no-store` on the
             origin response (which would forbid it). Origin sends
             `Cache-Control: private, max-age=60` and the CDN rule overrides.
  TIER 3 — the resolution service's local store: an in-process LRU of the top
           100,000 slugs (~40 MB), refreshed by a pub/sub invalidation channel
           (L31 §4.7) and by TTL. Measured hit ratio 96 % of origin requests.
  TIER 4 — Redis (shared): 2 GB holds 4 M slugs, i.e. the entire hot set.
  TIER 5 — Postgres: the source of truth, 47 GB, PK lookup 0.3 ms.
  ⇒ origin QPS = 1,736 × 0.03 (edge miss) = 52/s, of which 96 % are served by
    the local LRU ⇒ 2 database reads/s at peak. THE DATABASE NEVER SEES THE
    SPIKE. That is the whole design.
```

## 5. Request / data flow

```
 CREATE — POST /v1/links
 ─────────────────────────────────────────────────────────────────────────
 1. gateway      auth · rate limit (60/min free, 6,000/min partner)   [1 ms]
 2. validate     URL parse, scheme allow-list, length ≤ 2,048         [0.2 ms]
 3. abuse gate   deny-list, redirect-chain resolve, reputation        [40 ms]
 4. key          alias? → try INSERT with UNIQUE (409 on conflict)
                 else   → lease a block of 10,000 random slugs if the
                          local pool is empty (one query per ~2.4 hours)
                                                                      [0.1 ms]
 5. INSERT       links row (slug, url, redirect_type, expires_at, owner)  [1 ms]
 6. publish      Redis pub/sub: `inv:{slug}` = url  (so every edge node's LRU
                 can be updated or invalidated, L31)                  [0.3 ms]
 7. async        outbox → analytics, retro-scan queue, abuse classifiers
 8. return       201 {slug, short_url, manage_token}
                 TOTAL p50 44 ms, p99 190 ms (the abuse gate dominates)

 REDIRECT — GET /x7Kp2Qa, edge cache miss
 ─────────────────────────────────────────────────────────────────────────
 1. edge         lookup slug in the 60-second edge cache: HIT 98 % ⇒ done,
                 no origin contact, 2 ms at the PoP
                 MISS → forward to the origin PoP's resolution service
 2. resolution   local LRU (100k entries): HIT 96 % of origin requests ⇒ 0.4 ms
 3. Redis        GET link:{slug}  (2 GB, whole hot set)                [0.5 ms]
 4. Postgres     SELECT url, redirect_type, enabled, expires_at
                 FROM links WHERE slug = $1     (PK)                   [0.3 ms]
 5. checks       enabled? expires_at > now? Deny-list (edge-replicated)?
                 ⇒ 410 / 451 interstitial
 6. respond      302 Location: <url>   Cache-Control: private, max-age=60
 7. async        enqueue click event into a ring buffer, flushed every 100 ms
                 in batches of ~170 events to Kafka (L70) — NEVER a
                 synchronous write on the redirect path
                 TOTAL p50 1.2 ms (edge), 3.9 ms (origin), p99 22 ms
```

## 6. ASCII diagram

```
   user ──► DNS/anycast ──► nearest PoP
                              │
                    ┌─────────▼──────────────────────────────┐
                    │ EDGE CACHE (60 s, stale-while-revalidate)│  98 % hit
                    │ serves the 302 itself                    │
                    └─────────┬──────────────────────────────┘
                              │ 2 % miss
                    ┌─────────▼──────────────────────────────┐
                    │ resolution service (regional)          │
                    │ · local LRU 100k slugs (40 MB) 96 % hit│
                    │ · deny-list, edge-replicated           │
                    └────┬───────────────────────┬───────────┘
                         │ miss                  │ async click events
                  ┌──────▼──────┐          ┌─────▼──────────────┐
                  │ Redis 2 GB  │          │ ring buffer → Kafka│
                  │ hot set     │          │ → analytics (60 s) │
                  └──────┬──────┘          └────────────────────┘
                         │ miss (≈2/s at peak!)
                  ┌──────▼───────────────────┐        ┌───────────────┐
                  │ Postgres (source of truth)│◄───────│ key service   │
                  │ 47 GB, PK lookup 0.3 ms   │        │ (leases random│
                  │ slugs never reused        │        │  blocks)      │
                  └──────────────────────────┘        └───────────────┘

  WRITE PATH (1.16/s average — a rounding error):
     client → gateway → validate → abuse gate (40 ms) → key/alias →
              INSERT → pub/sub invalidate → 201

  THE SHAPE OF THE TRAFFIC, which is why the edges exist:
      writes: ▁▁▁▁▁▁▁▁▁▁▁▁  (flat, 1.16/s)
      reads:  ▁▁▂▅█▅▂▁▁▁▁▁  (15× spikes on campaign launches)
      ⇒ design for the SPIKE, size for the average. The origin never sees it.
```

## 7. Detailed example

**BookNook's affiliate and campaign links.** BookNook issues short links for publisher campaigns, author newsletters, and review-page "buy this book" buttons.

```
  measured production numbers:
    · 100,000 creates/day (attributes: 71 % campaign, 22 % review pages,
      7 % customer sharing)
    · 14 M redirects/day (140 : 1) with a 22× peak during a newsletter send
      (a 09:00 blast puts 40,000 concurrent readers on 12 slugs)
    · 6.1 % of creates are custom aliases (publisher brands)
    · destination changes: 3,100/month (campaign URLs are re-targeted often)
      ⇒ THIS IS WHY 302 IS THE DEFAULT: 3,100 changes/month would be
        impossible with 301, and 61 % of clicks would be invisible.
    · abuse: 210 malicious creates/day attempted; 197 blocked at create,
      13 caught by the retro-scan
```

**What the first version got wrong, and the measured cost:**

| v1 mistake | Symptom | Fix | Recovery |
|---|---|---|---|
| `301` everywhere | 61 % of clicks invisible; a typo'd destination unfixable | Default `302`, edge-cache the response for 60 s | Analytics recovered within a week; the unfixable links were reissued |
| Sequential slugs | A crawler walked 1.4 M links in 9 days; unlisted links leaked to a scraper | Random slugs from a leased pool | Reissued; key length unchanged at 7 |
| `SELECT` then `INSERT` for aliases | 3 duplicate aliases under concurrent creation | `UNIQUE` constraint + 409 | — |
| Synchronous click `UPDATE` | Redirect p99 210 ms under spike; the link table was a write hotspot | Ring buffer + batched flush (60 s freshness) | p99 22 ms |
| Slug reuse after delete | 6 old printed links pointed at new campaigns | Tombstones + never-reuse rule | 6 incidents, all customer-visible |
| No destination scan | Domain blocklisted for 4 days; **all** links dead | Create-time scan + interstitial + retro-scan | 4 days of every customer's links being blocked; a reputation rebuild |

**The blocklisting incident is the one to study**, because it is the failure that a technical design alone cannot prevent:

```
  4 days of domain blocklisting:
    · 14 M redirects/day failed with a browser interstitial
    · 71 % of links were campaign links — publishers saw zero attributed
      traffic and 9 of them suspended their accounts
    · the domain reputation took 11 weeks to recover
    · revenue attributed to short-link campaigns fell 84 % for those 4 days
  ⇒ the design change was organisational as much as technical: a 24/7 abuse
    queue with a 30-minute disable SLA, a pre-launch trust review with 3 major
    blocklist operators (so that incidents are resolved in hours, not days),
    and a SECOND domain kept warm and unused, so a burned domain can be
    swapped for customers who opt in (§9 step 5).
```

## 8. Code or configuration example

```python
# ── Key service: random appearance, sequential allocation, zero collisions ─
ALPHABET = string.ascii_letters + string.digits      # 62
KEY_LEN = 7

def _mint(n: int) -> list[str]:
    return ["".join(secrets.choice(ALPHABET) for _ in range(KEY_LEN))
            for _ in range(n)]

class KeyPool:
    """Leases blocks of pre-generated slugs. One DB query per BATCH links."""
    BATCH = 10_000

    def __init__(self): self._lease: list[str] = []

    def next_key(self) -> str:
        if not self._lease:
            self._lease = self._lease_block()
        return self._lease.pop()

    def _lease_block(self) -> list[str]:
        # 1) take any pre-generated keys created by the background minter
        rows = db.fetch("""
            DELETE FROM key_pool
            WHERE  ctid IN (SELECT ctid FROM key_pool LIMIT %s)
            RETURNING slug""", (self.BATCH,))
        keys = [r["slug"] for r in rows]
        # 2) if short, mint locally (still unique: UNIQUE constraint on links)
        if len(keys) < self.BATCH:
            keys += _mint(self.BATCH - len(keys))
        return keys

# The background minter keeps 5 M keys in reserve; it is a trivial cron job
# and it means the request path NEVER waits for key generation.
```

```python
# ── Create path: alias vs generated, abuse gate, never check-then-insert ──
@app.post("/v1/links")
async def create_link(req: CreateLink, request: Request):
    target = validate_url(req.url)                  # http/https only, ≤ 2048
    await rate_limiter.check(request)               # L95

    verdict = await abuse.shallow_check(target)     # 40 ms budget
    if verdict is BLOCK:
        raise HTTPException(422, detail={"code": verdict.reason})

    if req.alias:
        if not ALPHABET_ONLY.match(req.alias):       # '-' not in generator
            raise HTTPException(422, "invalid alias")  # alphabet: disjoint
        slug = req.alias
    else:
        slug = key_pool.next_key()

    try:
        row = await db.fetchrow("""
            INSERT INTO links (slug, url, redirect_type, expires_at,
                               manage_token_hash, created_by, verdict)
            VALUES ($1,$2,$3,
                    CASE WHEN $4::int IS NULL THEN NULL
                         ELSE now() + ($4::text||' seconds')::interval END,
                    $5,$6,$7)
            RETURNING slug, expires_at""",
            slug, target, req.redirect_type, req.ttl_seconds,
            sha256(req.manage_token), req.account_id, verdict.name)
    except UniqueViolation:
        # Covers BOTH an alias race and a vanishingly rare generated collision
        raise HTTPException(409, detail={"code": "alias_taken"}
                            if req.alias else {"code": "retry"})

    # Invalidate/prime every edge node's local LRU (L31)
    await redis.publish(f"link:{slug}", json.dumps({"url": target,
                                                    "t": req.redirect_type}))
    return {"slug": slug, "short_url": f"https://bn.link/{slug}"}
```

```go
// ── Redirect path: the hot path. Note what is NOT here. ──────────────────
func (s *Server) redirect(w http.ResponseWriter, r *http.Request) {
    slug := chi.URLParam(r, "slug")

    // 1. deny-list first: a blocked link must fail even if cached elsewhere
    if s.denied.Contains(slug) || s.denied.ContainsHost(slug) {
        s.renderInterstitial(w, slug, "blocked")
        return
    }

    // 2. local LRU (no network, ~400 ns)
    if e, ok := s.lru.Get(slug); ok {
        s.serve(w, r, e)                 // sets Location + Cache-Control
        s.clicks.Push(slug, r.Referer()) // non-blocking ring buffer
        return
    }

    // 3. Redis, then Postgres — with singleflight so one miss per slug
    e, err := s.singleflight.Do(slug, func() (*Link, error) {
        if l, err := s.redis.Get(slug); err == nil { return l, nil }
        return s.db.Get(slug)
    })
    if err != nil { http.Error(w, "not found", 404); return }

    s.lru.Add(slug, e)
    s.serve(w, r, e)
    s.clicks.Push(slug, r.Referer())
}

func (s *Server) serve(w http.ResponseWriter, r *http.Request, e *Link) {
    if !e.Enabled || (e.ExpiresAt != nil && e.ExpiresAt.Before(time.Now())) {
        http.Error(w, "gone", 410)
        return
    }
    // 302 by default: measurable AND changeable. The CDN caches the RESPONSE
    // for 60 s at the edge (a CDN rule, not this header), so a viral spike
    // never reaches this function.
    w.Header().Set("Location", e.URL)
    w.Header().Set("Cache-Control", "private, max-age=60")
    w.Header().Set("X-Robots-Tag", "noindex")
    w.WriteHeader(http.StatusFound)          // 302
}
```

```sql
-- ── The click pipeline: aggregate, never per-click rows on the hot path ──
-- Raw events land in a columnar store partitioned by day (L57/L58).
SELECT slug,
       date_trunc('hour', ts) AS bucket,
       count(*)                                   AS clicks,
       count(DISTINCT visitor_hash)               AS uniques,
       approx_count_distinct(country)             AS countries
FROM   click_events
WHERE  ts >= now() - interval '90 days'
GROUP  BY 1, 2;

-- ── The link table. Note the tombstone column and no DELETE anywhere. ────
CREATE TABLE links (
  slug              VARCHAR(16) PRIMARY KEY,
  url               TEXT        NOT NULL,
  redirect_type     SMALLINT    NOT NULL DEFAULT 302,
  verdict           SMALLINT    NOT NULL,        -- allow / interstitial / block
  enabled           BOOLEAN     NOT NULL DEFAULT true,
  expires_at        TIMESTAMPTZ,
  manage_token_hash BYTEA       NOT NULL,
  created_by        BIGINT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at        TIMESTAMPTZ                  -- tombstone: never reissue
);
CREATE INDEX links_expiry ON links (expires_at) WHERE expires_at IS NOT NULL;
```

```yaml
# ── CDN rule: cache the 302 at the edge, which the origin header forbids ──
# (CloudFront/Cloudflare equivalent of "cache the redirect, honour the TTL")
cache_rule:
  match: "path matches ^/[A-Za-z0-9]{3,16}$"
  cache_ttl: 60
  stale_while_revalidate: 30
  forward: { headers: none, cookies: none, query: none }  # slug is the key
  # ⚠ Exclude /v1/* and any interstitial path, or a customer's "blocked"
  #   page gets cached and follows them around for a minute.
```

## 9. Numerical example

BookNook: 100,000 creates/day, 14 M redirects/day, 22× peak, 7-char slugs.

**Step 1 — the origin load chain, tier by tier.**

```
  average redirects           = 14,000,000 / 86,400 = 162/s
  design peak (22× factor)    = 162 × 22 ≈ 856/s   ← the number to size for.
      It is not a diurnal curve: a newsletter blast puts ~40,000 readers on
      12 slugs inside 90 seconds, so the spike is extreme AND concentrated
      on a handful of keys.
  edge cache hit (98 %)       ⇒ origin sees 17/s
  local LRU hit (96 %)        ⇒ 0.7/s reach Redis
  Redis hit (99.4 %)          ⇒ 0.004/s reach Postgres
  ⇒ Postgres serves about 4 reads per 1,000 seconds at peak.
    A $30/month instance is over-provisioned by 10,000×, and the entire
    scalability conversation is about the EDGE, not the database.
```

**Step 2 — keyspace and collision arithmetic, 10-year horizon.**

```
  volume: 100,000/day × 365 × 10 = 365 M slugs
  62^7 = 3.52e12 ⇒ utilisation 0.0104 % of the space
  expected collisions (random): (3.65e8)² / (2 × 3.52e12) = 18.9
  ⇒ with a key POOL (no repeats by construction) collisions are ZERO;
    with naive random generation they are 19 over a decade, each resolved by
    one UNIQUE violation + retry. Either is acceptable; the pool simply avoids
    the retry path entirely and costs one cron job.
  shortener length vs space:
      6 chars: 5.68e10 → 1,556× headroom over 10 years (fine, but leaves no
               room for a viral organic growth or a second product)
      7 chars: 3.52e12 → 9,644× headroom    ← CHOSEN
      8 chars: 2.18e14 → 600,000× headroom  (unnecessary; 14 % longer URLs)
```

**Step 3 — what the 302 costs, in origin requests and in analytics.**

```
  with 301 (browser-cached):
      first click per (browser, slug) reaches the origin, then never again
      measured: 61 % of clicks invisible; origin load ~0
  with 302 + edge cache 60 s:
      each slug reaches the origin at most once per 60 s per PoP, so the
      ceiling for the 12 hot slugs is 12 × 24 PoPs × 1,440 minutes =
      414,720 fetches/day — and the measured figure is 2,100/day, because
      most PoPs never see those slugs.
  ⇒ the 302 gives 100 % click visibility for +2,100 origin fetches/day.
    That is 0.015 % of the redirect volume. THERE IS NO TRADE-OFF.
```

**Step 4 — analytics pipeline sizing.**

```
  click events: 14 M/day = 162/s average, 856/s peak
  event size: 120 B (slug 8, ts 8, referrer-bucket 2, country 2, ua 8,
              visitor_hash 16, padding 76) ⇒ 1.7 GB/day raw, 20 GB/day in a
              columnar store after 10× compression ≈ 2 GB/day on disk
  60-second freshness via a Kafka topic partitioned by slug:
      14,000,000 / 86,400 × 60 = 9,722 events per flush window;
      at 120 B that is 1.2 MB per minute — nothing.
  ⇒ analytics is not the scaling problem either; the SPIKE SHAPE is. The ring
    buffer in front of Kafka exists to decouple 856/s from the broker's
    willingness to accept 856/s bursts (§5, step 7).
```

**Step 5 — the cost of the blocklisting incident, which dominates every other number.**

```
  probability: 1 event per 2.5 years (industry base rate for a widely used
      short domain); measured cost when it happened:
        blocked redirects      14 M/day × 4 days = 56 M failed redirects
        attributed revenue     84 % drop for 4 days on campaign traffic
                               = 4 days × £41,000/day × 0.84 = £137,760
        customers lost         9 publishers suspended = £31,000/yr recurring
        reputation rebuild     11 calendar weeks at ~40 % of two engineers
                               ≈ 0.17 FTE-years ≈ £18,000
  ⇒ expected annual cost ≈ (£137,760 + £31,000 + ~£18,000) / 2.5 = £74,700/yr
  ⇒ compare: a second warm domain + 24/7 abuse queue + blocklist relationships
    costs ≈ £96,000/yr. The mitigation is a LOSS at face value by £21,300/yr.
  ⇒ BUT the second domain also lets enterprise customers opt into a domain
    with its own reputation, which BookNook sold to 4 publishers for £28,000/
    yr, making the package positive. THE POINT: quantify the risk, then find
    the version of the mitigation that also creates value — or accept the
    risk explicitly rather than by omission.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **302 default** | Measurable, changeable, revocable | Origin (edge) sees clicks | Almost always |
| **301** | Near-zero origin load; permanent | No analytics, destination frozen | Stable asset URLs, declared permanent |
| **Random 7-char slugs** | Unguessable, non-enumerable | Collision retries (or a pool) | Any link that might be unlisted |
| **Sequential slugs** | Shortest, no collisions | Enumerable; leaks growth rate | Internal-only, never public |
| **Hashed destination** | Dedupe, idempotent creation | Cross-tenant click leakage; write-path collision loop | Single-tenant, public destinations |
| **Key pool (leased blocks)** | No request-path coordination, random appearance | A background job and one extra table | **Recommended** |
| **Custom aliases** | Product value, SEO | Namespace, squatting, brand abuse | Paid tier with a review queue |
| **Never reuse slugs (tombstones)** | No stale-link data leaks | 47 GB over a decade; never reclaims | Always |
| **Edge-cached 302 (60 s)** | Survives viral spikes at zero origin cost | A disable takes up to 60 s | Any public shortener |
| **Synchronous click counting** | Real-time numbers | Kills cacheability and the write path | Never at scale |
| **Synchronous abuse scan** | Blocks most abuse at creation | 40 ms added to a 250 ms write path | Always, on the write path |
| **Interstitial for suspicious links** | Safety without blocking | Friction; must not be cached | Suspicious-but-not-blocked verdicts |

## 11. Failure scenarios

**11.1 — Domain blocklisted.** 4 days of dead links; £137k of attributed revenue; 11 weeks to recover reputation (§7, §9 step 5). **Fix:** create-time scanning, retro-scan, 24/7 queue, blocklist relationships, a warm second domain.

**11.2 — Slug reuse.** 6 printed links redirected to unrelated campaigns after a delete freed their slugs. **Fix:** tombstones + never-reuse (§4.7).

**11.3 — 301 everywhere.** 61 % of clicks invisible; 3,100 destination changes/month impossible. **Fix:** 302 default, edge-cached for 60 s (§4.4).

**11.4 — Cache stampede on a viral slug** (L29 §4.6). 856/s on one slug; every one missed the LRU because it was brand new. **Fix:** singleflight per slug so one fetch populates the cache; the edge cache absorbs the rest after the first 60 s.

**11.5 — Alias race.** Two customers created `bn.link/spring-sale` within 4 ms; both succeeded under `SELECT`-then-`INSERT`. **Fix:** `UNIQUE` + 409 (§4.6).

**11.6 — Click counter becomes a write hotspot.** A synchronous `UPDATE links SET clicks = clicks + 1` serialised all redirects for a viral slug: p99 210 ms, then lock contention timeouts. **Fix:** ring buffer + batched aggregation (§8).

**11.7 — Enumerable slugs.** A crawler read 1.4 M links in 9 days, including unlisted ones. **Fix:** random slugs; and alarm on 404 rate spikes (enumeration looks like a 404 storm) (§4.5).

**11.8 — Stale Redis after a disable.** A moderation block was invisible for 45 s because the edge and LRU still held the old value. **Fix:** publish invalidation on the disable path (not just on create), and take the deny-list check **before** the cache lookup (§8: the deny-list is checked first, so a block is immediate even with a warm cache).

**11.9 — `Cache-Control: no-store` plus a CDN rule conflict.** The origin's header forbade caching, overriding the CDN rule, so every redirect hit the origin; a spike took the service down. **Fix:** the origin sends `private, max-age=60` and the CDN rule overrides for slug paths only — verify with a real `curl -I` against a PoP, not by reading a dashboard.

## 12. Alternatives

| Instead of | Consider | When |
|---|---|---|
| A shortener service | A redirect table in your existing edge KV | The feature is small and the edge already exists |
| Random slugs | Hashes of the URL | You want idempotent creation and dedupe |
| 302 + edge cache | 301 | The destination is genuinely permanent and unmeasured |
| A second domain | A subdomain of the customer's own domain (CNAME) | Brand-sensitive customers; also spreads reputation risk |
| Own abuse pipeline | A commercial URL-reputation API | You cannot staff 24/7 review |
| Slug allocation service | A managed ID service (Snowflake-type) | You already run one; the pool is a 20-line version of it |
| Analytics in Postgres | A columnar/streaming store (L57, L58) | > 1 M clicks/day |

## 13. When to use it

- **A dedicated shortener** when links must be **measurable, changeable and revocable** — which is the requirement for nearly all marketing, partner and campaign traffic.
- **Edge-cached 302s** whenever redirect volume is spiky, which it always is.
- **A key pool** whenever you need unguessable keys without request-path coordination.
- **Tombstones** in any system where an identifier is printed, shared or embedded.
- **A create-time abuse gate** in any service where a user can supply a hidden destination.

## 14. When *not* to use it

- **When the URL is an internal identifier** — do not shorten what never leaves your systems; it adds a hop and a failure domain.
- **When permanence with zero infrastructure is the requirement** — a 301 to a stable path is better than a redirect service you must keep alive for a decade.
- **When the analytics are the point** — use real link tracking (UTM + server-side events) rather than inferring intent from clicks.
- **When the content is sensitive** — a shortener is an unauthenticated public pointer; do not use it for anything that needs access control.
- **When the destination changes constantly** — that is a feature of a 302, but if it changes daily the customer actually wants a landing page.

## 15. Common mistakes

1. **`301` by default**, destroying analytics and the ability to fix a destination (§4.4).
2. **Sequential slugs** for public links (§11.7).
3. **Reusing slugs** after delete or expiry (§4.7).
4. **Click counting on the redirect path** (§11.6).
5. **Check-then-insert for aliases** (§11.5).
6. **No abuse pipeline**, or a pipeline with no retro-scan — destinations are sold and hijacked after creation (§4.8).
7. **Caching an interstitial or a 410** so the user is stuck.
8. **Single domain** with no reputation fallback (§9 step 5).
9. **Sharding the database** before measuring that the origin sees ~4 reads/second (§9 step 1).
10. **No invalidation on disable**, only on create (§11.8).
11. **Ignoring the CDN/origin header interaction** and assuming the edge is caching when it is not (§11.9).
12. **Key length chosen without the brute-force and collision arithmetic** (§9 step 2).
13. **Trusting `Referer`** as an analytics source without noting its ~30–40 % missing rate and its spoofability.
14. **No expiry policy for anonymous links**, leaving an unbounded abuse surface forever.

## 16. Interview perspective

**What is actually being tested.** Whether you see that the write path is trivial and the **read path belongs at the edge**, that key allocation is the one real distributed decision, and that the redirect status code is a **product decision with irreversible consequences**. Strong candidates quantify the origin load chain and choose 302 with an edge cache. Weak candidates design a sharded database for 1.16 writes/s and choose 301 "because it's faster".

**Answers that land well:**

- *"How do you generate the short code?"* — "Random seven characters from base62, allocated from a pre-minted pool: the pool means no coordination on the request path and no collision retries, and randomness means unguessable. A counter would be shorter and collision-free, but it is enumerable — a crawler reading 1,000 links a second would walk the whole table."
- *"301 or 302?"* — "Three-oh-two by default, and I cache the response at the edge for 60 seconds. A 301 gets cached by the browser forever: we measured 61 % of clicks invisible, and the destination can never be changed — which matters because we re-target 3,100 campaign links a month. The edge cache means a viral spike never reaches the origin, so there is no performance cost to being measurably correct."
- *"What's your bottleneck?"* — "The edge, not the database. At 856 redirects a second, a 98 % edge hit rate and a 96 % local-LRU hit rate leave about four Postgres reads per second — the database is over-provisioned by orders of magnitude. The real risks are domain blocklisting and abuse, which is why the abuse gate is on the write path and a second domain is kept warm."
- *"How do you count clicks?"* — "Never on the redirect path. A ring buffer flushed every 100 milliseconds into a partitioned Kafka topic, aggregated into a columnar store, with 60-second freshness. A synchronous `UPDATE` on the link row turned one viral slug into a write hotspot and pushed p99 from 22 to 210 milliseconds."

**Common interview tasks:** estimate writes/reads and the peak factor; choose an ID scheme with arithmetic; decide 301 vs 302 and justify; design the cache hierarchy and invalidation; handle custom aliases; design abuse mitigation; size the analytics pipeline.

**Red flags interviewers watch for:**

- "Shard the database" for a workload with single-digit writes per second.
- `301` with no mention of analytics or changeability.
- Sequential IDs with no enumeration discussion.
- Slug reuse, or a hard `DELETE` of link rows.
- Click counting inline on the redirect.
- No abuse story at all.
- Unknown peak factor, or designing for the average.
- No numbers: no read:write ratio, no hit ratios, no keyspace.

## 17. Knowledge check

**Conceptual**

1. Explain why the write path of a URL shortener is trivial and the read path is the entire design. Give the tier-by-tier origin-load chain and the number of database reads per second it produces at peak.
2. Compare `301` and `302` on cacheability, measurability, changeability and origin load, and explain the per-link decision rule. Then explain how a 60-second edge cache of a 302 resolves the apparent contradiction.
3. Compare the five key-generation strategies on coordination cost, key length, guessability, dedupe and collision handling, and state which you would choose for a public shortener with unlisted links, and why.

**Practical scenario**

4. Design BookNook's shortener: 100,000 creates/day, 14 M redirects/day, a 22× peak, 6.1 % custom aliases, 3,100 destination changes/month, a 40 ms synchronous abuse budget, and a requirement that a blocked link fail within seconds everywhere. Give the requirements and capacity tables, the API, the schema (with tombstones), the key-allocation design with arithmetic, the read path with all cache tiers and their hit ratios, the redirect code policy, the abuse pipeline, the analytics pipeline, the failure table, and the cost. State what you would build differently if 60 % of links needed to be 301s.

**Calculation**

5. 100,000 creates/day for 10 years. (a) Compute total slugs. (b) Compute the keyspace for 6, 7 and 8 base62 characters and the utilisation for each. (c) Compute the expected number of random-key collisions with `n²/2K` for 7 characters. (d) Compute the expected time for an attacker at 1,000 guesses/s to find one specific slug, and state whether 7 characters is adequate for an unlisted link.

6. A peak of 856 redirects/s, an edge hit ratio of 98 %, a local-LRU hit ratio of 96 % of origin requests, and a Redis hit ratio of 99.4 % of what remains. (a) Compute the requests reaching Redis and Postgres per second. (b) Compute the same figures for a 90 % edge hit ratio and state what that would imply for capacity planning. (c) Compute the daily click-event volume at 14 M redirects/day and the size at 120 B per event. (d) Compute the number of events in one 60-second analytics flush at peak.

**Progress checklist**

- Phase 1 — Foundations (L1–17): **complete** ✅
- Phase 2 — Scaling (L18–39): **complete** ✅
- Phase 3 — Data and Storage (L40–63): **complete** ✅
- Phase 4 — Distributed Systems (L64–92): **complete** ✅
- Phase 5 — Complete system designs (L93–106):
  - [x] 1. Design Pastebin (L93)
  - [x] **2. Design a URL shortener (L94) ← you are here**
  - [ ] 3. Design a rate limiter (L95)
  - [ ] 4. Design a notification service (L96)
  - [ ] 5. Design search autocomplete (L97)
  - [ ] 6. Design file storage (L98)
  - [ ] 7. Design a web crawler (L99)
  - [ ] 8. Design real-time chat (L100)
  - [ ] 9. Design a news feed (L101)
  - [ ] 10. Design video streaming (L102)
  - [ ] 11. Design ride sharing (L103)
  - [ ] 12. Design e-commerce and inventory (L104)
  - [ ] 13. Design payment processing (L105)
  - [ ] 14. Design a metrics and logging platform (L106)

**NEXT LESSON: Phase 5, Topic 3 — Design a rate limiter**

---

---

# Lesson 95 — Design a Rate Limiter

*Phase 5, Topic 3*

## 1. Simple definition

**The problem.** Limit how many requests a client may make in a window: 60 per minute per IP for anonymous users, 6,000 per minute for a partner, 10 per minute per user for checkout. Exceed it and the service returns `429 Too Many Requests` with a `Retry-After`.

**Why it is a *design* problem and not a library.** A single-process limiter is a counter. A *distributed* limiter must make a decision that is (a) **accurate** enough not to be gamed, (b) **fast** enough (< 1 ms) not to become the bottleneck, (c) **available** enough not to take the service down when the limiter itself fails, and (d) **fair** across keys that may number in the millions. Those four pull against each other, and the resolution is the lesson.

Terms used in this lesson:

- **Limit / quota**: the allowed count (or rate) per key per window.
- **Key**: the dimension you limit on — IP, user, API key, tenant, endpoint, or a combination.
- **Window**: the period the limit applies to (second, minute, hour, day).
- **Burst**: how much a client may exceed the steady rate for a short period.
- **Fixed window**: reset the counter at a boundary.
- **Sliding log**: keep timestamps of each request.
- **Sliding window counter**: interpolate between two fixed windows.
- **Token bucket**: a bucket of `capacity` tokens that refills at `rate`/s; each request takes one.
- **Leaky bucket**: a queue drained at a constant rate — smooths output rather than admitting bursts.
- **Fail-open / fail-closed**: what happens when the limiter's own dependency is unavailable.

The one sentence to carry out of this lesson: **the algorithm is the easy part — the design decision is whether the limiter is exact (and therefore a shared, latency-bound service) or approximate (and therefore a local token bucket with periodic reconciliation), and that choice is decided by what abuse costs you versus what a false rejection costs you.**

## 2. Why it exists

**Because without it, one client can consume the service.** The classic cases, all real: a buggy mobile client retrying in a tight loop (L65 §4.3: retries are amplification), a scraper, a compromised API key, a partner's backfill job, and a retry storm that turns a small outage into a large one. A rate limiter is the difference between "one customer had a bad afternoon" and "everybody did".

**Because it is the cheapest protection you can buy.** BookNook's arithmetic: 6 API pods at £70/month each absorb a sustained 4,800 rps; a partner's misconfigured backfill generated **11,000 rps** for 40 minutes and saturated the database before anyone noticed. A limiter at the gateway costs **£0 of extra capacity** and would have capped the incident at 6,000 rps.

**Because it enables tiering and cost recovery.** Anonymous 60/min, free 600/min, partner 6,000/min — the limit *is* the commercial boundary, and enforcing it in code makes the pricing page true.

**Because it protects the costliest paths specifically.** A checkout call touches inventory, payment and the saga orchestrator; ten per minute per user is generous. A catalogue read is cheap; 600/minute is plenty. **Per-endpoint limits inside a global per-tenant limit** is how cost is actually controlled.

**And because it is where fairness and abuse meet.** Limiting by IP is unfair (a school behind one NAT) and evadable (a botnet). Limiting by user is fair and evadable (a thousand free accounts). Limiting by payment instrument is effective and hostile to families. Every choice has a victim, and the design must say who.

## 3. Real-world analogy

**A car park with a barrier.**

Cars enter through a barrier that admits at most `N` per hour. That is a **fixed window**, and it has a famous defect: at 09:59 you admit `N`, the counter resets at 10:00, and you admit `N` again — **2N cars in two minutes**. The car park is full and the road outside is gridlocked. This is real, and it is why fixed windows alone are not enough (§4.2).

The **token bucket** is the same car park with a different rule: the barrier holds a stack of `capacity` tickets and gains one new ticket every `1/rate` seconds, up to the stack's capacity. Arrivals grab a ticket if one is available; if the stack is empty, they wait outside. Now:

- **A burst is allowed** — up to `capacity` cars arrive at once and all get in, because the tickets were already minted. That is exactly what you want for a legitimate client that has been quiet for ten minutes and then makes ten calls together.
- **The long-run rate is bounded** — you cannot exceed `rate` cars per second on average, because that is how fast tickets appear.
- **No boundary defect** — there is no reset moment to exploit; the state is continuous.

The **leaky bucket** is the car park with a queue: cars are held outside and admitted at exactly one per `1/rate` seconds, no matter how they arrive. Output is **perfectly smooth** — useful when the road (the downstream) cannot tolerate bursts at all. The cost is **latency**: the queue outside is a waiting room, and a waiting room that grows is an outage with a delay (L76 §4.2).

And the analogy's most important point is not about cars at all: **the barrier must work when the ticket office is closed.** If the central ticket office (Redis) is unreachable, does the barrier open (fail-open: unprotected, but the road keeps moving) or close (fail-closed: everyone is blocked because a *limiter* broke)? For a public read API, fail-open. For a payment endpoint under attack, fail-closed. **That is a product decision, and it must be made before the incident, not during it** (§4.7).

## 4. Detailed technical explanation

### 4.1 Requirements

**Functional.**

| # | Requirement |
|---|---|
| F1 | Limit by key: IP, user id, API key, tenant, and combinations |
| F2 | Per-key **and** per-endpoint limits (a global cap plus a route cap) |
| F3 | Different limits per tier (anonymous/free/partner), loaded from config |
| F4 | Return `429` with `Retry-After` and `X-RateLimit-*` headers |
| F5 | Support both a steady rate and a burst allowance |
| F6 | Exemptions for internal traffic and allow-listed partners |
| F7 | Operators can change a limit for one key in under 60 s |

**Non-functional.**

| # | Requirement | Number |
|---|---|---|
| N1 | Decision latency | p99 < 1 ms added to the request |
| N2 | Availability | Must not reduce the API's availability (99.99 %) |
| N3 | Accuracy | Overshoot ≤ 1 % of the limit; no unbounded under-admission |
| N4 | Scale | 6,000 rps at peak, 2 M distinct keys/day, 40 endpoints |
| N5 | Memory | ≤ 8 GB of Redis for the whole limiting state |
| N6 | Fail mode | Per-route: fail-open for reads, fail-closed for payments |
| N7 | Config propagation | A limit change applies within 60 s |

### 4.2 The five algorithms

**Fixed window.** `INCR key:minute; EXPIRE 60`.

```
  + one counter per key per window; ONE Redis round trip; trivially correct
  − the boundary burst: at 10:59:59 a client sends 60, and at 11:00:00 sends
    60 more ⇒ 120 requests in 2 seconds against a 60/minute limit = 2× the
    allowed rate. A determined client can double their effective limit every
    window, forever.
  BookNook measured it: a scraper using the boundary pattern achieved
  11,900 requests/hour against a 6,000/hour limit — a 1.98× overshoot.
```

**Sliding log.** Store a timestamp per request in a sorted set; count entries in `(now − window, now]`.

```
  + exact: no boundary defect, no approximation
  − memory O(requests) per key: 6,000 rpm per partner × 100 partners =
    600,000 entries ≈ 48 MB just for partners, and a top-IP key at
    6,000/min = 14 KB per key
  − cost per decision grows with the limit (ZREMRANGEBYSCORE + ZCARD)
  ⇒ acceptable for low-limit expensive endpoints (10/min checkout), wrong
    for high-limit cheap ones.
```

**Sliding window counter (weighted).** Two fixed-window counters, interpolated:

```
  estimated = prev_count × (1 − elapsed_fraction) + curr_count
  + O(1) memory, O(1) time, no boundary defect beyond a small error
  − assumes the previous window's requests were uniformly distributed; the
    error is bounded by the previous window's count
  error measured by Cloudflare (public engineering write-up): with a
  100 : 1 ratio between adjacent windows, the approximation erred by
  0.003 % of the true count at 400,000 rps — i.e. negligible in practice,
  and worst-case ~1 key in 10,000 sees a small discrepancy.
  ⇒ THE PRACTICAL DEFAULT for high-volume public limits.
```

**Token bucket.** State is `(tokens, last_refill)`; refill lazily on read:

```
  tokens = min(capacity, tokens + (now − last_refill) × rate)
  if tokens ≥ 1: tokens -= 1; allow
  else: reject with Retry-After = (1 − tokens) / rate

  + admits bursts up to `capacity` and enforces `rate` in the long run
  + two numbers of state; no timer, no background job
  + the arithmetic is exact and cheap (§8)
  − a client that replenishes during idle time accumulates a full bucket, so
    a sudden burst of `capacity` requests always passes. If `capacity` is set
    to the full per-minute limit, that burst can hit a downstream that only
    tolerates a small one.
  ⇒ set capacity to the BURST you can absorb, not to the window limit.
```

**Leaky bucket / GCRA (generic cell rate algorithm).** Track a single "theoretical arrival time" (`TAT`):

```
  TAT = max(now, TAT) + 1/rate            # each admitted request schedules the
  allow if TAT − now ≤ burst_tolerance    # next; reject if too far ahead
  + one number of state (even smaller than a token bucket), smooths output
  + GCRA is the standard implementation (and the basis of many API gateways)
  − output is smoothed, which adds latency if you queue rather than reject
```

| Algorithm | State per key | Boundary defect | Burst | Cost per decision | Use for |
|---|---|---|---|---|---|
| Fixed window | 1 int | **2×** | at the boundary only | 1 round trip | coarse quotas (daily) |
| Sliding log | O(n) | none | none | O(log n) | low limits, high stakes |
| Sliding window counter | 2 ints | ~0.003 % | none | 1 round trip | public per-IP limits |
| **Token bucket** | 2 floats | none | **up to capacity** | 1 round trip | **the default** |
| Leaky bucket / GCRA | 1 float | none | none (smoothed) | 1 round trip | protecting fragile downstreams |

### 4.3 Where to enforce: the layering that matters

```
  LAYER 1  edge / CDN          coarse per-IP limits, bot mitigation.
                               Cheapest (no origin contact), weakest accuracy
                               (shared NATs, IPv6 rotation).
  LAYER 2  API gateway         per-API-key / per-tenant plans, 6,000 rpm.
                               THE main enforcement point: one place, one
                               config, one audit trail. (L87 §4.2)
  LAYER 3  service / route     per-endpoint and per-user limits, e.g. checkout
                               10/min/user. Enforced close to the cost.
  LAYER 4  dependency client   e.g. 200 calls/s to the payment provider,
                               shared across all callers (a BULKHEAD, L77).
  ⇒ the rule: ENFORCE WHERE THE COST IS INCURRED, and enforce coarsely where
    the volume is. A single global limiter at the gateway cannot protect one
    expensive endpoint; per-endpoint limiters cannot see a tenant's total.
    You need both, and they must not double-count the same request twice in a
    way that halves a customer's allowance.
```

### 4.4 Distributed state: three topologies

**A — Central (Redis) with atomic script.**

```
  every request: EVALSHA(token_bucket_lua, 1, key, rate, capacity, now)
  + exact across all instances; one place to inspect and change
  − 1 network round trip per request (~0.3–0.6 ms in-AZ) and Redis becomes a
    hard dependency of every request
  capacity: BookNook measured a single Redis node at 120,000 EVALSHA/s with
  a 0.4 ms p50; at 6,000 rps that is 5 % of one node.
  ⇒ correct and cheap at this scale; the issue is availability, not throughput.
```

**B — Local token bucket with periodic global reconciliation.**

```
  each pod keeps a local bucket per key, refilled from its own clock, and
  every T seconds it adds its consumption to Redis and takes an adjusted rate
  + no per-request network hop (0.02 ms), survives Redis being down
  − approximate: with P pods, a client can consume up to P × burst in the
    worst case between reconciliations. With 12 pods and a 10 s window, a
    6,000/min limit could see a 1,200-request overshoot — 20 %.
  ⇒ the accuracy is fixed by the reconciliation interval and the pod count,
    so the design is: overshoot_target → interval → pods.
```

**C — Hybrid: local for the common case, central for the exceptional.**

```
  every request: local bucket (cheap). Every Nth request, or whenever the
  local bucket crosses a threshold, one central check to re-sync.
  + near-exact with ~1/N of the network cost
  − two sources of truth; harder to reason about and to test
  ⇒ BookNook's choice: LOCAL primary + CENTRAL authoritative check on
    EVERY request for 3 high-risk routes only (checkout, login, payment),
    and local-only elsewhere. 6,000 rps total, of which 180 rps take the
    central path. That is 3 % network cost and exactness where it matters
    (§9 step 2).
```

### 4.5 Keying: the part with victims

| Key | Fair? | Evadable? | Problems |
|---|---|---|---|
| **IP** | No — a school, an office or a mobile carrier NAT shares one | Trivially (botnets, IPv6 rotation: a single /64 gives 2⁶⁴ addresses) | Blocks legitimate shared egress; punishes whole universities |
| **User id** | Yes | Yes — free accounts are cheap | A single user behind 1,000 accounts |
| **API key** | Yes, per contract | Only by stealing one | The right key for partners and paid tiers |
| **Account + IP** | Better | Harder | Two-dimensional state: 2 M keys × IPs |
| **Device / cookie** | Moderately | Yes, by clearing it | Privacy implications (GDPR: is it personal data?) |
| **Payment instrument** | Very effective | Only by having many cards | Hostile to households; requires the payment provider's cooperation |

```
  ⇒ BookNook's keying rule, by route class:
      anonymous read      ip + /64 (so an IPv6 /64 counts as one client —
                          otherwise a single botnet /64 gets 2^64 buckets)
      authenticated read  user_id
      authenticated write user_id AND ip      (two limits, both must pass)
      partner API         api_key (contractual), plus tenant total
      checkout            user_id, sliding log, 10/min, fail-closed
  ⇒ AND: a "reputation multiplier" — a key with a 30-day clean history gets
    2× its limit; a key that has tripped limits 5 times in a day gets 0.5×.
    This is cheap to implement and it converts a static limit into an
    adaptive one, which is what actually stops scrapers.
```

### 4.6 The response contract

```
  HTTP/1.1 429 Too Many Requests
  Retry-After: 12                       # seconds, or an HTTP-date
  X-RateLimit-Limit: 600
  X-RateLimit-Remaining: 0
  X-RateLimit-Reset: 1730217600          # epoch seconds
  X-RateLimit-Policy: 600;w=60

  body: { "error": "rate_limited", "retry_after_ms": 12000,
          "limit": 600, "window": 60 }

  ⇒ three rules that are commonly broken:
    (1) `Retry-After` must reflect the ACTUAL time until a token is available,
        not a fixed window length. Clients that honour it recover 3× faster
        in BookNook's measurement (p99 recovery after a limit from 41 s
        to 13 s).
    (2) The headers must be returned on SUCCESS responses too, or a well-
        behaved client cannot pace itself and will discover the limit by
        failing.
    (3) Never return 429 for a request you already processed. Check before
        the work, and make the rejection cheap (no database access on the
        reject path, or you have built a denial-of-service amplifier).
```

### 4.7 The fail mode, decided in advance

```
  If the limiter's store is unavailable, there are exactly two behaviours:

  FAIL-OPEN   allow the request, log it, decrement nothing
      · protects availability; loses protection exactly when you are being
        attacked (an attacker who can break Redis wins)
      · correct for: public reads, low-risk endpoints, anything where a
        false rejection is worse than an unbounded client
  FAIL-CLOSED reject everything not otherwise authorised
      · protects the dependency; turns a limiter outage into a full outage
      · correct for: payment authorisation, login, anything where an
        uncontrolled client can cause a financial loss

  ⇒ AND a third state is usually best: FALLBACK TO A LOCAL BUCKET when the
    central store is unreachable. The local bucket is approximate (topology B)
    but it is bounded, available, and it degrades accuracy rather than
    availability or safety. BookNook uses:
        reads       → local fallback, per-pod limit = global limit / pods
        checkout    → fail-closed (10/min is generous; a false rejection costs
                      one customer, an uncontrolled charge costs money)
        everything else → local fallback
  ⚠ AND: alert on fallback ACTIVATION, not just on 429 volume. A limiter
    silently running in fallback for 6 weeks is how a 20 % overshoot becomes
    permanent and nobody knows.
```

### 4.8 Config, and where the numbers come from

A limit is a **product parameter**, not a constant:

```
  · limits live in a config service with per-tier, per-route values, and are
    hot-reloaded (L87 §4.7) within 60 s
  · every route declares its limit in code (so the limit is reviewed with the
    code) and validates against a central registry (so two routes cannot
    accidentally share a bucket)
  · limits are derived from capacity, not invented:
        safe_limit = downstream_capacity × safety_margin / expected_callers
    e.g. checkout writes: 1,800 saga starts/s of capacity, 40 % of registered
    users active in a flash sale ⇒ a per-user limit that keeps the total
    inside capacity (§9 step 3)
```

## 5. Request / data flow

```
 A single request through four layers, with the decision cost of each.
 ─────────────────────────────────────────────────────────────────────────
 1. edge        IP-based coarse check, token bucket in edge KV
                key: ip/64 · limit 1,200/min · local to the PoP
                (the edge cannot see other PoPs' counts; the PoP's share is
                 the global limit / PoP count, with a safety factor)
                                                              [0.15 ms]
 2. gateway     API key → tenant plan lookup (cached, 60 s TTL)
                token bucket in Redis, EVALSHA, atomic:
                  tokens = min(cap, tokens + (now−last)×rate)
                  allow if tokens ≥ 1
                key: tenant:{id}:minute · 6,000/min · capacity 300
                                                              [0.45 ms]
 3. route       if route ∈ {checkout, login, payment}:
                  sliding-log check in Redis (exact, low limit)
                  key: user:{id}:checkout · 10/min
                else: local token bucket (in-process, per pod)
                                                              [0.02–0.6 ms]
 4. dependency  payment client has a shared 200/s bulkhead (L77): a semaphore
                + a token bucket, so no caller can starve the others
                                                              [0.01 ms]

 REJECT PATH — must be cheaper than the accept path, or the limiter is a
 ─────────────────────────────────────────────────────────────────────────
     return 429 immediately, BEFORE any database access, with Retry-After
     computed from the bucket. Measured: a reject costs 0.02 ms of CPU and no
     I/O. A reject path that queries the database is a DoS amplifier: 50,000
     rps of rejected requests would still cost 50,000 queries.

 ON SUCCESS — always emit the headers, and always count.
```

## 6. ASCII diagram

```
                         ┌─────────────────────────────────────┐
   client ──────────────►│ EDGE: per-IP/64 token bucket        │ 1,200/min
                         │ (edge KV, per-PoP share)            │
                         └──────────────┬──────────────────────┘
                                        │ allow
                         ┌──────────────▼──────────────────────┐
                         │ GATEWAY: per-tenant token bucket    │ 6,000/min
                         │ Redis EVALSHA (atomic, 1 RTT)       │ ← authoritative
                         └──────────────┬──────────────────────┘
                                        │ allow
              ┌─────────────────────────┼──────────────────────────┐
              ▼ (high-risk routes)      ▼ (everything else)        │
   ┌────────────────────────┐  ┌────────────────────────┐          │
   │ SLIDING LOG (Redis)    │  │ LOCAL token bucket     │          │
   │ checkout 10/min        │  │ in-process, per pod    │          │
   │ login    5/min         │  │ refilled from clock    │          │
   │ payment  3/min         │  │ + periodic reconcile   │          │
   │ fail-CLOSED            │  │ fail-OPEN (local)      │          │
   └───────────┬────────────┘  └───────────┬────────────┘          │
               └───────────┬───────────────┘                       │
                           ▼                                       │
                 ┌──────────────────────┐                          │
                 │ service / handler    │◄─────────────────────────┘
                 └──────────┬───────────┘
                            ▼
                 ┌──────────────────────┐
                 │ DEPENDENCY BULKHEAD  │  200/s to the payment provider,
                 │ (shared token bucket)│  shared by all callers
                 └──────────────────────┘

  THE FAIL-MODE DECISION, drawn:
        Redis unreachable?
           ├── read route   → local bucket (degraded accuracy, bounded)
           ├── checkout     → 503 (fail closed, on purpose)
           └── everything   → local bucket
        ⚠ alert on "fallback active > 5 min", not only on 429 rate.

  THE BOUNDARY DEFECT, drawn (why fixed windows are not enough):
        limit 60/min
        10:59:00 ──────────────────────────── 60 requests ─▶ allowed
        11:00:00 ──────────────────────────── 60 requests ─▶ allowed
                  ▲ two windows, 120 requests in ~2 s = 2× the limit ✗
        token bucket: no boundary, no defect — burst ≤ capacity, rate ≤ 1/s
```

## 7. Detailed example

**BookNook's API tiers and the incident that forced the design.**

```
  tiers (from the pricing page, implemented in config):
      anonymous      60/min per ip/64,      burst 20
      free user      600/min per user_id,   burst 60
      partner        6,000/min per api_key, burst 300
      internal       exempt, but capped by a 20,000/min pool
  per-route limits inside the tier:
      GET /v1/books/*        600/min free, 6,000/min partner
      POST /v1/cart          60/min
      POST /v1/checkout      10/min, sliding log, fail-closed
      POST /v1/auth/login    5/min per (user OR ip), fail-closed
      POST /v1/payments/*    3/min, fail-closed, plus the 200/s bulkhead
```

**The incident: 40 minutes of self-inflicted overload.**

```
  2025-11-14, a partner's backfill job was misconfigured to retry every
  request 5 times on timeout (L65 §4.3):
    14:02  backfill begins at 11,000 rps against a 6,000/min contract
    14:04  gateway p99 40 ms → 2.1 s; database CPU 96 %
    14:06  checkout error rate 4 % → 71 %; the database connection pool is
           exhausted by backfill queries (L77 §11.3)
    14:09  on-call paged by the checkout SLO burn alert (L89)
    14:22  the partner is contacted; they do not answer
    14:31  the partner's api_key is disabled at the gateway by hand
    14:41  the database recovers; backlog drains over 22 minutes
  ⇒ total: 45 minutes of degraded checkout, 31,000 failed orders, £147,000 of
    lost contribution, ~9 % of the monthly error budget consumed against the
    99.5 % SLO (L89 §9: 45 minutes of *full* outage would be ~22 %, so a
    partial outage scoring 9 % is consistent), and one very uncomfortable
    partner call.
  ⇒ THE FIX was not capacity. It was a limiter:
      · per-tenant limit at the gateway, hard-enforced (6,000/min), with
        a 429 and Retry-After — the partner's client honours it
      · per-route limits so the backfill's reads cannot starve checkout's
        writes (separate buckets, separate pools — L77 §4.2 bulkheads)
      · a database pool reserved for checkout (30 of 200 connections)
      · an alert on "any tenant > 2× its limit for 60 s"
  replayed in staging with the same misconfiguration: 0 failed checkouts;
    the partner saw 429s and backed off; the database peaked at 61 % CPU.
```

**The measurement that chose the algorithm:**

```
  BookNook prototyped three limiters at 6,000 rps:
      fixed window   p50 0.31 ms, p99 0.9 ms  — but a scraper achieved a
                     1.98× overshoot using the boundary pattern
      sliding log    p50 0.52 ms, p99 1.4 ms  — exact, but Redis memory for
                     partner keys was 48 MB and grew with the limit
      token bucket   p50 0.29 ms, p99 0.8 ms  — no boundary defect, 2 numbers
                     of state, admits a deliberate burst
  ⇒ token bucket everywhere, sliding log only where the limit is ≤ 20/min
    and the stakes are highest (checkout, login, payment).
```

## 8. Code or configuration example

```lua
-- ── Token bucket in Redis: atomic, one round trip, no clock assumptions ──
-- KEYS[1] bucket key   ARGV[1] rate/s   ARGV[2] capacity   ARGV[3] now (µs)
-- Returns {allowed(0|1), remaining, retry_after_ms}
local key      = KEYS[1]
local rate     = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local now      = tonumber(ARGV[3])

local b = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(b[1])
local ts     = tonumber(b[2])
if tokens == nil then tokens, ts = capacity, now end

-- lazy refill: no timer, no background job (see §4.2)
local elapsed_ms = math.max(0, (now - ts) / 1000)
tokens = math.min(capacity, tokens + elapsed_ms * rate / 1000)

local allowed, retry_after_ms = 0, 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  retry_after_ms = math.ceil((1 - tokens) / rate * 1000)
end

redis.call('HSET', key, 'tokens', tokens, 'ts', now)
-- expire well after the bucket would have refilled anyway (bounded memory)
redis.call('PEXPIRE', key, math.ceil(capacity / rate * 1000) + 60000)
return { allowed, math.floor(tokens), retry_after_ms }
```

```python
# ── The middleware: layered limits, fallback, and headers on success ─────
class RateLimitLayer:
    def __init__(self, cfg):
        self.cfg = cfg                    # hot-reloaded, 60 s (L87 §4.7)
        self.local = LocalBuckets()       # in-process, always available

    async def check(self, req) -> Decision:
        route = req.route
        plan  = self.cfg.plan_for(req.api_key)      # anon/free/partner
        rules = self.cfg.rules(plan, route)         # [(key, rate, capacity)]

        for key, rate, capacity in rules:           # e.g. user AND ip
            d = await self._decide(key, rate, capacity, route.fail_mode)
            if not d.allowed:
                return d                            # first failing rule wins
        return Decision.allow(remaining=...)

    async def _decide(self, key, rate, capacity, fail_mode):
        try:
            return await redis_bucket(key, rate, capacity)      # authoritative
        except RedisUnavailable:
            metrics.increment("ratelimit.fallback", {"route": key.prefix})
            if fail_mode is FailMode.CLOSED:
                # checkout / login / payment: refuse rather than risk it
                return Decision.reject(503, retry_after=1)
            # reads: degrade to a per-pod share of the global limit
            return self.local.check(key, rate / PODS, max(1, capacity // PODS))

# Always emit headers, on success AND failure (§4.6 rule 2)
def with_headers(resp, d):
    resp.headers["X-RateLimit-Limit"]     = str(d.limit)
    resp.headers["X-RateLimit-Remaining"] = str(d.remaining)
    resp.headers["X-RateLimit-Reset"]     = str(d.reset_epoch)
    if not d.allowed:
        resp.status = 429
        resp.headers["Retry-After"] = str(max(1, d.retry_after_ms // 1000))
    return resp
```

```python
# ── Local token bucket: no I/O, survives Redis, used for fallback ────────
class LocalBuckets:
    """Per-process buckets; refill is lazy and monotonic-clock based."""
    def __init__(self, max_entries=200_000):
        self.b = OrderedDict()          # LRU: bound memory, evict quiet keys
        self.max = max_entries

    def check(self, key, rate, capacity):
        now = time.monotonic()          # NEVER wall-clock (L66 §4.1)
        ent = self.b.get(key)
        if ent is None:
            if len(self.b) >= self.max:
                self.b.popitem(last=False)
            ent = self.b[key] = [float(capacity), now]
        else:
            self.b.move_to_end(key)
        tokens, ts = ent
        tokens = min(capacity, tokens + (now - ts) * rate)
        ent[1] = now
        if tokens >= 1:
            ent[0] = tokens - 1
            return Decision.allow(remaining=int(tokens - 1))
        ent[0] = tokens
        return Decision.reject(429, retry_after_ms=int((1 - tokens) / rate * 1000))
```

```yaml
# ── Limits as reviewed configuration, validated in CI ────────────────────
# Every route must declare a limit here; a route missing from this file fails
# the build, so "we forgot to limit it" cannot happen.
tiers:
  anonymous: { key: "ip64", rate: 60,  capacity: 20 }
  free:      { key: "user", rate: 600, capacity: 60 }
  partner:   { key: "api_key", rate: 6000, capacity: 300 }

routes:
  GET  /v1/books/*:      { free: 600,  partner: 6000, fail: open }
  POST /v1/cart:         { free: 60,   partner: 600,  fail: open }
  POST /v1/checkout:     { free: 10,   partner: 10,   fail: closed, algo: sliding_log }
  POST /v1/auth/login:   { anon: 5,    fail: closed,  algo: sliding_log, key: "user|ip" }
  POST /v1/payments/*:   { free: 3,    fail: closed,  algo: sliding_log }

alerts:
  - alert: TenantExceedsLimit
    expr: rate(ratelimit_rejected_total[60s]) by (tenant) > 0.5
          and ratelimit_rejected_total / ratelimit_total > 0.3
    for: 60s
    severity: page        # a tenant at > 30 % rejections needs a human
  - alert: RateLimiterFallbackActive
    expr: rate(ratelimit_fallback_total[5m]) > 0
    for: 5m
    severity: page        # accuracy is degraded; nobody would notice otherwise
```

## 9. Numerical example

BookNook: 6,000 rps peak, 2 M distinct keys/day, 12 API pods, 40 routes.

**Step 1 — is Redis even needed?**

```
  decisions/s at peak                                 = 6,000
  Redis EVALSHA cost measured                          = 0.4 ms p50
  Redis capacity measured (one node, single-threaded)  = 120,000 ops/s
  ⇒ 6,000 / 120,000 = 5 % of one node. Network cost is 0.4 ms added to a
    40 ms request = 1 %.
  ⇒ CENTRAL LIMITING IS AFFORDABLE AT THIS SCALE. The reason to go hybrid is
    availability and blast radius, not throughput. (At 500,000 rps the
    numbers change: 500,000 / 120,000 = 4.2 nodes, and the round trip becomes
    a design constraint — hence Cloudflare's local-plus-sync approach.)
```

**Step 2 — the hybrid's accuracy, and why it was chosen.**

```
  LOCAL PRIMARY + CENTRAL FOR 3 ROUTES:
      traffic split: checkout 0.7 %, login 1.4 %, payment 0.9 % = 3 %
      central decisions/s = 6,000 × 0.03 = 180
      local decisions/s   = 6,000 × 0.97 = 5,820 at 0.02 ms CPU each
  overshoot analysis for the local routes:
      reconciliation interval 10 s; 12 pods; per-pod limit = 600/60 = 10/s
      worst case: a client spread across all pods consumes
          12 × (pod limit × interval + capacity) = 12 × (10×10 + 20) = 1,440
          in the 10 s before reconciliation vs a 100 allowed
      ⇒ 14× overshoot in the theoretical worst case — WHICH IS WHY the
        per-pod limit is set to global/pods and the reconciliation ADDS a
        correction rather than trusting the local count:
          corrected_rate = max(0, (global_rate − Σ observed)/pods)
      measured overshoot with correction: 6 % at p99, 1.2 % at p50.
  ⇒ is 6 % acceptable? For a 600/min read limit, yes (36 requests). For a
    payment authorisation, NO — hence the central, exact, fail-closed path
    for the three routes that cost money.
```

**Step 3 — deriving a limit from capacity, not intuition.**

```
  checkout capacity: 1,800 saga starts/s before the database saturates
  flash-sale active users: 45,000 registered users hitting refresh
  ⇒ a per-user limit that keeps the total inside capacity:
        aggregate allowed = 1,800/s × 0.6 safety margin = 1,080/s
        per-user share    = 1,080 / 45,000 = 0.024/s = 1.44/min
      ⇒ 10/min per user × 45,000 = 7,500/min = 125/s at any instant IF all
        users are active simultaneously and each uses their full allowance,
        which is 6.9 % of capacity. Fine.
      ⇒ the limit is safe by 14× even in the adversarial case. That is the
        arithmetic a limit needs; "10/min feels right" is not a design.
```

**Step 4 — memory for the limiting state.**

```
  keys/day 2,000,000; hot window: all keys touched in the last hour
      token bucket: 2 floats + key overhead ≈ 96 B per key in Redis
      plausible hot set = 400,000 keys (20 % of a day) ⇒ 38 MB
      sliding-log keys (checkout/login/payment users) = 210,000 users ×
          10 entries × 24 B (zset overhead) = 50 MB
      ⇒ TOTAL ≈ 90 MB. A 1 GB Redis instance has 11× headroom, and the TTL
        per bucket bounds growth automatically.
  ⇒ compare with the naive "keep every key for 24 h": 2,000,000 × 96 B =
    192 MB, still fine — but with a per-IP key on IPv6 /64 and a naive
    unaggregated key you would allocate 2^64 × 96 B. The /64 aggregation is
    what keeps the state finite.
```

**Step 5 — the cost of the incident versus the cost of the limiter.**

```
  the 45-minute incident (§7):
      failed orders 31,000 × £4.75 contribution ≈ £147,000
      error budget 9 % of the month, plus 2 engineer-days of response
  the limiter:
      engineering 3 weeks (~£11,000) + £0 of infrastructure (Redis already
      present) + 0.4 ms of added latency = 1 % of the request budget
  ⇒ payback on ONE incident, with a 13× margin. And the second-order effect
    is larger: the limiter makes the flash-sale load test meaningful, because
    without it the test only measures the worst-case client, not the system.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **Fixed window** | Simplest, cheapest | 2× boundary overshoot | Daily quotas, non-adversarial clients |
| **Sliding window counter** | No meaningful defect, O(1) state | Small approximation | Public per-IP limits at volume |
| **Token bucket** | Bursts + steady rate, 2 numbers of state | Burst must be sized to downstream tolerance | **The default** |
| **Leaky bucket / GCRA** | Perfectly smooth output | Adds latency if queued; less burst tolerance | Fragile downstreams |
| **Sliding log** | Exact | O(limit) memory per key | Low limits, high stakes |
| **Central Redis** | Exact, one place to change | 1 RTT; Redis becomes a hard dependency | Anything under ~100k rps |
| **Local only** | Fast, survives Redis | Approximate; overshoot scales with pod count | Cheap routes with generous limits |
| **Hybrid** | Exact where it matters, cheap everywhere | Two mechanisms to test | **The pragmatic answer at scale** |
| **Key by IP** | Trivial, works unauthenticated | Unfair to NATs; evadable by botnets/IPv6 | Anonymous tier, with /64 aggregation |
| **Key by user** | Fair, contractual | Evadable by account creation | Authenticated tiers |
| **Fail-open** | Availability | No protection during the attack that breaks your limiter | Reads |
| **Fail-closed** | Safety | A limiter outage becomes a full outage | Money paths |
| **Local fallback** | Bounded, available, degraded | Silent accuracy loss without an alert | Almost everywhere |

## 11. Failure scenarios

**11.1 — Redis unavailable.** Every request needs a decision and the decision needs Redis. **Fix:** local fallback per fail-mode (§4.7), and an alert on fallback activation — the silent-fallback failure is worse than the outage.

**11.2 — Fixed-window boundary exploitation.** Measured 1.98× overshoot (§4.2). **Fix:** token bucket or sliding window counter.

**11.3 — The one-NAT school.** 4,000 students behind one /29; the anonymous 60/min limit locked out a whole school for an afternoon. **Fix:** authenticated traffic is keyed by user, not IP; anonymous limits raised per-IP but backed by bot detection; and a support path to allow-list an egress range.

**11.4 — IPv6 bucket explosion.** A scraper rotated through addresses in a single /64, getting 2⁶⁴ fresh buckets — the limiter saw 2⁶⁴ distinct keys and never triggered. **Fix:** aggregate to the /64 (or /56) for v6 keys (§4.5).

**11.5 — Retry storm against a 429.** A client that retries immediately on 429 turns a limit into an amplifier (L65 §4.3). **Fix:** `Retry-After` with a truthful value, plus (server-side) counting a retry-after-429 as two tokens — a well-known technique that makes instant retries strictly worse than waiting.

**11.6 — The limiter's own latency.** A per-request Redis call at 6,000 rps added 0.4 ms; at 500,000 rps the same design needs 4.2 Redis nodes and a 1 ms round trip, which becomes the largest single component of request latency. **Fix:** hybrid (local primary, central for high-risk) before you get there.

**11.7 — Double counting across layers.** The gateway charged 1 token and the route charged another, so the published 600/min delivered 300/min. **Fix:** one token per *request* consumed at one layer; route-level limits are separate dimensions with separate names, and the docs state both.

**11.8 — Silence after a limit change.** A partner's limit was raised in config, and the local buckets kept the old rate for 40 minutes because the config push failed validation silently. **Fix:** config propagation as an SLO (60 s p99), with an alert when a limit's applied value differs from its desired value (L87 §4.7, the config-drift metric).

## 12. Alternatives

| Instead of | Consider | When |
|---|---|---|
| A rate limiter you build | Your API gateway's built-in limiter | It covers per-key limits and you do not need custom algorithms |
| Per-request limiting | Concurrency limiting (semaphores) | The resource is connections/threads rather than requests |
| Rejecting | Queueing with a bounded wait | The client can tolerate 50–200 ms of extra latency |
| Fixed limits | Adaptive limits from measured load | You have good load signals and highly variable traffic |
| Limiting by key | Load shedding by priority (L76) | You must serve *someone*; limits protect fairness, shedding protects survival |
| Rate limits | Quotas with a budget (monthly) | The cost dimension is total spend, not rate |

## 13. When to use it

- **Every public API**, at the gateway, per key and per tenant.
- **Every costly or dangerous route** (auth, checkout, payment, search) with its own limit and fail-closed behaviour.
- **In front of every external dependency**, as a shared bulkhead (L77), so no caller starves the others.
- **Where abuse has a cost** — scraping, enumeration, credential stuffing, storage filling.
- **Where the pricing page makes a promise** — the limit is the product boundary.

## 14. When *not* to use it

- **As a substitute for load shedding.** A rate limiter protects fairness per key; a shard-wide overload needs shedding by priority (L76 §4.3). BookNook's flash sale needed *both*.
- **With limits nobody derived** — a limit invented in a meeting will either block legitimate use or protect nothing (§4.8).
- **On internal service-to-service calls, carelessly** — a global per-key limit between services causes retry storms; use concurrency limits instead.
- **Fail-closed on a public read path** — you have made the limiter the availability ceiling.
- **Silently**, without `X-RateLimit-*` headers, so clients must discover limits by failing.

## 15. Common mistakes

1. **Fixed window only**, and a 2× boundary defect (§4.2).
2. **No `Retry-After`**, or a wrong one (§4.6).
3. **No rate-limit headers on success**, so clients cannot pace.
4. **Limiting by IP for authenticated traffic**, punishing shared egress (§4.5).
5. **Not aggregating IPv6** to /64 (§11.4).
6. **Fail-closed everywhere** (an outage of the limiter becomes an outage of the service) or **fail-open everywhere** (no protection when attacked).
7. **Silent fallback** with no alert (§11.1).
8. **Double counting** across layers, halving a customer's allowance (§11.7).
9. **A reject path that touches the database** — a DoS amplifier (§5).
10. **Retries not disincentivised**, so 429s amplify load (§11.5).
11. **Per-request limiting where concurrency is the real resource**.
12. **Limits that cannot be changed without a deploy** (§4.8).
13. **No allow-list for internal traffic**, so your own crons get 429s at 02:00.
14. **No config-drift alert**, so the applied limit diverges from the published one (§11.8).

## 16. Interview perspective

**What is actually being tested.** Whether you know the algorithms *and* the operational decisions: where to enforce, what to fail to, how to key, and how to derive the number. Strong candidates pick a token bucket for the general case, a sliding log for high-stakes low-limit routes, name the fail mode per route, and compute the overshoot of a local-only design. Weak candidates recite "token bucket vs leaky bucket" and stop.

**Answers that land well:**

- *"Which algorithm?"* — "Token bucket as the default: no boundary defect, two numbers of state, and it admits a deliberate burst, which is what legitimate clients need. A sliding log where the limit is under twenty a minute and the stakes are money — checkout, login, payment — because exactness matters more than memory there. And never a fixed window alone: a client can get two full allowances in two seconds at the boundary, which we measured as a 1.98× overshoot against a scraper."
- *"Where do you enforce?"* — "At the gateway for per-tenant contractual limits, per-route in the service for anything expensive, and as a shared bucket in front of the payment provider so no caller can starve the others. Enforce where the cost is incurred, and don't double-charge a request across layers — that silently halves the published limit."
- *"What if Redis dies?"* — "It's decided per route, not globally. Reads fall back to a local per-pod bucket: bounded and approximate, never unavailable. Checkout and payment fail closed — ten a minute is generous, and a false rejection costs one customer while an uncontrolled charge costs money. And the important part is alerting on fallback activation, because otherwise the limiter runs degraded for weeks and nobody notices."
- *"How do you choose the limit?"* — "From capacity, not intuition: checkout saturates at 1,800 saga starts a second; 45,000 users in a flash sale at ten a minute each is 125 a second, which is 7 % of capacity, so the limit is safe by 14 times even if everyone is adversarial."

**Common interview tasks:** pick an algorithm with justification; design the distributed state and its accuracy; choose the key dimension; choose the fail mode; derive a limit; design the response contract; size the memory; design the layered enforcement.

**Red flags interviewers watch for:**

- Fixed window with no awareness of the boundary defect.
- "Use Redis" with no fail-mode discussion.
- No `Retry-After` or rate-limit headers.
- Limiting only by IP, with no NAT/IPv6 consideration.
- Fail-closed by default everywhere.
- A limit with no derivation.
- No per-route limits, only a global one.
- No numbers: no rps, no overshoot, no memory.

## 17. Knowledge check

**Conceptual**

1. Explain the fixed-window boundary defect with a worked example, then explain how a token bucket and a sliding window counter each eliminate it. State which you would choose for a 600/min public read limit and why.
2. Compare central, local and hybrid distributed limiting on accuracy, latency, availability and cost. Give the formula that relates overshoot to the number of pods, the reconciliation interval and the per-pod limit.
3. Explain fail-open, fail-closed and local fallback, state the route classes each belongs to, and explain why an alert on *fallback activation* is more important than an alert on 429 volume.

**Practical scenario**

4. Design BookNook's rate limiter: 6,000 rps peak, 2 M keys/day, 40 routes, four tiers (anonymous/free/partner/internal), three money paths, a 12-pod deployment, and an API gateway that already supports fixed-window limits. Give the requirements and capacity tables, the algorithm per route class with justification, the keying scheme including IPv6 aggregation, the layering and where tokens are consumed, the fail-mode matrix, the response contract, the config and hot-reload design, the memory arithmetic, the alerts, and the replay of the 45-minute incident showing it would have been prevented. Then state what you would change at 500,000 rps.

**Calculation**

5. A limit is 600 requests/minute. (a) Compute a fixed window's worst-case requests in any 2-second span and the multiplier over the intended rate. (b) A token bucket has rate 10/s and capacity 20: compute the maximum requests in a 5-second burst after 60 seconds of idle, and the long-run rate. (c) Compute the state size in Redis for 400,000 token-bucket keys at 96 B each. (d) A sliding log at 10/min for 210,000 users: compute the entries held and the memory at 24 B per entry.

6. A local-only limiter runs on 12 pods with a per-pod limit of 10/s, a capacity of 20, and a 10-second reconciliation interval, against a global limit of 600/min. (a) Compute the theoretical worst-case overshoot before reconciliation. (b) Compute the overshoot percentage against the global limit. (c) Compute the per-pod limit that would bound the overshoot to 10 %. (d) State whether this design is acceptable for a payment endpoint and what you would do instead.

**Progress checklist**

- Phase 1 — Foundations (L1–17): **complete** ✅
- Phase 2 — Scaling (L18–39): **complete** ✅
- Phase 3 — Data and Storage (L40–63): **complete** ✅
- Phase 4 — Distributed Systems (L64–92): **complete** ✅
- Phase 5 — Complete system designs (L93–106):
  - [x] 1. Design Pastebin (L93)
  - [x] 2. Design a URL shortener (L94)
  - [x] **3. Design a rate limiter (L95) ← you are here**
  - [ ] 4. Design a notification service (L96)
  - [ ] 5. Design search autocomplete (L97)
  - [ ] 6. Design file storage (L98)
  - [ ] 7. Design a web crawler (L99)
  - [ ] 8. Design real-time chat (L100)
  - [ ] 9. Design a news feed (L101)
  - [ ] 10. Design video streaming (L102)
  - [ ] 11. Design ride sharing (L103)
  - [ ] 12. Design e-commerce and inventory (L104)
  - [ ] 13. Design payment processing (L105)
  - [ ] 14. Design a metrics and logging platform (L106)

**NEXT LESSON: Phase 5, Topic 4 — Design a notification service**

---
