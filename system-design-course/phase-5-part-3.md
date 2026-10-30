# Lesson 99 — Design a Web Crawler

*Phase 5, Topic 7*

## 1. Simple definition

**The problem.** Fetch web pages continuously and politely: start from a seed set, extract links, follow them, store the content, and revisit pages often enough that the data stays fresh — without overloading any site, getting banned, or burning infinite resources on infinite URLs.

**Why it is the topic where "scale" means something different.** Most systems are hard because of *volume*. A crawler is hard because of **adversarial, unbounded input**: the URL space of the web is effectively infinite (calendars, session ids, filter combinations, mirrors), sites are of unknown health, some are hostile, robots rules differ, and every one of them notices how fast you visit. The engineering is therefore about **bounds and politeness** as much as throughput.

Terms defined before use:

- **Frontier**: the queue of URLs waiting to be fetched, organised by priority and by host.
- **Politeness**: constraining requests per host (e.g. one concurrent request, or one per second) so you do not degrade the site.
- **robots.txt**: the site's crawl rules, fetched once per host, cached, and honoured including `Crawl-delay`.
- **Canonicalisation**: normalising a URL (`?utm_source=`, trailing slash, default port, fragment) so the same page is not fetched five times.
- **Fingerprint (SimHash)**: a 64-bit near-duplicate signature of page content, so mirrors and syndicated copies collapse.
- **Crawl trap**: a URL space that generates endless distinct URLs (`?calendar=2026-10-30&next=…`).
- **Recrawl interval**: how long before a page is fetched again — the freshness control.
- **Host sharding**: partitioning crawl work **by host** so politeness state is local and total load on a host is bounded.
- **Change rate**: how often a page actually changes; the signal that should drive recrawl scheduling.

The one sentence to carry out of this lesson: **a crawler is a politeness engine with a priority queue — the hard part is not fetching fast, it is deciding what to fetch, how often, and when to stop, while never looking like an attack from any single host's point of view.**

## 2. Why it exists

**Because freshness has a price and a value curve.** BookNook crawls **120 million pages** — publisher catalogues, retailer listings, distributor feeds, author sites, and review sites — to keep prices, availability and metadata accurate. A page that changes daily (retail prices) is worth crawling daily; a page that changes yearly (an author's bio) is worth crawling monthly. Crawling everything daily costs **14× more** than crawling by change rate, for almost no gain in freshness (§9 step 3).

**Because politeness is contractual and enforced.** A crawler that sends 40 requests/second to a small bookshop's site is a denial of service, however unintentional. BookNook was blocked by two partners in the first month — one by `robots.txt`, one by a firewall rule — and a block costs a data source permanently. The rule is a **per-host budget**, and it must be impossible to violate by adding crawler capacity.

**Because the input is unbounded and adversarial.** A single "next month" calendar link generated **3.2 M** distinct URLs and consumed 61 % of a day's crawl budget before anyone noticed (§7). Infinite spaces, session-id churn, and mirror sites are not edge cases; they are the normal behaviour of a large crawl, and they must be bounded by design.

**Because duplicates dominate.** Measured on BookNook's corpus: **34 %** of fetched pages were exact or near duplicates of something already stored (syndicated book descriptions, mirror sites, print views). Without fingerprinting, a third of the crawl budget and storage is spent on copies.

**And because "which URL do I fetch next" is a distributed scheduling problem.** Priority (freshness × importance), politeness (per-host constraints), and sharding (who owns which host) interact; the frontier is a small database in its own right, and the second-most-common crawler outage after politeness is a frontier that thrashes.

## 3. Real-world analogy

**A library's acquisitions agent.**

The library wants current information about every bookshop in the country. It employs an agent with a list of shops. The agent's working day illustrates every design constraint:

- **One visit at a time per shop.** The agent does not send five colleagues to the same shop simultaneously — the shopkeeper would call the police. That is **one concurrent request per host**, and it is a property of the *schedule*, not of the agent's enthusiasm.
- **Some shops post a sign: "no visits on Sundays; ask before entering; no more than one visit a week."** The agent reads the sign at the door (robots.txt), writes it in a notebook (the robots cache), and obeys it — including for the agent's colleagues, forever after.
- **The agent triages the list.** Shops that restock daily get visited daily; the shop that has not changed its window display in a year gets visited quarterly. **Visiting every shop every day would be absurd** — same cost, no better information. That is change-rate scheduling.
- **Some shops have a back room with an endless corridor of identical doors** ("next room", "next room"…). A naive agent walks it forever. A good agent counts doors visited in one building and leaves. That is the crawl trap, and every crawler learns it the expensive way.
- **And the agent keeps one notebook page per shop, not per visit.** Two streets with the same shop name are the same shop (canonicalisation); a shop's leaflet is a copy of a leaflet the agent already has (fingerprinting).

The analogy's sharpest point: **the agent's value is not the number of visits, it is the number of *changes* recorded.** A crawler that fetches 10× more and records the same number of changes has spent 10× the money for nothing. That reframing is the whole design.

## 4. Detailed technical explanation

### 4.1 Requirements

**Functional.**

| # | Requirement |
|---|---|
| F1 | Crawl from seeds, following extracted links, within defined domains |
| F2 | Honour robots.txt and `Crawl-delay`, and stop on `429`/`5xx` with backoff |
| F3 | Canonicalise URLs and fingerprint content to avoid refetching duplicates |
| F4 | Detect crawl traps and stop descending |
| F5 | Recrawl each page according to its measured change rate |
| F6 | Extract structured fields (ISBN, price, availability, title) per domain |
| F7 | Record provenance: URL, fetch time, HTTP status, content hash, redirect chain |
| F8 | Support an operator "recrawl now" and a per-domain pause switch |

**Non-functional.**

| # | Requirement | Number |
|---|---|---|
| N1 | Throughput | 100 pages/s sustained (8.6 M/day) |
| N2 | Politeness | ≤ 1 concurrent request per host; ≤ 1 req/s unless `Crawl-delay` says less |
| N3 | Freshness | 90 % of high-change pages re-crawled within 24 h |
| N4 | Duplicate suppression | ≥ 30 % of fetch attempts avoided |
| N5 | Availability | The crawl keeps working if a shard or a parser dies |
| N6 | Storage | ≤ 5 TB of page content (compressed), 90 days of history |
| N7 | Ethics/legal | robots honoured, ToS recorded per domain, PII-minimal storage |

### 4.2 Capacity estimation

```
  CORPUS      120,000,000 URLs
  RECRAWL     adaptive; average interval 14 days ⇒
              120,000,000 / (14 × 86,400) = 99.2 pages/s
              ⇒ 100 pages/s is the design point, and it is STORAGE- and
                politeness-bound, not CPU-bound.

  FETCH ATTEMPTS vs USEFUL FETCHES
      duplicates suppressed by canonicalisation + fingerprint:
          URL-level: 18 % of discoveries are non-canonical variants
          content-level: 34 % of fetched pages are near duplicates
      ⇒ to store 120 M distinct pages we attempt ~120 M / (1 − 0.18) and
        detect 34 % content duplicates ⇒ 182 M attempts, 120 M kept.
      ⇒ 100 pages/s becomes 152 attempts/s at the network level, which is
        the number capacity must be planned on.

  STORAGE
      average HTML 180 KB; after gzip/brotli 41 KB
      120 M × 41 KB = 4.9 TB current
      + 90 days of change history at a 2.4 % daily change rate:
          120 M × 0.024 × 90 × 41 KB = 10.6 TB
      ⇒ object storage, ~15.5 TB total ≈ £330/month. Trivial.

  LINK GRAPH
      120 M pages × 150 outgoing links = 18 B edges
      stored as (from_hash, to_hash) at 16 B compressed = 288 GB
      ⇒ this, not the HTML, is the biggest storage component, and it is what
        priority scoring reads (PageRank-ish importance, L47's inverted index
        is the other consumer).

  BANDWIDTH
      152 attempts/s × 41 KB = 6.2 MB/s inbound = 50 Mbit/s. Modest:
      a crawler's constraint is HOSTS, not bandwidth.

  POLITENESS BUDGET — the real constraint
      with 1 req/s/host: 100 pages/s needs 100 hosts active at any time.
      BookNook's 120 M URLs span 41,000 hosts ⇒ at 1 req/s/host the fleet
      could theoretically do 41,000 req/s; the binding limit is our own
      politeness policy per host, so throughput = hosts × (1/delay).
      ⚠ but the top 12 hosts hold 41 % of the URLs ⇒ their 12 req/s bounds
        the crawl at 12 pages/s for those hosts alone (see §9 step 4).
```

### 4.3 Components and interfaces (internal)

```
  SEED MANAGER      sitemaps, partner feeds, operator seeds, discovered links
  FRONTIER          priority queues (front) + per-host queues (back)
                    API: push(url, priority, host) · pop(worker_id) -> url
  HOST REGISTRY     per-host state: robots, crawl-delay, next_allowed_at,
                    last_error, ban flag, concurrency limit
  FETCHER           HTTP client: timeouts, retries, conditional GET
                    (If-Modified-Since / If-None-Match), compression
  PARSER            HTML → (links, structured fields), sandboxed, time-limited
  DEDUPE            URL canonicalisation + SimHash near-duplicate detection
  STORE             page content (object storage) + metadata (Postgres)
  SCHEDULER         computes the next crawl time per URL from change rate
  OPS               pause per domain, recrawl-now, budget dashboard
```

### 4.4 The frontier: two levels, because priority and politeness are different

```
  NAIVE priority queue: pop the highest-priority URL.
      ✗ violates politeness: 40 URLs from the same host may be the top 40.
  NAIVE per-host queue: round-robin over hosts.
      ✗ ignores priority entirely: an author's bio competes with a price page.

  THE STANDARD ANSWER (Mercator-style, two levels):
      FRONT QUEUES — N priority levels (measured: 6 is plenty:
          0 = operator "recrawl now", 1 = price/availability (high change),
          2 = catalogue pages, 3 = author/publisher pages,
          4 = discovery, 5 = backfill of the long tail)
          each level is a FIFO; select a level with a bias so lower levels
          are not starved (a 1-in-20 chance of picking the lowest non-empty)
      BACK QUEUES — one per host (41,000 of them), holding that host's URLs.
          a back queue is "ready" when now ≥ host.next_allowed_at
      SELECT: pop from a front queue → the URL's host's back queue; then
          choose a READY back queue (round-robin or heap by next_allowed_at)
      ⇒ priority ordering WITHIN the constraint set, politeness enforced by
        construction, and no single host can monopolise the workers.
  MEASURED: naive priority queue → 412 requests/s to one host (an immediate
    ban). Two-level frontier → 1.0 req/s to that host, and the same overall
    throughput.
```

### 4.5 Politeness, robots, and the rules that keep you welcome

```
  THE POLITENESS RULES (all enforced in the HOST REGISTRY, not in the workers)
    1. one in-flight request per host (default); up to `Crawl-delay`-derived
       concurrency where the host permits it explicitly
    2. minimum delay between requests to a host: max(1 s, Crawl-delay)
    3. honour `Disallow` and `Allow` patterns from robots.txt, cached for 24 h
       per host (with a 1-hour revalidation on a 403/404 change)
    4. exponential backoff on 429/503, and RESPECT `Retry-After` exactly
    5. per-host daily request budget (e.g. 20,000/day) so a large site is not
       crawled disproportionately
    6. immediate pause on 401/403 storms, and a manual pause switch (§4.3)

  robots.txt DETAILS THAT MATTER:
      · it is per SCHEME+HOST+PORT, and fetched with your own User-Agent
      · `Crawl-delay` is not standard but is widely understood; honour it
      · if robots.txt is a 5xx, treat as "disallow everything" (conservative)
        and retry later; if 404, treat as "allow all"
      · robots.txt is a request from the site, not a licence: ToS and
        copyright are separate, and BookNook records both per domain (§4.9)

  MEASURED EFFECT OF THE HOST REGISTRY: before it existed, politeness was
  implemented in each worker (a sleep after a fetch). With 40 workers, the
  top host received 40 concurrent requests — the sleep did nothing, because
  the workers did not know about each other. Centralising the state per host
  is the entire fix, and it is the single most common crawler design bug.
```

### 4.6 Bounding the infinite: canonicalisation, traps, and fingerprints

```
  URL CANONICALISATION (rules applied in a fixed order, versioned)
      1. lowercase scheme and host; keep path case
      2. drop the fragment (#…)
      3. drop default ports (:80 for http, :443 for https)
      4. sort query parameters; drop known tracking params
         (utm_*, ref, sessionid, gclid, fbclid — a maintained list)
      5. normalise percent-encoding; resolve /a/b/../c
      6. strip a trailing slash except for the root
      ⇒ BookNook measured 18 % of discovered URLs as variants, and 7 % as
        tracking-parameter churn — 84 M avoided fetches/month.

  CONTENT FINGERPRINT: SimHash-64 over shingle 4-grams
      · identical hash ⇒ exact duplicate, keep one
      · Hamming distance ≤ 3 ⇒ near duplicate (measured recall 0.97, false
        positive 0.001 on BookNook's corpus; a naive "distance ≤ 10" gave
        0.4 % false positives, which silently dropped distinct editions)
      · store the canonical representative and a pointer from each variant

  TRAPS — five detectors, all needed (§7 incident):
      1. URL LENGTH > 2,048 characters → drop
      2. per-host URL COUNT ceiling per day (e.g. 5× the host's known page
         count, or 200,000 absolute) → halt the host and alert
      3. PATH DEPTH > 12 segments → deprioritise to the backfill level
      4. REPEATING SEGMENT PATTERN: the same path with an increasing numeric
         or date component repeated > 20 times → classify as a trap
      5. SITE-LEVEL: if > 90 % of a host's fetched pages are duplicates, stop
         descending that host and record why
      ⇒ each detector is cheap; the trap is expensive.
```

### 4.7 Scheduling recrawls by change rate

```
  EVERY page carries a measured change rate λ (changes/day), estimated from
  observed history: a Beta/Gamma posterior over observed changes per fetch
  interval, so a new page starts at the host's default and converges.

  THE PRACTICAL RULE (heuristic, measured — not an optimal-control result):
      interval = clamp( 24 h × (0.8 / λ)^0.5 , 6 h, 30 days )
      ⇒ λ = 1.0/day (price page)   → 24 h × 0.89 = 21 h
      ⇒ λ = 0.1/day (catalogue)    → 24 h × 2.83 = 68 h ≈ 3 days
      ⇒ λ = 0.003/day (author bio) → 24 h × 16.3 = 16 days
      ⇒ λ = 0 (static)             → 30 days (the floor)
      and results are CLAMPED so nothing is crawled more than 4×/day or less
      than monthly.

  WHY THIS BEATS UNIFORM RECRAWL (measured over 30 days):
      uniform 14-day recrawl of all 120 M pages: 99 pages/s, 8.6 M/day
        · 0.4 % of pages were more than 24 h stale when their data mattered
        · 71 % of fetches returned "not modified" — pure waste
      change-rate scheduling at the same 99 pages/s:
        · pages > 24 h stale when it mattered: 0.06 % (−85 %)
        · "not modified" responses: 22 % (−69 %)
      ⇒ SAME BUDGET, 6× better freshness on the pages that matter, because
        the budget is spent where changes happen.
  CONDITIONAL GET is mandatory alongside this: If-None-Match/If-Modified-Since
  turns 22 % of fetches into a 304 with no body — 22 % of the bandwidth for
  0.3 % of the information.
```

### 4.8 Distribution: shard by host, never by URL

```
  SHARD KEY = host (consistent hash), NOT the URL.
      why: all state that must be atomic is per host — next_allowed_at,
      in-flight count, robots, crawl-delay. Sharding by URL means every
      politeness decision needs a cross-shard coordination.
      ⇒ 120 M URLs / 41,000 hosts, sharded into 64 partitions by
        hash(host) % 64, each partition owning its hosts' queues and state.
  CONSEQUENCE 1: HOT PARTITIONS. One host with 4 M URLs is a hot shard.
      ⇒ split oversized hosts into sub-shards by URL hash WITH a shared
        politeness lease in Redis (one counter per host, TTL 1 s) so the
        sub-shards collectively respect the host's rate.
      BookNook has 3 such hosts (2 large retailers); they consumed 31 % of
      crawl capacity and required the lease mechanism.
  CONSEQUENCE 2: FAILOVER. A shard's queues live in a database (Postgres or
      Redis with AOF); a dead worker releases its in-flight URLs with a
      timeout, and the frontier re-queues them. The crawl is idempotent, so a
      duplicate fetch is harmless — the URL canonicaliser and the fingerprint
      absorb it.
```

### 4.9 Legal, ethical, and quality dimensions (part of the design, not a footnote)

- **robots.txt is honoured** by contract with your own integrity; record the version you saw and when.
- **Terms of service and copyright** are separate from robots. BookNook records, per domain: who approved the crawl, under what agreement, and what fields may be stored. A domain without a record cannot be crawled in production (enforced in code).
- **PII minimisation**: crawlers scoop up personal data incidentally. BookNook strips query strings from stored URLs (they often contain emails/tokens), does not store review-author names beyond the display name, and has a documented erasure path against the page store.
- **Load sensitivity**: never crawl a host during its peak if you can help it (measure response time; if it degrades, reduce rate — a self-imposed politeness feedback loop).
- **Quality**: a page is "good" if it parses, has the expected structured fields, and is not an error/anti-bot interstitial. Track **per-domain extraction success rate**; a drop from 98 % to 40 % means the site changed its template, and the data is silently wrong until someone notices — that alert is what keeps a crawl product trusted.

## 5. Request / data flow

```
 ONE URL'S LIFE, from discovery to recrawl
 ─────────────────────────────────────────────────────────────────────────
 1. DISCOVER  a sitemap or an extracted link yields a raw URL
 2. CANONICALISE → drop tracking params, sort query, strip fragment
              if seen in the URL store within 30 days → record the link
              edge and STOP (no fetch)                     [18 % of URLs]
 3. POLITENESS CHECK via host registry: robots allows? next_allowed_at?
              if not, schedule and park
 4. FETCH     conditional GET with If-None-Match; 10 s timeout; 2 retries
              304 → update last_seen, reschedule, STOP      [22 %]
              200 → continue
 5. FINGERPRINT  SimHash over shingles
              duplicate of a known page → store a pointer, STOP [34 %]
 6. PARSE     sandboxed (no JS by default; a headless renderer only for
              domains that declare it, at 40× the cost — see §9 step 5)
              extract: links (≤ 500/page), structured fields, canonical <link>
 7. STORE     content to object storage (key = content hash, deduped),
              metadata row, link edges
 8. SCHEDULE  update the change-rate posterior; compute the next interval
              (§4.7) and push back into the frontier's priority level
 ⇒ of 100 fetch attempts: 18 avoided by canonicalisation, 22 are 304s, 34
   are duplicates ⇒ 8 pages of genuinely new or changed content per 100
   attempts. THAT RATIO is the crawler's efficiency metric, and improving it
   is worth more than any throughput work.
```

## 6. ASCII diagram

```
   seeds: sitemaps · partner feeds · operator · discovered links
                          │
                          ▼
        ┌──────────────────────────────────────────────┐
        │ FRONTIER                                     │
        │  front queues: P0..P5 (priority)             │
        │  back queues: 1 per host (41,000)            │
        │  ready = now ≥ host.next_allowed_at          │
        └───────────────┬──────────────────────────────┘
                        │ pop (respecting host state)
        ┌───────────────▼──────────────────────────────┐
        │ HOST REGISTRY (the politeness brain)         │
        │  robots.txt + Crawl-delay + next_allowed_at  │
        │  in-flight count · error backoff · ban flag  │
        │  ⇒ ONE in-flight per host, by construction   │
        └───────────────┬──────────────────────────────┘
                        ▼
        ┌──────────────────────────────────────────────┐
        │ FETCHER  (64 shards by host)                 │
        │  conditional GET · timeout · retry · gzip    │
        └───────────────┬──────────────────────────────┘
                        ▼
        ┌───────────────────────┐   ┌──────────────────────────────┐
        │ DEDUPE                │   │ PARSER (sandboxed, 2 s limit)│
        │ · URL canonicalisation│   │ · links ≤ 500               │
        │ · SimHash-64 (≤ 3)    │   │ · structured fields          │
        │ · trap detectors ×5   │   │ · canonical <link>          │
        └───────────┬───────────┘   └───────────────┬──────────────┘
                    └───────────┬───────────────────┘
                                ▼
             ┌──────────────────────────────────────────┐
             │ STORE                                    │
             │ pages (content-addressed, 15.5 TB)       │
             │ link graph (18 B edges, 288 GB)          │
             │ metadata (Postgres: url, hash, status,   │
             │   fetched_at, change_rate, provenance)   │
             └───────────────────┬──────────────────────┘
                                 ▼
                     ┌───────────────────────────┐
                     │ SCHEDULER                 │
                     │ change-rate posterior →   │
                     │ next interval (§4.7) →    │
                     │ push back to the frontier │
                     └───────────────────────────┘

   THE EFFICIENCY RATIO, which is the metric that matters:
      100 fetch attempts
        −18  avoided by canonicalisation (never fetched)
        −22  HTTP 304 (conditional GET, no body)
        −34  content duplicates (fingerprint)
        ────
          8  genuinely new or changed pages   ← what you actually bought
      ⇒ improving 8/100 toward 15/100 doubles the value of the whole fleet.
```

## 7. Detailed example

**BookNook's catalogue crawler.** It keeps prices, availability, cover images and descriptions accurate across 41,000 domains, and feeds a book-discovery index.

```
  measured, at the 14-month point:
    120 M URLs across 41,000 hosts
    100 pages/s sustained, 152 fetch attempts/s (before canonicalisation)
    34 % content duplicates, 22 % of responses 304, 18 % of URLs variants
    ⇒ 8.1 useful pages per 100 attempts
    storage 15.5 TB total (4.9 TB current + 10.6 TB of 90-day history)
    the top 12 hosts hold 41 % of the URLs
    domain extraction success: 97.4 % overall; 14 domains below 60 %
```

**The crawl-trap incident (the expensive one):**

```
  2026-01-09, a distributor's site published a "next month" link on every
  calendar page. BookNook's depth limit was 12, and the trap generated
  3.2 M distinct URLs at depth 6.
    · it consumed 61 % of one day's crawl budget (5.2 M of 8.6 M fetches)
    · 99.7 % of those pages were identical calendars with a different month
    · the top-12 host freshness SLO was missed for 14 hours
    · cost: ~£180 of fetch capacity and, more importantly, a day of stale
      prices during a sale week
  fixes (all five detectors, §4.6):
    · per-host daily URL ceiling (200,000) — would have stopped it at hour 2
    · repeating-segment detection — would have stopped it at hour 1
    · "> 90 % duplicates from a host ⇒ stop descending" — hour 2
  ⇒ the detectors are cheap; the trap cost a week of engineering to analyse
    plus a stale-price weekend. EVERY crawler gets trapped at least once.
```

**The ban, and the politeness rewrite:**

```
  2025-11: a partner retailer's firewall blocked BookNook after 40 minutes of
  40 concurrent requests to their catalogue. Their engineering team emailed
  with the log excerpt. Consequences: 3 weeks without that retailer's prices,
  and a relationship repair visit.
  root cause: politeness was a `time.sleep(1)` in each worker. With 40
  workers, per-worker sleep means 40 requests/second in aggregate.
  fix: the HOST REGISTRY with a central per-host `next_allowed_at` and an
  in-flight count — a distributed rate limiter keyed by host (L95 applied to
  a crawler) — and a hard rule that the FETCHER cannot act without a lease
  from the registry.
  measured after the fix: max 1.0 req/s per host, no further blocks in
  14 months, and throughput UNCHANGED (100 pages/s) because the bottleneck
  had never been the popular hosts.
```

**The freshness payoff, quantified:**

```
  before (uniform 14-day recrawl):
      price error > 1 %: 11.2 % of listings at any time
      "out of stock shown as in stock": 3.1 % → measured by call-centre
      complaints (2,100/month)
  after (change-rate scheduling + conditional GET):
      price error > 1 %: 1.4 %
      out-of-stock errors: 0.4 %
      complaints: 2,100 → 190/month
      at £6.20 average handling cost: £11,842/month saved, plus the trust
      effect on conversion (not measured, and probably larger)
  ⇒ the same crawl budget, reallocated. 6× better freshness on the pages
    that matter is a SCHEDULING change, not a capacity change.
```

## 8. Code or configuration example

```python
# ── URL canonicalisation: a fixed, versioned rule order ──────────────────
TRACKING = {"utm_source","utm_medium","utm_campaign","utm_term","utm_content",
            "gclid","fbclid","ref","ref_src","sessionid","sid","mc_cid"}

def canonicalise(url: str) -> str | None:
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not p.hostname:
        return None
    scheme = p.scheme.lower()
    host   = p.hostname.lower().rstrip(".")
    port   = "" if p.port in (None, 80 if scheme == "http" else 443) else \
             f":{p.port}"
    path   = posixpath.normpath(unquote(p.path) or "/")
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
         if k.lower() not in TRACKING]
    q.sort()
    query = urlencode(q, doseq=True)
    out = f"{scheme}://{host}{port}{path}" + (f"?{query}" if query else "")
    return out if len(out) <= 2048 else None        # trap detector 1
```

```python
# ── Host registry: the only place politeness is decided ──────────────────
class HostRegistry:
    """A distributed limiter keyed by host (L95) — NOT a per-worker sleep."""

    async def lease(self, host: str) -> Lease | None:
        now = time.time()
        h = await self.state(host)                      # Redis hash, cached
        if h.get("paused") or now < h["next_allowed_at"]:
            return None
        if h["in_flight"] >= h["max_concurrency"]:      # default 1
            return None
        delay = max(h["min_delay"], h.get("crawl_delay", 0))   # robots says so
        key = f"lease:{host}"
        # atomic: acquire an in-flight slot and set the next allowed instant
        ok = await self.redis.eval(LEASE_LUA, 1, key,
                                   h["max_concurrency"], delay, now)
        if ok != 1:
            return None
        return Lease(host=host, expires=now + 30)       # fetcher must release

    async def release(self, lease: Lease, status: int, retry_after=None):
        host = lease.host
        await self.redis.eval(RELEASE_LUA, 1, f"lease:{host}")
        if status in (429, 503):
            back = int(retry_after) if retry_after else min(
                2 ** self.failures(host) * 60, 3600)    # honour Retry-After
            await self.set(host, next_allowed_at=time.time() + back)
            metrics.inc("crawl.backoff", {"host": host})
        elif status == 304:
            await self.set(host, min_delay=max(1, self.current_delay(host)))
        elif status in (401, 403):
            await self.bump_forbidden(host)             # 3 in an hour ⇒ pause
```

```python
# ── SimHash near-duplicate detection ────────────────────────────────────
def simhash(text: str, bits: int = 64) -> int:
    v = [0] * bits
    for sh in shingles(normalise_words(text), k=4):     # 4-gram shingles
        h = int.from_bytes(hashlib.blake2b(sh.encode(), digest_size=8).digest(),
                           "big")
        for i in range(bits):
            v[i] += 1 if (h >> i) & 1 else -1
    return sum(1 << i for i in range(bits) if v[i] > 0)

def is_duplicate(h: int, candidate: int) -> bool:
    # ≤ 3 bits: measured recall 0.97, false-positive 0.001 (§4.6).
    # Use banded LSH (4 × 16-bit bands) to avoid scanning the corpus.
    return bin(h ^ candidate).count("1") <= 3
```

```python
# ── Change-rate scheduling: the freshness budget, allocated by evidence ──
def next_interval_hours(change_rate: float, last_changed: float) -> float:
    """change_rate: observed changes per day (posterior mean)."""
    if change_rate <= 0.0005:
        base = 24 * 30                              # static → monthly floor
    else:
        base = 24 * (0.8 / change_rate) ** 0.5      # §4.7 heuristic
    # a page that changed on the last visit gets a short grace period, so a
    # fast-moving event is followed closely for a few visits
    if last_changed < 1:                            # changed at last fetch
        base = min(base, 6)
    return max(6, min(base, 24 * 30))
```

```yaml
# ── The operational rails. Without these, a crawler is a liability. ─────
politeness:
  max_concurrency_per_host: 1
  min_delay_seconds: 1.0            # raised automatically if the host slows
  honour_crawl_delay: true
  max_requests_per_host_per_day: 20000
  respect_retry_after: exact
trap_detectors:
  max_url_length: 2048
  max_path_depth: 12
  max_urls_per_host_per_day: 200000
  max_duplicate_ratio_per_host: 0.90
  max_repeating_segments: 20
provenance:
  require_legal_record_per_domain: true     # crawl fails closed otherwise
  store_query_strings: false                # PII minimisation (§4.9)
alerts:
  - alert: HostFetchRateExceeded
    expr: rate(crawl_requests_total[1m]) by (host) > 1.2
    severity: page                  # politeness is a hard invariant
  - alert: HostDuplicateRatioHigh
    expr: rate(crawl_duplicate_total[1h]) by (host) / rate(crawl_fetched_total[1h]) by (host) > 0.9
    severity: ticket                # a trap, or wasted budget
  - alert: DomainExtractionSuccessDrop
    expr: crawl_extract_success_ratio < 0.6
    for: 2h
    severity: page                  # silent data corruption in progress
  - alert: FreshnessSLOMiss
    expr: crawl_high_change_stale_ratio > 0.10
    severity: ticket                # the reason the crawler exists
```

## 9. Numerical example

BookNook: 120 M URLs, 41,000 hosts, 100 pages/s target.

**Step 1 — the capacity chain, from URLs to actual work.**

```
  recrawl interval (adaptive average)      14 days
  distinct pages/s required = 120 M / (14 × 86,400) = 99.2
     (the 18 % of discovered URLs that canonicalisation collapses are never
      fetched, so they never appear in this figure — canonicalisation removes
      work rather than adding it)
  of those 99.2 fetches/s: 22 % return 304, and 34 % of the remainder are
     content duplicates
     ⇒ useful pages/s = 99.2 × 0.78 × 0.66 = 51/s
  ⇒ THE CRAWLER SENDS 99 REQUESTS/S AND BUYS 51 PAGES/S OF INFORMATION.
    Improving the ratio is worth more than any infrastructure.
```

**Step 2 — how many workers, and why bandwidth is not the constraint.**

```
  worker cost per fetch: DNS 6 ms + TLS 30 ms (reused) + HTTP 180 ms + parse
      120 ms + store 20 ms ≈ 356 ms, of which ~230 ms is WAITING
  concurrency needed = 99.2 × 0.356 = 36 in-flight fetches
  ⇒ 48 worker threads (33 % headroom) on 6 pods.
  bandwidth = 99.2 × 41 KB = 4.1 MB/s = 33 Mbit/s
  ⇒ 33 Mbit/s is nothing; the limit is the PER-HOST delay, and adding pods
    does not increase throughput at all once every host is at its delay
    floor. That is why the crawl scales by adding HOSTS, not workers.
```

**Step 3 — the freshness arithmetic that justifies adaptive scheduling.**

```
  uniform 14-day recrawl, 120 M pages, budget 99 pages/s:
      a page that changes daily is on average 7 days stale
      share of pages that are high-change (λ > 0.5/day): 8 % = 9.6 M pages
      ⇒ 9.6 M pages are ~7 days stale, and they are the price pages
  adaptive, same budget:
      λ > 0.5/day pages get ~21 h intervals ⇒ 4.6 M page-fetches/day of the
      8.6 M daily budget (54 %) are spent on 8 % of the corpus
      ⇒ those pages are on average 10.5 h stale
  ⇒ mean staleness on the pages that matter: 7 days → 10.5 h (16×), for
    ZERO extra fetches. Cost: a change-rate estimator (a few hundred lines)
    and conditional GET.
  ⚠ this ONLY works with conditional GET: adaptive scheduling multiplies
    fetches to the same pages, and without 304s the bandwidth cost rises
    proportionally. Measured: 22 % of fetches are 304s afterwards.
```

**Step 4 — the hot-host problem, and the honest conclusion.**

```
  the top 12 hosts hold 41 % of URLs = 49.2 M pages
  at 1 req/s/host with 1 concurrency: those 12 hosts can supply 12 pages/s
  ⇒ to sustain the crawl's 99 pages/s, only 12 % can come from the top 12
    hosts, yet they hold 41 % of the desired crawl.
  ⇒ a 1 req/s politeness policy makes the large hosts the schedule — the
    long tail (40,988 hosts) is crawled far more often than needed, and the
    big sites are the bottleneck.
  RESOLUTION (the honest design choice):
    · negotiate explicit written permission with the 12 largest hosts for a
      10 req/s allowance (measured result: 3 agreed, covering 31 % of URLs)
    · reallocate the freed long-tail budget to the large hosts
    · accept that 1 req/s caps how fresh a huge site can be, and SET THE
      FRESHNESS SLO PER HOST CLASS rather than globally
  ⇒ 3 agreements moved the achievable freshness for 31 % of the corpus from
    7 days to under a day. The engineering was 2 weeks; the negotiation was
    3 months. STATE THIS IN THE DESIGN: the binding constraint on a crawler
    is often a relationship, not a machine.
```

**Step 5 — JavaScript rendering: the 40× cost decision.**

```
  61 % of BookNook's target sites render prices client-side.
  headless Chrome render: 1.2 s CPU + 340 MB RSS per page ⇒ 12 pages/s per
      node, and a 41 KB HTML fetch becomes ~900 KB of network + DOM.
  ⇒ at 99 pages/s fully rendered: 8.3 nodes of 16 vCPU (≈£2,400/month) vs
    £310/month for plain HTTP. A 7.7× infrastructure cost, plus fragility.
  the decision (measured): render ONLY for the 411 domains that require it
    (3.1 % of fetches), cache the rendered extraction, and monitor per-domain
    extraction success to catch a silent switch to client-side rendering.
  ⇒ cost £180/month instead of £2,400, for the same data, because the
    rendering decision was made per domain rather than globally.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **Two-level frontier (priority + per-host)** | Priority with politeness guaranteed | More state to run | **Always** |
| **Uniform recrawl** | Dead simple | 6× worse freshness on the pages that matter | The corpus is homogeneous and small |
| **Adaptive change-rate recrawl** | 16× fresher for the same budget | An estimator, and more per-host state | **Any corpus with mixed change rates** |
| **Conditional GET** | 22 % of fetches become 304s | Nothing | Always |
| **SimHash ≤ 3** | Collapses 34 % duplicates | Small false-negative rate | Always, with a measured threshold |
| **Headless rendering for all** | Correct data on JS sites | 7.7× cost, more fragility | Never |
| **Rendering per-domain** | Correct data where needed | A per-domain registry | **Chosen** |
| **Shard by host** | Politeness is local and atomic | Hot shards for big hosts | Always, with sub-sharding |
| **Aggressive crawling** | Freshness | Bans, legal exposure, ethics | Never |
| **Negotiated allowances** | 10× the rate on the hosts that matter | Weeks of relationship work | **The real lever at scale** |
| **Storing raw HTML** | Reprocessable, auditable | 15.5 TB with history | Always (it is cheap) |
| **Storing only extracted fields** | Cheap | An extraction bug means a recrawl | You cannot re-crawl cheaply |

## 11. Failure scenarios

**11.1 — Politeness violation and a ban** (§7). 40 concurrent requests because politeness lived in the workers. **Fix:** a central host registry with leases; a per-host rate alert.

**11.2 — Crawl trap** (§7). 3.2 M URLs from a calendar; 61 % of a day's budget. **Fix:** five detectors (§4.6), the per-host daily ceiling being the one that fires first.

**11.3 — Silent extraction breakage.** A retailer changed template; extraction success fell 98 % → 41 % over three weeks; prices were wrong and 2,100 complaints followed. **Fix:** a per-domain success-ratio alert (2 h at < 60 %) and a canary page per domain whose fields are asserted.

**11.4 — Duplicate explosion.** Without fingerprints, mirror sites tripled storage; with too loose a threshold (Hamming ≤ 10), 0.4 % of distinct editions were dropped. **Fix:** ≤ 3 bits, measured for recall and precision, with the representative page retained and variants linked.

**11.5 — Hot shard starvation.** One host with 4 M URLs saturated a partition, delaying everyone else on it. **Fix:** sub-shard oversized hosts with a shared politeness lease.

**11.6 — Frontier thrash.** The frontier re-queued in-flight URLs on every worker restart, re-fetching 1.2 M URLs/day. **Fix:** leases with expiry (not immediate re-queue), and idempotent handling of the duplicates that remain.

**11.7 — The robots.txt cache gone stale.** A host added `Disallow: /catalogue` and the 24 h cache kept crawling for 20 hours. **Fix:** revalidate on 403/404 and on a 24 h TTL; treat a robots 5xx as disallow-all.

**11.8 — PII in stored query strings.** A crawl of review pages captured email addresses in query params. **Fix:** don't store query strings (or strip known-PII params), plus a documented erasure path against the page store.

## 12. Alternatives

| Instead of | Consider | When |
|---|---|---|
| Crawling | A sitemap, API, or partner feed | The site offers one — it is always better |
| Crawling | A commercial data feed | Prices/metadata are available commercially and legally |
| Own headless renderer | A managed rendering API | Few domains, unpredictable JS |
| Own frontier | A managed crawl service | The crawl is not your differentiator |
| Change-rate scheduling | Uniform recrawl with conditional GET | Homogeneous corpus, small budget |
| Storing all HTML | Extracting and storing fields only | Storage-bound, and re-crawl is cheap |
| A crawler at all | Asking partners to push data | **Always the first thing to try** |

## 13. When to use it

- **When the data is not offered**: prices, availability and catalogue changes across many independent sites.
- **With adaptive scheduling + conditional GET**, for any corpus with mixed change rates.
- **A central host registry**, in any crawler with more than one worker.
- **Fingerprinting and canonicalisation**, whenever the corpus has mirrors or syndication.
- **Per-domain extraction monitoring**, for any crawl that feeds a product.

## 14. When *not* to use it

- **When an API or feed exists** — always prefer it; it is faster, legal, and cheaper.
- **When the site's terms forbid it** — robots is not the only rule.
- **For a one-off dataset** — buy it, or scrape it once with a human in the loop.
- **To obtain personal data** — that is a different legal regime entirely.
- **At a rate a small site cannot survive** — that is a denial of service with a User-Agent.

## 15. Common mistakes

1. **Politeness implemented per worker** instead of centrally (§11.1).
2. **No trap detectors**, and no per-host URL ceiling (§11.2).
3. **No conditional GET** (§4.7).
4. **Uniform recrawl** for a corpus with mixed change rates (§9 step 3).
5. **SimHash threshold chosen by feel**, silently dropping distinct editions (§11.4).
6. **Sharding by URL** rather than host (§4.8).
7. **No per-domain extraction-quality alert** (§11.3).
8. **Headless rendering for everything** (§9 step 5).
9. **No legal/provenance record per domain** (§4.9).
10. **Storing raw query strings** with PII (§11.8).
11. **In-flight URLs re-queued immediately** on worker failure (§11.6).
12. **Ignoring `Retry-After`** and backing off on your own schedule.
13. **No freshness metric** — so nobody knows whether the crawler works.
14. **Treating robots.txt as the whole legal story** (§4.9).

## 16. Interview perspective

**What is actually being tested.** Whether you know that a crawler is **politeness-bound and freshness-bound**, not throughput-bound, and whether you can bound an infinite input space. Strong candidates: two-level frontier, central host registry, canonicalisation + fingerprinting, trap detectors, change-rate scheduling, and a stated efficiency ratio. Weak candidates: "a queue of URLs and 50 worker threads".

**Answers that land well:**

- *"How do you scale the crawl?"* — "You mostly don't, by adding workers. Throughput is `hosts × (1 / delay)`, so once every host is at its politeness floor, more workers change nothing. At 120 million URLs across 41,000 hosts and one request a second per host, the constraint is the twelve hosts that hold 41 % of the URLs — and the fix there was negotiating written permission for a higher rate, which took three months and moved freshness for a third of the corpus from seven days to under one."
- *"How do you avoid duplicates?"* — "Two layers. Canonicalisation removes 18 % of discovered URLs before any fetch — tracking parameters, trailing slashes, fragment and query ordering. Then a SimHash-64 fingerprint at Hamming distance three or less collapses near-duplicates, which was 34 % of fetched pages. I measure the threshold rather than guessing it: at distance ten we silently dropped distinct editions of the same book."
- *"What's your metric?"* — "Information bought per request. Per hundred fetch attempts: eighteen are avoided, twenty-two return 304, thirty-four are content duplicates — so eight pages of genuinely new content. Improving eight toward fifteen doubles the value of the whole fleet, which is why I spend on the frontier and the fingerprinter rather than on more pods."
- *"How do you handle a site that generates infinite URLs?"* — "Five cheap detectors: URL length, per-host daily URL ceiling, path depth, repeating-segment patterns, and a duplicate-ratio cutoff per host. Ours got trapped by a calendar that generated 3.2 million URLs and ate 61 % of a day's budget; the repeating-segment and duplicate-ratio detectors would have stopped it inside the first hour."

**Common interview tasks:** design the frontier; enforce politeness; handle robots; dedupe URLs and content; bound traps; schedule recrawls; shard the crawl; monitor freshness and extraction quality.

**Red flags interviewers watch for:**

- Workers with `sleep(1)` as politeness.
- No robots.txt discussion, or "we'll respect it" with no mechanism.
- No duplicate handling.
- No trap limits.
- Uniform recrawl with no freshness metric.
- Headless rendering for all sites.
- Sharding by URL.
- No legal dimension at all.
- No numbers: no pages/s, no hosts, no ratio.

## 17. Knowledge check

**Conceptual**

1. Explain why a crawler's throughput is bounded by `hosts × (1/delay)` rather than by worker count, and derive what happens to throughput when the largest hosts hold a disproportionate share of the URLs. Then explain the two non-technical levers that actually change the outcome.
2. Explain the two-level frontier and why a single priority queue violates politeness. Then explain what "ready" means for a back queue and how priority starvation is avoided.
3. Explain the five trap detectors and, for each, the trap species it catches and the earliest point it can fire. Then explain why the per-host daily URL ceiling is the one that must exist even if the others are imperfect.

**Practical scenario**

4. Design BookNook's crawler: 120 M URLs, 41,000 hosts, 100 pages/s, mixed change rates, 61 % of sites requiring JS rendering, a legal-provenance requirement, and a 24 h freshness target for price pages. Give the requirements and capacity tables (including the efficiency ratio), the frontier design, the host-registry state and rules, the canonicalisation and fingerprinting policy with thresholds, the trap detectors, the change-rate estimator and scheduling rule, the sharding and hot-host handling, the rendering decision per domain, the monitoring with five alerts, the cost, and the incident replays. Then state what you would do if the largest host refused a rate increase.

**Calculation**

5. A corpus of 120 M URLs across 41,000 hosts, average recrawl interval 14 days, 22 % 304 responses, 34 % content duplicates, 18 % of discovered URLs non-canonical. (a) Compute fetch attempts/s needed. (b) Compute the useful-pages/s ratio. (c) If the 12 largest hosts hold 41 % of the URLs and allow 1 request/s each, compute the maximum pages/s obtainable from them and the share of the total crawl they can supply. (d) Compute how many additional hosts must be active simultaneously to sustain 100 pages/s at a 1 s delay.

6. Storage: 120 M pages at 41 KB compressed, a 2.4 % daily change rate, 90 days of history. (a) Compute the current store size. (b) Compute the history size. (c) Compute the monthly cost at £0.021/GB-month. (d) If headless rendering were applied to all fetches at 900 KB per page, compute the daily inbound volume and compare it with the 4.1 MB/s plain-HTTP figure.

**Progress checklist**

- Phase 1 — Foundations (L1–17): **complete** ✅
- Phase 2 — Scaling (L18–39): **complete** ✅
- Phase 3 — Data and Storage (L40–63): **complete** ✅
- Phase 4 — Distributed Systems (L64–92): **complete** ✅
- Phase 5 — Complete system designs (L93–106):
  - [x] 1. Design Pastebin (L93)
  - [x] 2. Design a URL shortener (L94)
  - [x] 3. Design a rate limiter (L95)
  - [x] 4. Design a notification service (L96)
  - [x] 5. Design search autocomplete (L97)
  - [x] 6. Design file storage (L98)
  - [x] **7. Design a web crawler (L99) ← you are here**
  - [ ] 8. Design real-time chat (L100)
  - [ ] 9. Design a news feed (L101)
  - [ ] 10. Design video streaming (L102)
  - [ ] 11. Design ride sharing (L103)
  - [ ] 12. Design e-commerce and inventory (L104)
  - [ ] 13. Design payment processing (L105)
  - [ ] 14. Design a metrics and logging platform (L106)

**NEXT LESSON: Phase 5, Topic 8 — Design real-time chat**

---

---

# Lesson 100 — Design Real-Time Chat

*Phase 5, Topic 8*

## 1. Simple definition

**The problem.** Deliver messages between users in near real time: one-to-one and group conversations, delivery and read receipts, presence, offline delivery, history that survives device loss, and ordering that matches what people said — at 40 million concurrent connections.

**Why "concurrent connections" is the defining number.** A chat system holds a **live socket open per active user**. That is not a request-rate problem, it is a **memory-and-file-descriptor problem**: 40 M connections at ~10 KB of per-connection state (buffers, framing, TLS record state) needs ~400 GB of RAM just for the sockets, spread over hundreds of gateway nodes. Chat is where "stateful connection" meets "horizontal scale", and the whole architecture follows from how you hold that socket without making the socket the source of truth.

Terms defined before use:

- **Gateway / connection node**: the server holding a user's socket. Stateless in *business* terms, stateful in *connection* terms.
- **Session registry**: the map from `user_id` → the gateway holding their connection(s). The routing table.
- **Conversation / thread**: an ordered sequence of messages shared by 2+ participants.
- **Sequence number (per conversation)**: the monotonically increasing integer that defines message order. **The ordering primitive.**
- **Delivery receipt**: acknowledgement that a device received a message.
- **Read receipt**: acknowledgement that a user saw it.
- **Fan-out**: duplicating one message to N recipients' inboxes/connections.
- **Outbox / inbox**: per-user queues of pending messages for offline delivery.
- **Presence**: whether a user is currently connected; inherently eventually consistent.

The one sentence to carry out of this lesson: **the socket is a delivery mechanism, not a source of truth — messages go to durable storage first, get a per-conversation sequence number, and are then pushed to whichever gateways currently hold the recipients' connections, with the client repairing any gap by sequence number.**

## 2. Why it exists

**Because ordering is the product.** People judge a chat app on whether messages appear in the order they were sent, and "the reply arrived before the question" is a trust-destroying bug. Ordering cannot come from timestamps (clocks disagree — L66) or from arrival time at a server (two servers, two arrivals). It must come from a **single writer per conversation** assigning a sequence number, and every client must be able to detect and repair gaps.

**Because delivery is at-least-once and duplicates are visible.** Every message must survive: an ACK lost on a flaky mobile network, a reconnect mid-send, a gateway dying with 5,000 sockets. So the client retries, and the server deduplicates on a client-generated message id (L74) — which means the *client* must generate identity, not the server.

**Because offline is the normal case, not the exception.** 31 % of BookNook's messages are delivered more than 5 seconds after they were sent, because one recipient was on a train. That is not an error path; it is the product. It requires durable per-user inboxes, a catch-up protocol ("everything after sequence N"), and push notifications to wake devices (L96).

**Because connections are expensive and fail constantly.** 40 M connections, a 4-hour average session, and a 1.2 % daily node-failure rate means **480,000 reconnections per day** plus the ones from mobile network changes. A reconnect must be cheap: resume from a cursor, not re-download history, and not invalidate presence for everyone.

**And because the read/write ratio is inverted relative to most systems.** Each message is written once and read by every participant on every device — 2.4 participations per message on average, ×1.5 devices. That fan-out, plus the fact that most messages are seen once and never again, is what makes chat a **write-amplification** problem rather than a cache-hit problem.

## 3. Real-world analogy

**A postal service with a notification bell, and a numbered ledger per conversation.**

Imagine every conversation as a **ledger** with numbered lines. When you say something, the clerk writes it on the next line of the ledger — **one clerk per ledger**, so the numbers never collide — and then rings the bell at each participant's door.

The analogy delivers the design's five properties:

**The ledger is the truth; the bell is a convenience.** If the bell fails (you were out), nothing is lost: you come back and read the ledger from your bookmark. This is exactly why messages are persisted before they are pushed, and why the socket can die without loss. **A chat system that treats the socket as the truth loses messages on every reconnect.**

**Numbered lines let you detect a missing page.** If your bookmark is at line 41 and the next page you receive starts at line 44, you know you missed two and can ask for them. That is the sequence number and the gap-repair protocol, and it is what makes ordering robust without trusting clocks.

**The bell is rung at the door, not at every address the person has ever had.** In practice the clerk rings the bell at whichever door you are currently behind (the session registry). If you are behind two doors (phone and laptop), both bells ring — but the ledger is written once. That is the socket-versus-storage separation.

**The clerk does not wait for you to answer before writing the next line.** Messages are appended regardless of delivery, so a slow reader never blocks a fast talker. This is what separates chat from request/response: the write path and the read path are fully decoupled.

And the analogy's instructive failure: **duplicate bells.** The clerk rings twice (a network retry), and you must not write the message down twice. The bell has a serial number — the client message id — so the second ring is recognised as the same event. Without that, every flaky mobile connection produces a duplicate message in the conversation, permanently.

## 4. Detailed technical explanation

### 4.1 Requirements

**Functional.**

| # | Requirement |
|---|---|
| F1 | 1:1 and group conversations (up to 500 members) with text, images, and file references |
| F2 | Real-time delivery to online users; durable delivery to offline users |
| F3 | Per-conversation ordering guaranteed and observable by clients |
| F4 | Delivery and read receipts |
| F5 | Presence (online / last seen), typing indicators |
| F6 | History with pagination, searchable per user |
| F7 | Multi-device: the same message on every device, with independent read cursors |
| F8 | Idempotent sending (retries never duplicate) |
| F9 | Editing, deletion, and reactions |

**Non-functional.**

| # | Requirement | Number |
|---|---|---|
| N1 | Send→deliver latency | p50 < 120 ms, p99 < 500 ms (both parties online) |
| N2 | Concurrent connections | 40,000,000 |
| N3 | Message volume | 2.4 B messages/day |
| N4 | Ordering | Total order per conversation; no gaps after reconnect |
| N5 | Durability | No acknowledged message is ever lost |
| N6 | Availability | 99.99 % send; read paths degrade gracefully |
| N7 | Reconnect | Catch-up from cursor in < 1 round trip, O(missed messages) |

### 4.2 Capacity estimation

```
  CONNECTIONS  40,000,000 concurrent at peak
      per connection: TLS state 3.2 KB + socket buffers 6 KB + framing
      state 0.8 KB ≈ 10 KB ⇒ 400 GB of RAM for connections alone
      ⇒ at 60,000 connections/node (with 64 GB RAM, 40 % headroom for GC),
        667 gateway nodes. At 40,000/node: 1,000 nodes.
      ⇒ THE FLEET SIZE IS SET BY CONCURRENCY, not by message rate.

  MESSAGES     2,400,000,000/day = 27,778/s average
      peak factor 3.4 (evening) = 94,444/s
      fan-out: 2.4 participations × 1.5 devices = 3.6 deliveries/message
      ⇒ 340,000 pushes/s at peak. This is the real write load.

  STORAGE      2.4 B messages × 480 B (text + ids + indexes) = 1.15 TB/day
      = 420 TB/year, hot (12 months) + archive
      90 % of messages are < 400 B and 61 % are never read after 24 h
      ⇒ hot tier (last 90 days, 104 TB) on fast storage; older on cheap

  RECONNECTIONS 40 M × 1.2 % node failure/day + mobile network churn
      measured 480,000 + 1.9 M = 2.38 M/day = 27/s average, 400/s bursts

  PRESENCE     40 M users × a 30 s heartbeat = 1.33 M heartbeats/s
      ⇒ batching is mandatory (§4.7): a 5 s aggregate per user.
```

### 4.3 API

```
CONNECT   WSS /socket?resume={session_token}&cursors={conv_id:seq,...}
  →  server: authenticate, register in the session registry, replay missed
     messages (bounded), then stream live frames.
  ⚠ the resume token encodes (user, device, last known sequence per
    conversation, expiry) so a reconnect is ONE round trip.

POST /v1/conversations/{cid}/messages
     Idempotency-Key: <client-generated UUIDv7>     # the dedupe key (L74)
     { "body": "…", "type": "text",
       "attachments": ["f_…"], "reply_to": "msg_…" }
  →  201 { "message_id": "msg_…", "seq": 98124,
           "server_ts": "…", "status": "stored" }
     errors: 409 duplicate (returns the ORIGINAL seq) · 413 too large
             429 rate limited (per user, per conversation)

GET  /v1/conversations/{cid}/messages?after_seq=98124&limit=100
  →  200 { "messages": [...], "next_seq": 98201, "has_more": false }

POST /v1/conversations/{cid}/read     { "upto_seq": 98201 }
POST /v1/conversations/{cid}/typing   { "state": "start|stop" }   # ephemeral
GET  /v1/conversations?since=<cursor>&limit=50    # conversation list + unread

FRAMES (over the socket, compact binary):
  C→S  SEND {client_id, cid, body…}          S→C  ACK {client_id, seq}
  S→C  MSG {cid, seq, …}                     C→S  RECEIPT {cid, upto_seq}
  S→C  PRESENCE {user, state}                S→C  TYPING {user, cid}
```

### 4.4 Data model

```sql
-- Messages: the durable ledger. Partitioned by conversation hash, then by time.
CREATE TABLE messages (
  conversation_id BIGINT NOT NULL,
  seq             BIGINT NOT NULL,          -- per-conversation, contiguous
  message_id      UUID NOT NULL,            -- globally unique (snowflake/UUIDv7)
  sender_id       BIGINT NOT NULL,
  client_id       UUID NOT NULL,            -- dedupe key from the device
  body            TEXT,
  type            SMALLINT NOT NULL,
  reply_to        UUID,
  server_ts       TIMESTAMPTZ NOT NULL DEFAULT now(),
  edited_at       TIMESTAMPTZ,
  deleted_at      TIMESTAMPTZ,
  PRIMARY KEY (conversation_id, seq)        -- the ordering index AND the
) PARTITION BY HASH (conversation_id);       -- pagination index
CREATE UNIQUE INDEX msg_client ON messages (conversation_id, client_id);
CREATE UNIQUE INDEX msg_id     ON messages (message_id);

-- The sequence allocator: ONE row per conversation ⇒ one writer, no gaps
CREATE TABLE conversation_seq (
  conversation_id BIGINT PRIMARY KEY,
  next_seq        BIGINT NOT NULL,
  writer_epoch    BIGINT NOT NULL           -- fencing for failover (L78)
);

-- Per-device read state (each device has its own cursor)
CREATE TABLE device_cursors (
  user_id BIGINT, device_id UUID, conversation_id BIGINT,
  delivered_upto BIGINT, read_upto BIGINT, updated_at TIMESTAMPTZ,
  PRIMARY KEY (user_id, device_id, conversation_id)
);

-- Membership, with a join sequence so a new member does not see history
CREATE TABLE members (
  conversation_id BIGINT, user_id BIGINT, role SMALLINT,
  joined_at_seq BIGINT NOT NULL,
  left_at TIMESTAMPTZ,
  PRIMARY KEY (conversation_id, user_id)
);

-- Offline inbox: only for users with no live session (see §4.5)
CREATE TABLE inbox (
  user_id BIGINT, device_id UUID,
  conversation_id BIGINT, seq BIGINT,
  enqueued_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (user_id, device_id, conversation_id, seq)
) PARTITION BY HASH (user_id);
```

### 4.5 The send path: sequence allocation is the serialisation point

```
  SENDING A MESSAGE (correct order of operations)
  1. CLIENT generates client_id (UUIDv7) and sends SEND.
  2. GATEWAY authenticates, checks membership and rate limits (L95).
  3. DEDUPE: INSERT ... ON CONFLICT (conversation_id, client_id) DO NOTHING
     returning the existing row ⇒ a retry returns the ORIGINAL seq (409/200),
     never a duplicate message.
  4. SEQUENCE ALLOCATION — the only serialisation in the whole system:
        UPDATE conversation_seq SET next_seq = next_seq + 1
        WHERE conversation_id = $1 RETURNING next_seq - 1;
     ONE row, ONE writer, a 0.3 ms transaction. At 5,000 messages/s on a
     single hot conversation that is 0.3 ms × 5,000 = 1.5 s of work per
     second ⇒ 150 % of the row's capacity ⇒ SPLIT the conversation
     (BookNook caps groups at 500 members and shards above that, §9 step 4).
  5. INSERT the message row (with the allocated seq) in the SAME transaction.
  6. ACK the sender immediately with the seq (the client shows a tick).
  7. ASYNC FAN-OUT: look up members → session registry → gateways → push MSG
     frames; for members with no session, write to `inbox` and enqueue a push
     notification (L96).
  8. UPDATE per-conversation metrics and the unread counters.

  WHY THE SENDER'S ACK PRECEDES THE FAN-OUT: the sender's experience must not
  depend on 500 recipients' connections. Measured: ACK in 14 ms, fan-out p99
  380 ms for a 500-member group. If the ACK waited for fan-out, send latency
  would be a function of group size — and the 95th percentile group would
  make the product feel broken.
```

### 4.6 The delivery path, and the gap-repair protocol

```
  THE SOCKET IS A HINT. Every frame the client receives carries (cid, seq).
  The client applies a message only if seq == expected_next; otherwise:
      seq > expected_next  → GAP: request the missing range explicitly
                             (GET /messages?after_seq=expected&limit=100)
      seq ≤ last_applied   → DUPLICATE: ignore (at-least-once delivery, L73)

  THIS IS WHY CHAT IS CORRECT DESPITE UNRELIABLE DELIVERY:
      · out-of-order frames (two gateways, a proxy, two devices) are repaired
        by the client, deterministically, using the sequence number
      · a lost frame is repaired by the gap request, not by luck
      · a reconnect resumes from the highest contiguous seq, so the resume is
        O(missed), not O(history)

  RESUME ON RECONNECT:
      1. client sends cursors {cid: last_contiguous_seq}
      2. gateway asks the message store for messages after each cursor,
         bounded to 200 per conversation and 2,000 total
      3. if the gap is larger (a week offline), the gateway returns a
         "catch-up required" frame and the client fetches per conversation
         via HTTP, so a long-absent user does not hold a socket busy for
         minutes (measured: 3.1 % of reconnects take this path)

  DELIVERY vs READ RECEIPTS:
      delivered_upto advances when the DEVICE acknowledges (per device)
      read_upto advances when the USER opens the conversation (per user;
                     the minimum of that user's device cursors, so a device
                     that never opened does not hold the read state back)
      ⇒ do NOT conflate them; the difference is what produces "delivered but
        unread", and it is the most-requested feature in chat apps.
```

### 4.7 Presence and typing: deliberately lossy systems

```
  PRESENCE is inherently eventually consistent: a user's state is a fact about
  a connection, and connections fail without notice. Three rules:
    1. a 30 s heartbeat per connection; absence for 90 s ⇒ offline
    2. aggregate before publishing: heartbeat every 30 s, but publish only
       STATE TRANSITIONS in 5 s buckets ⇒ 1.33 M heartbeats/s (115 B/day)
       collapse to ~160 M presence events/day (40 M users × ~4 transitions)
    3. publish presence ONLY to users who are looking: the conversation list
       view subscribes to its visible participants. Measured: a naive
       "broadcast presence to all contacts" design produced 940 M events/day
       for 40 M users; scoped subscriptions produced 41 M/day (−96 %).

  TYPING indicators are fire-and-forget and must NEVER be persisted or
  retried: a stale "typing…" is worse than none. They are dropped under load
  by design (they are the first thing shed, per L76).
```

### 4.8 Multi-device, and the one thing that must not be shared

```
  EVERY DEVICE IS ITS OWN READER:
      · it has its own delivered/read cursor
      · it receives its own copies of messages (fan-out × devices)
      · it repairs its own gaps
  BUT the user sees ONE conversation state. The merge rules are:
      · read_upto(user) = min over the user's active devices? NO — that
        would let a neglected tablet hold read state back forever.
        ⇒ read_upto(user) = min over devices SEEN IN THE LAST 7 DAYS.
        (Measured: the naive "all devices" rule showed 1.9 M unread badges
        that no device could clear — the most-reported bug of the whole
        product.)
      · the conversation list's unread count derives from read_upto(user)
      · message edits and deletes fan out to every device as delta frames
        keyed by (cid, seq, edit_version)
  ⚠ SYNCHRONISING DEVICES DIRECTLY (peer-to-peer) is a trap: it makes the
    message store a cache, breaks ordering guarantees, and turns a phone's
    offline clock into a source of truth. Devices talk to the server.
```

### 4.9 Gateway lifecycle: deploys, drains and the 40 M connection problem

```
  A GATEWAY HAS 60,000 SOCKETS. A DEPLOY MOVES ALL OF THEM. The rules:
    1. DRAIN, don't kill: mark the node "no new connections", send a
       RECONNECT frame with a jittered delay (0–30 s) so 60,000 clients do
       not stampede a single instant (L65 §4.3), then close sockets
    2. server-side reconnect budget: 400 reconnects/s per cluster region;
       clients exceeding it back off exponentially
    3. resumable sessions: the resume token is stateless (signed), so ANY
       gateway can accept the reconnect — no session affinity needed
    4. deploy in waves of 5 % of nodes, with the connection-error rate as the
       canary metric

  MEASURED, before and after:
      naive deploy (kill and restart): 60,000 clients reconnect in 1.8 s;
        the session registry took 14 s to converge, and 41,000 messages were
        delivered late by > 2 s
      drained deploy: zero late messages, reconnect p99 640 ms, and the
        deploy became invisible (the SLO is "a deploy must not be noticeable")
```

## 5. Request / data flow

```
 SEND — one message, two participants online
 ─────────────────────────────────────────────────────────────────────────
 t+0     client A: SEND {client_id, cid: 7781, body}
 t+6 ms  gateway A: auth, membership, rate limit
 t+7 ms  dedupe insert (ON CONFLICT DO NOTHING) and seq allocation, one
         transaction: seq = 98125
 t+9 ms  ACK to A  → the sender sees a tick at 9 ms
 t+11 ms fan-out: read members (2), session registry → both online:
         A on gw-117, B on gw-402
 t+14 ms push MSG{cid, seq, …} to B's gateway
 t+19 ms B's device receives, applies, sends RECEIPT{delivered_upto: 98125}
 t+24 ms B's read receipt (B has the conversation open) → read_upto 98125
 t+26 ms A receives both receipts → "delivered", then "read"
 ⇒ p50 send→deliver 19 ms; p99 380 ms (mobile network). Well inside N1.

 OFFLINE RECIPIENT — the normal case (31 % of deliveries)
 ─────────────────────────────────────────────────────────────────────────
 t+11 ms fan-out finds B has no session
 t+13 ms write to inbox (user_b, device_*, 7781, 98125)
 t+14 ms enqueue a push notification (L96, transactional category)
 t+30 s  B reconnects with cursors {7781: 97990}
 t+34 ms gateway fetches 135 missed messages for that conversation, streams
         them, B applies them in order and advances its cursor
 ⇒ B never knows it was offline, except for the timestamps.

 GROUP OF 500 — the fan-out shape
 ─────────────────────────────────────────────────────────────────────────
 500 members × 1.5 devices = 750 deliveries; 412 online
   the 412 online pushes go out in parallel from 8 gateways (bounded by
   per-gateway concurrency, not by group size)
   the 88 offline members get inbox rows + one push each
   measured p99 fan-out completion: 380 ms; p99.9 1.9 s (one slow gateway)
 ⇒ the group size affects TAIL latency, not the sender's ACK — which is why
   the ACK is emitted before fan-out (§4.5).
```

## 6. ASCII diagram

```
    ┌──────────┐        ┌──────────┐        ┌──────────┐
    │ phone A  │        │ laptop A │        │ phone B  │
    └────┬─────┘        └────┬─────┘        └────┬─────┘
         │ WSS               │ WSS               │ WSS
         ▼                   ▼                   ▼
   ┌───────────────────────────────────────────────────────┐
   │ GATEWAY FLEET — 667–1,000 nodes, 60k sockets each     │
   │ · terminates TLS, holds the socket, no business state │
   │ · resume token is stateless ⇒ any node can accept     │
   │ · drains on deploy: RECONNECT frames, jittered 0–30 s │
   └───────────────┬───────────────────────────────────────┘
                   │  (send, receipts, presence)
                   ▼
   ┌─────────────────────────┐   ┌──────────────────────────────┐
   │ MESSAGE SERVICE         │   │ SESSION REGISTRY             │
   │ · dedupe on client_id   │◄─►│ user_id → [(gateway, device)]│
   │ · seq allocator (1 row  │   │ Redis, TTL = heartbeat       │
   │   per conversation)     │   └──────────────────────────────┘
   │ · message store (PG,    │
   │   partitioned by cid)   │   ┌──────────────────────────────┐
   └───────────┬─────────────┘   │ PUB/SUB FABRIC               │
               │                 │ gateway ⇄ gateway routing    │
               │                 └──────────────────────────────┘
               ▼
   ┌─────────────────────────┐   ┌──────────────────────────────┐
   │ FAN-OUT WORKERS         │   │ INBOX (offline, per device)  │
   │ members → sessions →    │──►│ + push notification enqueue  │
   │ per-gateway batches     │   │   (L96)                      │
   └─────────────────────────┘   └──────────────────────────────┘

   THE TWO LOAD-BEARING PROPERTIES:

   (1) ONE WRITER PER CONVERSATION assigns seq:
        UPDATE conversation_seq SET next_seq = next_seq + 1
          WHERE conversation_id = 7781 RETURNING next_seq - 1;
        ⇒ 0.3 ms, total order, no gaps. The ONLY serialisation point.

   (2) THE CLIENT REPAIRS GAPS BY SEQ:
        apply if seq == expected
        else if seq > expected → fetch the missing range
        else → duplicate, ignore
        ⇒ ordering survives out-of-order sockets, gateways, and reconnects.
          This is why chat works and why the socket is only a hint.
```

## 7. Detailed example

**BookNook's reader-to-author chat** (a "message the author" feature inside the reading app) plus reader-to-reader book-club groups up to 500 members.

```
  measured at the 18-month point:
    40 M concurrent connections at peak (evening, 19:00–22:00 local)
    2.4 B messages/day, 340,000 push deliveries/s at peak
    2.38 M reconnections/day
    group ceiling 500 (above that, fan-out p99 exceeded 2 s)
    31 % of messages delivered > 5 s after send (offline recipients)
```

**The incident that produced the sequence design:**

```
  2025-04, first version ordered by server timestamp:
    · two gateway nodes handling a fast back-and-forth produced messages
      that appeared out of order in 0.4 % of conversations (clock skew of
      8–40 ms between nodes, L66)
    · a reconnect re-downloaded the last 50 messages and applied them again,
      producing duplicates in 1.1 % of sessions
    · users noticed; the phrase "your app scrambles messages" appeared in
      41 reviews
  fix:
    · a per-conversation sequence allocator (one row, one writer)
    · client-generated client_id for dedupe
    · the gap-repair protocol, client-side
  measured after: out-of-order incidence 0.4 % → 0.000 %, duplicates
  1.1 % → 0.000 % (a duplicate is now impossible by construction), reviews
  mentioning scrambling: 41 → 0.
```

**The unread-badge bug (multi-device), and its fix:**

```
  symptom: 1.9 M users reported unread counts they could not clear — a
  conversation showed "3 unread" on every device and opening it changed
  nothing.
  cause: read_upto was computed as the MINIMUM over all of a user's devices,
  including a tablet last seen 14 months ago.
  fix: minimum over devices seen in the last 7 days, with an explicit
  "end session on this device" that zeroes its cursors.
  measured: unread complaints 41,000/week → 210/week, and the remaining ones
  were genuine (a device that had never synced).
```

**The scale ceilings, measured honestly:**

| Dimension | Ceiling found | Cause | Response |
|---|---|---|---|
| Group size | ~500 members | Fan-out p99 2 s at 500 → 6 s at 2,000 | Cap groups; "broadcast" channels use a read-fan-out model instead (§12) |
| Single conversation rate | ~5,000 msg/s | `conversation_seq` row contention (0.3 ms per update) | Cap; high-rate rooms are sharded streams with a merge |
| Connections per node | 60,000 | GC pauses at 70,000 (JVM) / RSS at 64 GB | 667–1,000 nodes; the node count is the cost driver |
| Presence events | Scoped subscriptions required | Naive broadcast = 940 M events/day | Subscribe only to visible participants (−96 %) |
| History pagination | 100 messages/request | Memory per response frame | Cursor pagination by seq, never by timestamp |

## 8. Code or configuration example

```python
# ── The send path: dedupe + sequence allocation in ONE transaction ───────
async def send_message(user, cid: int, body: str, client_id: UUID) -> Message:
    async with db.transaction():
        # 1. membership + rate limit (L95) inside the transaction is too slow;
        #    both are checked before this call and re-validated by the FK/trigger
        # 2. DEDUPE on the client's id — the retry path returns the ORIGINAL
        row = await db.fetchrow("""
            INSERT INTO messages (conversation_id, seq, message_id, sender_id,
                                  client_id, body, type)
            VALUES ($1,
                    (UPDATE conversation_seq SET next_seq = next_seq + 1
                     WHERE conversation_id = $1 RETURNING next_seq - 1),
                    $2, $3, $4, $5, 1)
            ON CONFLICT (conversation_id, client_id) DO NOTHING
            RETURNING seq, message_id, server_ts""",
            cid, new_message_id(), user.id, client_id, body)
        if row is None:                             # duplicate: return original
            old = await db.fetchrow("""SELECT seq, message_id, server_ts
                                       FROM messages
                                       WHERE conversation_id=$1 AND client_id=$2""",
                                    cid, client_id)
            return Message(**old, duplicate=True)
    # 3. ACK first, fan out after (§4.5) — never await recipient delivery
    await ack_sender(user.id, client_id, row["seq"])
    await fanout.enqueue(cid, row["seq"], row["message_id"], user.id)
    return Message(**row)
```

```python
# ── Fan-out: members → sessions → gateways, with an offline fallback ─────
async def fanout(cid: int, seq: int, mid: UUID, sender: int):
    members = await members_cache.get(cid)          # Redis, 60 s TTL
    online: dict[str, list[tuple[int, str]]] = defaultdict(list)
    offline: list[tuple[int, str]] = []

    for m in members:
        if m == sender:
            continue                                # the sender has the ACK
        sessions = await registry.sessions(m)       # user → [(gw, device)]
        if sessions:
            for gw, device in sessions:
                online[gw].append((m, device))
        else:
            offline.append(m)

    # batch per gateway: ONE frame carrying N messages beats N frames
    await asyncio.gather(*[
        bus.publish(f"gw.{gw}", Frame.messages(cid, seq, mid, recipients))
        for gw, recipients in online.items()
    ])
    if offline:
        await inbox.insert_many(cid, seq, offline)
        await notifications.enqueue(cid, seq, offline)   # L96, transactional
```

```go
// ── Client-side application: the gap-repair algorithm, in full ───────────
func (s *Session) OnFrame(f Frame) {
    switch f.Type {
    case FrameMsg:
        exp := s.nextSeq[f.CID]                 // per-conversation expectation
        switch {
        case f.Seq == exp:
            s.store.Apply(f); s.nextSeq[f.CID] = exp + 1
            s.sendReceipt(f.CID, f.Seq)         // delivered_upto
        case f.Seq < exp:
            return                              // duplicate: ignore (L73)
        case f.Seq > exp:
            s.gaps[f.CID] = append(s.gaps[f.CID], f)   // buffer out-of-order
            if len(s.gaps[f.CID]) == 1 {
                go s.fetchRange(f.CID, exp, f.Seq-1)   // repair explicitly
            }
        }
    case FrameCatchupRequired:
        go s.httpCatchup(f.CID)                 // long absence: not on the socket
    }
}

// Resume on reconnect: ONE round trip carrying every cursor
func (s *Session) Resume() {
    req := ConnectRequest{Token: s.resumeToken, Cursors: s.nextSeq}
    s.ws = dial("/socket", req)                // stateless token ⇒ any gateway
}
```

```yaml
# ── Gateway and deploy configuration that keeps reconnects survivable ────
gateway:
  max_connections: 60000            # GC/latency ceiling, not RAM
  heartbeat_seconds: 30
  offline_after_seconds: 90
  send_buffer_frames: 256           # backpressure: a slow client gets closed,
                                    # never allowed to consume node memory
  max_frame_bytes: 65536
drain:
  no_new_connections: true
  reconnect_jitter_seconds: [0, 30]
  reconnect_budget_per_region: 400  # clients/s
  wave_size_percent: 5
presence:
  aggregate_seconds: 5
  subscribers: conversation_visible_only
alerts:
  - alert: MessageNotDeliveredFast
    expr: histogram_quantile(0.99, rate(chat_deliver_seconds_bucket[5m])) > 0.5
    severity: page                # N1 breach: the product feels broken
  - alert: SequenceGapDetected
    expr: increase(chat_gap_repairs_total[5m]) > 100
    severity: ticket              # frames are being lost or reordered
  - alert: GatewayDrainIncomplete
    expr: chat_gateway_open_sockets > 0 and chat_gateway_draining_for_seconds > 120
    severity: page                # a stuck drain blocks the deploy
  - alert: ReconnectStorm
    expr: rate(chat_connections_total[1m]) > 800
    severity: page                # a node died, or a deploy stampeded
```

## 9. Numerical example

BookNook: 40 M connections, 2.4 B messages/day, 500-member groups.

**Step 1 — the fleet size comes from connections, not messages.**

```
  connections      40,000,000
  per-node ceiling 60,000 (memory 40 GB + GC headroom; measured)
  nodes needed     40,000,000 / 60,000 = 667
  + 25 % headroom for deploy waves, node loss and peak  = 834 nodes
  cost            834 × £85/month ≈ £70,890/month
  ⇒ compare the MESSAGE service: 94,444 msg/s peak across ~40 pods
    ≈ £3,400/month. THE GATEWAY FLEET IS 96 % OF THE COMPUTE BILL, and it is
    paying for IDLE SOCKETS, not for work. That framing is what drives design
    decisions like "should presence be a separate lightweight tier?"
    (BookNook moved presence to a UDP-ish lightweight protocol on the same
    socket and reduced per-connection memory from 14 KB to 10 KB, saving
    £28,000/month of node cost).
```

**Step 2 — the sequence allocator's ceiling, and the honest arithmetic.**

```
  one conversation's `conversation_seq` row: 0.3 ms per UPDATE (fsync-bound
  in a synchronous-commit setup; 0.08 ms with group commit)
  ⇒ max messages/s on ONE conversation:
        synchronous commit:  1 / 0.0003 = 3,333/s
        group commit:        1 / 0.00008 = 12,500/s
  BookNook's busiest conversation (a live author Q&A): 3,900 msg/s peak
  ⇒ ABOVE the synchronous limit, BELOW the group-commit limit.
  response: (a) enable group commit for sequence allocation (a deliberate
  durability trade, acceptable because the message row insert is the durable
  record), and (b) cap a "chat" at 3,000 msg/s and route anything faster to a
  broadcast/stream model with a merge (§12).
  ⇒ 3,900/s would have failed 15 % of sends at peak without the fix, and the
    failure mode is a 500 on a *message send* — the least acceptable 500 in
    the product.
```

**Step 3 — fan-out cost per message, in deliveries and bytes.**

```
  average message: 2.4 participations × 1.5 devices = 3.6 deliveries
  frame size: 180 B header+ids + body (median 140 B) = 320 B
  ⇒ per message: 3.6 × 320 B = 1.15 KB of egress
  at 94,444 msg/s peak: 108 MB/s = 870 Mbit/s of egress
  monthly egress: 2.4 B × 1.15 KB × 30 = 82.8 TB ≈ £600/month at
      £0.0072/GB within a region (and 0 for same-AZ traffic)
  ⇒ the egress is trivial; THE COST IS THE IDLE CONNECTION. Again: chat is
    a concurrency business, not a throughput business.
```

**Step 4 — the group-size ceiling, derived rather than guessed.**

```
  fan-out work per message = members × devices
     500 members → 750 deliveries
  fan-out throughput per worker: 40,000 deliveries/s (batched per gateway)
  ⇒ 500-member message consumes 750/40,000 = 18.75 ms of worker time
  at 40 concurrent groups of 500 messaging, arrival 20 msg/s per group
  total arrival to fan-out = 800 msg/s × 750 = 600,000 deliveries/s
  ⇒ 600,000 / 40,000 = 15 workers busy — fine.
  BUT p99 fan-out completion at 500 members measured 1.9 s (one slow
  gateway; a single batch waits for the slowest publish).
  ⇒ the tail, not the average, sets the ceiling: at 2,000 members, p99 was
    6.4 s, which violates N1 for recipients. Hence the 500 cap, and
    "channels" (read-fan-out) above it.
```

**Step 5 — reconnect capacity during a deploy.**

```
  834 nodes at 60,000 sockets each = 50.0 M socket slots
  cluster reconnect budget: 400/s/region × 6 regions = 2,400/s
  ⇒ moving ALL sockets takes 50,000,000 / 2,400 = 20,833 s = 5.8 hours,
    which is why the budget — not the wave size — is the lever.
  waves of 2 % (17 nodes, 1.02 M sockets): 1,020,000 / 2,400 = 425 s per
    wave × 50 waves = 5.9 h. Same total, as it must be: the reconnect budget
    is the throughput of the whole operation.
  THE FIX, and it is not cosmetic: raise the effective reconnect rate by
  sharding it per gateway. A drained node's clients reconnect to 833 other
  nodes, and each of those can accept a small number per second without
  hurting anything — so the budget is per SOURCE-cluster, not global, and a
  drained node can be recovered in 90 s while the cluster's aggregate
  acceptance stays under the level that would stress the session registry.
  ⇒ measured with per-node budgets: a full rolling deploy takes 24 minutes,
    zero late messages, reconnect p99 640 ms, and the session registry peaks
    at 31 % of its capacity instead of 94 %.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **Persist-then-push** | No message loss ever | Storage; a write before every delivery | **Always** |
| **Push-then-persist** | Lower send latency | Loss on any crash | Never for user messages |
| **Per-conversation sequence** | Total order, gap detection | One serialisation point per conversation | **Always** |
| **Timestamps as order** | No allocator needed | Out-of-order messages under skew (§7) | Never |
| **Single writer per conversation** | Simple, correct | A per-conversation rate ceiling (§9 step 2) | Groups up to a few thousand msg/s |
| **Fan-out on write** | Fast reads; simple clients | Write cost ∝ members (caps group size) | Groups ≤ ~500 |
| **Fan-out on read** | Unlimited members (channels) | Slow reads; complex history | Broadcast channels, > 500 |
| **Long-lived sockets** | Real-time, low overhead | 667+ nodes of idle connections; drain complexity | **Always for chat** |
| **Long-poll** | Simpler infra, no socket state | Higher latency and per-request overhead | Small scale, or web-only |
| **Scoped presence subscriptions** | 96 % fewer events | Subscriber bookkeeping | Always |
| **read_upto = min over active devices** | Correct badges | A rule about "active" (7 days) | Always |
| **Idle-device inclusion in read state** | Simplicity | Unclearable unread badges (§7) | Never |
| **Group cap at 500** | Meets the latency SLO | A product limitation | Until you build read-fan-out |

## 11. Failure scenarios

**11.1 — Out-of-order messages.** Timestamp ordering produced 0.4 % scrambled conversations (§7). **Fix:** per-conversation sequence allocation; client-side gap detection.

**11.2 — Duplicate messages on retry.** A lost ACK plus a retry created two messages, permanently. **Fix:** client-generated `client_id` with a unique index; a retry returns the original `seq`.

**11.3 — Unclearable unread badges** (§7). 1.9 M users with unread they could not clear. **Fix:** `read_upto` = min over devices seen in 7 days, plus an explicit "end session".

**11.4 — Deploy storms.** Killing nodes lost 41,000 messages' timeliness and thrashed the session registry (§4.9). **Fix:** drain with jittered RECONNECT frames, stateless resume tokens, 2 % waves, per-node reconnect budgets.

**11.5 — A slow client eats the node.** One client with a 4 MB/s downstream consumed the node's send buffer until the process OOMed. **Fix:** bounded per-connection send buffers; close the socket on overflow and let the client resume from its cursor (which is safe *because* the store is the truth).

**11.6 — Session registry divergence.** A node died without cleaning its registry entries; 60,000 users were "online" on a dead node, so their messages went nowhere for 90 s. **Fix:** registry entries are heartbeats with a TTL (90 s), and fan-out falls back to the inbox when a push fails — measured recovery: 90 s of delayed delivery, zero loss.

**11.7 — The hot conversation.** An author Q&A at 3,900 msg/s hit the allocator's row contention; 15 % of sends 500'd. **Fix:** group commit for the allocator; a 3,000 msg/s cap per conversation with a documented fallback to read-fan-out mode.

**11.8 — Presence broadcast flood.** Naive presence broadcast to all contacts generated 940 M events/day and saturated the pub/sub fabric at peak. **Fix:** subscribe only to participants visible in the current view; a 5 s aggregation bucket.

**11.9 — History pagination by timestamp.** Two messages sharing a millisecond caused a page to repeat or skip rows. **Fix:** cursor pagination by `seq` (which is unique per conversation by construction), never by timestamp.

## 12. Alternatives

| Instead of | Consider | When |
|---|---|---|
| Own socket infrastructure | A managed realtime service | You need chat, not a realtime platform |
| Fan-out on write | Read-fan-out channels | Groups above ~500, or broadcast |
| Custom protocol | A standard messaging protocol | Federation or interoperability matters |
| Per-device inbox materialisation | A shared conversation store + per-device cursors | Almost always — the inbox is only for offline wakeups |
| Sockets | Server-sent events + HTTP POST | Web-only, low message rate, simpler infra |
| A sequence allocator per conversation | A global sequence with per-conversation filtering | Very low volume; simpler, but no per-conversation contiguity |
| Own push notifications | The notification platform (L96) | Always — do not rebuild push |

## 13. When to use it

- **Persist-then-push with per-conversation sequencing**, for any chat, collaborative comment stream, or multi-user state machine with a visible order.
- **A separate gateway tier** when connections outnumber messages by 400:1, because the cost model demands it.
- **Gap-repair protocols** whenever delivery can be reordered or lost.
- **Inbox + push notifications** whenever offline is a normal case.
- **Scoped presence and ephemeral typing**, always.

## 14. When *not* to use it

- **Websockets for request/response APIs** — an HTTP call is simpler and more cacheable.
- **Chat infrastructure for notifications** — use the notification platform (L96).
- **Fan-out on write for channels with 100,000 members** — use read-fan-out.
- **Long-lived sockets for a low-traffic admin tool** — polling is fine.
- **Peer-to-peer device sync** — it breaks ordering and makes a phone the source of truth.

## 15. Common mistakes

1. **Ordering by timestamp** (§11.1).
2. **Server-generated message ids only**, so retries duplicate (§11.2).
3. **Socket treated as the source of truth**, so reconnects lose messages.
4. **Awaiting fan-out before ACKing the sender** (§4.5).
5. **No gap detection** on the client, so out-of-order frames are applied in arrival order.
6. **Unbounded per-connection send buffers** (§11.5).
7. **Read state as the min over all devices** (§11.3).
8. **Presence broadcast to all contacts** (§11.8).
9. **Killing gateway nodes on deploy** (§11.4).
10. **Pagination by timestamp** (§11.9).
11. **Session registry without TTLs**, so dead nodes look live (§11.6).
12. **No per-conversation rate ceiling**, so a hot room fails sends (§11.7).
13. **Billing surprise**: sizing for messages rather than for connections (§9 step 1).
14. **Typing indicators persisted and retried** — stale "typing…" forever.

## 16. Interview perspective

**What is actually being tested.** Whether you separate the **socket** from the **source of truth**, whether you can name the ordering primitive, and whether you know the cost of 40 M idle connections. Strong candidates: persist-then-push, per-conversation sequence, client-side gap repair, client-generated ids, drain-not-kill deploys. Weak candidates: "use Redis pub/sub and websockets".

**Answers that land well:**

- *"Design chat. Start with ordering."* — "A single writer per conversation assigns a monotonically increasing sequence number — one row, one update, 0.3 milliseconds. Timestamps cannot do it: two gateways with 8 milliseconds of skew produced out-of-order messages in 0.4 % of conversations, which is the bug users describe as 'your app scrambles messages'. With a sequence, every client can detect a gap and ask for the missing range explicitly."
- *"What happens when the socket dies?"* — "Nothing, because the socket is a hint. Messages are persisted first, then pushed. On reconnect the client sends its cursor per conversation and gets everything after it — one round trip, O(missed). If a long absence makes that too big, the server returns 'catch-up required' and the client fetches over HTTP so it does not hold a socket busy for minutes."
- *"What's expensive?"* — "The connections, not the messages. Forty million concurrent sockets at ten kilobytes each is 400 gigabytes of RAM, so 667 nodes with headroom — about £70,000 a month — while the message service itself is 40 pods. Chat is a concurrency business, which is why presence is scoped to what's visible and why per-connection memory is a first-class engineering target."
- *"How do you handle a 500-person group?"* — "Fan-out on write: up to 500 members, 1.5 devices each, 750 deliveries, p99 380 milliseconds. Above 500 the tail breaks — 2,000 members measured 6.4 seconds — so channels use read-fan-out instead, where the message is written once and readers merge. The group cap is derived from the latency SLO, not from an arbitrary limit."
- *"Deploys?"* — "Drain, don't kill. Mark the node, send RECONNECT frames with a zero-to-thirty-second jitter so sixty thousand clients do not stampede one instant, and take the resume token as a stateless signed blob so any node can accept the reconnect. Waves of two percent, and the canary metric is late messages, not error rate."

**Common interview tasks:** design send/deliver; guarantee ordering; handle offline and reconnect; design receipts and presence; multi-device; size the fleet; plan deploys.

**Red flags interviewers watch for:**

- Timestamp ordering.
- No dedupe on client retry.
- "Redis pub/sub is the message store."
- No offline story.
- No ordering discussion at all.
- Sizing by message rate and then being surprised by connection count.
- Killing nodes on deploy.
- Presence broadcast to everyone.
- Unbounded per-connection buffers.

## 17. Knowledge check

**Conceptual**

1. Explain why the socket cannot be the source of truth, and enumerate the three failure modes that follow from treating it as one. Then explain the ordering primitive and why timestamps are not an acceptable substitute.
2. Explain the gap-repair protocol from the client's point of view, including all three cases (`==`, `<`, `>` expected). Then explain what a reconnect costs in the best and worst cases, and how the "catch-up required" path bounds it.
3. Explain why the gateway fleet is sized by connections rather than messages, and compute the fleet for 40 M connections at 60,000 per node. Then explain how presence scoping and per-connection memory each reduce the bill.

**Practical scenario**

4. Design BookNook's chat: 40 M concurrent connections, 2.4 B messages/day, groups up to 500, multi-device, receipts, presence, offline delivery via the notification platform (L96), and a deploy that must be invisible. Give the requirements and capacity tables, the API and frames, the schema including the allocator and cursors, the send and delivery paths with every ordering rule, the gap-repair algorithm, the presence and typing design, the multi-device merge rules, the gateway lifecycle and drain plan, the alerts, the cost breakdown, and the incident replays. Then state what changes if groups must support 50,000 members.

**Calculation**

5. 40 M concurrent connections at 10 KB each. (a) Compute the total RAM for connection state. (b) At 60,000 connections per 64 GB node, compute the node count and, with 25 % headroom, the deployable fleet. (c) Compute the monthly cost at £85/node. (d) If per-connection memory drops to 8 KB, recompute and state the saving.

6. A fan-out worker sustains 40,000 deliveries/s. A 500-member group has 1.5 devices per member. (a) Compute deliveries per message and the worker time consumed. (b) At 20 msg/s per group and 40 concurrent groups, compute the total delivery rate and the workers required. (c) Compute the same for 2,000-member groups and state the conclusion about group size. (d) If the observed p99 fan-out is 1.9 s at 500 members, state the dominant cause and one mitigation.

**Progress checklist**

- Phase 1 — Foundations (L1–17): **complete** ✅
- Phase 2 — Scaling (L18–39): **complete** ✅
- Phase 3 — Data and Storage (L40–63): **complete** ✅
- Phase 4 — Distributed Systems (L64–92): **complete** ✅
- Phase 5 — Complete system designs (L93–106):
  - [x] 1. Design Pastebin (L93)
  - [x] 2. Design a URL shortener (L94)
  - [x] 3. Design a rate limiter (L95)
  - [x] 4. Design a notification service (L96)
  - [x] 5. Design search autocomplete (L97)
  - [x] 6. Design file storage (L98)
  - [x] 7. Design a web crawler (L99)
  - [x] **8. Design real-time chat (L100) ← you are here**
  - [ ] 9. Design a news feed (L101)
  - [ ] 10. Design video streaming (L102)
  - [ ] 11. Design ride sharing (L103)
  - [ ] 12. Design e-commerce and inventory (L104)
  - [ ] 13. Design payment processing (L105)
  - [ ] 14. Design a metrics and logging platform (L106)

**NEXT LESSON: Phase 5, Topic 9 — Design a news feed**

---

---

# Lesson 101 — Design a News Feed

*Phase 5, Topic 9*

## 1. Simple definition

**The problem.** Given a user who follows N other users, produce a ranked, paginated, always-scrollable list of recent posts from those follows — in tens of milliseconds, for 300 million users, where one user may have 200 follows and another may have 90 million.

**Why that asymmetry is the entire design.** Two forces pull in opposite directions:

- **Precompute** (fan-out on write): when a post is created, push its id into every follower's feed list. Reads become a single lookup of a prebuilt list. Cost is paid at write time and scales with **follower count** — a celebrity with 90 M followers makes one post cost 90 M writes.
- **Compute on read** (fan-out on read): at read time, gather recent posts from everyone the user follows, merge, rank. Writes are trivially cheap. Cost is paid at read time and scales with **follow count** × read rate.

The answer is not either. It is **a hybrid, partitioned by user type**, and the partitioning threshold is derived from arithmetic, not from taste (§4.5).

Terms defined before use:

- **Feed / timeline**: the ordered, ranked list of posts shown to a user.
- **Fan-out on write (push)**: precomputing each follower's feed at post time.
- **Fan-out on read (pull)**: assembling the feed at read time.
- **Celebrity / high-fan-out account**: an account whose posts are too expensive to push.
- **Feed list (per user)**: a capped, ordered list of post ids in a fast store (Redis/Redis-like), typically the most recent ~800–1,000.
- **Candidate generation then ranking**: retrieve ~1,000 candidates cheaply, then score with a model and return the top 20.
- **Backfill / merge**: mixing pulled celebrity posts into a pushed feed at read time.
- **Cursor pagination**: paginating by a stable rank key, never by offset.
- **Feed freshness vs ranking stability**: a feed that reorders while you scroll is a bug.

The one sentence to carry out of this lesson: **a news feed is a write-amplification problem you solve twice — push for the many, pull for the few, and merge at read time — with the split point chosen by the arithmetic of follower counts, and pagination that never moves under the reader's thumb.**

## 2. Why it exists

**Because the numbers make a single approach impossible.** BookNook's social reading feed: 300 M users, 40 M posts/day, 2.1 B feed reads/day. With pure write-fan-out, the average post (180 followers) is cheap — 180 list inserts — but the tail is fatal: the 12 accounts with over 10 M followers generate **120 M list inserts/day between them**, against a total capacity of roughly 400 M/s of Redis list operations cluster-wide… and one 90 M-follower post alone is 90 M inserts, which at 100,000 ops/s takes **15 minutes** (§9 step 2). With pure read-fan-out, a user following 2,000 accounts needs to fetch and merge 2,000 recent-post lists per page — impossible at 24,000 reads/s.

**Because the read path must be fast and predictable.** p99 < 200 ms for a feed page, including ranking, with a 61 % mobile share where 200 ms is already a third of the budget. That forbids merging 2,000 sources at read time, and it forbids ranking 40,000 candidates.

**Because ranking beats chronology, and ranking needs candidate sets.** Users engage 3–8× more with a ranked feed than a chronological one (BookNook measured 4.1× on follows-recommendations and 2.8× on book discussions). But ranking needs a bounded candidate set (1,000–2,000) and a feature store; the architecture must therefore produce *candidates* cheaply and *scores* only for those.

**Because pagination is a correctness property.** Offset pagination over a list that changes between requests produces duplicates and skipped posts. A user who scrolls and sees the same post twice, or misses one, notices — and it is the most common complaint about feed products. Cursors must be stable.

**And because the feed is where the product's economics live.** Feed ranking drives session length, which drives ad inventory and subscription conversions. That means the feed is iterated constantly — so the pipeline must separate **retrieval** (infrastructure, stable) from **ranking** (models, changed weekly). Coupling them is how a feed becomes unmaintainable.

## 3. Real-world analogy

**A newspaper delivered to your door, printed by a newsroom that also runs a wire service.**

**Fan-out on write is the personalised edition.** Each morning the newsroom assembles your copy from everything your favourite columnists wrote, prints it, and delivers it. When you open the door, the paper is **already there** — that is the precomputed feed, and it is why reading is instant.

**The celebrity column is different.** A column read by ninety million people cannot be reprinted into ninety million personalised editions by lunchtime. So the newsroom does not: it prints **one** edition of that column and delivers it as an insert — every subscriber gets the same insert, added to their paper as they read. That is fan-out on read for high-fan-out accounts, and **the insert is the same object for everyone**, which is exactly why it is cheap.

**The split is arithmetic, not editorial.** The newsroom decides which columns are "inserts" by counting subscribers: below some threshold, reprinting is cheaper than keeping a separate insert pipeline; above it, the reverse. **The threshold moves as the audience moves**, which is why it must be computed from follower counts rather than hard-coded per celebrity.

**And the paper never reflows under your thumb.** Once printed, page 3 stays page 3 for that edition. If you stop reading and come back an hour later, you get the **next** edition — not a reordered version of the one you were reading. That is cursor pagination and rank stability: a feed that reorders mid-scroll is a newspaper that reprints itself while you hold it.

The analogy's instructive failure: **the insert can be stale.** A celebrity insert prepared at 06:00 does not contain a 09:00 column. So the merge must happen at read time (or be refreshed frequently), and its freshness is a deliberate trade — the insert is cheaper *and* slower, and everyone must know that.

## 4. Detailed technical explanation

### 4.1 Requirements

**Functional.**

| # | Requirement |
|---|---|
| F1 | A feed of posts from accounts the user follows, ranked by relevance with recency as a strong prior |
| F2 | Infinite scroll with stable, cursor-based pagination |
| F3 | New posts appear on a "refresh" action, not silently mid-scroll |
| F4 | Posts from muted/blocked accounts are excluded, always |
| F5 | Edited and deleted posts must disappear from feeds |
| F6 | Ads/recommendations may be inserted, clearly separable from follows |
| F7 | Feed responds within 200 ms p99 at 24,000 reads/s |

**Non-functional.**

| # | Requirement | Number |
|---|---|---|
| N1 | Read latency | p99 < 200 ms (retrieval < 40 ms, ranking < 60 ms) |
| N2 | Write→visible | p95 < 30 s for a normal account; < 2 min for a celebrity post |
| N3 | Scale | 300 M users, 2.1 B feed reads/day, 40 M posts/day |
| N4 | Storage | ≤ 1,000 ids per precomputed feed (capped list) |
| N5 | Availability | Feed degrades to a cached/stale page rather than failing |
| N6 | Consistency | No duplicate or skipped posts within a scroll session |
| N7 | Deletions | Removed from feeds within 60 s |

### 4.2 Capacity estimation

```
  USERS        300,000,000 (120 M daily active)
  POSTS        40,000,000/day = 463/s average, peak 1,850/s
  READS        2,100,000,000 feed-page requests/day = 24,306/s average
               peak factor 3.1 = 75,349/s
  FOLLOW GRAPH 300 M users × 180 average follows = 54 B edges
               power law: the top 12 accounts have > 10 M followers each;
               the top 1,000 have > 1 M; the median user has 180.

  WRITE FAN-OUT, if applied to everything:
      Σ followers of all posting accounts per day.
      average post → 180 list inserts ⇒ 40 M × 180 = 7.2 B inserts/day
      = 83,333/s average, 333,000/s at peak
      + the tail: the top 12 accounts post 40 times/day each at 10 M+ followers
        ⇒ 12 × 40 × 10 M = 4.8 B inserts/day from 480 posts
      ⇒ total ≈ 12 B inserts/day, 139,000/s average, 556,000/s peak, and
        the tail posts are 40 % of it.
      BookNook's measured capacity for feed-list inserts: 400,000/s
      ⇒ PURE PUSH DOES NOT FIT AT PEAK, and one 90 M-follower post would
        take 90 M / 400,000 = 225 s of cluster capacity on its own.

  STORAGE (precomputed feeds)
      120 M daily-active feeds × 1,000 ids × 8 B = 960 GB in a fast store,
      plus 300 M total = 2.4 TB if precomputed for everyone.
      ⇒ precompute for ACTIVE users only, and rebuild on demand: 960 GB
        (Redis-class) ≈ £18,000/month. This is the single biggest line item.

  READ PATH
      per feed page: 1 push-list read (1,000 ids) + 1 pull of ~120 celebrity
      accounts' recent posts + ranking over ~1,500 candidates
      ⇒ 4 Redis ops + 1 feature-store batch + 1 model call ≈ 34 ms p50
```

### 4.3 API

```
GET /v1/feed?cursor=<opaque>&limit=20
  →  200 { "items": [
             { "post_id": "p_…", "author_id", "rank_score": 0.87,
               "reason": "followed_author|recommended", "inserted_at" },
             … 20 items … ],
           "next_cursor": "eyJ0IjoxNzMw…",   # opaque: (rank_key, post_id)
           "refresh_after": "2026-10-30T12:00:00Z" }

  ⚠ the cursor encodes the RANK KEY and the LAST POST ID, plus the feed
    SNAPSHOT id — so the next page is consistent with the page you are
    reading, even if new posts arrived in between (§4.7).

POST /v1/posts
     Idempotency-Key: <uuid>
     { "body": "…", "attachments": [...], "visibility": "followers" }
  →  201 { "post_id": "p_…", "fanout": "push|deferred" }   # deferred for
                                                           # high-fan-out
DELETE /v1/posts/{id}     → 202    # removal from feeds within 60 s (N7)
POST /v1/feed/mark-seen   { "upto_cursor": "…" }          # for freshness
POST /v1/users/{id}/follow  /  unfollow                   # graph edits

GET /internal/feed/{user_id}/debug     # ops: why is this post (not) here?
```

### 4.4 Data model

```sql
-- Posts: the source of truth. Immutable except for edit/delete metadata.
CREATE TABLE posts (
  id            BIGINT PRIMARY KEY,          -- snowflake: time-sortable
  author_id     BIGINT NOT NULL,
  body          TEXT,
  visibility    SMALLINT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL,
  deleted_at    TIMESTAMPTZ,
  edit_version  INTEGER NOT NULL DEFAULT 1
) PARTITION BY RANGE (created_at);           -- daily partitions

-- The social graph. Two directions, both needed.
CREATE TABLE follows (
  follower_id  BIGINT NOT NULL,
  followee_id  BIGINT NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (follower_id, followee_id)
);
CREATE TABLE followers (
  followee_id  BIGINT NOT NULL,
  follower_id  BIGINT NOT NULL,
  PRIMARY KEY (followee_id, follower_id)     -- for fan-out AND follower counts
);

-- High-fan-out accounts: the PULL set. Membership is data, not config.
CREATE TABLE celebrity_accounts (
  author_id      BIGINT PRIMARY KEY,
  follower_count BIGINT NOT NULL,
  promoted_at    TIMESTAMPTZ,                -- crosses the threshold upward
  demoted_at     TIMESTAMPTZ,                -- and back down again
  threshold_used BIGINT NOT NULL             -- so the decision is auditable
);

-- Post bodies and counters live in a KV store for the read path
-- (post_id → {author, body, media, counters}); the feed stores only ids.
```

**The feed list itself (not SQL — this is a list store):**

```
  feed:{user_id}  → sorted set or capped deque of post ids, newest ~1,000
                    score = post_id (snowflake ⇒ time-ordered) for retrieval,
                    ranking happens after retrieval, not in the list.
  ⇒ storing ids only keeps a 1,000-entry feed at 8 KB; storing bodies would
    make it 400 KB and multiply the storage by 50×.
```

### 4.5 The hybrid: where the push/pull boundary goes

```
  COST PER POST, both strategies (measured per 1,000 followers):
      PUSH: 1,000 list inserts ≈ 12 ms of cluster capacity
            + the read is O(1) — one list read
      PULL: 0 at write time, but the READ must fetch this author's recent
            posts for every reader ⇒ 1,000 followers × 2.1 reads/day
            = 2,100 fetch operations/day for that author.

  THE DECISION RULE (per author, recomputed daily):
      push_cost   = followers × (posts/day) × insert_cost
      pull_cost   = followers × (feeds_read/day) × fetch_cost
      Both scale with followers ⇒ the ratio is what matters, and PUSH wins
      when insert_cost < fetch_cost × (reads per follower per post)…
      measured: a list insert costs 0.3 µs of cluster capacity; a pull-side
      fetch (one author's recent-post list, merged) costs 41 µs.
      ⇒ PUSH is ~137× cheaper per follower-post, so push wins until the
        WRITE BURST becomes unacceptable.

  THE REAL CRITERION IS WRITE BURST, not average cost:
      a 90 M-follower post = 90 M inserts = 225 s of the entire cluster's
      insert capacity. That is a denial of service caused by one user.
  ⇒ THRESHOLD: promote an author to the PULL set when
        followers × peak_posts_per_second > WRITE_BURST_BUDGET
      with WRITE_BURST_BUDGET = 5 % of cluster insert capacity (20,000
      inserts/s sustained for no more than 30 s).
      measured threshold: 1,000,000 followers. 4,100 accounts exceed it,
      holding 71 % of all follow edges.
  ⇒ BookNook pushes for 299,995,900 accounts and pulls for 4,100.

  READ-TIME MERGE (the hybrid's cost):
      candidates = push_list(user)[0:1,000]
                 ∪ pull_recent(pull_set(user), limit=120 authors × 20 posts)
                 ∪ recommendations (≤ 5% of slots)
      ⇒ 1,000 + 2,400 candidates, ranked to 20. The merge is bounded because
        the PULL set per user is small: a user follows at most a handful of
        the 4,100, and the merge caps at 120 of them by recency of post.
```

### 4.6 The write path: fan-out, with all of its hazards

```
  POST CREATED → fan-out decision
    · author in the PULL set → write the post, publish an event, DONE.
      followers will merge it at read time. p95 visible in < 2 min (bounded
      by the pull cache's TTL, §4.7).
    · otherwise → enqueue a fan-out job with the follower list, processed in
      BATCHES of 5,000 with a per-batch rate limit.

  HAZARDS, each with a measured incident behind it:
   1. WRITE AMPLIFICATION AT PEAK: 12:00 and 19:00 have 3× the posts; the
      fan-out queue must be *priority-ordered* (a post from an account whose
      followers read feeds soonest goes first) and BACKPRESSURED (L76): when
      the queue exceeds 30 s of work, new posts are marked `deferred` and
      served by a short-lived pull path instead of pushed. Correctness is
      preserved because the read path can always fall back to pulling.
   2. INACTIVE FOLLOWERS: 62 % of followers are inactive (no read in 30
      days). Pushing to them is pure waste.
      ⇒ fan out only to ACTIVE followers (a bitmap updated daily), and
        REBUILD a feed from the graph the first time an inactive user
        returns (§4.8). Measured: 12 B inserts/day → 4.6 B (−62 %).
   3. DELETIONS AND EDITS: a pushed id must disappear within 60 s (N7).
      ⇒ do not delete from 300 M lists. Instead, filter at read time with a
        tombstone set (a Bloom filter of recent deletions, hydrated every
        10 s) — O(1) per candidate, and it works for the pull path too.
   4. UNFOLLOW: pushed ids from an unfollowed account must vanish.
      ⇒ read-time filter against the follow set (already loaded for the pull
        path), not a retroactive list edit.
   5. FAN-OUT FAILURE: a batch dies mid-way ⇒ some followers have the post,
      some do not. The job is idempotent (list add of an id is idempotent),
      so it simply retries from the last checkpointed follower.
```

### 4.7 The read path: candidates, ranking, and stable pagination

```
  STEP 1 — RETRIEVE (budget 40 ms, no model, no joins)
      a) push_list = ZRANGEBYSCORE feed:{uid} +inf -inf LIMIT 0 1000
      b) pull_list = for each of up to 120 pull-set follows:
             recent_posts:{author} (a capped list of their last 20 post ids)
         merged by recency ⇒ 2,400 ids → take the newest 1,000
      c) union, dedupe, drop tombstones (Bloom), drop muted/blocked,
         drop already-seen (the client's cursor state)
      ⇒ ~1,500 candidates in 11–18 ms.

  STEP 2 — HYDRATE (budget 20 ms)
      one batched multi-get for post bodies + counters (KV), one batched
      feature fetch (author affinity, recency, engagement priors)

  STEP 3 — RANK (budget 60 ms)
      score = w1·recency + w2·affinity(user→author) + w3·predicted_engagement
              + w4·diversity_penalty + w5·freshness_bonus
      · recency is a strong prior: half-life 6 h for social posts
      · diversity: no more than 3 consecutive posts from one author
      · a deterministic tie-break on post_id so ranks are stable
      ⇒ top 20, with their scores.

  STEP 4 — PAGINATE STABLY (the part everyone gets wrong)
      cursor = (snapshot_id, rank_key of the 20th item, post_id)
      the next page re-ranks the SAME CANDIDATE SNAPSHOT and returns items
      with rank_key < cursor.rank_key.
      ⇒ NO duplicates, NO skipped posts, even if 400 new posts arrived.
      ⚠ the snapshot lives 10 minutes in a per-user cache keyed by
        snapshot_id (800 candidates × 8 B = 6.4 KB). After 10 minutes the
        cursor expires and the client is told to refresh — which is honest,
        and is why the API returns `refresh_after`.

  FRESHNESS: "pull to refresh" builds a NEW snapshot. A live "N new posts"
  banner is a separate cheap count (the push list's head minus the seen
  cursor), never a mid-scroll reorder.
```

### 4.8 Cold feeds, staleness and rebuilds

```
  A user returns after 40 days:
    · their precomputed list was evicted (we only keep active users' feeds)
    · they follow 180 accounts, 4 of which are pull-set
    · rebuild: read each followee's recent_posts (180 × 1 KV get, batched in
      3 multi-gets = 14 ms), merge, rank, write the list
    ⇒ measured cold-feed p99: 410 ms (vs 34 ms warm). Acceptable once per
      session, so the read path has a "cold" mode with a 1 s deadline and a
      chronological fallback if ranking overruns.

  STALENESS BOUND: with a 10-minute snapshot and a 2-minute pull cache, the
  worst-case age of a celebrity post on a cold page is 2 minutes (N2 allows
  exactly that). State the number; it is a product decision disguised as a
  cache TTL.
```

### 4.9 Ranking is a separate, iterable subsystem

```
  SEPARATION OF CONCERNS, enforced in code:
      RETRIEVAL produces a candidate set (infrastructure, stable for years)
      RANKING consumes candidates + features and produces an order
              (models, changed weekly)
  WHY: BookNook changed its ranking model 31 times in a year. Each change
  needed: offline evaluation (NDCG@20 on a held-out week), a shadow run on
  1 % of traffic (log-only), then a 5 % A/B with a guardrail on
  "feed depth" and "scroll-back rate". None of that is possible if ranking
  is entangled with retrieval.
  GUARDRAILS (measurable, and they must be, or the model optimises the wrong
  thing): session length, 7-day return rate, report rate, "hide post" rate,
  diversity of authors shown, and time-to-first-meaningful-interaction.
```

## 5. Request / data flow

```
 A USER OPENS THE FEED (warm, page 1)
 ─────────────────────────────────────────────────────────────────────────
 t+0     GET /v1/feed (no cursor ⇒ new snapshot)
 t+3 ms  retrieve: ZRANGE feed:{uid} 0..999                    [3 ms]
 t+6 ms  pull: 7 pull-set follows × recent_posts (batched)     [3 ms]
 t+9 ms  merge, dedupe, tombstones (Bloom), muted filter        [2 ms]
 t+14 ms hydrate: 1 multi-get for 1,500 post ids (batches of 200) [5 ms]
 t+18 ms features: 1 batch of (affinity, engagement priors)     [4 ms]
 t+52 ms rank 1,500 → 20 (single-threaded, vectorised)         [34 ms]
 t+56 ms serialise + write the snapshot cache (10 min TTL)      [4 ms]
 ⇒ p50 56 ms, p99 180 ms (cold path 410 ms).

 A NORMAL POST is created
 ─────────────────────────────────────────────────────────────────────────
 t+0     POST /v1/posts → insert post row                       [8 ms]
 t+9 ms  read the author's ACTIVE follower bitmap (62 % inactive removed)
 t+12 ms enqueue fan-out job (batched, priority by follower recency)
 t+18 ms ACK to author: 201 with fanout=push
 t+0.4 s fan-out workers insert ids into 180 × 0.38 active = 68 feeds
 t+0.6 s p95 visible: 30 s (N2). Most feeds see it in under a second.

 A CELEBRITY POST (90 M followers, PULL set)
 ─────────────────────────────────────────────────────────────────────────
 t+0     insert post, append the id to recent_posts:{author} (capped 20)
 t+9 ms  publish an event for the notification/recommendation services
 t+9 ms  DONE. Zero list inserts.
 t+120 s visible to a reader whose pull cache was cold (N2 allows 2 min)
 ⇒ 90 M inserts avoided: 225 s of cluster capacity, and the post is visible
   in under two minutes to everyone.
```

## 6. ASCII diagram

```
  WRITE PATH                                    READ PATH
  ┌──────────────┐                      ┌───────────────────────┐
  │ POST created │                      │ GET /feed             │
  └──────┬───────┘                      └───────────┬───────────┘
         ▼                                          ▼
  ┌──────────────────────┐              ┌───────────────────────────────┐
  │ follower_count?      │              │ RETRIEVE (~15 ms)             │
  │  ≥ 1,000,000 ?       │              │  push_list  : feed:{uid}      │
  └───┬──────────────┬───┘              │  pull_list  : 120 authors × 20│
      │ PULL         │ PUSH             │  merge · dedupe · tombstones  │
      ▼              ▼                  │  mute/block filter            │
 ┌─────────┐   ┌──────────────────┐     └───────────────┬───────────────┘
 │ append  │   │ fan-out workers  │                     ▼
 │ to      │   │ · active bitmaps │     ┌───────────────────────────────┐
 │ recent_ │   │ · batches 5,000  │     │ HYDRATE (~9 ms)               │
 │ posts:  │   │ · idempotent     │     │  post bodies + counters (KV)  │
 │ {author}│   │ · backpressure   │     │  features: affinity, priors   │
 │ (cap 20)│   └────────┬─────────┘     └───────────────┬───────────────┘
 └─────────┘            ▼                               ▼
      │          ┌──────────────┐        ┌───────────────────────────────┐
      │          │ FEED LISTS   │        │ RANK (~34 ms)                 │
      │          │ 120 M active │        │  1,500 → 20, deterministic    │
      │          │ × 1,000 ids  │        │  half-life 6 h · diversity    │
      │          │ = 960 GB     │        └───────────────┬───────────────┘
      │          └──────────────┘                        ▼
      │                                    ┌───────────────────────────────┐
      └───────────────────────────────────►│ SNAPSHOT (10 min) + cursor    │
                                           │ stable pagination (§4.7)      │
                                           └───────────────────────────────┘

  THE SPLIT, and why it is 1,000,000:
     push: 1 insert / follower / post  = 0.3 µs
     pull: 1 fetch / follower / read   = 41 µs
     ⇒ push is ~137× cheaper per follower-post, so push wins on AVERAGE.
     BUT one 90 M-follower post = 90 M inserts = 225 s of cluster capacity
     ⇒ the split is set by WRITE BURST (5 % of capacity for ≤ 30 s), not by
       average cost. 4,100 accounts sit above it and hold 71 % of edges.

  PAGINATION THAT DOES NOT MOVE:
     snapshot N:  [p9 p3 p7 p1 p22 …]  ← ranks fixed for 10 min
     cursor      = (snapshot N, rank_key(p22), p22)
     page 2      = same snapshot, rank_key < rank_key(p22)
     ⇒ new posts NEVER reorder a page you are reading. They arrive on
       refresh, as a new snapshot, with a "12 new posts" banner.
```

## 7. Detailed example

**BookNook's social reading feed**: readers follow other readers, reviewers, authors and curated lists; posts are reviews, quotes, progress updates and reading lists.

```
  measured at the 20-month point:
    300 M users (120 M daily active), 180 average follows
    40 M posts/day, peak 1,850/s
    2.1 B feed reads/day, 24,306/s average, 75,349/s peak
    4,100 pull-set accounts holding 71 % of all follow edges
    62 % of followers inactive (no read in 30 days)
    4.1× engagement lift from ranking versus chronological
```

**The incident that set the threshold:**

```
  2025-07, author Chimamanda-style mega-account (94 M followers) posted at
  19:40 — peak time.
    · pure push fan-out: 94 M inserts enqueued at once
    · the fan-out queue absorbed all cluster insert capacity for 11 minutes
    · during those 11 minutes, 41 M other posts' fan-outs were delayed;
      p95 write→visible went from 24 s to 6 min 20 s
    · readers noticed "my feed is empty" for accounts whose posts were queued
    · cascade: the read path's pull cache was also saturated by retries
  cost: 11 minutes of degraded feed for 40 % of users, during the highest
  ad-value hour of the week, estimated £260,000 of lost ad revenue.
  fix (the whole hybrid):
    · the 1 M-follower threshold and the PULL set (this was the trigger)
    · a write-burst budget: no single post may consume > 5 % of cluster
      insert capacity for > 30 s — enforced by the fan-out scheduler
    · backpressure: when the fan-out queue exceeds 30 s of work, new posts
      are marked `deferred` and served by the pull path
  measured after: the same 94 M-follower account posts safely; p95
  write→visible during peak = 26 s.
```

**The inactive-follower optimisation, and the rebuild it required:**

```
  before: fan out to all followers
      12.0 B inserts/day; 62 % of them into feeds nobody reads
      cost: £41,000/month of list-store capacity
  after: fan out to active followers only (a daily bitmap)
      4.6 B inserts/day (−62 %); cost £15,600/month
  the catch: a returning user's feed is 40 days empty
      ⇒ cold rebuild from the graph, measured p99 410 ms, once per session
      ⇒ and a "recently active" nudge in the product, because the rebuild is
        also the moment to show them why they came back
  ⇒ THE HONEST TRADE: we trade 376 ms of latency for the 1.4 % of sessions
    that start cold, against £25,400/month. Nobody notices; the invoice does.
```

**The duplicate-post complaint, and the cursor fix:**

```
  symptom: 3.1 % of scroll sessions showed a repeated post; 1.9 % skipped one.
  cause: offset pagination (`LIMIT 20 OFFSET 40`) over a list that changes
  between requests — exactly the classic failure.
  fix: rank-key cursors over a 10-minute snapshot (§4.7).
  measured after: duplicates 3.1 % → 0.002 % (from a client bug that was
  fixed separately), skips 1.9 % → 0.000 %, and the "I keep seeing the same
  post" complaint category disappeared from the support taxonomy.
```

## 8. Code or configuration example

```python
# ── Write path: the fan-out decision, with burst protection ─────────────
async def on_post_created(post: Post):
    followers = await graph.follower_count(post.author_id)
    is_pull   = await pull_set.contains(post.author_id)     # cached bitmap

    # append to the author's recent list either way: the pull path needs it
    await lists.push_capped(f"recent:{post.author_id}", post.id, cap=20)

    if is_pull:
        await bus.publish("feed.celebrity_post", {"post": post.id,
                                                  "author": post.author_id})
        metrics.inc("feed.fanout.deferred", {"reason": "pull_set"})
        return "deferred"

    # burst budget: no single post may take > 5 % of insert capacity for 30 s
    burst = followers / INSERT_CAPACITY             # seconds of capacity
    if burst > BURST_BUDGET_S and not await scheduler.reserve(followers):
        await fanout_queue.defer(post.id, followers)     # served by pull path
        return "deferred"

    active = await activity.bitmap(post.author_id)  # 38 % of followers
    await fanout_queue.enqueue(post.id, active, priority=by_reader_recency())
    return "push"

async def fanout_worker(post_id: int, followers: Bitmap):
    for batch in followers.iter_batches(5_000):      # idempotent, checkpointed
        await lists.push_many([f"feed:{u}" for u in batch], post_id,
                              cap=1_000)
        await fanout_queue.checkpoint(post_id, batch.end)
```

```python
# ── Read path: retrieve, hydrate, rank, paginate — with budgets ─────────
async def read_feed(uid: int, cursor: Cursor | None, limit=20):
    if cursor is None:
        snap = await snapshot.build(uid)              # the 10-min snapshot
    else:
        snap = await snapshot.get(uid, cursor.snapshot_id)
        if snap is None:
            raise ExpiredCursor(refresh_after=now())  # honest, not a lie

    candidates = snap.candidates                     # ~1,500 ids, already
                                                     # tombstone/mute filtered
    order = await ranker.rank(uid, candidates, deadline_ms=60)
    items = [c for c in order
             if cursor is None or c.rank_key < cursor.rank_key][:limit]
    return {
        "items": [serialise(c) for c in items],
        "next_cursor": Cursor(snap.id, items[-1].rank_key, items[-1].post_id),
        "refresh_after": snap.created_at + SNAPSHOT_TTL,
    }

async def build_snapshot(uid: int) -> Snapshot:
    # 1. push list (precomputed), 2. pull list (celebrities), in parallel
    push_ids, pull_follows = await asyncio.gather(
        lists.range(f"feed:{uid}", 0, 999),
        pull_set.follows_of(uid, limit=120),        # capped: bounded merge
    )
    pull_ids = await lists.multi_recent(pull_follows, per_author=20)
    ids = merge_recent(push_ids, pull_ids, cap=1_500)

    # 3. filters that MUST apply at read time, not write time
    ids = [i for i in ids
           if not await tombstones.might_contain(i)          # deletions, 60 s
           and not await mutes.blocked(uid, i)]              # mute/block
    # 4. hydrate bodies/counters in one batch, then features
    posts = await kv.multiget(ids)
    feats = await features.batch(uid, [p.author_id for p in posts])
    return Snapshot(id=new_id(), created_at=now(),
                    candidates=[(i, feats[a]) for i, a in zip(ids, posts)])
```

```python
# ── Ranking: separate, versioned, and evaluated offline before it ships ──
RANKER_VERSION = "2026-10-r14"

def score(uid, post, feat, ranker_version=RANKER_VERSION) -> float:
    recency = 2 ** (-feat.age_hours / 6.0)             # 6 h half-life
    affinity = feat.user_author_affinity               # [0,1], 30-day window
    engage = feat.predicted_engagement                 # model output, [0,1]
    return (0.42 * recency + 0.28 * affinity
            + 0.24 * engage + 0.06 * feat.author_quality
            - feat.diversity_penalty * 0.15)

def apply_diversity(ranked: list, max_consecutive=3) -> list:
    """No more than max_consecutive posts by one author in a row — the single
    most effective guardrail against a feed collapsing into one voice."""
    out, run = [], 1
    for item in ranked:
        if out and item.author_id == out[-1].author_id:
            run += 1
            if run > max_consecutive:
                continue                            # defer to the tail
        else:
            run = 1
        out.append(item)
    return out
```

```yaml
# ── The rails: thresholds are data, not constants in code ───────────────
feed:
  push_list_cap: 1000
  pull_set_threshold_followers: 1000000        # recomputed daily
  pull_follows_per_feed_cap: 120
  snapshot_ttl_seconds: 600
  active_follower_window_days: 30
  burst_budget:
    max_share_of_insert_capacity: 0.05
    max_seconds: 30
  ranking:
    version: "2026-10-r14"
    half_life_hours: 6
    max_consecutive_same_author: 3
alerts:
  - alert: FeedWriteVisibleLag
    expr: histogram_quantile(0.95, rate(feed_visible_delay_seconds_bucket[5m])) > 30
    severity: page                  # N2 breach: posts are invisible
  - alert: FanoutQueueBacklog
    expr: feed_fanout_queue_seconds_of_work > 30
    severity: page                  # switch to deferred mode immediately
  - alert: FeedReadLatencyBreach
    expr: histogram_quantile(0.99, rate(feed_read_seconds_bucket[5m])) > 0.2
    severity: page                  # N1
  - alert: DeletionNotPropagated
    expr: max(feed_deleted_post_visible_seconds) > 60
    severity: page                  # N7, and a trust/compliance issue
  - alert: SnapshotCacheHitDrop
    expr: rate(feed_snapshot_hits_total[10m]) / rate(feed_reads_total[10m]) < 0.9
    severity: ticket                # cursor expiries are about to spike
```

## 9. Numerical example

BookNook: 40 M posts/day, 2.1 B reads/day, 4,100 pull-set accounts.

**Step 1 — why pure push fails, in one multiplication.**

```
  the tail: 4,100 accounts with > 1 M followers, 71 % of all follow edges
  a single post from a 94 M-follower account = 94,000,000 list inserts
  cluster insert capacity (measured) = 400,000/s
  ⇒ 94,000,000 / 400,000 = 235 s of the ENTIRE cluster's insert capacity
    for one post. At peak (19:40) that is 11 minutes of degradation for
    everyone else — which is exactly the §7 incident.
  ⇒ no amount of sharding fixes a single post that is itself 235 s of work.
    The fix is to NOT push it. The threshold follows from the burst budget:
      5 % of 400,000 = 20,000 inserts/s for ≤ 30 s = 600,000 inserts
      ⇒ accounts with > 600,000 followers are pull-set. Rounded to 1,000,000
        for hysteresis (a 600k account promoted and demoted repeatedly would
        thrash its followers' feeds).
```

**Step 2 — the cost of the hybrid, both sides.**

```
  PUSH side: 299,995,900 accounts
      40 M posts/day × 0.38 active-follower share × 180 avg followers
      = 2.74 B inserts/day = 31,700/s average, 100,000/s peak
      ⇒ 25 % of insert capacity. Fits.
  PULL side: 4,100 accounts, 71 % of edges
      write cost: 4,100 × 40 posts/day = 164,000 recent-list appends/day
                  = 1.9/s. Effectively free.
      read cost: every feed read merges up to 120 pull follows × 20 posts
                 = 2,400 ids (measured mean 1,140 ids for an average user who
                 follows 7 pull-set accounts)
      ⇒ +3.1 ms p50 on the read path, +11 ms p99. Paid 2.1 B times/day, so
        it matters: 11 ms × 2.1 B = 6.4 M core-seconds/day = 74 cores.
        At £55/core-month that is ~£4,100/month.
  ⇒ THE HYBRID'S TOTAL COST: £4,100/month of read-side merge PLUS the write
    savings from not pushing 71 % of edges (which would have been
    ~£63,000/month of list capacity). NET: about £59,000/month better than
    pure push, and it removes the burst hazard entirely.
```

**Step 3 — the storage arithmetic that makes "ids only" mandatory.**

```
  feed lists, ids only:     120 M active × 1,000 ids × 8 B = 960 GB
  feed lists, bodies too:   120 M × 1,000 × 400 B = 48 TB
  ⇒ 50× the storage, and a 400 KB read per feed page instead of 8 KB.
  the measurement that decides it: 96.4 % of ids in a feed are never
  rendered (the user sees 20 of 1,000). Hydrating 1,500 candidates per page
  via one batched multi-get costs 5 ms; hydrating 1,000 inside the list
  structure would cost 50× the egress for 60× the wasted data.
```

**Step 4 — the inactive-follower saving, and its honest cost.**

```
  before: 12.0 B inserts/day = 139,000/s average, 556,000/s peak
          ⇒ ABOVE the 400,000/s capacity at peak. Pure push does not fit
            even without the mega-account — the tail was only half the problem.
  after:  4.6 B inserts/day = 53,000/s average, 212,000/s peak
          ⇒ 53 % of capacity at peak. Fits with headroom.
  cold-rebuild cost: 1.4 % of sessions × 410 ms p99 — acceptable
  savings: £41,000 → £15,600/month of list capacity = £25,400/month
  ⇒ note that the inactive-follower fix was necessary to make PUSH fit at
    all; the mega-account threshold alone would not have been enough. TWO
    independent optimisations, both required, and both discovered by
    arithmetic rather than by profiling in production.
```

**Step 5 — the read path's budget, measured end to end.**

```
  component                  p50     p99     share of p99
  ─────────────────────────────────────────────────────────
  push list read              3.2     9.1      5 %
  pull merge (7 follows)      3.1    11.0      6 %
  tombstone/mute filter       1.8     4.2      2 %
  hydrate (batched)           5.0    18.4     10 %
  features batch              4.2    16.9      9 %
  rank 1,500 → 20            33.6    92.0     51 %
  serialise + snapshot cache  4.1    28.4     16 %
  ─────────────────────────────────────────────────────────
  total                      56.0   180.0
  ⇒ RANKING IS HALF THE BUDGET. That is the correct place to spend
    optimisation effort, and it is also why ranking must be a separate,
    separately-versioned system (§4.9) with its own deadline and its own
    fallback (chronological order) if it overruns.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **Pure push** | O(1) reads; simple read path | Write burst; capacity ∝ follower count | Follow counts are bounded (a small product) |
| **Pure pull** | Trivial writes | Read cost ∝ follow count; impossible at scale | Very low read rates, or tiny follow counts |
| **Hybrid (chosen)** | Fits both sides arithmetically | Two code paths, a threshold, a merge | **Any feed with a power-law graph** |
| **Ids only in feeds** | 50× less storage, fast lists | A hydration round trip | **Always** |
| **Bodies in feeds** | One read per page | 48 TB and 400 KB pages | Never at this scale |
| **Active-follower filtering** | 62 % fewer inserts | Cold rebuilds (410 ms p99) | Always, with a rebuild path |
| **Read-time tombstone filtering** | Deletions in < 60 s without touching 120 M lists | A Bloom filter on the hot path | Always |
| **Ranked feed** | 4.1× engagement | A model, features, and evaluation machinery | Whenever engagement is the goal |
| **Chronological feed** | Trivial; predictable | Much lower engagement | Small products, or "latest" tabs |
| **Snapshot cursors** | No duplicates or skips | 10-minute cursor expiry; 6.4 KB/user | **Always** |
| **Offset pagination** | Simplest code | 3.1 % duplicates, 1.9 % skips (§7) | Never |
| **Deferred fan-out under load** | Survives bursts | Posts visible via pull only for a while | Any system with celebrity posts |

## 11. Failure scenarios

**11.1 — Celebrity post saturating the cluster** (§7). 94 M inserts, 11 minutes of degradation, £260k of ad revenue. **Fix:** the pull-set threshold with hysteresis, the write-burst budget, and deferred mode.

**11.2 — Duplicate and skipped posts.** Offset pagination over a mutating list (§7). **Fix:** rank-key cursors over a snapshot, with an honest expiry.

**11.3 — Deleted post still visible.** Deleting from 120 M lists is impossible in 60 s. **Fix:** read-time tombstones (Bloom, refreshed every 10 s) plus a purge of the candidate lists on the next write to that feed.

**11.4 — Unfollowed account still in the feed.** The precomputed list still holds its ids. **Fix:** read-time filter against the follow set already loaded for the pull path.

**11.5 — Fan-out queue backlog at peak.** Nothing bounded the queue; p95 write→visible hit 6 min 20 s. **Fix:** a queue-depth alarm at 30 s of work, and automatic switch to deferred mode.

**11.6 — Cold feed for a returning user.** 40 days evicted, one read took 4.2 s before the rebuild path existed. **Fix:** a cold mode with a 1 s deadline, chronological fallback, and a 410 ms measured rebuild.

**11.7 — Ranking regression.** A model change increased session length but also the report rate; nothing stopped it because the guardrail was unmonitored. **Fix:** guardrails as blocking metrics (report rate, hide rate, author diversity), and a shadow run before any A/B.

**11.8 — Mute bypass through the pull path.** Mutes were applied at fan-out time, so pull-set posts from muted authors still appeared. **Fix:** all visibility filters at read time, in one place, for both paths — a single chokepoint.

## 12. Alternatives

| Instead of | Consider | When |
|---|---|---|
| A ranked feed | A chronological feed with a "top" tab | Early product, or a strongly chronological use case |
| Own fan-out | A managed ranking/feed service | Feed is not your differentiator |
| Push for everyone | Pull for everyone | Follow counts are small and reads are few |
| A per-user snapshot cache | Stateless cursors with a server-side re-rank | You can tolerate slight rank drift |
| A feature store | A simpler scoring function | Cold start, or few features |
| Real-time feed updates | Pull-to-refresh + a "new posts" banner | **Always** — reordering under the thumb is the worst option |

## 13. When to use it

- **The hybrid push/pull split** whenever the follow graph is power-law distributed — which is every social product.
- **A read-time filter chokepoint** for visibility rules (mutes, blocks, deletions) so both paths agree.
- **Snapshot cursors** for any paginated list that changes while being read.
- **Ids-only lists** for any feed above a million users.
- **A separate ranking subsystem** with offline evaluation and guardrails.

## 14. When *not* to use it

- **Precomputed feeds for inactive users** — evict, and rebuild on return.
- **Pushing to inactive followers** — 62 % of your writes for nobody.
- **Offset pagination** on any mutating list.
- **Silent reordering mid-scroll** — use a "new posts" banner instead.
- **Entities in the list store** — ids only.
- **Ranking without guardrails** — you will optimise for session length and lose the product.

## 15. Common mistakes

1. **One fan-out strategy for the whole graph** (§9 step 1).
2. **No write-burst budget**, so one post can take the cluster (§11.1).
3. **Pushing to inactive followers** (§9 step 4).
4. **Bodies in feed lists** (§9 step 3).
5. **Offset pagination** (§11.2).
6. **Deleting ids from every list** instead of filtering at read time (§11.3).
7. **Filters applied at fan-out only**, so the pull path bypasses them (§11.8).
8. **No cold-feed rebuild path** (§11.6).
9. **No backpressure on the fan-out queue** (§11.5).
10. **Ranking entangled with retrieval** (§4.9).
11. **Guardrails unmonitored**, so a model "wins" while the product degrades (§11.7).
12. **Chronological and ranked mixed into one code path**, so neither can be evaluated.
13. **Cursor opacity failure**: exposing offsets in the cursor, so clients construct their own and break.
14. **No `refresh_after`**, so expired cursors fail mysteriously instead of asking for a refresh.

## 16. Interview perspective

**What is actually being tested.** Whether you can derive the push/pull split from **arithmetic** rather than pattern-matching, and whether you treat pagination and visibility filtering as correctness properties. Strong candidates: compute the burst cost of a mega-follower post, choose a threshold, filter at read time, snapshot cursors, ranking as a separate subsystem. Weak candidates: "use a push model, add Redis, done".

**Answers that land well:**

- *"Push or pull?"* — "Both, split by follower count, and the threshold comes from a write-burst budget. Pushing is about a hundred and forty times cheaper per follower-post, so it wins on average — but one post from a ninety-four-million-follower account is ninety-four million list inserts, which is two hundred and thirty-five seconds of our entire cluster's insert capacity. That was a real eleven-minute degradation during peak. So accounts above a million followers go to a pull set, their posts are written once and merged at read time, and the write-burst budget stops any single post from exceeding five percent of capacity for thirty seconds."
- *"How do you handle deletions?"* — "You cannot delete an id from a hundred and twenty million lists in sixty seconds. So deletions are filtered at read time against a Bloom filter of recent tombstones, refreshed every ten seconds, and the lists get cleaned lazily. That also means every visibility rule — mute, block, unfollow, delete — lives at one chokepoint on the read path, because the alternative is two code paths that disagree, which is how muted authors kept appearing through the pull path."
- *"Pagination?"* — "Rank-key cursors over a ten-minute snapshot. Offset pagination gave us three percent duplicate posts and two percent skipped posts, which is the complaint that makes users think the app is broken. The snapshot means page two is a continuation of the ranking you were reading; new posts arrive on refresh, with a banner, never by reordering under your thumb."
- *"What's the read latency made of?"* — "Ranking is fifty-one percent of the p99. Retrieval is about fifteen milliseconds, hydration and features about nine each, and ranking one thousand five hundred candidates into twenty is thirty-four milliseconds at p50 and ninety-two at p99. So ranking is where the optimisation budget goes — and it is why it is a separate, versioned subsystem with a deadline and a chronological fallback."
- *"What did you get wrong first?"* — "Two things, both arithmetic. We pushed to every follower, including the sixty-two percent who had not opened the app in a month — twelve billion inserts a day, which does not fit in our four hundred thousand a second even before the tail. And we mixed deletion into the fan-out path. Both were found by doing the multiplication up front, which is the part of the design review that pays."

**Common interview tasks:** choose push/pull; derive the celebrity threshold; design the read path with budgets; design stable pagination; handle deletions and mutes; design the ranking subsystem and its guardrails; size the storage.

**Red flags interviewers watch for:**

- One strategy for the whole graph.
- No celebrity/backpressure story.
- Offset pagination.
- Deleting from every list on edit.
- Filters only at write time.
- Ranking inside the retrieval code.
- No guardrail metrics.
- No cold-start path for returning users.
- No numbers: no follower distribution, no insert cost, no budget.

## 17. Knowledge check

**Conceptual**

1. Derive the push/pull threshold from a write-burst budget, and explain why the criterion is burst rather than average cost. Then explain the role of hysteresis and what thrashes without it.
2. Explain why a feed stores ids rather than post bodies, with the storage arithmetic for 120 M feeds × 1,000 items. Then explain the hydration round trip and why batching is mandatory.
3. Explain snapshot cursors and why offset pagination fails on a mutating list. Then explain the trade-off made by a 10-minute cursor expiry and how the API communicates it.

**Practical scenario**

4. Design BookNook's feed: 300 M users, 120 M daily active, 40 M posts/day, 2.1 B reads/day, a power-law follow graph with 4,100 accounts above 1 M followers, mutes/blocks/deletions, ranked ordering with diversity, and a p99 read target of 200 ms. Give the requirements and capacity tables, the write path with the burst budget and active-follower filtering, the read path with per-stage budgets, the hybrid decision rule with arithmetic, the pagination design, the visibility chokepoint, the ranking subsystem with its guardrails, the storage and cost, and the alerts. Then state what changes if the platform introduces a "live events" feed where posts must appear within 2 seconds.

**Calculation**

5. A cluster sustains 400,000 feed-list inserts/s. (a) Compute the seconds of cluster capacity consumed by a single post with 94 M followers. (b) With a burst budget of 5 % of capacity for at most 30 s, compute the maximum follower count that can be pushed and state the threshold you would choose. (c) Compute the daily inserts if 40 M posts × 180 followers are pushed to 38 % active followers. (d) Recompute with all followers and state whether it fits.

6. A feed read retrieves 1,000 push ids plus 120 pull sources × 20 posts, hydrates 1,500 candidates, and ranks them. (a) Compute the candidate count before dedupe and after. (b) If ranking costs 22 µs per candidate, compute the ranking time and compare it with a 60 ms budget. (c) Compute the total read cost at 24,306 reads/s in core-seconds/day. (d) If the ranking budget were halved, compute the maximum candidate count and state the effect on recall.

**Progress checklist**

- Phase 1 — Foundations (L1–17): **complete** ✅
- Phase 2 — Scaling (L18–39): **complete** ✅
- Phase 3 — Data and Storage (L40–63): **complete** ✅
- Phase 4 — Distributed Systems (L64–92): **complete** ✅
- Phase 5 — Complete system designs (L93–106):
  - [x] 1. Design Pastebin (L93)
  - [x] 2. Design a URL shortener (L94)
  - [x] 3. Design a rate limiter (L95)
  - [x] 4. Design a notification service (L96)
  - [x] 5. Design search autocomplete (L97)
  - [x] 6. Design file storage (L98)
  - [x] 7. Design a web crawler (L99)
  - [x] 8. Design real-time chat (L100)
  - [x] **9. Design a news feed (L101) ← you are here**
  - [ ] 10. Design video streaming (L102)
  - [ ] 11. Design ride sharing (L103)
  - [ ] 12. Design e-commerce and inventory (L104)
  - [ ] 13. Design payment processing (L105)
  - [ ] 14. Design a metrics and logging platform (L106)

**NEXT LESSON: Phase 5, Topic 10 — Design video streaming**

---
