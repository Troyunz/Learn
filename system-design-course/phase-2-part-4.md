# Lesson 31 — CDNs and Edge Caching

*Phase 2, Topic 14*

## 1. Simple definition

A **CDN** (Content Delivery Network) is a globally distributed fleet of caching servers, operated by someone else, that sits between your users and your servers and answers requests from a location physically close to the user.

Terms defined before use:
- **Origin**: your own server — the authoritative source of the content. Everything in this lesson is a copy of something the origin produced.
- **POP** (Point of Presence): a physical facility the CDN operates, usually inside or next to a major internet exchange. A POP is a building full of racks in, say, Mumbai or Frankfurt.
- **Edge server**: one caching machine inside a POP. "The edge" is the collective noun for all of them. When people say "served from the edge", they mean an edge server answered without contacting your origin.
- **Cache fill / origin fetch**: what happens on a miss — the edge server requests the object from your origin and stores it on the way back.
- **Offload** (or **origin offload**): the fraction of requests, or of bytes, that the CDN answered without touching your origin. This is the single number that tells you whether a CDN is earning its keep.

Lessons 26–30 covered caching *inside* your system. A CDN is the same idea — store a copy near the consumer — pushed as far outward as it is physically possible to push it: into the user's own city.

Two things are being bought, and they are independent:
1. **Distance reduction.** Fewer milliseconds of round trip, because bytes travel 200 km instead of 12,000 km.
2. **Load and bandwidth reduction.** Your origin never sees the request at all, so you do not pay to serve it and you do not need capacity for it.

A CDN that improves latency but not offload is usually misconfigured. So is one that offloads well but serves from the wrong continent.

## 2. Why it exists

In **Lesson 1** we measured BookNook's traffic and found that book-cover images consumed roughly **40× the bandwidth of the application itself**. The HTML and JSON that represent the actual product of the business were a rounding error next to the JPEGs. That ratio is not unusual; it is the normal shape of any content-bearing website.

That single fact drives four separate problems, and a CDN addresses all four.

### Bandwidth cost

Cloud egress — bytes leaving the provider's network toward the public internet — is billed per gigabyte, and it is the most expensive line item most teams never look at. Typical list price is **$0.08–$0.12 per GB** for the first tier. Commodity CDN egress is typically **$0.01–$0.03 per GB**, and some vendors do not meter it at all. Moving the 40× bandwidth tail onto a network that charges a quarter of the price is a direct, immediate, non-architectural saving. Section 9 works the number for BookNook.

### Latency

Light in glass travels at roughly **200,000 km/s**, which is about 5 µs per kilometre. A round trip from Mumbai to Virginia is about 24,000 km of fibre once you account for the actual cable routes, giving a floor near **120 ms** of pure physics, and a real-world measured RTT (round-trip time) of **200–230 ms** once routers, queues and undersea detours are included. No amount of server optimisation touches this. The only way to reduce it is to **move the bytes closer**, which is exactly and only what a CDN does.

This compounds badly: a page needing a TCP handshake, a TLS handshake (Lesson 7) and several TCP slow-start rounds pays that RTT five or six times before the first image is fully rendered.

### Origin offload

Offload is a capacity argument, not just a cost argument. If 96 % of requests never reach your origin, your origin fleet is sized for 4 % of traffic. That changes the number of app servers, the size of the load balancer (Lesson 21), and the blast radius of a traffic spike. It also makes **Lesson 15's availability maths** work in your favour: the component most likely to fail (your own stack) is in the path for a small minority of requests.

### DDoS absorption

A **DDoS** (Distributed Denial of Service) attack floods you with more traffic than you can serve. A large CDN has multi-terabit capacity spread across hundreds of POPs, and a volumetric attack is naturally split across all of them by **anycast** routing — the practice of announcing the same IP address from many locations, so the internet's own routing sends each attacker to their nearest POP. A 2 Tbps attack against a single origin is fatal; against 300 POPs it is under 7 Gbps per POP, which is noise. You cannot buy this property at any price with a single-region origin.

**None of these four is "it depends".** Bandwidth cost depends on your egress volume and your price per GB. Latency depends on the geographic distribution of your users relative to your origin. Offload depends on your cacheable fraction and hit ratio. DDoS absorption depends on whether your attack surface is a single IP or an anycast network. Each is a number you can compute, and Section 9 computes all of them.

## 3. Real-world analogy

**A publisher's regional warehouses.**

A publisher prints books in one factory in Virginia. Without warehouses, a reader in Mumbai orders and the book ships from Virginia: three weeks, high freight cost, and if the factory burns down nobody anywhere gets a book.

With regional warehouses:
- The publisher ships pallets of the popular titles to Mumbai, London and São Paulo once. That is a **cache fill**.
- A Mumbai reader's order is satisfied locally in two days. That is an **edge hit**.
- An order for an obscure title not stocked locally goes back to the factory. That is an **edge miss**.
- A regional distribution hub serving several city warehouses, so they do not all independently bother the factory, is an **origin shield** / **tiered cache**.
- "This edition is recalled, pull it from every shelf immediately" is a **purge**.
- "Stop selling this after 31 December" is a **TTL** (time to live).
- Printing the print-run number on the spine, so the 2nd edition is a physically different object from the 1st, is **content-hashed filenames** — and it means you never need a recall, because you simply ship a different object.

**Where the analogy breaks down:**

| Publisher's warehouses | CDN |
|---|---|
| Stock is a physical, finite count — selling one removes it | A cached object is copied, not consumed; one object serves unlimited requests |
| The publisher decides what to stock in advance | Most CDNs are **pull**: the edge stocks an object only after the first user asks for it, so that user pays the full origin latency |
| Every warehouse is one building with one inventory | One POP may hold dozens of edge servers with *independent* caches, so "cached in Mumbai" may mean 1 of 24 machines has it |
| A recall takes days and everyone accepts that | Teams expect purges to be instant; a global purge realistically takes **1–30 seconds** to reach every POP, and clients may hold the old copy far longer |
| The warehouse holds the same book the factory printed | The edge may legitimately hold **several different objects at the same URL** — gzip vs brotli, mobile vs desktop — which is the `Vary` problem, and getting it wrong leaks one user's content to another |
| A warehouse cannot alter a book | Edge compute can rewrite, redirect and personalise the response in flight |

That fifth row is the one that causes production incidents. A CDN keyed on URL alone will happily serve a Brotli-compressed body to a client that cannot decompress it, or — far worse — serve a logged-in user's personalised HTML to the next anonymous visitor.

## 4. Detailed technical explanation

### How a request reaches an edge server

A CDN works by taking over DNS (Lesson 4). You `CNAME` your hostname to the CDN's hostname. The CDN's authoritative nameservers return an address that is either anycast (the same IP everywhere, with BGP routing deciding which POP you reach) or geo-targeted unicast (a different IP per region). The user's browser then opens TCP and TLS to an edge server, believing it is talking to `cdn.booknook.com`. The CDN holds a certificate for your hostname, which is why onboarding a CDN always involves certificate provisioning or domain validation.

The key consequence: **TLS terminates at the edge**, not at your origin. The edge decrypts, inspects, caches and re-encrypts toward your origin. You are trusting the CDN with your plaintext. This is a real security consideration, and it is why some organisations cannot use a shared CDN for certain content (see Section 14).

### Push versus pull

| | **Pull CDN** | **Push CDN** |
|---|---|---|
| How content arrives | On the first user request for an object, the edge fetches it from origin and caches it | You upload objects to the CDN's storage before any user asks |
| First request | Slow — full origin round trip plus fill | Fast — already there |
| Configuration | Point the CDN at an origin hostname; done | You own a publishing pipeline |
| Storage cost | You store only what users want | You store everything, everywhere, whether wanted or not |
| Deleting content | Purge | Delete from the CDN's storage |
| Good for | Large catalogues with a long tail (BookNook's 400,000 covers) | Small sets of huge, predictably-hot objects — a game patch, a software release, a launch-day video |

Pull is the default in essentially every modern CDN and is what people mean when they say "a CDN" without qualifying. Push survives for large-file distribution where you know in advance that the whole world will want the same 4 GB object at the same minute and you cannot afford the first-request stampede.

**The pull CDN's cold-start problem is real and quantifiable.** With 300 POPs and independent caches, the "first request" penalty is paid up to 300 times for every object — once per POP — and more if a POP has multiple edge servers with independent caches. That is what origin shield fixes.

### The cache key, and `Vary`

The **cache key** is the string the edge uses to decide whether two requests are "the same request". By default it is typically:

```
scheme + host + path + (some or all of the query string)
```

Everything not in the key is ignored for matching purposes. This creates two symmetrical failure modes:

- **Key too narrow** → different responses collide on one entry. Two users get each other's content. This is a security incident, not a performance bug.
- **Key too wide** → identical responses stored separately. This is **cache fragmentation**: hit ratio collapses, origin load rises, and the cache fills with duplicates.

Fragmentation is the more common mistake. Marketing appends `?utm_source=newsletter&utm_campaign=spring` to links. If the CDN includes the full query string in the key, one cover image now has thousands of distinct cache entries with identical bytes, each needing its own origin fill. The fix is an explicit query-string policy: ignore all parameters except a named allow-list (for an image resizer, perhaps only `w` and `fm`).

**`Vary`** is the HTTP response header by which the origin tells caches "this response depends on these request headers; include them in the key."

```
Vary: Accept-Encoding
```
means "I have gzip and brotli variants; key them separately." That is correct and necessary.

```
Vary: User-Agent
```
is almost always a mistake. There are hundreds of thousands of distinct User-Agent strings in the wild, so this effectively sets the hit ratio to near zero while appearing to work in testing. If you need device-class variation, use a CDN feature that normalises the User-Agent into a small number of buckets (`mobile`/`tablet`/`desktop`) and keys on *that*.

```
Vary: Cookie
```
is the one that silently destroys caching. Every user has a distinct session cookie, so every user gets their own cache entry — a cache with a 100 % miss rate that still costs you storage. If a response genuinely varies by cookie, it is private and should be marked `Cache-Control: private` rather than cached at the edge at all.

The rule: **`Vary` on headers with low cardinality that you control. Never `Vary` on headers with unbounded cardinality.**

### Origin shield and tiered caching

**Tiered caching** inserts a middle layer between the edge POPs and your origin. On a miss, an edge asks a **mid-tier** cache (regional), and only if that misses does anything reach your origin. An **origin shield** is the degenerate, most common case: *one* designated POP, usually chosen to be near your origin, through which all origin fills funnel.

Why it matters, concretely. Suppose a cover image is requested once in each of 120 POPs during a TTL window.

- Without shield: **120 origin fills** for one object.
- With shield: the first edge miss fills the shield from origin; the other 119 fill from the shield. **1 origin fill.**

The shield therefore multiplies your effective offload. It also:
- Converts 120 long-haul TLS connections into 1, which matters because connection setup, not bytes, is often the origin's bottleneck.
- Gives you a single place to apply request coalescing, so a stampede (Lesson 29) on a hot object collapses to one origin request rather than N.
- Costs one extra hop (typically 10–40 ms) on the miss path only.

The trade-off is explicit: shields improve offload and reduce stampedes, at the cost of added miss latency and a new concentration point. If the shield POP has an incident, your fill path degrades (it usually falls back to direct-to-origin rather than failing).

### Cache-Control directives that matter specifically to CDNs

Lesson 6 covered browser-facing HTTP caching. CDNs respond to the same header but with extra directives aimed at **shared** caches. The crucial distinction:

- A **private cache** is the browser's own cache — one user.
- A **shared cache** is a CDN or reverse proxy — many users.

| Directive | Who obeys it | What it does | When you want it |
|---|---|---|---|
| `public` | Both | Explicitly permits shared caches to store it, even if the request had an `Authorization` header | Static assets, public catalogue pages |
| `private` | Browser only | **Forbids** CDNs and proxies from storing it | Anything user-specific: `/account`, `/cart`, personalised HTML |
| `no-store` | Both | Do not write to disk or memory anywhere | Genuinely secret responses |
| `no-cache` | Both | May store, but **must revalidate** before every reuse. (It does *not* mean "do not cache" — a persistent misreading) | Content that changes unpredictably but is usually unchanged |
| `max-age=N` | Both | Fresh for N seconds | Baseline freshness |
| `s-maxage=N` | **Shared caches only** | Overrides `max-age` at the CDN. Lets you hold an object for a day at the edge while browsers keep it for a minute | The single most useful CDN directive |
| `stale-while-revalidate=N` | Shared caches (and modern browsers) | After expiry, serve the stale copy **immediately** and refresh in the background for up to N seconds | Almost everything cacheable |
| `stale-if-error=N` | Shared caches | If the origin returns 5xx or is unreachable, keep serving the stale copy for up to N seconds | Almost everything cacheable |
| `immutable` | Both | "This will never change; do not revalidate even on reload" | Content-hashed filenames only |
| `must-revalidate` | Both | Forbids serving stale on error — the opposite of `stale-if-error` | Correctness-critical data (prices, stock) |

`s-maxage` deserves emphasis because it resolves a real conflict. You want long edge TTLs (high offload) but short browser TTLs (so a purge is actually effective, since you cannot purge a browser). `Cache-Control: public, max-age=60, s-maxage=86400` gives you exactly that: the browser re-asks every minute, the edge answers for a day without troubling the origin.

`stale-while-revalidate` converts your cache from a *latency* optimisation into an *availability* optimisation. With `s-maxage=300, stale-while-revalidate=86400`, no user ever waits for an origin fetch after the first one — the edge always has something to serve, and refreshes happen off the critical path. This is the edge-level equivalent of the stampede mitigations in Lesson 29, and it is free.

`stale-if-error` is the one that turns a CDN into a disaster-recovery layer. With `stale-if-error=604800`, a seven-day origin outage leaves your catalogue pages still serving. Users see slightly old stock counts instead of a blank page. For BookNook, that is the difference between a bad day and a lost quarter.

### Cache busting: content hashes versus query strings

You cannot purge a browser cache. Once you tell a browser `max-age=31536000`, that copy is out of your control for a year. So you need a way to make the browser request a *new object* rather than reuse the old one. This is **cache busting**.

Two approaches:

**Query-string versioning**: `/static/app.js?v=47`
**Content-hashed filenames**: `/static/app.4f3a9c1e.js`, where `4f3a9c1e` is a hash of the file's bytes, generated by the build tool.

Content hashing is **strictly better**, for five reasons, not one:

1. **Some intermediaries ignore query strings in the cache key.** Old proxies, some corporate caches, and misconfigured CDNs treat `app.js?v=47` and `app.js?v=48` as the same object. The response you get is then indeterminate. A different path cannot be collapsed this way.
2. **`immutable` becomes truthful.** `/static/app.4f3a9c1e.js` really never changes — any change produces a different hash and therefore a different URL. You can safely set `max-age=31536000, immutable` and the browser will not even revalidate on a hard refresh. With `?v=`, you are asserting immutability about a path that *does* change, which is a lie that eventually bites.
3. **Deploys become atomic and rollback-safe.** Old and new assets coexist at distinct URLs. A user who loaded the old HTML five minutes ago can still fetch the old JS. With query-string versioning the old URL now returns the *new* file, and a half-loaded page gets a mismatched JS/CSS pair — the classic "deploy caused a blank screen for users mid-session" incident.
4. **Unchanged files keep their hash, so they keep their cache entry.** Rebuild and redeploy 200 assets of which 3 changed, and only 3 URLs are new. Query-string versioning that bumps a global `?v=` invalidates all 200 at once, producing a thundering herd to origin on every deploy.
5. **No purge is ever required for static assets.** This removes an entire operational procedure and its failure modes.

The cost is that your build tool must emit hashed names and your HTML must reference them — which is what every modern bundler does by default. The HTML itself is then the *only* thing that needs a short TTL, because it is the index that points at the hashed assets.

This yields the standard two-tier policy:

```
HTML           → s-maxage=60, stale-while-revalidate=600   (short, revalidated)
Hashed assets  → max-age=31536000, immutable                (effectively forever)
```

### Purge versus TTL expiry

**TTL expiry** is passive: the object becomes stale when its clock runs out. It requires no action and no coordination, which is why it is reliable.

**Purge** (also called *invalidate*) is active: you call the CDN's API and tell it to drop an object now. Three flavours:

| Purge type | Mechanism | Typical propagation | Risk |
|---|---|---|---|
| **Single URL** | Exact key removed everywhere | 1–10 seconds, commonly quoted as "under 5 s" | Low |
| **Surrogate key / cache tag** | Objects tagged `book:9780141036144` at response time; purge by tag removes all of them | 1–10 seconds | Low; this is the best tool for content-driven invalidation |
| **Wildcard / purge-everything** | Entire zone or path prefix dropped | **30 seconds to several minutes** | **High** — your hit ratio goes to zero and 100 % of traffic hits origin simultaneously |

Realistic expectations matter here, because engineers routinely design workflows that assume purge is instantaneous and global. It is neither:

- Propagation is **eventually consistent across POPs**. For 5–30 seconds after your API call, different users in different cities legitimately see different versions. Any design requiring "everyone sees the new price at the same instant" cannot be built on purge.
- Purge does **not** reach browsers. A user holding a `max-age=3600` copy will keep it for up to an hour no matter what you purge. This is the whole reason `s-maxage` exists.
- `purge-everything` is a self-inflicted stampede. For BookNook, going from 96 % offload to 0 % instantaneously multiplies origin request rate by **25×**. If you must do it, do it off-peak, or use tags.

**Rule of thumb:** design so that purge is an exception, not a workflow. Static assets use content hashes and are never purged. Catalogue pages use short `s-maxage` plus `stale-while-revalidate` and self-correct within a minute. Purge is reserved for genuine emergencies — a mispriced product, a legal takedown, leaked content.

### Edge compute

**Edge compute** runs your code on the edge server itself, in the request path. The two reference implementations are **Cloudflare Workers** (V8 isolates, ~5 ms cold start, JavaScript/WASM) and **AWS Lambda@Edge / CloudFront Functions** (Node/Python at regional edges, or a restricted JS runtime at the true edge).

The defining constraints, which determine what belongs there:
- **No durable local state.** Each POP is independent. Anything you write at the edge is local to that POP unless you call back to a central store — at which point you have paid the latency you were trying to avoid.
- **Tight CPU and memory limits.** Typically single-digit to tens of milliseconds of CPU and tens of MB of memory. These are not containers.
- **Latency to your database is the origin RTT.** An edge function in Mumbai calling a Virginia database is *slower* than just hitting the origin, because it adds a hop without removing one.

What **genuinely belongs** at the edge:

1. **Request normalisation** — stripping tracking query parameters, canonicalising the `Accept` header, collapsing `User-Agent` into a device class. Directly raises hit ratio.
2. **Cache-key manipulation** — building a custom key from, say, country plus device class.
3. **Routing, rewrites and redirects** — A/B test bucketing, locale redirects, legacy URL mapping. Saves a full origin round trip per redirect.
4. **Auth gate / token validation** — verifying a signed JWT or a signed URL and rejecting invalid requests at the edge, so bad traffic never reaches your origin. Verification is local CPU; no network call.
5. **Header and security policy injection** — adding `Content-Security-Policy`, HSTS, CORS headers without an origin deploy.
6. **Response assembly from cached fragments** — stitching a cached shell with a small personalised fragment, so the bulk stays cacheable.
7. **Bot and abuse filtering** — cheap heuristics applied before the request consumes origin capacity.

What **does not** belong at the edge:
- Business logic that needs your primary database (ordering, inventory decrement, payments).
- Anything requiring strong consistency across POPs.
- Anything needing more than a few milliseconds of CPU, large dependencies, or a long-lived connection pool.
- Anything whose correctness depends on seeing all traffic — rate limiting at the edge is per-POP unless you use a coordinated counter, so a "100 requests per minute" limit across 300 POPs can permit far more.

The test: **does the work remove a round trip or an origin request, using only data already present in the request?** If yes, it belongs at the edge. If it needs to ask the origin anything, it almost certainly does not.

### Dynamic content acceleration

A CDN can help with content it **cannot cache at all**. This is called **DSA** (Dynamic Site Acceleration) or dynamic acceleration, and the mechanism is network, not storage:

1. **Connection termination at the edge.** TCP and TLS handshakes complete against a server 10 ms away instead of 220 ms away. For TLS 1.3 that removes 2 RTTs of the critical path; for TLS 1.2, 3 RTTs. That alone is often the largest single win for an uncacheable page.
2. **Warm, pooled, long-lived connections edge→origin.** The edge keeps persistent TLS connections open to your origin, so the per-request cost is one RTT of data transfer, not a fresh handshake.
3. **Optimised backbone routing.** The CDN's private network between POPs typically beats public BGP paths, both in RTT and in loss. Lower loss matters disproportionately because TCP interprets loss as congestion and collapses its window.
4. **Larger initial congestion windows and modern congestion control** (BBR) on the edge→user leg, tuned far more aggressively than a default origin.
5. **Protocol upgrade.** The edge can speak HTTP/3 (QUIC) to the user even if your origin only speaks HTTP/1.1, eliminating head-of-line blocking (Lesson 9) on lossy mobile networks.

Measured effect for a genuinely uncacheable API call from Mumbai to a Virginia origin: typically **30–50 % reduction in time-to-first-byte**, with the gain concentrated in connection setup. It is not as dramatic as a cache hit, but it is free once the CDN is in place, and it applies to the checkout flow — the requests that make money.

### What should NOT be put on a CDN

- **Per-user responses without `private`.** Anything containing a name, an address, a cart, an order. If it must traverse the CDN, mark it `Cache-Control: private, no-store` and confirm the CDN honours it.
- **Responses that set `Set-Cookie`.** Caching one of these hands one user's session cookie to the next. Most CDNs refuse by default; do not override that.
- **Data requiring strong consistency.** Stock counts at the moment of checkout, account balances, auction bids. A CDN is an eventually consistent store by construction; the full treatment is **Phase 3, Topic 20 — CAP theorem**.
- **Write endpoints.** `POST`, `PUT`, `PATCH`, `DELETE` are never cacheable. They can be *proxied* for acceleration, but there is nothing to cache.
- **Very large, very cold objects.** A 2 GB archive downloaded twice a year occupies edge storage and gets evicted (Lesson 30) before the second request. Serve it from object storage directly.
- **Content with legal data-residency constraints.** Caching EU personal data on a POP in a non-adequate jurisdiction can be a compliance breach regardless of its technical merit.
- **Anything where you cannot tolerate the CDN holding your TLS private key or plaintext.** Some regulated workloads genuinely cannot.

## 5. Request / data flow

A user in Mumbai loads a BookNook product page. The page HTML references 12 cover images. Follow one image, `/img/covers/9780141036144.jpg`, from start to finish.

1. The browser resolves `cdn.booknook.com`. DNS (Lesson 4) returns a CNAME to the CDN, whose nameservers return an anycast address. The resolver caches it for the record's TTL.
2. The browser opens TCP to that address. BGP anycast routes the packets to the **nearest POP**, Mumbai, 12 ms away — not to Virginia, 220 ms away. No DNS geolocation was required; the internet's routing did it.
3. TLS 1.3 handshake completes against the edge server, which presents a certificate for `cdn.booknook.com`. One RTT (12 ms), not 220 ms.
4. The browser sends `GET /img/covers/9780141036144.jpg` with `Accept-Encoding: gzip, br`.
5. The edge computes the **cache key**: host + path, plus `Accept-Encoding` because the origin previously returned `Vary: Accept-Encoding`. Tracking query parameters, if any, were stripped by the configured query-string allow-list — so a link from the newsletter and a link from Google map to the same key.
6. **Lookup.** Assume a miss (first request for this cover in Mumbai today).
7. **Request coalescing.** 40 other users in Mumbai request the same cover in the same 200 ms window. The edge does **not** issue 41 origin fetches; it issues one and queues the rest against it. This is edge-level stampede protection (Lesson 29), on by default at every major CDN.
8. The edge forwards the miss to the **origin shield** POP in Virginia over a warm, pooled connection on the CDN backbone. The shield checks *its* cache.
9. Shield hit → the object returns without touching BookNook at all. (If the shield misses, it makes one origin fetch to BookNook's load balancer, which routes to an app server or object store.)
10. Response travels back: origin/shield → Mumbai edge. Headers include `Cache-Control: public, max-age=60, s-maxage=86400, stale-while-revalidate=604800, stale-if-error=604800`, `ETag`, `Vary: Accept-Encoding`.
11. The Mumbai edge **stores** the object under the computed key with an edge TTL of 86,400 s (from `s-maxage`, which overrides `max-age` for shared caches).
12. The edge serves all 41 queued users, rewriting the `Cache-Control` as-is so browsers cache for 60 s only, and adds `Age: 0` plus a `CF-Cache-Status`/`X-Cache: MISS` header.
13. The next Mumbai request for this cover is a **HIT**: no origin contact, ~12 ms RTT, `Age: 37`.
14. After 86,400 s the edge entry becomes stale. Because `stale-while-revalidate=604800` is set, the next user is served the **stale object immediately** (still ~12 ms) while the edge asynchronously revalidates with `If-None-Match: "<etag>"`.
15. Origin replies `304 Not Modified` — a few hundred bytes, no image body. The edge resets freshness. Nobody waited.
16. BookNook's publishing team replaces the cover. The CMS issues a purge by **surrogate key** `book:9780141036144`. Within ~1–10 s every POP drops the entry. The next request is a miss and refills.
17. Browsers still holding the 60-second copy continue to show the old cover for up to 60 s. This is expected and is the reason `max-age` is short while `s-maxage` is long.
18. Two days later BookNook's origin suffers a database outage and returns 503. Because `stale-if-error=604800` is set, edges continue serving the cached cover. Users see a working catalogue. The origin fleet is given room to recover without a thundering herd.

## 6. ASCII diagram

```
ORIGIN-ONLY — every byte crosses an ocean, every time
   Mumbai user                                      us-east-1 origin
      │  ── TCP SYN ─────────────────────────────────────▶ │
      │  ◀──────────────────────────────── SYN/ACK ─────── │   220 ms
      │  ── TLS ClientHello ──────────────────────────────▶ │   220 ms
      │  ◀──────────────────────────── ServerHello ─────── │
      │  ── GET /img/covers/978...jpg ────────────────────▶ │   220 ms
      │  ◀──────── 95 KB, 3 slow-start rounds ──────────── │   660 ms
                                        TOTAL ≈ 1,320 ms for ONE image
                                        × 12 covers on the page


WITH CDN — the ocean is crossed ONCE, by the CDN, not the user
   Mumbai user        Mumbai POP (edge)      Shield POP        Origin
      │  12 ms RTT  ▶   [ cache ]
      │ ◀─ HIT 96% ──   [ 95 KB ]                            (never contacted)
                             │ MISS 4%
                             │ ── CDN backbone ──▶ [ shield cache ]
                                                        │ MISS 15% of misses
                                                        │ ──▶ [ BookNook ]
      TOTAL on hit ≈ 72 ms            Origin sees 0.04 × 0.15 = 0.6% of traffic


CACHE KEY — too wide destroys the hit ratio
   /cover.jpg?utm_source=newsletter  ─┐
   /cover.jpg?utm_source=google      ─┼─ 3 entries, IDENTICAL bytes,
   /cover.jpg?utm_source=twitter     ─┘  3 origin fills, 33% hit ratio
                 │ strip non-allow-listed query params at the edge
                 ▼
   /cover.jpg                         ── 1 entry, 1 fill, 99% hit ratio


VARY — too wide is the SAME bug, via headers
   Vary: Accept-Encoding   →   2 variants (gzip, br)        ✓ correct
   Vary: User-Agent        →   ~200,000 variants            ✗ hit ratio ≈ 0
   Vary: Cookie            →   1 variant PER USER           ✗ and a data-leak
                                                               risk if you
                                                               ever drop it


TIERED CACHING — why the shield exists
   WITHOUT SHIELD                        WITH SHIELD
   POP1 ─┐                               POP1 ─┐
   POP2 ─┤                               POP2 ─┤
   ...   ├──▶ ORIGIN  (120 fills)        ...   ├──▶ SHIELD ──▶ ORIGIN
   POP120┘                               POP120┘    (119 hits)   (1 fill)


CACHE BUSTING — why hashes beat query strings
   ?v=47  : path is CONSTANT  → old HTML + new JS can MISMATCH mid-deploy
            some proxies ignore the query string entirely → wrong file served
            "immutable" would be a LIE

   .4f3a9c1e.js : path CHANGES with content
            old and new coexist → atomic deploy, safe rollback
            unchanged files keep their hash → keep their cache entry
            max-age=31536000, immutable is TRUE
```

## 7. Detailed example

### BookNook

**Starting state.** Everything is served from one region, `us-east-1`. Three app servers behind an ALB (Lesson 21), covers served by the app from an attached volume. Lesson 1's measurement: covers are ~40× the application's own bandwidth. Monthly egress is ~85 TB, of which ~98.7 % is images, JS and CSS. The bill is roughly **$7,660/month** in egress alone, before compute. p95 page load in Mumbai is around **4.1 seconds**; in Virginia it is 380 ms. Indian traffic is 15 % of users and 3 % of revenue, and nobody has connected those two numbers.

**Step 1 — move static assets to a pull CDN.** `cdn.booknook.com` CNAMEs to the CDN; the origin is the existing load balancer. No code changes. Immediate result: hit ratio **71 %**. Disappointing. Investigation finds three causes:
- The CDN is configured to include the **full query string** in the cache key, and the marketing team appends `utm_*` parameters to every link. One cover has 2,000 cache entries.
- The origin emits `Vary: User-Agent` because an old device-detection middleware set it on every response, including images.
- JS and CSS are served as `/static/app.js` with `max-age=300`, so they expire every five minutes and refill from 120 POPs.

**Step 2 — fix the cache key.** Query-string allow-list reduced to `w` and `fm` (the image resizer's parameters); `Vary` reduced to `Accept-Encoding`. Hit ratio jumps to **93 %**. This is the single highest-leverage change in the whole project and it is pure configuration.

**Step 3 — content-hashed asset names.** The build emits `/static/app.4f3a9c1e.js`. Those get `Cache-Control: public, max-age=31536000, immutable`. The HTML gets `public, max-age=60, s-maxage=600, stale-while-revalidate=86400`. Hit ratio reaches **96.4 %**, and deploys stop causing a five-minute origin spike because only the 3 changed assets get new URLs.

**Step 4 — enable origin shield.** Shield POP placed in Virginia, next to the origin. Origin request rate drops from ~39 req/s (the 4 % miss stream, fanned out across 120 POPs) to ~6 req/s. Origin fleet is reduced from 3 servers to 2, with more headroom than before.

**Step 5 — add `stale-while-revalidate` and `stale-if-error` everywhere cacheable.** Two weeks later a bad migration takes the primary database down for 22 minutes. Previously that would have been a full outage. Instead: the catalogue continued serving from stale edge copies, search degraded, and only checkout failed. Measured revenue impact fell by roughly 80 % versus the previous comparable incident.

**Step 6 — edge compute, used narrowly.** Three Workers are deployed:
- Strip `utm_*` and `fbclid` from the cache key (locks in Step 2 so a future marketing campaign cannot regress it).
- Redirect `/book/<isbn>` legacy URLs to `/books/<isbn>` at the edge, removing a 220 ms origin round trip for 4 % of traffic.
- Normalise `User-Agent` to `mobile|desktop` and set it as a cache-key component, so the mobile-optimised HTML can be cached instead of being `private`.

A fourth proposal — "run the recommendations engine at the edge" — is **rejected**, because recommendations need the user's order history from the primary database in Virginia. An edge function in Mumbai calling Virginia would add a hop and remove none. It stays at the origin, behind a Redis cache (Lesson 27).

**Result.** Egress cost falls from ~$7,660 to ~$2,100/month. Mumbai p95 page load falls from 4.1 s to 640 ms. Indian conversion rate rises measurably. Origin capacity requirement falls by a third. Section 9 computes these numbers in full.

### Contrasting example — a live sports streaming service

Same technology, inverted constraints, which is why it is instructive.

A live football match is delivered as **HLS**: the video is cut into 4-second `.ts` segments plus a `.m3u8` **manifest** listing the currently available segments.

| | BookNook covers | Live match segments |
|---|---|---|
| Object lifetime | Years | A segment is relevant for ~30 seconds, then never requested again |
| TTL | `s-maxage=86400` | Segment `s-maxage=60`; manifest `s-maxage=2` |
| Cache key cardinality | 400,000 covers, long tail | A handful of live segments per bitrate — tiny working set, *extreme* concurrency |
| Dominant risk | Low hit ratio from key fragmentation | **Stampede on the manifest**: 3 million viewers poll it every 4 seconds = 750,000 req/s, all for an object that changes every 4 seconds |
| Why the CDN is essential | Cost and distance | **Fan-out**: no origin on earth serves 750,000 req/s; the edge must absorb essentially 100 % |
| Purge | Occasionally, by tag | Never — TTLs are shorter than any purge propagation time would be |
| Hit ratio target | 96 % | **99.99 %** — a 0.1 % miss rate is still 750 origin req/s |

Two design lessons transfer directly. First, **when the TTL is shorter than the purge propagation time, purge is meaningless** — the object expires before the purge arrives, so you design purely with TTLs. Second, **request coalescing is load-bearing, not an optimisation**: with a 2-second manifest TTL and 750,000 req/s, every expiry would produce a 750,000-request origin stampede without it. The same mechanism that was a nicety for BookNook is the only thing keeping the stream alive here.

And one anti-pattern shared by both: a team once put per-viewer DRM licence issuance behind the same CDN hostname as the segments. Licences are per-user and uncacheable; mixing them into the same zone made it impossible to reason about hit ratio and risked a key leak if `Vary`/`private` were ever misconfigured. Separate hostnames for cacheable and uncacheable content is cheap insurance.

## 8. Code or configuration example

> Illustrative only — not production-ready. Error handling, security headers, authentication, signed URLs, observability, rate limits and vendor-specific hardening are omitted or simplified for clarity. Do not paste this into a production zone.

### Origin response headers — the policy that does most of the work

```http
# ---------- Content-hashed static asset: /static/app.4f3a9c1e.js ----------
# public        : shared caches (the CDN) may store it, not just the browser
# max-age       : browser keeps it for 1 year...
# immutable     : ...and must NOT revalidate, even on a manual reload.
#                 This is only TRUE because the filename contains a content
#                 hash: changing the bytes changes the URL.
Cache-Control: public, max-age=31536000, immutable
# Tell caches there are gzip and brotli variants. Low cardinality (2-3), so
# this is a SAFE Vary. Never Vary on User-Agent or Cookie.
Vary: Accept-Encoding


# ---------- Catalogue HTML: /books/9780141036144 ----------
# max-age=60     : browsers re-ask every minute, so a purge is effective within
#                  60 s even though we CANNOT purge a browser cache.
# s-maxage=600   : SHARED caches (the CDN) keep it 10 minutes, overriding
#                  max-age. This is how you get high offload AND fast purges.
# stale-while-revalidate=86400
#                : after the 600 s expires, serve the STALE copy instantly and
#                  refresh in the background. No user ever waits on an origin
#                  fetch after the first one. (Edge-level Lesson 29 mitigation.)
# stale-if-error=604800
#                : if the origin 5xxs or is unreachable, keep serving the stale
#                  copy for up to 7 days. This is what kept the catalogue up
#                  during the 22-minute database outage in Section 7.
Cache-Control: public, max-age=60, s-maxage=600, stale-while-revalidate=86400, stale-if-error=604800
# Strong validator so revalidation returns a 304 (a few hundred bytes) instead
# of the whole body.
ETag: "v3-9780141036144-7f1c"
# Surrogate keys: lets the CMS purge "everything about this book" in one call,
# instead of enumerating URLs. Stripped by the CDN before reaching the client.
Surrogate-Key: book:9780141036144 author:1421 catalogue


# ---------- Logged-in account page: /account ----------
# private  : the CDN MUST NOT store this. Only the user's own browser may.
# no-store : do not persist it anywhere at all.
# Getting this wrong serves one customer's address to the next visitor.
Cache-Control: private, no-store
```

### nginx at the origin — emit the right headers, and never cache the wrong thing

```nginx
server {
    listen 443 ssl;
    server_name origin.booknook.com;

    # Hashed assets: safe to pin forever because the URL changes with content.
    location ~* "^/static/.+\.[0-9a-f]{8}\.(js|css|woff2)$" {
        add_header Cache-Control "public, max-age=31536000, immutable" always;
        add_header Vary "Accept-Encoding" always;
        # Do NOT add Vary: User-Agent here. A legacy device-detection module
        # doing that globally is what held BookNook's hit ratio at 71%.
        try_files $uri =404;
    }

    location /img/covers/ {
        add_header Cache-Control
            "public, max-age=3600, s-maxage=86400, stale-while-revalidate=604800, stale-if-error=604800" always;
        add_header Vary "Accept-Encoding" always;
        try_files $uri /img/covers/placeholder.jpg;
    }

    location /books/ {
        proxy_pass http://app_upstream;
        # Strip any Set-Cookie the app emits on a PUBLIC page. If a cacheable
        # response carries Set-Cookie, a shared cache can hand one user's
        # session to another. Most CDNs refuse to cache these by default --
        # removing it at the origin means we never rely on that default.
        proxy_hide_header Set-Cookie;
        add_header Cache-Control
            "public, max-age=60, s-maxage=600, stale-while-revalidate=86400, stale-if-error=604800" always;
    }

    # Anything user-specific is explicitly fenced off from shared caches.
    location ~* "^/(account|cart|checkout|orders)" {
        proxy_pass http://app_upstream;
        add_header Cache-Control "private, no-store" always;
    }
}
```

### CDN cache-key configuration (vendor-neutral pseudo-config)

```yaml
# The cache key decides what counts as "the same request".
# Too WIDE  -> fragmentation, low hit ratio, high origin load.
# Too NARROW -> different users collide on one entry. That is a security bug.
cache_key:
  include:
    - host
    - path
    - query_params:
        # ALLOW-LIST, never deny-list. A deny-list silently fails the moment
        # marketing invents a new tracking parameter.
        allow: [w, fm, q]          # image resizer parameters only
    - header: Accept-Encoding      # matches the origin's Vary
    - custom: device_class         # set by the edge function below (2 values)
  exclude:
    - all_cookies                  # cookies must NEVER enter a shared cache key

origin_shield:
  enabled: true
  location: us-east-1              # next to the origin: 120 POPs -> 1 fill
  # Trade-off: adds ~15-30 ms to the MISS path only, in exchange for roughly
  # a 10x reduction in origin fill traffic and automatic stampede collapsing.

tiered_cache:
  mid_tier: regional               # edge -> regional -> shield -> origin

request_coalescing: true           # N concurrent misses for one key => 1 fetch

# TTLs the CDN applies when the origin forgets to send Cache-Control.
# Never leave this as "cache everything" -- that is how /account gets cached.
default_ttl:
  200: 0s                          # fail CLOSED: no header => no caching
  404: 10s                         # brief negative caching absorbs scanner floods
  5xx: 0s
```

### Cloudflare Worker — work that genuinely belongs at the edge

```javascript
export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // 1. NORMALISE the cache key. Tracking parameters do not change the bytes,
    //    so they must not create new cache entries. Pure local CPU, no network.
    const ALLOWED = new Set(["w", "fm", "q"]);
    for (const key of [...url.searchParams.keys()]) {
      if (!ALLOWED.has(key)) url.searchParams.delete(key);
    }

    // 2. LEGACY REDIRECT at the edge. Returning 301 here saves a full
    //    origin round trip (~220 ms from Mumbai) for ~4% of traffic.
    if (url.pathname.startsWith("/book/")) {
      url.pathname = url.pathname.replace("/book/", "/books/");
      return Response.redirect(url.toString(), 301);
    }

    // 3. DEVICE CLASS. Collapsing ~200,000 User-Agent strings into 2 buckets
    //    turns an uncacheable response into a cacheable one. This is the
    //    correct alternative to `Vary: User-Agent`.
    const ua = request.headers.get("User-Agent") || "";
    const deviceClass = /Mobi|Android|iPhone/.test(ua) ? "mobile" : "desktop";
    url.searchParams.set("__dc", deviceClass);   // becomes part of the key

    // 4. AUTH GATE. Verifying a signed token is local CPU only -- no database
    //    call -- so rejecting here removes load from the origin entirely.
    //    NOTE: anything needing the primary DB does NOT belong here. An edge
    //    function in Mumbai calling a Virginia database ADDS a hop and removes
    //    none; that work stays at the origin.
    if (url.pathname.startsWith("/premium/")) {
      const token = request.headers.get("Authorization");
      if (!(await isSignatureValid(token, env.SIGNING_KEY))) {
        return new Response("Forbidden", { status: 403 });
      }
    }

    const cacheKey = new Request(url.toString(), request);
    const cache = caches.default;

    let response = await cache.match(cacheKey);
    if (!response) {
      response = await fetch(cacheKey);          // goes to shield, then origin
      response = new Response(response.body, response);
      // ctx.waitUntil lets the response return to the user IMMEDIATELY while
      // the cache write finishes in the background. Forgetting this makes the
      // user wait on cache I/O.
      ctx.waitUntil(cache.put(cacheKey, response.clone()));
    }

    // 5. SECURITY HEADERS injected without an origin deploy.
    response = new Response(response.body, response);
    response.headers.set("Strict-Transport-Security", "max-age=31536000");
    response.headers.set("X-Content-Type-Options", "nosniff");
    return response;
  },
};
```

### Build configuration — content hashing

```javascript
// webpack.config.js (any modern bundler does this; the principle is universal)
module.exports = {
  output: {
    // [contenthash] is a hash of the FILE CONTENTS, not of the build or the
    // version number. Two consequences that query-string versioning cannot give:
    //  (a) an unchanged file keeps its name, so it keeps its cache entry --
    //      a deploy invalidates only what actually changed;
    //  (b) old and new assets coexist at different URLs, so a user who loaded
    //      the old HTML can still fetch the matching old JS. No mid-deploy
    //      blank screens, and rollback is instant.
    filename: "[name].[contenthash:8].js",
    path: __dirname + "/dist/static",
    publicPath: "https://cdn.booknook.com/static/",
  },
  // The HTML is the only short-TTL object: it is the index that points at the
  // hashed assets. Purge/expire the HTML and everything else follows.
};
```

### Purge — by tag, never by wildcard

```bash
# GOOD: surrogate-key purge. Removes every URL tagged with this book --
# the product page, the JSON API response, the cover, the author page --
# in one call. Propagates to all POPs in roughly 1-10 seconds.
curl -X POST "https://api.cdn.example/zones/$ZONE/purge" \
     -H "Authorization: Bearer $TOKEN" \
     -d '{"tags": ["book:9780141036144"]}'

# DANGEROUS: purge everything. Hit ratio goes from 96% to 0% instantly, so
# origin request rate multiplies by ~25x in one step -- a self-inflicted
# stampede (Lesson 29). Propagation is also the slowest kind, 30 s to minutes.
# If you ever genuinely need this, do it off-peak and watch origin saturation.
# curl -X POST ... -d '{"purge_everything": true}'
```

## 9. Numerical example

Every assumption is stated. All prices are **typical list prices as of writing** and vary by vendor, region and committed-use discount; the *method* is what matters, not the exact cents.

### Assumptions

```
Traffic
  Page views per day                     2,000,000
  Days per month                                30
  Assets per page view                          14   (1 HTML + 1 JS + 1 CSS + 11 images)

Payload sizes (compressed where applicable)
  HTML (gzipped)                              18 KB
  JS bundle                                  220 KB
  CSS                                         40 KB
  Cover images: 11 × 95 KB                 1,045 KB
  ------------------------------------------------
  Static per page view (JS+CSS+images)     1,305 KB
  Dynamic per page view (HTML)                18 KB
  Total per page view                      1,323 KB

Pricing
  Cloud origin egress to internet        $0.090 / GB
  CDN egress to end user                 $0.020 / GB
  (1 GB = 1,000 MB throughout, matching how cloud vendors bill)
```

**Sanity check against Lesson 1.** Static bytes ÷ dynamic bytes = 1,305 ÷ 18 = **72.5×** for a cold visitor, and roughly **40×** once you account for returning visitors whose JS and CSS are already in the browser cache but whose images are mostly new (different books each visit). That is the "40× the application's own bandwidth" figure from Lesson 1, and it is the entire financial motivation for this lesson.

### Part A — Monthly egress volume

```
Static bytes/day  = 2,000,000 × 1,305 KB = 2,610,000,000 KB = 2,610 GB
Dynamic bytes/day = 2,000,000 ×  18 KB   =    36,000,000 KB =    36 GB
Total/day                                                    = 2,646 GB

Static/month      = 2,610 × 30 = 78,300 GB
Dynamic/month     =    36 × 30 =  1,080 GB
Total/month                     = 79,380 GB  ≈ 79.4 TB
```

### Part B — Cost, origin-only

```
79,380 GB × $0.090/GB = $7,144.20 per month
Annualised:             $85,730.40
```

All of it leaves one region, so all of it also traverses one load balancer and one app fleet.

### Part C — Cost with a CDN

Additional assumption: **static hit ratio 96 %** (achieved in Section 7 after fixing the cache key); HTML is cached at the edge too, but with a 600 s `s-maxage`, giving a **70 %** hit ratio on HTML.

```
USER-FACING EGRESS (now billed by the CDN, not the cloud)
  79,380 GB × $0.020/GB                            = $1,587.60

ORIGIN FILL — static
  Miss fraction                                    = 1 - 0.96 = 0.04
  Fill volume   = 78,300 GB × 0.04                 = 3,132 GB
  Cost          = 3,132 × $0.090                   =   $281.88

ORIGIN FILL — dynamic HTML
  Miss fraction                                    = 1 - 0.70 = 0.30
  Fill volume   = 1,080 GB × 0.30                  =   324 GB
  Cost          = 324 × $0.090                     =    $29.16

TOTAL WITH CDN                                     = $1,898.64 / month
```

### Part D — Adding an origin shield

Additional assumption: 120 POPs serve meaningful traffic; the shield absorbs **85 %** of the edge miss stream (the remaining 15 % are genuinely-first fetches of long-tail objects).

```
Static fill reaching the true origin  = 3,132 GB × 0.15 = 469.8 GB
  Cost                                = 469.8 × $0.090  =  $42.28
Dynamic fill reaching the origin      =   324 GB × 0.15 =  48.6 GB
  Cost                                =  48.6 × $0.090  =   $4.37

TOTAL WITH CDN + SHIELD = $1,587.60 + $42.28 + $4.37    = $1,634.25 / month
```

### Part E — The saving

```
Origin-only                       $7,144.20
CDN + shield                      $1,634.25
---------------------------------------------
Monthly saving                    $5,509.95   (77.1% reduction)
Annual saving                    $66,119.40

Effective cost per GB delivered   $1,634.25 / 79,380 GB = $0.0206/GB
                                  versus $0.090/GB  → 4.4x cheaper per byte
```

**Break-even check.** A commodity CDN plan costs roughly $200–$2,000/month in fixed fees depending on tier and features. Even at the top of that range the project pays for itself in the first month on bandwidth alone, before counting the compute reduction below.

### Part F — Origin request rate (the capacity argument)

```
Asset requests/day = 2,000,000 × 14 = 28,000,000
Average req/s      = 28,000,000 / 86,400 = 324 req/s
Peak factor (typical e-commerce diurnal peak ≈ 3×) → 972 req/s at peak

ORIGIN-ONLY
  Origin must be sized for                      972 req/s

WITH CDN, NO SHIELD
  Origin sees 4% of static + 30% of HTML.
  Static share of requests = 13/14 = 0.929 ; HTML share = 1/14 = 0.071
  Origin fraction = (0.929 × 0.04) + (0.071 × 0.30)
                  = 0.0372 + 0.0213 = 0.0585  → 5.85%
  Peak origin load = 972 × 0.0585 = 56.9 req/s

WITH CDN + SHIELD (shield absorbs 85% of the miss stream)
  Origin fraction = 0.0585 × 0.15 = 0.00878  → 0.88%
  Peak origin load = 972 × 0.00878 = 8.5 req/s

Reduction: 972 / 8.5 = 114x
```

At 8.5 req/s the origin fleet is no longer sized by request volume at all; it is sized by the slowest endpoints and by redundancy requirements. BookNook drops from 3 app servers to 2 and still has more headroom than before. At roughly $150/server/month that is another **$150/month**, which is small next to the bandwidth saving — a useful reminder that for content-heavy sites **the CDN's financial case is egress, not compute**.

### Part G — p95 latency by geography

**Assumptions:**
- Origin in `us-east-1`. Cold-connection cost for one 95 KB image = **3 RTT setup** (TCP 1 + TLS 1.3 1 + request/response 1) **+ 3 RTT transfer** (TCP slow start: initial window ≈ 14.6 KB, then ≈ 29 KB, then ≈ 58 KB, cumulatively passing 95 KB on the third round) = **6 RTT total**.
- Edge RTTs are measured from a user to their nearest POP in the same metro.
- Traffic mix: US-East 25 %, US-West 20 %, London 15 %, Mumbai 15 %, Frankfurt 10 %, São Paulo 10 %, Sydney 5 %.

```
Region      traffic  RTT→origin  origin p95   RTT→edge  CDN-hit p95
                        (ms)      (6×RTT)       (ms)      (6×RTT)
US-East       25%        15          90           8          48
US-West       20%        70         420          10          60
London        15%        85         510           9          54
Mumbai        15%       220       1,320          12          72
Frankfurt     10%        95         570          11          66
São Paulo     10%       125         750          14          84
Sydney         5%       230       1,380          13          78
```

**Weighted origin-only p95:**
```
0.25×90  =  22.5
0.20×420 =  84.0
0.15×510 =  76.5
0.15×1320= 198.0
0.10×570 =  57.0
0.10×750 =  75.0
0.05×1380=  69.0
---------------------
TOTAL    = 582.0 ms
```

**Weighted CDN-hit p95:**
```
0.25×48 = 12.00
0.20×60 = 12.00
0.15×54 =  8.10
0.15×72 = 10.80
0.10×66 =  6.60
0.10×84 =  8.40
0.05×78 =  3.90
---------------------
TOTAL   = 61.80 ms
```

**Blending in the misses.** A miss costs the edge latency plus an edge→shield→origin fetch over the CDN's warm, pooled backbone — roughly **2 × the origin RTT**, not 6, because the connection is already established and the backbone path is better.

```
Traffic-weighted origin RTT
  = 0.25×15 + 0.20×70 + 0.15×85 + 0.15×220 + 0.10×95 + 0.10×125 + 0.05×230
  = 3.75 + 14.0 + 12.75 + 33.0 + 9.5 + 12.5 + 11.5
  = 97.0 ms

Miss latency ≈ 61.8 + (2 × 97.0) = 255.8 ms

Mean latency = 0.96 × 61.8 + 0.04 × 255.8
             = 59.33 + 10.23 = 69.6 ms
```

**But note what happens to the percentiles** (Lesson 16):

```
Hit ratio = 96%  ⇒ the slowest 4% of requests are the misses.
p95 falls INSIDE the hit distribution       → p95  ≈ 62 ms
p99 falls INSIDE the miss distribution      → p99  ≈ 256 ms
p95 improvement = 582 / 62 = 9.4x
```

This is the single most important line in the calculation: **the hit ratio sets which percentile the miss penalty starts to dominate.** At 96 % offload, misses govern p99 but not p95. Push the hit ratio to 99 % and misses govern only p99.9. Let it slip to 90 % and misses govern p95 as well, and your headline latency number doubles — which is why the cache-key work in Section 7 Step 2 mattered so much more than it looked.

**Per-page effect.** The page needs 14 assets, multiplexed over a single HTTP/2 connection, so they do not serialise. Measured end-to-end p95 page load for Mumbai fell from **4,100 ms to 640 ms** — a 6.4× improvement, less than the per-asset 18× because DNS, HTML render, JS parse and execution are unchanged by a CDN. Always distinguish *per-asset* latency gains from *page-level* gains; conflating them is how CDN projects over-promise.

## 10. Trade-offs

**Pull CDN with long edge TTLs**
- *Advantages:* enormous egress saving; 5–10× latency reduction for distant users; 100× origin offload; DDoS absorption included; no application changes required for static content.
- *Disadvantages:* content is eventually consistent across POPs; purge is not instant; you must get the cache key and `Vary` right or you get either fragmentation or a data leak; TLS plaintext is visible to a third party.
- *Operational cost:* a new vendor, a new configuration surface, a new set of dashboards (hit ratio, offload, 5xx-from-edge), and a new on-call dependency.
- *Unnecessary complexity when:* your users are all in one metro and your payload is 20 KB of JSON. Then the CDN adds a hop and a vendor for a few milliseconds.

**Origin shield / tiered caching**
- *Advantages:* 5–10× further reduction in origin fills; automatic stampede collapsing; far fewer origin TLS connections.
- *Disadvantages:* adds 15–40 ms to the miss path; creates a concentration point; one more thing to configure wrongly.
- *Choose it when:* your origin's bottleneck is request rate or connection count, or your catalogue has a long tail (which fragments across POPs). Skip it if you have a tiny, uniformly-hot object set.

**`stale-while-revalidate` + `stale-if-error`**
- *Advantages:* removes origin latency from the user's critical path entirely; turns the CDN into a read-only disaster-recovery layer; costs nothing.
- *Disadvantages:* users can see content up to `stale-*` seconds old; during an origin outage you may serve stale data for a long time without obvious signal.
- *Do not use when:* staleness is unsafe — prices at checkout, stock at the point of reservation, legally-mandated disclosures. Use `must-revalidate` there.

**Content-hashed filenames**
- *Advantages:* true `immutable`; atomic deploys; instant rollback; only changed files lose their cache entry; purge never needed for assets.
- *Disadvantages:* requires a build step and a manifest; the HTML must be short-TTL; debugging "which build is this user on?" needs the hash in a header or meta tag.
- *There is no realistic case for preferring query-string versioning.* It is a legacy workaround for not having a build pipeline.

**Edge compute**
- *Advantages:* removes whole round trips; raises hit ratio by normalising keys; applies policy without an origin deploy.
- *Disadvantages:* a second deployment target with different tooling, limits and debugging; no durable local state; easy to accidentally move business logic to a place that cannot see the database.
- *Unnecessary complexity when:* the same thing can be expressed in CDN configuration. Config does not have bugs at 3 a.m.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| Cache key includes `utm_*` query parameters | Pages feel slow for campaign traffic specifically; everyone else is fine | Hit ratio plotted *by traffic source*; a flood of distinct keys for identical bytes in the edge logs | Query-string allow-list at the CDN or in an edge function; hit ratio recovers within one TTL window |
| Origin emits `Vary: User-Agent` | Near-zero hit ratio; origin load matches pre-CDN levels while the CDN bill still arrives | Offload metric flat near 0 %; `Vary` visible in response headers | Remove the header at origin, or normalise User-Agent to a device class at the edge; purge affected paths |
| A personalised page cached as `public` | **One user sees another user's name, cart or address** | Usually a customer report — which is far too late; proactively, an automated check that no response with `Set-Cookie` is cacheable | Immediate purge by tag; set `private, no-store` at origin; strip `Set-Cookie` on public routes. Treat as a security incident, not a bug |
| `purge-everything` run during peak | Site-wide slowdown for several minutes; possible origin saturation and 5xx | Hit ratio cliff to 0 %; origin RPS spike of ~25× | Nothing to do but ride it out; prevent by restricting the API token's permissions and using tag-based purge |
| Origin down, no `stale-if-error` | Full outage — the edge dutifully forwards and returns 502/504 | Edge-reported 5xx rate; origin health checks (Lesson 24) | Add `stale-if-error`; many CDNs also offer an "always online"/serve-stale toggle that can be enabled during the incident |
| Edge TTL long, browser TTL also long, content wrong | Some users see stale content for hours after a purge; different users disagree | Reports that are inconsistent between users; `Age` header far higher than expected | Shorten `max-age`, lengthen `s-maxage`. You cannot fix the already-distributed browser copies — wait them out |
| POP-level outage or regional CDN incident | A whole country is slow or erroring while the rest of the world is fine | Synthetic monitoring from multiple regions; RUM latency broken down by country | CDN reroutes anycast automatically within seconds to minutes; your lever is a second CDN or a DNS failover to origin, which is a large and separate investment |
| Content-hashed deploy without keeping old assets | Users mid-session get 404s for JS/CSS and a blank page | Spike in 404s on `/static/`; JS error reporting | Keep the previous N builds' assets available; never delete on deploy. Recovers as users reload |
| Edge function throws on an unexpected request shape | 5xx for a subset of requests, often one route or one locale | Edge-function error rate and invocation logs, separate from origin errors | Roll back the function (seconds); add a top-level try/catch that falls through to plain `fetch(request)` |
| Negative caching of a transient 500 | Users see an error page that persists after the origin recovered | 5xx served with a non-zero `Age` header | Never cache 5xx for more than a second or two; purge the affected keys |
| Rate limiting implemented per-POP at the edge | An attacker spreads across POPs and exceeds the intended global limit by ~N× | Origin sees more traffic than the configured limit should allow | Use the CDN's coordinated/global rate limiter, or enforce the hard limit at the origin as well |
| Image resizer parameters not allow-listed (`?w=301`, `?w=302`…) | Slow image loads; origin CPU pinned on resizing | Huge key cardinality on image paths; origin resize queue depth | Snap `w` to a fixed set of allowed widths at the edge; reject or round anything else |

## 12. Alternatives

| Approach | What it actually does | Pros | Cons |
|---|---|---|---|
| **CDN (pull)** | Caches at hundreds of POPs near users | Best latency and offload per unit of effort; DDoS absorption; no app changes for static | Vendor dependency; eventual consistency; cache-key foot-guns; TLS plaintext at a third party |
| **CDN (push)** | You publish objects to CDN storage ahead of demand | No cold-start penalty; predictable for launch events | You own a publishing pipeline; you pay to store everything everywhere |
| **Multi-region origins + GeoDNS** | Full copies of your stack in several regions, DNS steers users | Serves *dynamic* and write traffic closer; no third party in the TLS path | Multiplies infrastructure cost; forces you to solve cross-region data replication (Lesson 32 and **Phase 2, Topic 18 — Database partitioning and sharding**); DNS caching makes failover slow (Lesson 4) |
| **Object storage with public access** (S3, GCS) | Serves static files directly from a storage service | Cheap storage; removes files from your app servers; trivially simple | Still single-region, so distance latency is unchanged; egress still at cloud prices; no edge logic |
| **Browser caching alone** (Lesson 6) | The user's own cache | Free; zero latency on a hit; no vendor | Helps only *returning* users on the *same* asset; a first visit is still a full origin round trip; cannot be purged |
| **Reverse-proxy cache at the origin** (Varnish, nginx) (Lesson 25) | One cache in front of your app, in your region | Full control; no vendor; good origin offload for compute | Does nothing for distance latency; does not absorb DDoS; it is your capacity being consumed |
| **Anycast without caching** | Many POPs that proxy but do not store | Terminates TLS near the user; DDoS absorption; works for uncacheable content | No offload — every request still reaches origin |
| **Peer-to-peer / WebRTC distribution** | Viewers relay segments to each other | Dramatically cheaper for very large live audiences | Only viable for synchronous mass events; NAT traversal; privacy and client-CPU concerns |
| **Multi-CDN** | Two or more CDN vendors with traffic steering | Removes single-vendor outage risk; competitive pricing; per-region performance routing | Doubles configuration surface; halves each vendor's hit ratio unless carefully partitioned; genuinely expensive in engineering time |

**How to choose, concretely.** If your payload is dominated by static bytes and your users are geographically spread → pull CDN, no argument. If your payload is small JSON and your users are in one metro → a reverse-proxy cache plus good browser caching is sufficient. If your *writes* need to be fast globally → a CDN cannot help you, and you are looking at multi-region data, which is Lesson 32 and beyond. If you are a major platform whose revenue stops when a CDN vendor has an incident → multi-CDN, and budget a quarter for it.

## 13. When to use it

1. **When static bytes dominate your egress.** BookNook's 40× image-to-application ratio from Lesson 1 is the textbook case. If images, video, JS, CSS or downloads are most of your traffic, a CDN pays for itself on bandwidth price alone, before any latency benefit.
2. **When your users are geographically distributed and your origin is not.** If more than ~20 % of users are more than ~50 ms from your origin, distance is your dominant latency term and no server-side optimisation can touch it.
3. **When you need origin offload for capacity, not just cost.** A product launch, a Black Friday, a viral post, or a TV mention produces a spike your origin cannot be sized for. 96 % offload turns a 25× spike into a 1× spike at the origin.
4. **When you need DDoS protection.** This is not optional for any public, revenue-bearing site. A single-origin IP is a single target; an anycast network is not.
5. **When you want cheap read-path disaster recovery.** `stale-if-error` keeps your catalogue serving through an origin outage for the cost of one header.
6. **When you want to apply edge logic** — redirects, auth gates, header policy, A/B bucketing — without a round trip and without an origin deploy.
7. **Even for uncacheable dynamic content**, if users are far away: dynamic acceleration typically removes 30–50 % of time-to-first-byte purely by terminating TCP/TLS near the user.

## 14. When *not* to use it

1. **When all your users are near your origin.** A domestic-only internal tool with a 20 ms origin RTT gains a few milliseconds and acquires a vendor, a bill, and a new configuration surface that can leak data if misconfigured. Use a reverse-proxy cache (Lesson 25) instead.
2. **When your content is entirely per-user and uncacheable.** A banking dashboard where every byte is personalised gets offload of exactly zero. Dynamic acceleration may still justify it, but *caching* does not — and you must be certain every response is `private`.
3. **When you cannot legally or contractually let a third party terminate TLS or store your data.** Regulated workloads, data-residency requirements, and some government contracts rule this out regardless of merit.
4. **When your content requires strong consistency.** Stock counts at the point of reservation, auction bids, account balances. A CDN is eventually consistent by construction (**Phase 3, Topic 20 — CAP theorem**); serving a stale balance is worse than serving a slow one.
5. **When your objects are large and cold.** A 2 GB archive fetched twice a year will be evicted (Lesson 30) before the second request, so you pay fill cost on every single request and get no offload.
6. **As a substitute for fixing an origin that is slow for non-distance reasons.** If your p95 is 3 seconds because of an unindexed query, a CDN hides it from cached paths and leaves checkout exactly as broken. Fix the query (Lesson 27 onwards); the CDN is not a performance tool for uncached work.

## 15. Common mistakes

**Beginner mistakes**
- Assuming "I added a CDN" means content is cached. Without correct `Cache-Control` from the origin, many CDNs cache nothing, and you have bought a proxy.
- Reading `no-cache` as "do not cache". It means "cache, but revalidate every time". The directive meaning "do not store" is `no-store`.
- Including the whole query string in the cache key, then wondering why campaign traffic is slow.
- Leaving `Vary: User-Agent` or `Vary: Cookie` in place, which sets the hit ratio to approximately zero while everything still *works*, so nobody notices for months.
- Using `?v=2` cache busting and being surprised when a mid-deploy user gets old HTML with new JS and a blank screen.
- Expecting a purge to be instantaneous and global, and designing a price-change workflow that assumes it.
- Running `purge-everything` at peak to fix one wrong image.
- Setting a long `max-age` on HTML, then discovering that no purge can reach a browser cache.
- Caching an endpoint that returns `Set-Cookie`, thereby sharing one user's session with the next visitor.
- Measuring only *request* hit ratio and ignoring *byte* hit ratio. A 96 % request hit ratio with all the misses on large objects can still mean most of your bytes come from origin.

**Experienced-engineer mistakes**
- Not enabling origin shield on a long-tail catalogue, so a cold object is fetched from origin once per POP — up to 120× the fills you expected.
- Setting a default TTL for responses that lack `Cache-Control`, which eventually catches an authenticated endpoint. Fail closed: no header means no caching.
- Omitting `stale-if-error` and then building a complex origin failover for a problem one response header would have covered.
- Treating edge compute as a general application runtime and putting logic there that needs the primary database, adding a hop while removing none.
- Implementing rate limiting at the edge without a coordinated counter, so the effective global limit is N× the configured one across N POPs.
- Caching 404s or 500s with a meaningful TTL, so a transient origin error becomes a sticky error page with a growing `Age`.
- Letting image-resizer parameters pass through unconstrained, so `?w=301` and `?w=302` create unbounded key cardinality and pin origin CPU on resizing.
- Deleting old build assets on deploy, 404-ing users who are mid-session on the previous build.
- Putting cacheable and uncacheable content on the same hostname, so hit-ratio dashboards become uninterpretable and one `Vary` mistake becomes a data leak.
- Adding a second CDN for resilience without partitioning traffic, halving both vendors' hit ratios and doubling origin fill.
- Reporting the per-asset latency improvement (18×) as the user-visible improvement (6.4×). The difference is DNS, render, parse and execute, none of which a CDN changes.

## 16. Interview perspective

CDNs appear in almost every system-design interview that involves images, video, or a global user base — which is almost all of them. Most candidates say "add a CDN" and stop. That sentence earns nothing.

**How to use it:**

1. **Justify it with a number.** *"Images are roughly 40× the application's own bandwidth, so ~98 % of our egress is static. At $0.09/GB from the cloud versus $0.02/GB from a CDN, that's a 4× unit-cost reduction before we talk about latency."* Quantification is the whole game.
2. **Separate the two benefits.** Latency (distance) and offload (cost and capacity) are independent, and a design can win one while losing the other. Saying so demonstrates you understand the mechanism rather than the slogan.
3. **Name the cache key and `Vary` explicitly.** *"I'd allow-list query parameters and `Vary` only on `Accept-Encoding`; `Vary: User-Agent` would drop the hit ratio to near zero."* Very few candidates volunteer this, and it is the thing that actually determines whether a CDN works.
4. **Describe your TTL policy in two tiers.** Content-hashed assets at `max-age=31536000, immutable`; HTML at short `max-age` with long `s-maxage` plus `stale-while-revalidate`. Explain *why* the split exists: you can purge a CDN, you cannot purge a browser.
5. **Volunteer `stale-if-error`.** *"This also gives us read-path disaster recovery for free — the catalogue stays up through an origin outage."* Interviewers notice when availability thinking appears without being prompted.
6. **Mention origin shield when the catalogue has a long tail.** *"With 120 POPs and 400,000 covers, without a shield we'd pay up to 120 origin fills per object."* This is a scale-reasoning signal.
7. **Draw the boundary of what belongs at the edge.** Normalisation, redirects, token verification — yes. Anything needing the primary database — no, because it adds a hop and removes none.
8. **State what you would *not* cache.** `/account`, `/cart`, anything with `Set-Cookie`, anything needing strong consistency. Saying this unprompted signals production experience, because it is the thing that causes real incidents.
9. **Connect to percentiles.** *"At 96 % hit ratio, misses govern p99, not p95 — if the hit ratio slips to 90 % the misses start governing p95 and our headline latency doubles."* This links back to Lesson 16 and shows you think in distributions.

**Red flags interviewers watch for:**
- Saying "add a CDN" with no mention of cache keys, TTLs, or what is cacheable.
- Proposing a CDN for a workload that is entirely per-user and uncacheable, without noticing the hit ratio would be zero.
- Believing purge is instant and global, and designing a workflow that depends on it.
- Not knowing `s-maxage` exists, and therefore having no answer to "how do you get long edge TTLs with fast invalidation?"
- Suggesting `?v=N` cache busting and being unable to explain why hashed filenames are better.
- Putting business logic or database access at the edge.
- No awareness that a misconfigured cache key or `Vary` is a **data-leak** risk, not just a performance issue.
- Claiming a CDN improves write latency or helps `POST` requests get faster at the database.
- Ignoring the origin entirely afterwards — a CDN with a 96 % hit ratio still needs a healthy origin for the other 4 %, for fills, and for every write.

## 17. Knowledge check

Write your answers down before continuing.

**Conceptual**

1. Explain precisely why `Cache-Control: public, max-age=60, s-maxage=86400` is a better policy for BookNook's catalogue HTML than either `max-age=86400` alone or `max-age=60` alone. Your answer must state what each of the two caches involved does with each directive, and must explain the specific operational capability that the split preserves and that neither single-directive option provides.

2. A colleague proposes cache-busting BookNook's JavaScript with `/static/app.js?v=48`, arguing it is simpler than content-hashed filenames and achieves the same result. Give four distinct technical reasons why content hashing is strictly better, each describing a different failure mode that query-string versioning permits. Then describe the one scenario in which query-string versioning causes a user-visible incident that content hashing makes structurally impossible.

3. BookNook enables a CDN and the hit ratio settles at 71 %. Describe the three most likely causes, how you would distinguish between them using only response headers and edge logs, and what each one's fix is. Then explain why a hit ratio of 71 % versus 96 % changes *which percentile* of the latency distribution is governed by origin fetches, and why that matters more than the change in the mean.

**Practical scenario**

4. BookNook's pricing team needs a price correction to be visible to all users within 90 seconds, worldwide. The current policy for `/books/{isbn}` is `public, max-age=3600, s-maxage=86400, stale-while-revalidate=604800`. The CMS can issue surrogate-key purges. Describe exactly what happens, step by step, if the team simply issues a purge under the current policy — including what different users in different countries see and for how long. Then state the specific header changes required to meet the 90-second guarantee, explain why `stale-while-revalidate` must be reconsidered for this particular route, explain what part of the 90-second guarantee still cannot be met by purge alone and what mechanism would be required instead, and state the cost of your change in terms of origin request rate.

**Calculation**

5. BookNook expands into Japan. Assume: 400,000 additional page views/day; 14 assets per page view; 1,323 KB per page view; a Tokyo user's RTT to the `us-east-1` origin is 165 ms and to the Tokyo POP is 9 ms; one asset fetch costs 6 RTT cold and 1 RTT when the connection is already warm; cloud egress is $0.090/GB and CDN egress is $0.020/GB; the static hit ratio is 96 % and the origin shield absorbs 85 % of the miss stream; a miss costs the edge RTT plus 2× the origin RTT.
   (a) Compute the additional monthly egress in GB (30-day month) and the monthly cost both origin-only and with CDN + shield, showing each step.
   (b) Compute the per-asset p95 latency for a Tokyo user origin-only and on a CDN hit, and then the blended mean latency at a 96 % hit ratio.
   (c) At what hit ratio does the *p95* (not the mean) stop being governed by edge hits and start being governed by misses? Explain the reasoning, then compute what the p95 would be at a hit ratio of 93 %.
   (d) BookNook's finance team argues the CDN is not worth it for Japan alone because the monthly saving is small. Compute the saving, then state one quantified non-cost argument from your part (b) answer that changes the decision.
   Show every step and state any additional assumptions you make.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 32.

---

**Progress checklist**

Phase 1 — Foundations: **complete** (Lessons 1–17)

Phase 2 — Scaling:
`[x] 1. Vertical scaling` · `[x] 2. Horizontal scaling` · `[x] 3. Stateless application servers` · `[x] 4. Load balancers` · `[x] 5. Layer 4 versus Layer 7` · `[x] 6. Load-balancing strategies` · `[x] 7. Health checks and failover` · `[x] 8. Proxies and API gateways` · `[x] 9. Caching fundamentals` · `[x] 10. Cache placement and strategies` · `[x] 11. Cache invalidation and TTLs` · `[x] 12. Cache stampedes` · `[x] 13. Hot keys and eviction policies` · `[x] 14. CDNs and edge caching`
`[ ] 15. Database replication` ← next
`[ ] 16–22` not started

Phases 3–5: not started.

**NEXT LESSON: Phase 2, Topic 15 — Database replication**

---

# Lesson 32 — Database Replication

*Phase 2, Topic 15*

## 1. Simple definition

**Replication** is keeping a copy of the same data on more than one machine, and keeping those copies in agreement as the data changes.

Terms defined before use:
- **Leader** (also **primary**, **master**, **source**): the node that accepts writes. It is the authority on what the current state is.
- **Follower** (also **replica**, **standby**, **secondary**, **hot standby**, **read replica**): a node that receives a stream of changes from the leader and applies them to its own copy. It does not accept writes from clients.
- **Replication log / change stream**: the ordered sequence of changes the leader ships to followers. Order matters absolutely — applying the same changes in a different order produces a different database.
- **Replication lag**: how far behind the leader a follower is, measured in time (e.g. 250 ms) or in bytes of unapplied log. It is never zero.
- **Apply / replay**: the follower executing the changes it received.
- **Failover**: promoting a follower to leader because the old leader is gone.
- **Promotion**: the act of making a follower start accepting writes.

Industry terminology is shifting from master/slave to **leader/follower** or **primary/replica**. All three pairs mean the same thing, and you will meet all three in documentation. This lesson uses leader/follower, with primary/replica used where a specific product does.

The one-sentence summary to carry forward: **replication gives you redundancy, read capacity and geographic proximity. It does not give you write capacity, and it is not a backup.** Both halves of that sentence are the most commonly misunderstood things in this topic, and Sections 9 and 11 prove them.

## 2. Why it exists

BookNook currently runs one PostgreSQL instance. Lessons 18–31 scaled everything *around* it — multiple app servers (Lesson 19), a load balancer (Lesson 21), Redis (Lesson 27), a CDN (Lesson 31). Every one of those layers ends at the same single database, and four distinct problems remain.

### Availability

One database means one failure takes the whole site down. **Lesson 15's availability maths** said that a single component at 99.9 % gives you at most 99.9 %, and no amount of redundancy elsewhere improves it. Worse, recovery from backup is measured in *hours*: restore a dump, replay WAL, verify, repoint the application. For a 400 GB database that is realistically 2–6 hours. A warm follower turns that into 30–60 seconds.

### Read throughput

A single machine has a finite number of cores, a finite amount of RAM for the buffer cache, and a finite disk. BookNook's reads — product pages, search result hydration, order history — vastly outnumber writes. Caching (Lessons 26–30) absorbs most of them, but cache misses, cache fills, and anything not cacheable still land on one machine. Replicas let you serve those reads from N machines.

### Geographic latency

Lesson 31 moved *static* bytes close to users. It did nothing for a database query, because a database query must reach the data. A follower in Frankfurt lets a European app server read locally at 2 ms instead of crossing the Atlantic at 85 ms. The writes still go to the leader — but for a read-heavy workload, most requests get faster.

### Workload isolation

Analytics queries, nightly reports and ad-hoc investigation are enormously expensive and unpredictable. Running them on the leader competes with checkout for buffer cache and CPU. A dedicated follower absorbs them, and if someone writes a terrible query it damages only that follower.

**What replication explicitly does not solve:** write throughput, and dataset size. Both of those require **Phase 2, Topic 18 — Database partitioning and sharding**. Replication copies *the whole dataset* everywhere; sharding splits it. They are orthogonal, and production systems usually need both. This distinction is the single most valuable thing in this lesson, and Section 9 quantifies it.

## 3. Real-world analogy

**A chain of bookshops with one head-office stock ledger.**

Head office maintains the authoritative record of prices, titles and stock. Every branch keeps its own copy so staff can answer customer questions instantly without phoning head office.

- Head office is the **leader**; branches are **followers**.
- A courier delivering a list of today's changes to every branch is the **replication log**.
- A branch quoting a price that changed twenty minutes ago is **replication lag**.
- A customer who just changed their own delivery address at head office, then asks a branch and is told the old one, is the **read-your-own-writes** problem.
- Head office burning down, and the Manchester branch being declared the new head office, is **failover and promotion**.
- The courier van for a closed branch piling up undelivered sacks in head office's loading bay until it is physically blocked is a **replication slot retaining WAL**.
- A clerk at head office writing "delete all titles by this author" and that instruction being faithfully couriered to every branch within seconds is **why a replica is not a backup**.

**Where the analogy breaks down:**

| Bookshop chain | Database replication |
|---|---|
| A branch could, in a pinch, take a correction locally and phone it in | A follower is strictly read-only. Writing to it directly corrupts the replication stream and the follower will usually refuse or break |
| The courier delivers once a day; everyone expects staleness | Lag is typically **milliseconds**, which is worse in a subtle way: it is short enough that engineers assume it is zero and build code that breaks only occasionally, under load |
| Branches process deliveries in parallel across several staff | Historically a follower replays the log **single-threaded**, so a leader with 16 cores writing in parallel can outrun a replica that replays serially. This is the dominant cause of unbounded lag |
| Making Manchester the new head office is a decision someone makes calmly | Failover is automated, must happen in seconds, and must guarantee the *old* leader cannot still be accepting writes — otherwise you get **split brain**, two authorities, and divergent data that must be reconciled by hand |
| Branches reduce head office's workload for everything | Followers reduce **read** load only. Every branch must still process **100 % of the change stream**, so branches do not make head office able to accept more changes — and past a point, processing changes consumes a branch entirely |
| A branch's copy protects against head office burning down | A follower protects against hardware loss, **not** against a bad instruction. Corruption and deletion replicate at full speed |

The last two rows are the whole lesson. Everything in Sections 9 and 11 is an elaboration of them.

## 4. Detailed technical explanation

### What actually gets replicated

"Send the changes to the follower" hides a real design decision: *what is a change?* There are four answers in production use, with genuinely different failure modes.

**Statement-based replication.** Ship the SQL. The follower runs the same `UPDATE books SET stock = stock - 1 WHERE isbn = ?`.

**Row-based replication** (also called *logical*, in MySQL's terminology). Ship the resulting row images: "row with primary key X changed from {stock: 7} to {stock: 6}".

**Physical / WAL-based replication.** The **WAL** (Write-Ahead Log) is the durability mechanism every serious database already has: before changing a data page, write a record describing the change to an append-only log, so a crash can be recovered by replaying it. Physical replication simply ships those same WAL records — descriptions of byte-level changes to disk pages — to the follower, which applies them to its own identical pages. PostgreSQL's streaming replication is this.

**Logical replication (decoded).** Decode the WAL back into logical row changes and ship *those*, with a published schema. PostgreSQL's logical replication and `pgoutput`/Debezium-style CDC (Change Data Capture) are this.

| | Statement-based | Row-based | Physical (WAL) | Logical (decoded) |
|---|---|---|---|---|
| **What is shipped** | The SQL text | Before/after row images | Byte-level page diffs | Decoded row changes with schema |
| **Bandwidth** | Smallest — one statement can change a million rows | Large — proportional to rows changed | Large — proportional to pages touched, including index pages | Medium — rows only, no index pages |
| **Follower CPU** | High — re-plans and re-executes every statement | Low — direct row apply | **Lowest** — no SQL layer at all | Low–medium |
| **Cross-version replication** | Possible | Possible | **No** — identical major version and often identical build required | **Yes** — this is its main advantage |
| **Cross-engine (Postgres → Kafka/warehouse)** | No | Limited | **No** | **Yes** |
| **Replicate a subset of tables** | Awkward | Yes | **No — all or nothing** | **Yes** |
| **Follower readable while applying** | Yes | Yes | Yes (hot standby, with caveats) | Yes |
| **Distinct failure mode** | **Non-determinism.** `NOW()`, `RANDOM()`, `UUID()`, `LAST_INSERT_ID()`, triggers, non-deterministic UDFs, and `UPDATE ... LIMIT` without `ORDER BY` produce *different results* on the follower. The databases silently diverge. Autoincrement collisions on multi-leader | **Bloat on wide updates.** `UPDATE orders SET flag=true` over 50 M rows ships 50 M row images. Also: no primary key means the follower must do a full scan per row to find the target, taking lag from milliseconds to hours | **Tight coupling.** Follower must be the same major version, same page layout, same architecture. Rolling major upgrades are impossible, so upgrades need logical replication or a dump. Also: a **corrupt page replicates as a corrupt page**, byte for byte | **Sequences, DDL and TOAST gaps.** Schema changes do not replicate automatically; sequence values are not advanced on the subscriber; large values stored out-of-line may not be sent on update unless replica identity is full. Higher CPU to decode. Silent data skew if the publication is misconfigured |
| **Typical use** | MySQL legacy; still a default fallback in `MIXED` mode | MySQL default (`binlog_format=ROW`) | PostgreSQL HA standbys; the default for failover | Upgrades, CDC pipelines, cross-engine feeds, selective replication |

**The practical rule.** Use **physical replication for high availability** — it is the cheapest to apply and gives a byte-identical standby that can be promoted. Use **logical replication for anything crossing a version, an engine or a subset boundary**. Avoid statement-based replication unless a legacy system forces it, and if it does, audit every non-deterministic function in your codebase, because divergence is silent: no error is raised, the two databases simply stop agreeing, and you discover it months later.

MySQL's `binlog_format=MIXED` exists precisely because of this: it uses statement-based replication for efficiency and automatically switches to row-based for statements the server recognises as non-deterministic. It is a pragmatic patch over a fundamental problem, and "recognises as non-deterministic" is not the same as "is deterministic".

### The three topologies

**Single-leader (leader–follower).** One node accepts writes; N nodes replicate from it. Followers may themselves have followers (**cascading replication**), which reduces load on the leader when you have many replicas.

- *Guarantees:* a single, total order of writes. No write conflicts are possible, ever, because there is only one writer. This is enormously valuable and is why it is the default for 95 % of systems.
- *Costs:* the leader is a write bottleneck and a failure point requiring failover. Writers far from the leader pay full cross-region latency.
- *Use when:* almost always. This is the default and you should need a reason to deviate.

**Multi-leader.** Two or more nodes accept writes and replicate to each other. Usually one leader per region, or one per datacentre.

- *Guarantees:* local write latency in every region; survives a whole region being partitioned off.
- *Costs:* **write conflicts become possible and must be resolved.** Two leaders accept conflicting updates to the same row at the same moment, and no amount of cleverness makes that not have happened. Resolution strategies are last-write-wins (which silently discards data and depends on clock synchronisation), application-defined merge functions, or CRDTs (Conflict-free Replicated Data Types — structures designed so that concurrent updates always merge deterministically). Each is a real engineering cost.
- *Use when:* you genuinely need low write latency on more than one continent, or offline-capable clients (a mobile app writing locally and syncing is a multi-leader system whether you call it that or not).
- *Common misuse:* enabling multi-leader "for high availability" when single-leader with fast failover would do. You have accepted conflict resolution — a permanent correctness burden — to avoid 40 seconds of failover.

**Leaderless.** Any node accepts a write; the client (or a coordinator) writes to several nodes and reads from several nodes. Dynamo, Cassandra, Riak, ScyllaDB.

- *Mechanism:* with N replicas, a write is acknowledged after W of them accept it, and a read consults R of them. If `W + R > N`, the read set and write set must overlap in at least one node, so the read is guaranteed to see the latest acknowledged write. This is a **quorum**.
- *Guarantees:* no failover is needed because no node is special. Smooth degradation — losing a node reduces redundancy rather than causing an outage.
- *Costs:* no single ordering of writes, so conflicts and concurrent versions are a normal condition, handled with version vectors, read repair and anti-entropy. Quorum arithmetic is subtle, and `W + R > N` does not give you everything people assume it does.
- *Use when:* you need very high write availability across many nodes and your data model tolerates eventual consistency.

The full consistency treatment — what these guarantees mean formally, and what you must give up — is **Phase 3, Topic 20 — CAP theorem**. The replication *mechanics* in this lesson come first because you cannot reason about CAP without knowing what a replica actually does.

### Read scaling versus write scaling

This is the section to remember.

Add a follower and your **read** capacity increases, because reads can be spread across more machines. Add a follower and your **write** capacity does not increase — it *decreases slightly* — because:

1. **Every follower must apply 100 % of the writes.** A follower is not a partial copy. There is no division of write labour. Ten followers do not each do a tenth of the work; they each do all of it.
2. **The leader must ship the log to every follower**, consuming network bandwidth and, with synchronous replication, waiting for acknowledgements.
3. **Each follower's capacity is therefore `C − (cost of replaying all writes)`**, and as the write rate rises, that subtracted term grows until nothing is left for reads.

So replication is a read-scaling technique with a hard ceiling set by the write rate. Section 9 computes exactly where that ceiling is for BookNook. The only way past it is to make each node responsible for a *subset* of the data — which is **Phase 2, Topic 18 — Database partitioning and sharding**.

A second, sharper limitation: **replay is often single-threaded.** PostgreSQL applies WAL with a single startup process (though commit apply can be parallelised in recent versions and some workloads); MySQL needs `replica_parallel_workers` configured to use multiple threads. A leader with 32 cores accepting highly-concurrent writes can generate log faster than a single replay thread can consume it. When that happens, lag does not stabilise at a higher value — it grows **without bound**, exactly like the queueing cliff in Lesson 14, and the replica never catches up until write traffic drops.

### Failover and promotion

Failover has five steps, and skipping any of them causes a known incident class.

1. **Detect.** Health checks (Lesson 24) determine the leader is gone. The timeout is a direct trade-off: too short and a GC pause or a network blip triggers an unnecessary failover; too long and you extend the outage. **10–30 seconds** is typical.
2. **Choose.** Select the follower with the most applied log. Choosing a lagging replica discards everything the others had.
3. **Fence the old leader (STONITH — "shoot the other node in the head").** Guarantee the old leader cannot accept another write: revoke its network access, stop the VM, or remove it from the proxy. **This is the step teams skip, and it is the one that produces split brain.** If the old leader is merely *unreachable* rather than *dead*, it may still be serving writes to clients that can reach it. Two leaders both accepting writes produces divergent histories that no automated process can reconcile.
4. **Promote.** The chosen follower finishes applying its log and switches to read-write.
5. **Redirect.** Clients must learn the new leader. Options: a proxy/virtual IP that is repointed (fast, seconds), service discovery (fast), or DNS (**slow and unreliable** because of resolver caching — Lesson 4). Use a proxy.
6. **Rebuild.** The old leader, if it returns, must be reset to follow the new leader, discarding any writes it accepted after the split (`pg_rewind` or a full re-sync).

**Data loss is expected on asynchronous failover.** Any transaction committed on the leader but not yet shipped is gone. At 600 writes/s and 800 ms of lag, that is roughly 480 lost transactions. Whether that is acceptable is a business question — for a like counter, fine; for payment records, not — and the mitigation is synchronous replication, which is **Phase 2, Topic 16 — Synchronous versus asynchronous replication**, the next lesson.

### Replication slots and WAL retention — the operational hazard

A **replication slot** is a server-side marker recording how much WAL a particular follower has confirmed receiving. Its purpose is correctness: the leader will **not delete WAL that a slot has not yet confirmed**, so a follower that disconnects briefly can reconnect and resume rather than needing a full rebuild.

That guarantee is also the hazard, and it is the most common way a replication setup takes down a *healthy* leader:

- A follower goes offline — crashed, network-partitioned, or simply decommissioned without dropping its slot.
- The leader keeps generating WAL, and keeps every byte of it, because the slot has not confirmed.
- WAL accumulates on the leader's disk at the full write rate.
- When the disk fills, PostgreSQL cannot write WAL, and because WAL is written *before* every change, **the leader stops accepting writes entirely and will PANIC and shut down.**

A follower that is merely *down* has thereby taken the **leader** down. The failure propagates in the wrong direction, which is what makes it surprising.

Mitigations, each with its own trade-off:
- `max_slot_wal_keep_size` (PostgreSQL 13+): cap retention. Past the cap the slot is **invalidated**, and that follower must be rebuilt from a base backup. You have traded "the leader dies" for "the follower needs rebuilding", which is unambiguously the better trade.
- **Archive WAL to object storage** so a lagging follower can fetch old segments from S3 instead of from the leader's disk.
- **Alert on `pg_replication_slots.restart_lsn` distance and on WAL directory size** — not on replica lag alone, because a *disconnected* replica reports no lag at all; it reports nothing.
- **Have a runbook step that drops the slot** when a replica is decommissioned. Orphaned slots from deleted replicas are extremely common.

The same class of problem exists elsewhere: MySQL binlog retention (`binlog_expire_logs_seconds`) will *not* block the primary, but it will silently break a replica that falls behind the retention window, which then needs a rebuild. Postgres chose safety-of-the-replica over safety-of-the-primary; know which trade your engine made.

### Why a replica is not a backup

A replica applies everything the leader does, as fast as possible. That includes everything you did not mean to do.

| Threat | Does a replica protect you? | Why |
|---|---|---|
| Leader hardware/disk failure | **Yes** | The replica has its own hardware |
| Leader process crash | **Yes** | Promote the replica |
| Availability-zone loss | **Yes**, if the replica is in another AZ | Independent failure domain |
| `DROP TABLE orders` | **No** | Replicated in milliseconds. All copies lose the table simultaneously |
| `UPDATE users SET email = NULL` with a missing `WHERE` | **No** | Same |
| Application bug writing garbage for six hours | **No** | Faithfully replicated garbage |
| Ransomware / compromised credentials | **No** | The attacker's writes replicate; the replica may also be reachable with the same credentials |
| Silent logical corruption from a bad migration | **No** | Replicated |
| **Physical** page corruption on the leader | **No**, for physical replication | WAL ships byte-level page changes; a corrupt page is reproduced exactly |
| Accidental schema change | **No** | Replicated (physical/statement); with logical replication, often *not* replicated, which causes a different problem — divergence |

A backup is distinguished by three properties a replica lacks: it is a **point-in-time** copy (so you can go back to before the mistake), it is **immutable** (nothing can overwrite it), and it is **isolated** (different credentials, different account, ideally different provider). You need both: replication for availability, backups with **PITR** (Point-In-Time Recovery — a base backup plus archived WAL, letting you restore to any second) for durability against mistakes. And a backup you have never restored is a hypothesis, not a backup; test restores on a schedule.

### Replica lag: where it comes from, and what it does to your application

Causes, in rough order of frequency:
1. **Single-threaded replay** saturating one core while the leader writes with many.
2. **Long-running read queries on the replica** blocking apply, because applying WAL would remove rows the query still needs. PostgreSQL's `max_standby_streaming_delay` controls whether the query is cancelled or the apply is paused — you must choose which one you want.
3. **Bulk writes**: a 50-million-row `UPDATE` or a large index build generates a WAL burst the replica digests serially.
4. **Network bandwidth** between leader and replica, especially cross-region.
5. **Slower replica hardware** — a common false economy, since the replica must do all the write work regardless.
6. **Lock contention on the replica** during apply.

The application-visible consequence is **read-your-own-writes** violation: a user posts a review, the write goes to the leader, the redirect's read goes to a replica 300 ms behind, and their review is not there. They post it again. Standard mitigations:
- **Read-after-write routing**: for a bounded window after a user writes (say 5 seconds), route that user's reads to the leader. Simple, effective, and what most teams do.
- **Monotonic reads**: pin a user's session to one replica so they never see time move backwards (replica A at 100 ms lag, then replica B at 900 ms lag, makes data appear to vanish).
- **LSN/GTID tracking**: the write returns the leader's log position; subsequent reads require a replica that has applied at least that position, or fall back to the leader. Most precise, most plumbing.

Route by *intent*, not by *endpoint*: a `GET` that triggers a write, or a read inside a write transaction, must go to the leader. Blanket "GET → replica, POST → leader" routing breaks the first time someone adds a counter increment to a page view.

## 5. Request / data flow

BookNook runs one leader and four followers in `us-east-1`, plus one cascading follower in `eu-central-1`. Follow a single order from write through to a European read.

1. A customer clicks **Buy**. The app server opens a transaction on the **leader** via the connection pooler (PgBouncer). Writes always go to the leader; there is no alternative under single-leader replication.
2. The leader executes `INSERT INTO orders ...` and `UPDATE books SET stock = stock - 1 ...`. Before touching any data page it appends **WAL records** describing each change to its write-ahead log.
3. The client issues `COMMIT`. The leader writes a commit record and **fsyncs** the WAL to durable storage. Durability is now achieved *on the leader*.
4. Because replication is **asynchronous** (the default), the leader returns `COMMIT` success to the application **immediately** — it does not wait for any follower. The user sees "Order confirmed" after ~4 ms. *(Changing this trade-off is the whole of Phase 2, Topic 16 — Synchronous versus asynchronous replication.)*
5. A **WAL sender** process on the leader streams the new WAL records to each connected follower over TCP. Each follower has a **replication slot** recording its confirmed position, so the leader will not recycle WAL those followers have not yet received.
6. Each follower's **WAL receiver** writes the records to its own local WAL and fsyncs, then confirms receipt. At this instant the data exists durably in five places, but is *visible* in only one.
7. Each follower's **startup/apply process** replays the records against its own data pages. Only now does the order become visible to queries on that follower. The gap between step 3 and step 7 is the **replication lag** — typically **5–50 ms** within a region, **80–300 ms** cross-region.
8. The cascading follower in Frankfurt receives its stream from one of the `us-east-1` followers, not from the leader. This saves the leader a WAL sender and a transatlantic stream, at the cost of adding that follower's lag to Frankfurt's own.
9. The app redirects the customer to `/orders/12345`. The router sees the user wrote **220 ms ago**, inside the 5-second read-after-write window, so it routes this read to the **leader**. The order is definitely there.
10. Six seconds later the customer refreshes. The window has expired, so the read goes to a **follower**, chosen by the pooler using least-connections (Lesson 23) across the four local followers. Lag is 11 ms; the order is long since applied.
11. Meanwhile a customer in Germany loads the same book's product page. The Frankfurt app server reads from the **Frankfurt follower**: 2 ms instead of an 85 ms transatlantic round trip. The stock count it shows may be ~200 ms stale, which is acceptable for display.
12. That German customer then clicks **Buy**. The write *must* cross the Atlantic to the leader: 85 ms, unavoidable under single-leader replication. Stock is decremented authoritatively on the leader, so no oversell occurs despite the stale display.
13. The analytics follower, configured with `hot_standby_feedback = off` and a long `max_standby_streaming_delay`, runs a 40-minute reporting query. Its lag rises to 40 minutes. This is fine — nothing user-facing reads from it — **provided monitoring excludes it from lag alerting and provided it does not hold a slot that forces WAL retention on the leader.**
14. At 02:00 the leader's host fails. Health checks detect it after 12 s. The orchestrator compares applied LSNs across the four followers, picks the most advanced, **fences the old leader** by revoking its security-group ingress, promotes the chosen follower, and repoints the pooler. Total: ~38 seconds. Transactions committed on the leader but not yet shipped — roughly 480 at 600 writes/s and 800 ms of lag — are **lost**.
15. The other three followers are reconfigured to follow the new leader. The old host returns an hour later and is rewound (`pg_rewind`) to discard its divergent tail before rejoining as a follower.

## 6. ASCII diagram

```
SINGLE-LEADER — one writer, many readers
                    writes ONLY
   app ──────────────────▶ ┌───────────┐
                           │  LEADER   │  accepts INSERT/UPDATE/DELETE
                           │  (WAL)    │
                           └─────┬─────┘
            ┌────────────┬───────┴───────┬────────────┐
            ▼            ▼               ▼            ▼
      ┌─────────┐  ┌─────────┐    ┌─────────┐  ┌─────────┐
      │follower1│  │follower2│    │follower3│  │follower4│   read-only
      └─────────┘  └─────────┘    └─────────┘  └─────────┘
            ▲            ▲               ▲            ▲
            └────────────┴── reads ──────┴────────────┘
   NOTE: EVERY follower applies 100% of the writes. There is no division
   of write labour. 4 followers do NOT each do 1/4 of the work.


THE WRITE-SCALING ILLUSION
   Capacity per node = 4,000 read-equivalents/sec
   Replay cost       = 1.2 read-equivalents per write

   writes/s │ replay cost │ reads LEFT per replica │ replicas to serve 11,400 r/s
   ---------┼-------------┼────────────────────────┼─────────────────────────────
       600  │     720     │        3,280           │   4   (feasible)
     1,500  │   1,800     │        2,200           │   6
     2,500  │   3,000     │        1,000           │  12
     3,000  │   3,600     │          400           │  29
     3,333  │   4,000     │            0           │  INFINITY — no replica can
                                                       serve ANY read.
   Past ~3,333 writes/s, adding replicas does NOTHING. The wall is the WRITE
   rate, not the replica count.  ⇒ you need SHARDING (Phase 2, Topic 18).


MULTI-LEADER — local writes, but conflicts are now YOUR problem
   us-east LEADER ◀──────── bidirectional ────────▶ eu-central LEADER
         │                                                │
     writes ok                                        writes ok
         │                                                │
   t=0  SET price = 12.99                           SET price = 14.99
         └──────────────▶  CONFLICT  ◀──────────────┘
   Same row, same instant, two authorities. Nothing prevents this.
   Resolve by: last-write-wins (SILENTLY DISCARDS one) / merge fn / CRDT.


LEADERLESS — quorum, no special node
   N = 3 replicas,  W = 2,  R = 2,  W + R = 4 > 3  ⇒ read set and write
                                                     set must OVERLAP
   write ──▶ [n1 ✓] [n2 ✓] [n3 ✗ down]      acked after 2 of 3
   read  ──▶ [n1 ✓] [n3 ✗ stale]            at least one has the new value
                     └── version comparison picks the newer; read repair
                         writes it back to n3


REPLICATION SLOT — the hazard that kills a HEALTHY leader
   follower goes offline, slot NOT dropped
            │
            ▼
   LEADER ──▶ WAL cannot be recycled (the slot has not confirmed)
            ▶ WAL dir grows at 2.4 MB/s = 207 GB/day
            ▶ disk 200 GB free  ⇒ FULL in ~23 hours
            ▶ cannot write WAL ⇒ cannot write ANYTHING
            ▶ PANIC — THE LEADER SHUTS DOWN
   A DOWN REPLICA KILLED THE PRIMARY. The failure travelled upstream.
   FIX: max_slot_wal_keep_size (invalidate the slot, rebuild the replica)
        + alert on WAL directory size, NOT only on replica lag
        (a DISCONNECTED replica reports no lag at all)


REPLICA ≠ BACKUP
   DROP TABLE orders;
        │
        ├──▶ leader:    gone in 3 ms
        ├──▶ follower1: gone in 11 ms
        ├──▶ follower2: gone in 11 ms
        ├──▶ follower3: gone in 12 ms
        └──▶ follower4: gone in 14 ms
   5 copies. Zero recoverability. You needed a point-in-time, IMMUTABLE,
   ISOLATED backup — which replication is not, by design.


READ-YOUR-OWN-WRITES
   t=0    POST /reviews ───▶ LEADER      (committed, ack'd)
   t=4ms  302 redirect
   t=9ms  GET /reviews ────▶ FOLLOWER (lag 300 ms) ──▶ review NOT THERE
                                                        user posts it AGAIN
   FIX: route this user's reads to the LEADER for ~5 s after any write.
```

## 7. Detailed example

### BookNook

**Starting state.** One PostgreSQL instance, 16 vCPU, 64 GB RAM, 400 GB of data. Peak traffic 12,000 queries/second, of which 95 % are reads and 5 % are writes. Redis (Lesson 27) absorbs most catalogue reads; what reaches Postgres is cache misses, order history, search hydration and checkout. Backups are a nightly `pg_dump` to object storage. Measured restore time, tested once, 14 months ago: **4 hours 20 minutes**.

**Incident 1 — the outage that starts the project.** The instance's EBS volume degrades. The database is unresponsive for 9 minutes, then the instance is replaced and recovery from the nightly dump begins. Total downtime: **5 hours 10 minutes**, with **7 hours of orders lost** (everything since the last nightly dump). This is a single-component availability problem exactly as Lesson 15 described, and no amount of app-server redundancy was ever going to help.

**Step 1 — one synchronous-capable standby for availability, not for reads.** A single physical streaming replica in a different availability zone, with automated failover via Patroni. Not used for queries at all, initially. RTO (Recovery Time Objective — how long until service resumes) falls from 5 hours to **~40 seconds**. RPO (Recovery Point Objective — how much data can be lost) falls from 24 hours to **~800 ms of writes**, because replication is asynchronous. Both numbers are now business decisions rather than accidents.

**Step 2 — PITR backups, because the standby is not a backup.** Base backups plus continuous WAL archiving to object storage in a *separate* AWS account with object-lock immutability. This is deliberately a separate project from replication, for the reasons in Section 4. Restore is tested quarterly, and the test is what makes it a backup.

**Step 3 — read replicas.** Two more followers added and wired into the connection pooler. The app's data layer gains an explicit `read_only=True` flag on query call sites. Leader CPU drops from 78 % to 41 % at peak.

**Incident 2 — the read-your-own-writes bug.** Customers report that newly posted reviews "disappear". The review `POST` goes to the leader, the follow-up `GET` goes to a replica 280 ms behind. Fix: a 5-second read-after-write window pinning that user's reads to the leader, implemented in the routing layer, not in each endpoint. **23 lines of code.** The important part is where it lives: putting it in each endpoint guarantees that the next endpoint someone writes will have the bug again.

**Incident 3 — a replica takes down the leader.** An engineer terminates the analytics replica to save cost and does not drop its replication slot. Over the next 23 hours the leader retains every WAL segment the dead slot has not confirmed. At 03:40 the WAL volume fills, Postgres PANICs, and the **leader shuts down** — caused by a replica that had not existed for a day. Fixes applied: `max_slot_wal_keep_size = 64GB`, an alert on WAL directory size (not just on replica lag, because a disconnected replica reports no lag at all), and a decommissioning runbook step that drops the slot.

**Incident 4 — the analytics replica with unbounded lag.** A 40-minute reporting query on the analytics replica, with `hot_standby_feedback = on`, prevents the leader from vacuuming rows the query still needs. Table bloat grows, the leader's performance degrades, and lag alerts fire for a replica nobody reads. Fixes: `hot_standby_feedback = off` on the analytics replica (accepting that long queries may be cancelled by conflicting WAL apply), a separate lag alerting policy for that node, and a plan to move heavy analytics to a warehouse fed by **logical** replication rather than reading a physical standby.

**Step 4 — hitting the actual wall.** BookNook grows. Writes reach 2,500/s during a flash sale. Each replica now spends 3,000 of its 4,000 read-equivalents/s on WAL replay, leaving 1,000/s for reads. Serving the read load would need **12 replicas**, each costing the same as the leader, each contributing less than a third of a machine's worth of useful read capacity. Lag becomes unstable because single-threaded replay cannot keep up with 16-way concurrent writes on the leader.

The team's instinct is "add more replicas". The arithmetic in Section 9 shows that is the wrong move: the ceiling is the **write** rate, and no number of replicas raises it. The correct answers, in order of cost:
1. Reduce write volume — batch the view-counter updates that are 40 % of writes into a Redis counter flushed every 10 seconds. Writes fall from 2,500/s to 1,550/s immediately, for one day's work.
2. Enable parallel replay workers so lag stabilises.
3. Split the few highest-write tables into their own database (**functional partitioning**).
4. Shard — **Phase 2, Topic 18 — Database partitioning and sharding**.

That ordering matters in interviews as much as in practice: the cheapest fix for a write-throughput problem is almost always *writing less*.

### Contrasting example — a collaborative document editor

A document editor where several people type into the same document simultaneously, and which must work on a plane with no connectivity.

| | BookNook | Collaborative editor |
|---|---|---|
| Topology | **Single-leader** | **Multi-leader** — every client device is effectively a leader writing to its local copy |
| Why | One ordering of writes prevents overselling stock | A user typing offline must not be blocked waiting for a server |
| Conflicts | **Impossible** — only one node writes | **Routine and expected** — two people edit the same paragraph constantly |
| Conflict resolution | N/A | A **CRDT** (Conflict-free Replicated Data Type) or OT (Operational Transformation): data structures mathematically designed so that concurrent edits merge to the same result regardless of arrival order |
| Last-write-wins acceptable? | N/A | **No** — it would delete a collaborator's paragraph, which is a product failure, not a consistency subtlety |
| Replication lag tolerance | 300 ms is fine for display; 0 required at checkout | Seconds or hours of offline divergence is normal and must merge cleanly |
| Failover | Promote a follower in ~40 s | No concept of failover; there is no leader to lose |

The transferable lesson: **topology is dictated by whether concurrent conflicting writes are possible in your domain, and by what the correct resolution is.** BookNook cannot tolerate two authorities on stock, so single-leader is correct and the cross-Atlantic write latency is simply paid. The editor cannot tolerate blocking a typing user, so multi-leader is correct and the conflict-resolution machinery is simply built. Choosing multi-leader for BookNook would mean inventing a stock-merge function — and there isn't a correct one, because "both sold the last copy" has no merge that satisfies the customer.

## 8. Code or configuration example

> Illustrative only — not production-ready. Authentication, TLS, certificate management, monitoring, backup configuration, connection limits, resource tuning and failover orchestration are simplified or omitted. Use a managed service or a vetted tool (Patroni, repmgr, RDS, Cloud SQL) rather than this.

### PostgreSQL leader — `postgresql.conf`

```ini
# ---------------- Write-ahead log ----------------
# 'replica' emits enough WAL for physical streaming replication and PITR.
# 'logical' additionally allows logical decoding (CDC, cross-version upgrades)
# at the cost of slightly more WAL volume.
wal_level = replica

# One WAL sender process per connected follower, PLUS one per concurrent
# pg_basebackup. Set this ABOVE your replica count or a rebuild will be
# refused at exactly the moment you need it.
max_wal_senders = 10

# Replication slots guarantee the leader keeps WAL a follower has not yet
# confirmed. That guarantee is also the hazard -- see max_slot_wal_keep_size.
max_replication_slots = 10

# THE SINGLE MOST IMPORTANT LINE IN THIS FILE.
# Caps how much WAL a slot may force the leader to retain. Past this cap the
# slot is INVALIDATED and that follower must be rebuilt from a base backup.
# Without this, an offline follower retains WAL forever, fills the leader's
# disk, and the LEADER PANICS AND SHUTS DOWN. You are choosing
# "rebuild a replica" over "lose the primary". Always make that trade.
max_slot_wal_keep_size = 64GB

# Archive WAL to immutable object storage. This is the BACKUP half of the
# story and is NOT provided by replication: it is what lets you recover from
# DROP TABLE, which every replica will faithfully reproduce in milliseconds.
archive_mode = on
archive_command = 'wal-g wal-push %p'

# ---------------- Synchronous replication (preview) ----------------
# Commented out: the default here is ASYNCHRONOUS, so COMMIT returns as soon
# as the LEADER has fsynced -- it does not wait for any follower. That is why
# failover loses the in-flight tail (~480 transactions at 600 w/s and 800 ms
# lag). Turning this on trades write latency for RPO, which is the entire
# subject of Phase 2, Topic 16 -- Synchronous versus asynchronous replication.
# synchronous_standby_names = 'ANY 1 (standby_az_b, standby_az_c)'
# synchronous_commit = on
```

### PostgreSQL follower — `postgresql.conf`

```ini
# Serve read-only queries while continuously applying WAL.
hot_standby = on

# How long to PAUSE WAL apply rather than cancel a conflicting read query.
#   Low value  -> replica stays current; long analytics queries get CANCELLED.
#   High value -> long queries survive; REPLICATION LAG grows by up to this much.
# There is no setting that gives both. Choose per replica, by its role:
max_standby_streaming_delay = 30s       # user-facing replica: stay current
# max_standby_streaming_delay = 4h      # analytics replica: let queries finish

# Tell the LEADER which rows this replica's queries still need, so the leader
# does not vacuum them away.
#   on  -> long replica queries cause TABLE BLOAT ON THE LEADER (Incident 4)
#   off -> no bloat, but long replica queries may be cancelled
# For an analytics replica, 'off' is almost always correct.
hot_standby_feedback = off

# Parallel WAL apply. Default single-threaded replay is the #1 cause of
# unbounded lag: a 16-core leader writing concurrently can out-produce one
# replay thread, and lag then grows WITHOUT BOUND (the Lesson 14 queueing
# cliff), never recovering until write traffic drops.
max_parallel_apply_workers_per_subscription = 4

primary_conninfo = 'host=leader.booknook.internal user=replicator application_name=standby_az_b'
# Named slot so the leader retains WAL for THIS follower specifically.
# OPERATIONAL RULE: when decommissioning a replica, DROP ITS SLOT.
# An orphaned slot from a deleted replica is what caused Incident 3.
primary_slot_name = 'standby_az_b'
```

### Monitoring — the queries that would have caught Incidents 3 and 4

```sql
-- Replication lag in SECONDS, per connected follower, from the LEADER's view.
-- write_lag  : leader -> follower received
-- flush_lag  : ... -> follower fsynced   (this bounds your RPO)
-- replay_lag : ... -> follower VISIBLE to queries (this is what users feel)
SELECT application_name, client_addr, state,
       write_lag, flush_lag, replay_lag,
       pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) AS replay_bytes_behind
FROM pg_stat_replication;

-- CRITICAL: the query above shows NOTHING for a DISCONNECTED replica --
-- it simply vanishes from the view. A dead replica reports zero lag because
-- it reports nothing at all. You MUST additionally monitor slot retention,
-- or you will not see Incident 3 coming.
SELECT slot_name, active,
       pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn))
         AS wal_retained_for_this_slot,
       wal_status      -- 'reserved' | 'extended' | 'unreserved' | 'LOST'
FROM pg_replication_slots
ORDER BY pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn) DESC;
-- ALERT RULE: active = false AND wal_retained > 16 GB  => page someone.
-- An inactive slot retaining WAL is a countdown to the leader shutting down.
```

### Application routing — read/write split done safely

```python
import time

READ_AFTER_WRITE_WINDOW_SECONDS = 5   # must exceed p99.9 replication lag

class Router:
    """Chooses leader vs follower. The ONLY place this decision is made."""

    def __init__(self, leader_pool, follower_pools, max_acceptable_lag_ms=1000):
        self.leader = leader_pool
        self.followers = follower_pools
        self.max_lag_ms = max_acceptable_lag_ms
        # Per-session timestamp of the user's last write.
        self._last_write_at = {}

    def note_write(self, session_id):
        # Called on EVERY successful write. Opens the read-after-write window.
        self._last_write_at[session_id] = time.monotonic()

    def pick(self, session_id, read_only):
        # 1. Writes always go to the leader. Under single-leader replication
        #    there is no alternative -- a follower will reject the write.
        if not read_only:
            return self.leader

        # 2. READ-YOUR-OWN-WRITES. Within the window after this user's write,
        #    send THEIR reads to the leader. Without this, a user posts a
        #    review, is redirected, reads a follower 280 ms behind, does not
        #    see their review, and posts it again. (Incident 2.)
        last = self._last_write_at.get(session_id)
        if last is not None and (time.monotonic() - last) < READ_AFTER_WRITE_WINDOW_SECONDS:
            return self.leader

        # 3. Exclude followers whose lag exceeds the threshold. A lagging
        #    follower is not "slightly stale", it is actively wrong for most
        #    user-facing reads -- and a 4-hour-behind analytics replica must
        #    never appear in this pool at all.
        healthy = [f for f in self.followers if f.lag_ms() < self.max_lag_ms]

        # 4. FAIL SAFE, NOT FAST: if every follower is lagging, fall back to
        #    the leader. Serving correct data slowly beats serving wrong data
        #    quickly. Note this ADDS load to the leader exactly when things are
        #    already bad, so alert on it -- it is a precursor to an outage.
        if not healthy:
            return self.leader

        # 5. MONOTONIC READS: pin a session to ONE follower. Hopping between
        #    a 20 ms-lag and a 900 ms-lag follower makes data appear to travel
        #    backwards in time, which looks like data loss to the user.
        return healthy[hash(session_id) % len(healthy)]


# Route by INTENT, not by HTTP method. A GET that increments a view counter is
# a WRITE. Blanket "GET -> follower" routing breaks the first time someone adds
# a counter to a page-view handler, and it breaks silently.
def get_order(session_id, order_id):
    conn = router.pick(session_id, read_only=True)
    return conn.query("SELECT * FROM orders WHERE id = %s", order_id)

def place_order(session_id, payload):
    conn = router.pick(session_id, read_only=False)
    result = conn.execute("INSERT INTO orders ... RETURNING id", payload)
    router.note_write(session_id)      # MUST be called, or step 2 never fires
    return result
```

### Logical replication — for upgrades, CDC and subsets

```sql
-- ON THE LEADER: publish a SUBSET of tables. Physical replication cannot do
-- this -- it is all-or-nothing, byte-for-byte, same-major-version-only.
CREATE PUBLICATION booknook_analytics FOR TABLE orders, order_items, books;

-- REPLICA IDENTITY controls what the UPDATE/DELETE change records contain.
-- Default = primary key only. With no primary key, the subscriber must do a
-- FULL TABLE SCAN to locate each row, turning milliseconds of lag into hours.
-- This is the most common logical-replication performance disaster.
ALTER TABLE order_items REPLICA IDENTITY USING INDEX order_items_pkey;

-- ON THE SUBSCRIBER (possibly a DIFFERENT major version, or a warehouse):
CREATE SUBSCRIPTION booknook_analytics_sub
  CONNECTION 'host=leader.booknook.internal dbname=booknook'
  PUBLICATION booknook_analytics;

-- KNOWN GAPS you must handle yourself -- logical replication does NOT ship:
--   * DDL. ALTER TABLE on the leader does not reach the subscriber; the
--     subscription BREAKS when a row arrives with an unknown column.
--   * Sequence values. After a logical-replication cutover, nextval() on the
--     subscriber restarts low and you get DUPLICATE KEY errors on your first
--     inserts. Advance sequences manually as part of the cutover runbook.
--   * TRUNCATE, by default in older versions.
```

## 9. Numerical example

Every assumption is stated. Numbers are **typical/approximate** for a well-tuned PostgreSQL instance on modern cloud hardware; your measured values will differ, and the *method* is what transfers.

### Assumptions

```
Workload (peak)
  Total queries/second                         12,000
  Read fraction                                   95%  → 11,400 reads/s
  Write fraction                                   5%  →    600 writes/s

Hardware (identical for leader and every replica)
  Capacity per node                      4,000 "read-equivalents"/second
  ("Read-equivalent" = the cost of one typical indexed point-read.
   Normalising everything to one unit is what makes this arithmetic possible.)

Relative costs, measured
  1 read                                 = 1.0 read-equivalents
  1 write executed on the LEADER         = 3.0 read-equivalents
      (parse, plan, index maintenance, WAL generation, constraint checks)
  1 write REPLAYED on a follower         = 1.2 read-equivalents
      (physical WAL replay: no SQL layer, no planning, no constraint
       re-checking -- which is why physical replication is the cheapest kind)

Operating targets
  Maximum sustained utilisation                     70%
      (Lesson 14: utilisation above ~80% puts you on the queueing cliff,
       where latency rises non-linearly rather than gracefully)
  Redundancy                                        N+1
      (must survive losing one replica without breaching the 70% target)
```

### Part A — Can the leader also serve reads?

```
Leader's write cost = 600 writes/s × 3.0 = 1,800 read-equivalents/s
Leader capacity                          = 4,000
Spare                                    = 4,000 - 1,800 = 2,200 r-e/s
At the 70% target, usable               = (4,000 × 0.70) - 1,800
                                         = 2,800 - 1,800 = 1,000 reads/s
```

The leader *could* serve 1,000 reads/s. **We deliberately will not.** The leader is the only node that can accept writes; spending its headroom on reads means a read spike causes a *write* outage, and checkout failing is categorically worse than a product page being slow. The leader serves writes and read-after-write traffic only. This is a correctness-of-blast-radius decision, not a capacity one.

### Part B — How many replicas does BookNook need?

```
Each replica must replay 100% of writes -- not a share of them:
  Replay cost per replica = 600 × 1.2 = 720 read-equivalents/s

Raw read capacity per replica = 4,000 - 720 = 3,280 reads/s
At the 70% utilisation target = (4,000 × 0.70) - 720
                              = 2,800 - 720 = 2,080 reads/s usable

Replicas needed for 11,400 reads/s
  = 11,400 / 2,080 = 5.48  → 6 replicas
Plus N+1 redundancy                        → 7 replicas

Check: lose one replica.
  6 remaining × 2,080 = 12,480 reads/s  ≥ 11,400 ✓  (utilisation rises to
  11,400 / (6 × 4,000 - 6 × 720) = 11,400 / 19,680 = 58% of raw -- fine)
```

**Answer: 7 replicas** (6 for load, 1 for redundancy), plus 1 leader = 8 nodes.

Note the efficiency loss nobody budgets for:
```
Useful read capacity per replica / raw capacity = 3,280 / 4,000 = 82%
18% of every replica you buy is consumed by replaying writes you
have already paid to execute once on the leader.
```

### Part C — The wall: where replicas stop helping

Let `W` = writes/second. Per-replica read capacity is:

```
read_capacity(W) = 4,000 - 1.2W
```

```
   W (writes/s) │ replay cost │ reads left   │ efficiency │ replicas needed
                │   (1.2W)    │ per replica  │            │ for 11,400 r/s
   ─────────────┼─────────────┼──────────────┼────────────┼────────────────
         600    │      720    │    3,280     │    82%     │        4
       1,000    │    1,200    │    2,800     │    70%     │        5
       1,500    │    1,800    │    2,200     │    55%     │        6
       2,000    │    2,400    │    1,600     │    40%     │        8
       2,500    │    3,000    │    1,000     │    25%     │       12
       3,000    │    3,600    │      400     │    10%     │       29
       3,200    │    3,840    │      160     │     4%     │       72
       3,300    │    3,960    │       40     │     1%     │      285
       3,333    │    4,000    │        0     │     0%     │   IMPOSSIBLE
```

```
The hard ceiling:
  4,000 - 1.2W = 0   ⇒   W = 4,000 / 1.2 = 3,333 writes/second
```

**At 3,333 writes/second, a replica is fully consumed by replay and can serve zero reads.** Adding replicas adds nothing — each new machine arrives already saturated. The curve is not gentle either: between 2,500 and 3,000 writes/s the replica count required nearly triples, and between 3,000 and 3,300 it rises tenfold. You hit a wall you cannot see on a linear capacity graph.

Compare growth in the two dimensions, which is the point of the whole exercise:

```
DOUBLE THE READS (11,400 → 22,800/s) at W = 600:
  22,800 / 2,080 = 10.96 → 11 replicas + 1 spare = 12.   SOLVED BY REPLICATION.

DOUBLE THE WRITES (600 → 1,200/s):
  Leader write cost = 1,200 × 3.0 = 3,600 of 4,000 = 90% utilisation.
  Past the queueing cliff. Replicas CANNOT help -- they do not accept writes.
  ⇒ Need a bigger leader (vertical scaling, Lesson 18 -- finite),
    or fewer writes (batching), or SHARDING (Phase 2, Topic 18 -- Database
    partitioning and sharding), which is the only answer that keeps working.
```

### Part D — The single-threaded replay cliff

**Additional assumption:** replay is single-threaded and one core sustains ~1,100 write-replays/second; the leader has 16 cores and accepts writes concurrently.

```
Leader sustainable write rate (16 cores, 3.0 r-e each):
  4,000 / 3.0 = 1,333 writes/s

Single-threaded replica replay rate:          1,100 writes/s

1,333 > 1,100  ⇒  the leader can out-produce the replica's replay.
```

```
At W = 1,300 writes/s:
  Replica consumes                   1,100/s
  Backlog growth rate   = 1,300 - 1,100 = 200 writes/s accumulating
  After 1 hour: 200 × 3,600 = 720,000 unapplied writes
  Lag in time = 720,000 / 1,100 = 654 seconds = ~11 minutes, and STILL GROWING
```

This is the Lesson 14 queueing cliff again, in a new place: when arrival rate exceeds service rate, the queue is **unbounded**. Lag does not settle at a higher number; it increases forever until write traffic drops. A monitoring threshold of "alert if lag > 5 s" will fire, but the fix is not more replicas — each new replica has the identical single-threaded bottleneck. The fixes are parallel apply workers, fewer/batched writes, or sharding.

### Part E — Data loss on asynchronous failover (RPO)

```
Assumptions: asynchronous replication; measured p50 replay lag 800 ms;
             600 writes/s at the moment of failure.

Transactions committed on the leader but not yet replicated
  = 600 writes/s × 0.800 s = 480 transactions   ← LOST on promotion

At BookNook's average order value of £24 and an order being 8% of writes:
  Lost orders    = 480 × 0.08 = 38.4 orders
  Lost revenue   = 38.4 × £24 = £921 per failover event
```

£921 is not the real cost. The real cost is **38 customers whose payment was taken by the payment provider but whose order does not exist in your database**, which is a reconciliation and trust problem, not a revenue problem. That asymmetry — small money, large trust damage — is exactly what justifies synchronous replication for the orders table, and it is the subject of **Phase 2, Topic 16 — Synchronous versus asynchronous replication**.

### Part F — Availability improvement (Lesson 15 applied)

```
Assumptions: single node availability 99.9% (8.77 h/year down);
             failover is automated and succeeds 95% of the time;
             failover takes 40 s.

SINGLE NODE
  Availability = 99.9%          → 525.6 minutes down/year

LEADER + 1 STANDBY WITH AUTOMATED FAILOVER
  Failures/year ≈ 525.6 min / 60 min per incident ≈ 8.8 incidents
  Successful failovers  = 8.8 × 0.95 = 8.36  → 40 s each = 5.6 min/year
  Failed failovers      = 8.8 × 0.05 = 0.44  → full 60-minute recovery
                                             = 26.4 min/year
  Total downtime ≈ 32 min/year
  Availability   = 1 - (32 / 525,600) = 99.994%

Improvement: 525.6 min → 32 min  = 16x less downtime.
```

Note where the remaining downtime concentrates: **82 % of it comes from the 5 % of failovers that fail.** Raising failover success from 95 % to 99 % is worth more than any further reduction in failover duration. That is a counter-intuitive and very interview-worthy result, and it is why rehearsing failover (game days) beats optimising it.

### Part G — WAL retention, quantified (Incident 3)

```
Assumptions: 600 writes/s; average 4 KB of WAL per write transaction
             (includes index and full-page-write overhead);
             200 GB free on the leader's WAL volume.

WAL generation rate = 600 × 4 KB = 2,400 KB/s = 2.4 MB/s
Per hour            = 2.4 × 3,600 = 8,640 MB  = 8.64 GB/hour
Per day             = 8.64 × 24   = 207.4 GB/day

Time to fill 200 GB with an orphaned slot:
  200 / 8.64 = 23.1 hours   ⇒ the LEADER PANICS and shuts down.

With max_slot_wal_keep_size = 64 GB:
  64 / 8.64 = 7.4 hours before the slot is INVALIDATED.
  Consequence: that replica must be rebuilt from a base backup
               (400 GB at ~200 MB/s ≈ 33 minutes).
  The LEADER SURVIVES.
```

**The trade is explicit and always worth making:** a 33-minute replica rebuild versus a total database outage. Set the cap. Also note that the 7.4-hour grace period must exceed your realistic replica-recovery time, or brief replica outages will cost you rebuilds — tune the cap to your WAL rate, not to a number copied from a blog post.

## 10. Trade-offs

**Single-leader replication (the default)**
- *Advantages:* one total write order, so write conflicts are structurally impossible; linear read scaling up to the write wall; near-free availability improvement; straightforward mental model; every mature database supports it well.
- *Disadvantages:* leader is a write bottleneck and a promotion-requiring failure point; cross-region writers pay full RTT; replica lag leaks into application semantics (read-your-own-writes, monotonic reads).
- *Operational cost:* failover automation, fencing, lag monitoring, slot hygiene, routing logic. Real but bounded, and largely solved by managed services.
- *Unnecessary complexity when:* one node comfortably serves your load *and* you can tolerate restore-from-backup downtime. For an internal tool with 20 users, a standby is a liability you will not maintain.

**Multi-leader**
- *Advantages:* local write latency in every region; survives a region partition without failover; enables offline-capable clients.
- *Disadvantages:* **write conflicts become a permanent correctness obligation.** Last-write-wins silently discards data and depends on clock sync; merge functions must be written for every mutable field; CRDTs constrain your data model. Debugging divergence is genuinely hard.
- *Choose it when:* the business requires low-latency writes on multiple continents, or clients must write offline. Not for "better availability" — single-leader with fast failover covers that at a fraction of the complexity.

**Leaderless / quorum**
- *Advantages:* no failover; graceful degradation; very high write availability; tunable consistency per query via R and W.
- *Disadvantages:* no total write order; concurrent versions are normal; quorum arithmetic is subtle and `W + R > N` guarantees less than most people assume; operationally a different skill set.
- *Choose it when:* write availability across many nodes matters more than a single ordering, and your data model is conflict-tolerant.

**More read replicas**
- *Advantages:* straightforward read scaling; workload isolation; geographic read proximity; each one also a failover candidate.
- *Disadvantages:* every replica costs a full machine and gives less than a full machine of read capacity (82 % at 600 writes/s, 25 % at 2,500); more slots to manage; more lag surface; and the returns collapse as writes grow.
- *Hard stop:* at `W = capacity / replay_cost` (3,333 writes/s here) replicas contribute nothing. Past that, only sharding helps.

**Physical versus logical replication**
- *Physical:* cheapest to apply, byte-identical, ideal for HA. Costs: same major version required, all-or-nothing, and corruption replicates.
- *Logical:* cross-version, cross-engine, subset-capable — the only way to do a major upgrade with seconds of downtime. Costs: higher CPU, DDL does not replicate, sequences do not advance, replica identity must be configured or lag explodes.
- *Most production systems run both*: physical for the HA standby, logical for the warehouse feed and for upgrades.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| Leader host fails, automated failover works | ~40 s of write errors, then normal service. ~480 recent transactions silently missing | Health checks (Lesson 24) fail; orchestrator (Patroni/RDS) reports promotion | Follower promoted, proxy repointed, other followers re-parented, old leader rewound and rejoined |
| Leader fails, **fencing skipped** → **split brain** | Two groups of users write to two "leaders"; later, some writes vanish and some rows conflict | Divergent LSN/GTID timelines; primary-key collisions; duplicate orders | **Manual reconciliation.** Stop one node, diff the divergent window, replay by hand. Hours to days. Prevent with STONITH; there is no clean automated recovery |
| Replication lag spike during a bulk `UPDATE` | Newly written data invisible on refresh; counters jump backwards | `replay_lag` in `pg_stat_replication`; alert at > 1 s for user-facing replicas | Router excludes lagging replicas and falls back to the leader; batch the bulk write into chunks with pauses |
| Read-your-own-writes violation | "My review disappeared" — user re-submits, creating a duplicate | Duplicate-submission rate; support tickets; rarely caught in testing because lag is near zero on a dev box | 5-second read-after-write window pinning that session to the leader |
| **Orphaned replication slot** from a decommissioned replica | Nothing at all — until the leader's disk fills and **the entire database shuts down** | `pg_replication_slots` where `active = false` and retained WAL is growing. **Not** visible in lag metrics, because a disconnected replica reports nothing | Drop the slot; free disk; restart. Prevent with `max_slot_wal_keep_size` and a decommissioning runbook step |
| Single-threaded replay cannot keep up | Followers drift minutes behind; read traffic falls back to the leader, which then saturates too | Lag growing monotonically rather than oscillating | Enable parallel apply; batch or reduce writes; shard. Adding replicas does **not** help — each has the same bottleneck |
| Long analytics query on a replica with `hot_standby_feedback = on` | Leader slows down for everyone; table bloat grows | Leader dead-tuple count rising; `pg_stat_activity` on the replica showing a multi-hour query | Set `hot_standby_feedback = off` on analytics replicas; move analytics to a logically-replicated warehouse |
| `DROP TABLE` / bad migration | Data gone everywhere simultaneously | Application errors; it is already too late | **PITR from archived WAL.** Replicas are useless here, by design. Hours of restore time, and it only works if you actually have PITR configured and tested |
| Physical page corruption on the leader | Random query errors; possibly wrong results before any error | Checksum failures; `pg_checksums`; corrupt-page errors | Replicas reproduced the corrupt page byte-for-byte. Restore from backup. This is a second reason physical replication is not a backup |
| Statement-based replication with `NOW()` or `RANDOM()` | Leader and replica silently return **different values** for the same query | Only by explicit row-level comparison (`pt-table-checksum`). No error is ever raised | Switch to row-based; re-sync divergent replicas. Divergence can go undetected for months |
| Logical replication, DDL added on leader only | Subscription stops; replica freezes at a point in time while appearing healthy | Subscription error log; lag growing without the leader being busy | Apply the DDL on the subscriber, resume. Automate schema propagation in the deploy pipeline |
| Logical replication on a table with no primary key | Lag grows from milliseconds to hours | Subscriber CPU pinned; sequential scans in `pg_stat_activity` | Add a primary key or set `REPLICA IDENTITY FULL` with a suitable index |
| Failover promotes a **lagging** replica | Larger-than-expected data loss — potentially minutes of writes | Compare applied LSNs across candidates before promoting | Orchestrator must select the most-advanced candidate; configure it explicitly rather than trusting the default |
| All replicas lagging; router falls back to leader | Site stays correct but gets slow; leader utilisation climbs | Fallback-to-leader rate; leader CPU | Correct behaviour, but it is a precursor to an outage — alert on the fallback rate itself, not just on lag |
| Cross-region replica, transatlantic link degrades | European reads become stale or fall back across the ocean | `write_lag` rising on that replica specifically | Serve Europe from the leader temporarily; the link recovers; consider cascading replication to reduce leader fan-out |

## 12. Alternatives

| Approach | What it actually does | Pros | Cons |
|---|---|---|---|
| **Single-leader replication** | Full copies; one writer | Simple; no conflicts; scales reads; near-free HA | Does not scale writes; lag leaks into app semantics; failover needed |
| **Multi-leader replication** | Full copies; many writers | Local write latency; region-partition tolerant; offline clients | Conflicts are permanent engineering work; LWW silently loses data |
| **Leaderless / quorum** | Full copies; any node writes | No failover; graceful degradation; tunable R/W | No total order; subtle quorum semantics; different ops skill set |
| **Sharding / partitioning** (**Phase 2, Topic 18**) | **Splits** the data; each node owns a subset | **The only thing that scales writes**; also scales dataset size beyond one machine | Cross-shard queries and transactions are hard; rebalancing is hard; needs a good shard key. Usually combined *with* replication (each shard is replicated) |
| **Caching** (Lessons 26–30) | Keeps hot reads out of the database | Order-of-magnitude read reduction for far less money than replicas | Does nothing for writes; staleness; stampedes; hot keys |
| **Vertical scaling** (Lesson 18) | A bigger leader | Zero architectural change; genuinely the right first move surprisingly often | Finite ceiling; still one failure domain; cost rises super-linearly |
| **CQRS / read models** | Writes to a normalised store, reads from a purpose-built projection | Each side optimised independently; projections can live anywhere | Eventual consistency by construction; two models to keep correct |
| **Backups + PITR** | Immutable point-in-time copies | **The only protection against `DROP TABLE`, bugs and ransomware** | Slow recovery (hours); not an availability mechanism |
| **Multi-region active-active** | Multi-leader across regions | Survives region loss; local writes everywhere | All multi-leader costs, plus data-residency and conflict complexity |
| **Distributed SQL** (Spanner, CockroachDB, Yugabyte) | Sharding + replication + consensus, built in | Horizontal writes *and* strong consistency in one system | Higher write latency from consensus; operational and cost overhead; lock-in |

**How to choose, concretely.** Reads too high → cache first (cheapest by an order of magnitude), then replicas. Availability too low → one standby with automated failover and fencing. Data loss unacceptable → synchronous replication (next lesson) *and* PITR backups. **Writes too high → nothing in this lesson helps; reduce writes, then shard.** Dataset too large for one machine → shard. Writes must be fast on two continents → multi-leader, and budget for conflict resolution.

## 13. When to use it

1. **When a database outage is unacceptable.** This is the primary reason and applies to essentially every production system that earns money. One standby with automated failover takes RTO from hours (restore from backup) to tens of seconds, and it is the single highest-value database change most teams can make.
2. **When reads substantially outnumber writes and caching is not enough.** BookNook's 95:5 ratio is typical of e-commerce, content and social products. Replicas are the natural second line after Redis (Lessons 26–30).
3. **When you need workload isolation.** Analytics, reporting and ad-hoc investigation on a dedicated replica stop a badly-written query from damaging checkout.
4. **When users are geographically distributed and read-heavy.** A regional follower turns an 85 ms cross-Atlantic read into a 2 ms local one. Lesson 31's CDN could not do this; only a data copy can.
5. **When you need zero-downtime major-version upgrades.** Logical replication to a new-version subscriber, then a cutover, replaces a multi-hour `pg_upgrade` outage with seconds.
6. **When you need a CDC feed** into a warehouse, search index or event stream without dual-writing from the application (dual writes are a consistency bug waiting to happen).
7. **When your RPO must be near zero.** Synchronous replication is built on this machinery — **Phase 2, Topic 16 — Synchronous versus asynchronous replication**.

## 14. When *not* to use it

1. **To scale writes.** It does the opposite: every replica applies 100 % of writes, and at `capacity / replay_cost` writes/second (3,333 in Section 9) a replica can serve zero reads. Adding replicas past that point is pure cost. Use **Phase 2, Topic 18 — Database partitioning and sharding**.
2. **As a backup.** `DROP TABLE` reaches every replica in milliseconds, as does application corruption, as does ransomware, as does a corrupt page under physical replication. You need point-in-time, immutable, isolated backups, and you need to have tested a restore.
3. **To fix an unindexed query or an N+1 pattern.** A replica runs the same bad query on the same bad plan, just on a different machine, while adding lag and operational surface. Fix the query.
4. **When one node comfortably serves your load and you can tolerate restore-from-backup downtime.** An internal tool with 20 users does not need a standby it will never test; an untested failover path is a liability, not a safety net.
5. **Before caching.** A Redis instance costs a fraction of a replica and often removes 90 %+ of read traffic. Replication is the *second* answer to a read-throughput problem, not the first.
6. **Multi-leader "for availability".** You have taken on permanent conflict-resolution obligations to avoid 40 seconds of failover. Use multi-leader only when you need local writes on multiple continents or offline clients.
7. **When your dataset no longer fits one machine.** Replication copies the whole dataset to every node, so it makes the size problem N times more expensive, not better. That is a sharding problem.

## 15. Common mistakes

**Beginner mistakes**
- Believing replicas scale writes. They do not — every replica applies every write.
- Treating a replica as a backup, and discovering otherwise after a `DROP TABLE`.
- Assuming replication lag is zero because it is 3 ms on a laptop, then shipping read-after-write bugs that only appear under production load.
- Routing by HTTP method (`GET` → replica) instead of by intent, so the first `GET` that increments a counter goes to a read-only node and fails — or worse, silently reads stale data inside a write transaction.
- Not excluding lagging replicas from the read pool, so a 40-minute-behind analytics node serves a customer's order history.
- Forgetting that the leader must also be sized for the replication fan-out and for read-after-write traffic.
- Writing directly to a replica and being confused by the error — or, on engines that permit it, by the silent divergence.
- Monitoring only replica lag. A **disconnected** replica reports no lag because it reports nothing; the metric that matters is slot retention.
- Sizing replicas smaller than the leader to save money, when a replica must perform 100 % of the write work regardless.

**Experienced-engineer mistakes**
- Failing over without **fencing** the old leader, producing split brain and a manual reconciliation measured in days.
- Leaving an orphaned replication slot after decommissioning a replica, filling the leader's WAL volume and taking the **primary** down — the failure travelling upstream.
- Not setting `max_slot_wal_keep_size`, i.e. choosing "lose the leader" over "rebuild a replica".
- Running `hot_standby_feedback = on` on an analytics replica, causing bloat on the leader for the benefit of a node nobody reads.
- Promoting the *first available* replica rather than the *most advanced* one, multiplying data loss.
- Using statement-based replication with non-deterministic functions and discovering silent divergence months later. No error is ever raised.
- Setting up logical replication without configuring `REPLICA IDENTITY`, so a table without a primary key forces a full scan per changed row and lag goes from milliseconds to hours.
- Forgetting that logical replication does not ship DDL or advance sequences, so a cutover produces immediate duplicate-key errors.
- Setting a read-after-write window shorter than p99.9 lag, which fixes the bug for 99 % of users and leaves a reproducible-only-under-load tail.
- Allowing session hopping between replicas with different lag, producing non-monotonic reads where data appears to travel backwards.
- Not alerting on the *fallback-to-leader* rate, which is the leading indicator of a replica-tier failure turning into a full outage.
- Reaching for more replicas when the real constraint is write throughput, spending months and a large budget on machines that arrive pre-saturated.
- Never rehearsing failover. Section 9 Part F showed 82 % of residual downtime comes from failovers that *fail*, so rehearsal is worth more than tuning failover speed.

## 16. Interview perspective

Replication is one of the highest-frequency topics in system-design interviews, and it has two specific traps that separate candidates immediately.

**How to use it:**

1. **State the read/write split early and compute from it.** *"This is roughly 95 % reads, so replication is the right lever for reads; writes will need a different answer."* This single framing tells the interviewer you know the limits before you are asked.
2. **Say explicitly that replication does not scale writes, and prove it with the mechanism.** *"Every replica applies 100 % of the write stream, so at around 3,300 writes per second a replica is fully consumed by replay and contributes zero read capacity. Past that, only sharding helps."* This is the single highest-value sentence in the topic.
3. **Say explicitly that a replica is not a backup.** *"A `DROP TABLE` reaches every replica in milliseconds. We need PITR backups in a separate account with object lock — and a tested restore."* Most candidates never mention backups at all.
4. **Name the topology and justify it.** *"Single-leader, because stock decrement has no correct merge function for concurrent conflicting writes — multi-leader would require me to invent one, and 'both customers bought the last copy' has no good answer."*
5. **Volunteer read-your-own-writes and name a fix.** *"After a write I pin that session's reads to the leader for five seconds, implemented in the routing layer so new endpoints get it for free."*
6. **Describe failover as five steps, and emphasise fencing.** Detect → choose most-advanced → **fence** → promote → redirect via proxy, not DNS. Then add: *"Skipping fencing gives split brain, which has no automated recovery."*
7. **Quantify the RPO.** *"Async replication at 800 ms lag and 600 writes/s loses about 480 transactions on failover. For orders that's unacceptable, so I'd use synchronous replication for that path and accept the extra write latency."* This sets up the next topic naturally.
8. **Mention replication slots if HA is discussed in depth.** *"I'd cap `max_slot_wal_keep_size`, because an orphaned slot from a dead replica will fill the leader's disk and take the primary down."* Almost nobody says this, and it is unmistakably operational experience.
9. **Order the fixes by cost when writes are the problem.** Reduce writes (batch counters) → parallel replay → functional split → shard. The cheapest fix for a write problem is usually writing less.
10. **Connect to the rest of the course.** Caching first (Lessons 26–30), then replicas; CDN for static bytes (Lesson 31) but never for a database query; consistency semantics deferred to **Phase 3, Topic 20 — CAP theorem**.

**Red flags interviewers watch for:**
- Saying "add read replicas" as the answer to a **write**-throughput problem.
- Claiming replicas give linear scaling with no ceiling, or being unable to say where the ceiling is.
- Treating a replica as a backup, or omitting backups entirely from an availability discussion.
- Not knowing that replication lag exists, or assuming it is zero.
- No read-your-own-writes handling in a design with user-generated content.
- Describing failover without fencing, or without choosing the most-advanced candidate.
- Using DNS for leader redirection without acknowledging resolver caching (Lesson 4).
- Proposing multi-leader without mentioning conflict resolution — or proposing last-write-wins without acknowledging it silently discards data and depends on clock synchronisation.
- Confusing replication with sharding, or using the words interchangeably.
- Assuming synchronous replication is free; it converts a local fsync into a network round trip on every commit.
- Sizing replicas smaller than the leader "because they only do reads".

## 17. Knowledge check

Write your answers down before continuing.

**Conceptual**

1. Explain, mechanically, why adding read replicas increases read capacity but does not increase write capacity — and why past a certain write rate it stops increasing read capacity as well. Your answer must identify the specific quantity that is subtracted from each replica's capacity, derive the write rate at which a replica's usable read capacity reaches zero, and state what architectural change is required beyond that point and why it works when replication does not.

2. A colleague argues that because BookNook has five live copies of its data (one leader, four followers), nightly backups are redundant and can be switched off to save money. Rebut this with four distinct failure classes that five replicas do not protect against, naming for each one why replication specifically fails to help. Then state the three properties that distinguish a backup from a replica, and explain why a backup that has never been restored does not satisfy them.

3. Compare physical (WAL) replication with logical (decoded) replication. For each, state what is shipped over the wire, the distinct failure mode that does not exist in the other, and one task that is *only* possible with that one. Then explain why most mature production systems run both simultaneously rather than choosing one, and which role each plays.

**Practical scenario**

4. BookNook's leader fails at 02:00. The orchestrator detects the failure after 12 seconds, promotes follower-2, and repoints the connection pooler. However, the old leader was only network-partitioned from the orchestrator, not actually dead, and a subset of application servers in the same subnet could still reach it. Fencing was not configured. Describe exactly what happens over the next 20 minutes, including what two different groups of users experience and why neither group sees an error. Explain why this situation cannot be automatically resolved once both nodes rejoin the network, and what the manual recovery actually involves. Then state the specific mechanism that prevents it, explain why the orchestrator could not simply "ask the old leader to stop", and describe one application-level design that would have bounded the damage.

**Calculation**

5. BookNook expands and its workload changes. Assume: peak 30,000 queries/second; 88 % reads, 12 % writes; each node has a capacity of 5,000 read-equivalents/second; a read costs 1.0 read-equivalents; a write costs 3.0 on the leader and 1.4 to replay on a follower; the maximum sustained utilisation target is 70 %; N+1 redundancy is required; the leader serves no ordinary reads.
   (a) Compute the read rate and the write rate. Can a single leader even accept this write rate within the 70 % target? Show the arithmetic and state the conclusion explicitly.
   (b) Compute each replica's usable read capacity at the 70 % target, and the number of replicas required including N+1.
   (c) Derive the write rate at which a replica's usable read capacity reaches zero. How far is BookNook from that wall as a percentage, and what does that imply about how much growth headroom replication alone provides?
   (d) A proposal batches view-counter updates through Redis, removing 45 % of writes. Recompute (a), (b) and (c) under the new write rate, and state the change in replica count and in distance to the wall.
   (e) An alternative proposal is to buy replicas with twice the capacity (10,000 read-equivalents/second). Compute the new replica count and the new wall. Then state which of the two proposals you would implement first and justify it with the numbers, including at least one consideration that is not capacity.
   Show every step and state any additional assumptions you make.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 33.

---

**Progress checklist**

Phase 1 — Foundations: **complete** (Lessons 1–17)

Phase 2 — Scaling:
`[x] 1. Vertical scaling` · `[x] 2. Horizontal scaling` · `[x] 3. Stateless application servers` · `[x] 4. Load balancers` · `[x] 5. Layer 4 versus Layer 7` · `[x] 6. Load-balancing strategies` · `[x] 7. Health checks and failover` · `[x] 8. Proxies and API gateways` · `[x] 9. Caching fundamentals` · `[x] 10. Cache placement and strategies` · `[x] 11. Cache invalidation and TTLs` · `[x] 12. Cache stampedes` · `[x] 13. Hot keys and eviction policies` · `[x] 14. CDNs and edge caching` · `[x] 15. Database replication`
`[ ] 16. Synchronous versus asynchronous replication` ← next
`[ ] 17–22` not started

Phases 3–5: not started.

**NEXT LESSON: Phase 2, Topic 16 — Synchronous versus asynchronous replication**


# Lesson 33 — Synchronous versus Asynchronous Replication

*Phase 2, Topic 16*

## 1. Simple definition

Lesson 32 established the mechanics of leader–follower replication: the leader writes every change to its **write-ahead log (WAL)**, streams that log to followers, and each follower replays it to reproduce the leader's state.

This lesson answers one question about that pipeline:

> *"At what point in the pipeline does the leader tell the client **'your write is committed'**?"*

That single decision is the difference between synchronous and asynchronous replication.

- **Asynchronous replication**: the leader acknowledges the write as soon as *its own* copy is durable. The WAL is shipped to followers afterwards, on a best-effort basis. The client is told "done" before any follower has seen the data.
- **Synchronous replication**: the leader waits until one or more designated followers confirm they have the data, and only then acknowledges. The client is told "done" after at least one other machine holds the write.

Terms defined before use, because the rest of the lesson is unreadable without them:

- **Acknowledgement (ack)**: the response the database sends to the client saying the `COMMIT` succeeded. Everything after the ack is the database's problem; everything before it, the client can still retry.
- **Durable**: written somewhere that survives the loss of power or the process. In practice this means `fsync()` has returned — the operating system has been told to push the data out of its page cache onto the storage device, and the device has confirmed.
- **Flush vs write vs apply**: three distinct stages on a follower. *Write* = the bytes are in the follower's OS page cache. *Flush* = the bytes are `fsync`'d to the follower's disk. *Apply* (also called *replay*) = the follower has actually executed the change, so queries on that follower can now see it. These are three different guarantees and three different costs.
- **RPO (Recovery Point Objective)**: the maximum amount of *data*, expressed as a span of time, that the business accepts losing in a disaster. "RPO = 5 seconds" means: after a failover, it is acceptable that the surviving system is missing up to the last 5 seconds of acknowledged writes.
- **RTO (Recovery Time Objective)**: the maximum acceptable *downtime* during the same disaster. RPO is about lost data; RTO is about lost time. They are independent and commonly confused.

The shortest correct summary of this lesson: **synchronous replication buys you RPO = 0 and pays for it in write latency on every single write, forever.**

## 2. Why it exists

Lesson 32 gave you read replicas, and the implicit assumption was that replication is "basically instant". It is not, and the gap matters in exactly one moment: **the moment the leader dies.**

Consider BookNook's PostgreSQL primary in `eu-west-1a` with one asynchronous replica in `eu-west-1b`. A customer completes checkout:

1. The primary writes the order to its WAL and `fsync`s it.
2. The primary returns `201 Created`. The customer's card is charged. The confirmation email is queued.
3. 4 milliseconds later, before the WAL segment reaches the replica, the primary's host suffers a hardware fault and never comes back.
4. You promote the replica. The order **does not exist**.

The customer has a charge on their card, an email in their inbox, and no order in your database. No amount of retrying fixes this: the write was acknowledged, so the application correctly believes it succeeded and will never retry it. The data is simply gone.

Asynchronous replication means that **a window of acknowledged-but-unreplicated writes always exists**, and the size of that window is your real RPO. Synchronous replication exists to shrink that window to zero.

The reason this is a *topic* rather than an obvious choice is that closing the window is expensive. Every synchronous write pays a network round trip plus a remote disk flush, on the critical path, before the user sees a response. At cross-region distances that round trip is 60–90 ms (typical), which is 50× the entire cost of a local commit. You are trading a guaranteed, continuous, user-visible latency cost against a rare, catastrophic data-loss cost.

That is the whole topic. Everything below is about quantifying both sides so the trade is made deliberately rather than by accident.

## 3. Real-world analogy

**A law firm filing a signed contract, with an off-site records office.**

A client signs a contract in your office. The contract legally exists once it is filed. You also keep a duplicate at an off-site records office so that a fire in your building does not destroy the firm's records.

- **Asynchronous**: you stamp the contract, put the duplicate in the outgoing courier tray, and immediately tell the client *"you're all set."* The courier leaves at some point in the next few minutes. If your office burns down in those few minutes, the duplicate burns with it and the client's contract is gone — even though you told them it was filed.
- **Synchronous (remote write)**: you fax the duplicate and wait for the fax machine to report "received" before telling the client "you're all set." The off-site office has the paper in its inbound tray. It has not been filed yet, but it exists outside your building.
- **Synchronous (remote flush)**: you wait for the off-site clerk to phone back and confirm *"it's in the fireproof cabinet."* Stronger, slower.
- **Synchronous (remote apply)**: you wait for the off-site clerk to confirm *"it's in the cabinet **and** indexed in the register, so anyone who walks in and asks for this contract will find it."* Strongest, slowest, and the only level that makes the off-site office immediately *useful* for lookups.
- **Semi-synchronous**: you wait for confirmation, but with a stopwatch — if the off-site office has not answered in two seconds, you tell the client "you're all set" anyway and let the courier sort it out.
- **Quorum**: you have three off-site offices and you wait for *any two* of them to confirm. One office being slow today no longer slows down every client.

**Where the analogy breaks down:**

| Law firm | Database replication |
|---|---|
| The courier tray is visibly full, so you can see the backlog | Replication lag is invisible unless you explicitly monitor it; a healthy-looking system can be 40 seconds behind (Lesson 34) |
| One contract per client, a few per hour | Thousands of transactions per second, so a backlog of "a few minutes" means millions of lost writes |
| Waiting for the fax costs the clerk's time, not the client's | The synchronous wait is on the **user's** critical path — it is added to every checkout, every page load that writes |
| If the fax fails you can simply call | If the only synchronous standby is unreachable, the leader **blocks every write indefinitely** — the "safe" option causes a total outage |
| The contract is void if not filed | In PostgreSQL the transaction is already committed *locally* before the wait begins; the wait only delays the client's acknowledgement. A crash of the leader during the wait can leave a transaction that is committed locally but was never acknowledged |
| Offices are either "near" or "far" | Distance is continuous and physics-bound: ~5 µs per kilometre of fibre each way, so London↔Virginia is ~70 ms round trip no matter what you buy |

That fifth row is the most important and the least intuitive. Naively, "synchronous" sounds strictly safer. In practice, a single required synchronous standby converts an *availability* problem on the standby into a *total write outage* on the leader. Safety and availability are in direct tension here, and that tension is the reason quorum configurations exist.

## 4. Detailed technical explanation

### The five stages of a replicated commit

Every replicated write passes through the same stages. Synchronous and asynchronous differ *only* in which stage triggers the client acknowledgement.

```
Stage 0  Client sends COMMIT
Stage 1  Leader writes the commit record into its WAL buffer (in memory)
Stage 2  Leader fsyncs its WAL to local durable storage
Stage 3  WAL bytes arrive at the follower's OS page cache          ("write")
Stage 4  Follower fsyncs those bytes to its own durable storage    ("flush")
Stage 5  Follower replays the record into its data pages           ("apply")
```

| Ack after stage | Name | Survives |
|---|---|---|
| 1 | local asynchronous commit | nothing beyond a clean shutdown |
| 2 | **asynchronous replication** | leader process crash, OS crash — **not** leader disk/host loss |
| 3 | synchronous, remote write | follower *process* crash; not follower host loss |
| 4 | synchronous, remote flush | loss of the entire leader host |
| 5 | synchronous, remote apply | loss of leader host **and** gives reads on that follower immediate visibility |

This table is the entire lesson in compressed form. Everything else is consequences.

### Asynchronous replication, precisely

The leader acks at stage 2. A separate process (in PostgreSQL, the WAL sender) streams WAL to each follower continuously and independently of client transactions.

Key properties:
- **Write latency is independent of the number of followers.** Adding a tenth replica costs the leader some CPU and network bandwidth but adds zero latency to any commit. This is why read-replica fan-out (Lesson 32) is cheap.
- **Followers cannot slow down or block writes.** A dead follower is a monitoring problem, not an outage.
- **RPO is non-zero and variable.** It equals the replication lag at the instant of failure. Under normal load that may be 2 ms; during a bulk import it may be 40 seconds. Your RPO is therefore not a constant you choose — it is a *measured distribution* you must monitor, which is exactly what Lesson 34 is about.
- **Failover is lossy by construction.** Promoting a follower discards every write that had not reached it.

### Synchronous replication, precisely

The leader performs stages 1 and 2, then **waits** for confirmation from a defined set of followers before acking.

Key properties:
- **Write latency = local commit time + the slowest required follower's response time.** Not the average — the *maximum*. This is the central cost.
- **RPO = 0 with respect to the synchronous set.** Any acknowledged write exists on at least one other durable device.
- **Availability of writes now depends on the followers.** If the required set cannot be satisfied, commits block.
- **It is not a consensus protocol.** PostgreSQL's synchronous replication does not perform leader election or guarantee that the right node is promoted. It guarantees only *"a copy exists elsewhere."* Full consensus — where the cluster itself agrees on the leader and the committed prefix — is **Phase 5, Topic 6 — Consensus algorithms (Raft and Paxos)**.

An important and widely-misunderstood subtlety in PostgreSQL: the transaction is written and flushed locally **before** the synchronous wait begins. If the leader crashes mid-wait, that transaction is present after recovery even though the client never got an ack. If a client cancels the query during the wait (`Ctrl-C`, statement timeout), PostgreSQL emits a warning and the transaction remains committed locally. So synchronous replication prevents *losing* acknowledged writes; it does **not** prevent *unacknowledged* writes from existing. Clients must therefore still treat "no response" as "unknown", not as "failed" — which is why idempotency keys (Lesson 13) are not optional.

### Semi-synchronous replication

**Semi-synchronous replication** = synchronous replication with a timeout and an automatic fallback to asynchronous.

The leader waits for a follower ack, but if no ack arrives within *T* milliseconds, it acknowledges the client anyway and continues in asynchronous mode until the follower recovers.

- MySQL implements this natively (`rpl_semi_sync_source_timeout`, default 10,000 ms).
- **PostgreSQL does not have this feature.** There is no timeout on a synchronous wait: a PostgreSQL leader with `synchronous_commit = on` and an unreachable sole synchronous standby blocks writes **forever**. Operators simulate semi-synchronous behaviour externally, by having a cluster manager (Patroni, repmgr) detect the stalled standby and rewrite `synchronous_standby_names` to empty. Patroni's `synchronous_mode_strict = false` does exactly this.

The honest description of semi-synchronous is: *"RPO = 0 most of the time, RPO > 0 exactly when something is already going wrong."* That is a weaker guarantee than it sounds, because the moments when the standby is lagging or unreachable are precisely the moments when a correlated failure is most likely. Semi-synchronous is best understood as **an availability optimisation with a safety asterisk**, not as a safety mechanism.

### Quorum-based replication

Instead of naming one required follower, you require *any k of n*.

```
synchronous_standby_names = 'ANY 2 (replica_b, replica_c, replica_d)'
```

The leader acks when any 2 of the 3 named standbys confirm. Why this is qualitatively better than `k = n`:

- **It removes the slowest node from the critical path.** With `ANY 2 (of 3)`, you wait for the *second-fastest*, not the slowest. One replica doing a checkpoint or garbage collection no longer stalls every write.
- **It tolerates failure without losing the guarantee.** Any single standby can die and writes continue, still with RPO = 0.
- **It makes the latency distribution dramatically tighter**, which we quantify in Section 9.

PostgreSQL also offers `FIRST k (...)`, a *priority*-ordered variant: the named standbys have fixed precedence and the leader waits for the k highest-priority available ones. `ANY k` is latency-driven; `FIRST k` is topology-driven (e.g. "always prefer the standby in the same region"). `ANY` is usually what you want for latency; `FIRST` is what you want when the standbys are not interchangeable.

Quorums become much more powerful when both reads and writes use them, because `R + W > N` yields read guarantees as well as write guarantees. That full treatment — quorum arithmetic, sloppy quorums, hinted handoff — is **Phase 4, Topic 17 — Quorums (R + W > N)**. For now, hold onto one idea: a quorum converts *"wait for the slowest"* into *"wait for the k-th fastest"*, and that single change is what makes strong durability affordable at scale.

### The durability-versus-latency trade-off, in physics

Replication latency is dominated by the speed of light in fibre: roughly 200,000 km/s, i.e. **~5 µs per kilometre, each way**. You cannot buy your way out of this. Typical (approximate) round-trip times:

| Topology | Typical RTT | Example |
|---|---|---|
| Same host (different process) | 0.05 ms | Not a real durability boundary — ignore |
| Same availability zone | 0.1 – 0.5 ms | `eu-west-1a` → `eu-west-1a` |
| Cross-AZ, same region | 0.5 – 2 ms | `eu-west-1a` → `eu-west-1b` |
| Cross-region, same continent | 10 – 30 ms | `eu-west-1` (Ireland) → `eu-central-1` (Frankfurt) |
| Cross-region, transatlantic | 65 – 90 ms | `eu-west-1` → `us-east-1` |
| Cross-region, antipodal | 250 – 320 ms | `eu-west-1` → `ap-southeast-2` |

Add to the RTT the follower's own `fsync` (0.2–1.0 ms typical on cloud NVMe/EBS gp3) when using flush-level synchronous commit.

The practical consequence is a hard rule you can state in an interview and defend:

> **Synchronous replication across AZs within a region is usually affordable. Synchronous replication across regions usually is not, unless writes are rare or the business genuinely cannot tolerate any loss.**

### Why synchronous replication is a tail-latency problem (Lesson 16)

A synchronous commit's latency is `max(local_commit, follower_1, follower_2, …)` over the required set. Lesson 16 established that taking a maximum over independent samples pulls you deep into the tail.

If a single follower acknowledges in 1 ms at p50 but occasionally stalls to 25 ms at p99 (checkpoints, WAL file recycling, background fsync, noisy neighbours), then with `k` *required* followers:

```
P(commit hits a slow path) = 1 − (1 − 0.01)^k
k = 1 : 1.00 %
k = 2 : 1.99 %
k = 3 : 2.97 %
```

Your database's p99 becomes, approximately, each follower's p99 — and your p98 degrades too. By contrast, with `ANY 1 (of 2)` you need *both* to be slow simultaneously:

```
P(both slow) = 0.01 × 0.01 = 0.0001 = 0.01 %
```

A 100× reduction in the probability of a slow commit, from a configuration change. This is the same mathematics as the hedged-request and power-of-two-choices arguments from Lessons 16 and 23: **requiring all of N amplifies the tail; requiring any k of N suppresses it.**

### PostgreSQL `synchronous_commit` levels, one at a time

This setting is the single most important knob in this lesson. It is **per-transaction settable**, which is the feature most teams fail to exploit.

**`synchronous_commit = off`**
Ack after stage 1. The commit returns before even the *local* WAL has been flushed to disk. This has nothing to do with replication — it is local asynchronous commit.
- *Guarantee*: transactional integrity is preserved (the database never corrupts, and partial transactions never appear), but recent commits can vanish on an OS or hardware crash.
- *Window*: bounded by roughly `3 × wal_writer_delay` (default `200 ms`, so up to ~600 ms of commits).
- *Use when*: the data is reconstructible — click events, view counters, non-critical telemetry.

**`synchronous_commit = local`**
Ack after stage 2. Wait for the local WAL flush only; ignore replicas entirely.
- *Guarantee*: survives process crash and OS crash of the leader. Does **not** survive loss of the leader's disk or host.
- *This is asynchronous replication.* Note the trap: `local` is also how you *exempt one specific transaction* from a cluster-wide synchronous policy.

**`synchronous_commit = remote_write`**
Ack after stage 3. Wait for the local flush plus confirmation that a synchronous standby has *written* the WAL into its OS page cache.
- *Guarantee*: survives loss of the leader host **and** a crash of the standby's PostgreSQL process. Does **not** survive the standby's OS or host dying at the same instant, because the bytes were still only in RAM there.
- *Cost*: one network round trip, no remote disk flush. Typically the best latency-to-safety ratio for cross-AZ setups.

**`synchronous_commit = on`** (the default)
Ack after stage 4. Wait for the local flush plus confirmation that a synchronous standby has `fsync`'d the WAL to durable storage.
- *Guarantee*: a correlated loss of the leader host does not lose the write; the data is on two independent devices.
- *Does not guarantee visibility*: the standby has the bytes on disk but may not have replayed them, so a read issued against that standby immediately afterwards can still return stale data. This surprises people constantly.
- *Cost*: one round trip plus one remote `fsync`.

**`synchronous_commit = remote_apply`**
Ack after stage 5. Wait until the standby has replayed the record, so queries on that standby observe it.
- *Guarantee*: durability **and** read-your-writes on the synchronous standby — this is the only level that gives you a consistency guarantee rather than just a durability guarantee.
- *Cost*: the highest, and the most fragile. Replay on a hot standby can be blocked by conflicting read queries (a long `SELECT` holding a snapshot that the replayed change would invalidate). Replay delays therefore propagate directly into leader write latency. A single long analytical query on the standby can stall every write on the primary.

**The critical gotcha**: if `synchronous_standby_names` is empty, then `remote_write`, `on`, and `remote_apply` **all silently behave exactly like `local`**. There is no error and no warning. Many teams believe they have synchronous replication because `synchronous_commit = on` is the default, while `synchronous_standby_names` was never set. The default configuration of PostgreSQL is asynchronous replication. Verify with the query in Section 8.

### Why most systems choose asynchronous plus strong backups

The majority of production systems, including most large ones, run asynchronous replication. This is a considered choice, not laziness, and the reasoning is:

1. **The loss window is small and the failure is rare.** Same-AZ or cross-AZ async lag under normal load is typically 1–20 ms. An uncontrolled leader loss might happen once every year or two. Expected data loss is therefore tiny.
2. **The latency cost of synchronous is paid on 100 % of writes, forever.** You pay continuously to insure against something that happens rarely.
3. **Asynchronous replication cannot cause a write outage.** Synchronous replication can, and does — it converts standby problems into primary problems.
4. **Most "lost write" risk is better handled elsewhere.** Idempotency keys plus a durable, replicated message log (Phase 4, Topic 5 — Message queues) in front of the critical path means a lost order can be *replayed* rather than mourned.
5. **Backups cover the other disaster.** Synchronous replication protects against *host loss*. It does not protect against the far more common disasters: `DELETE` without a `WHERE`, a bad migration, or ransomware — all of which replicate perfectly and instantly to every synchronous standby. The defence there is **point-in-time recovery (PITR)**: periodic base backups plus continuous WAL archiving, letting you restore to any second before the mistake.

A defensible default for a system like BookNook:

```
Primary (eu-west-1a)
  ├── synchronous standby (eu-west-1b, remote_write)   ← RPO 0 for host loss
  ├── asynchronous read replicas (eu-west-1a/b/c)      ← read scale, Lesson 32
  ├── asynchronous standby (eu-central-1)              ← regional disaster, RPO ~seconds
  └── continuous WAL archiving to object storage       ← PITR, RPO ~seconds, covers human error
```

Note that only the second line costs write latency. The rest are free, and the last one covers a failure class that no amount of synchronous replication addresses.

## 5. Request / data flow

A customer clicks **Place order** on BookNook. The cluster is configured with `synchronous_commit = on` and `synchronous_standby_names = 'ANY 1 (replica_b, replica_c)'`, where `replica_b` is cross-AZ and `replica_c` is cross-region.

1. The browser sends `POST /orders` with an `Idempotency-Key` header (Lesson 13). The load balancer routes it to an app server (Lesson 23).
2. The app server takes a pooled connection to the **primary** — writes never go to a replica (Lesson 32).
3. The app issues `BEGIN`, inserts the order row and the order-line rows, and decrements stock.
4. Each statement generates WAL records in the primary's shared WAL buffer. Nothing is durable yet; nothing has been sent anywhere.
5. The app issues `COMMIT`. The primary appends a commit record to the WAL buffer and assigns it an **LSN (Log Sequence Number)** — a monotonically increasing byte offset into the WAL stream that uniquely names this position. LSNs are the currency of the next lesson, so note them now.
6. The primary writes and `fsync`s the WAL up to that LSN to its local disk. *(Stage 2 — ~0.3 ms typical on NVMe.)* The transaction is now committed locally and will survive a primary restart.
7. The backend process registers itself in the primary's wait queue for that LSN, then **sleeps**. It does not hold CPU. It is not holding row locks that matter to this discussion, but it *is* holding its connection and its transaction slot.
8. The WAL sender processes — one per connected standby, running independently — push the new WAL bytes to `replica_b` and `replica_c` over their replication connections.
9. `replica_b` (cross-AZ, ~1 ms RTT) receives the bytes into its OS page cache, `fsync`s them *(stage 4, ~0.3 ms)*, and sends back a feedback message containing its `flush_lsn`.
10. `replica_c` (cross-region, ~70 ms RTT) is still mid-flight. It will confirm later.
11. The primary receives `replica_b`'s confirmation. The quorum requirement — `ANY 1` — is now satisfied at `flush` level. The waiting backend is woken.
12. The primary returns `COMMIT` success to the application. **Total added latency ≈ 1.3 ms, not 70 ms**, because the quorum needed only the fastest of the two.
13. The app server returns `201 Created`. The customer sees the confirmation page.
14. ~70 ms later, `replica_c` confirms. Its ack is now irrelevant to this transaction; it simply advances the primary's record of `replica_c`'s position so WAL can eventually be recycled.
15. Separately and asynchronously, each replica's startup process **replays** the WAL into its data pages *(stage 5)*. Only now can a read on that replica see the new order. This step is **not** waited for at `synchronous_commit = on` — which is precisely the gap that creates read-after-write anomalies in Lesson 34.
16. The archiver process ships the completed WAL segment to object storage, extending the PITR window.

Step 15 is the one to carry forward. The write is **durable** on the replica long before it is **visible** on the replica. Durability and visibility are different properties with different costs, and conflating them is the root of most replication confusion.

## 6. ASCII diagram

```
ASYNCHRONOUS — ack at stage 2. Replicas are off the critical path.

 client ──COMMIT──▶ LEADER
                      │ write WAL buffer        (0.05 ms)
                      │ fsync local             (0.30 ms)
 client ◀──ACK────────┤                         ◀── user is already done here
                      │                              total ≈ 1.3 ms
                      └─ WAL sender ──▶ replica (whenever)
                                          │ write + fsync + replay
                                          ▼
 ╔══════════════════════════════════════════════════════════════╗
 ║  DANGER WINDOW: everything acked but not yet shipped is      ║
 ║  LOST FOREVER if the leader dies now.  Width = current lag.  ║
 ╚══════════════════════════════════════════════════════════════╝


SYNCHRONOUS (on) — ack at stage 4. Replica is ON the critical path.

 client ──COMMIT──▶ LEADER
                      │ fsync local             (0.30 ms)
                      │──── WAL ────▶ REPLICA
                      │                 │ write to OS cache   (remote_write ACKS HERE)
                      │                 │ fsync to disk       (on ACKS HERE)
                      │                 │ replay into pages   (remote_apply ACKS HERE)
                      │◀─── ack ────────┘
 client ◀──ACK────────┤
                      total ≈ 1.3 ms same-AZ · 2.6 ms cross-AZ · 71.6 ms cross-region
 ╔══════════════════════════════════════════════════════════════╗
 ║  DANGER WINDOW: zero. Every acked write exists on 2 devices. ║
 ╚══════════════════════════════════════════════════════════════╝


WAIT-FOR-ALL vs QUORUM — where the tail latency goes (Lesson 16)

  ALL 2 (b, c):   b:1ms ───────┐
                  c:70ms ──────┴──────────────────────────────▶ 70 ms
                  waits for the SLOWEST.  p99 of the commit = p99 of the worst replica.

  ANY 1 (b, c):   b:1ms ───────▶ 1 ms   ◀── wakes here
                  c:70ms ─ ─ ─ ─ ─ ─ (irrelevant)
                  waits for the FASTEST.  A slow replica costs nothing.
                  P(slow commit) = P(b slow) × P(c slow) = 0.01 × 0.01 = 0.01%


FAILOVER UNDER ASYNC — what RPO actually means

  leader WAL:   ... [T97][T98][T99][T100][T101][T102]   ← acked to clients
  replica WAL:  ... [T97][T98][T99]                     ← received
                                   ╲____________________╱
                                     3 transactions = RPO
  leader dies ✖  → promote replica → T100, T101, T102 NEVER EXISTED.
  Cards charged. Emails sent. Orders gone. No retry will ever happen,
  because the application was told they succeeded.
```

## 7. Detailed example

### BookNook — discovering the problem the expensive way

BookNook runs a PostgreSQL 16 primary in `eu-west-1a`, two asynchronous read replicas (Lesson 32), and nightly `pg_dump` backups. `synchronous_standby_names` has never been set, so despite `synchronous_commit = on` being the default, **replication is fully asynchronous** and nobody knows it.

**Phase 1 — the incident.** The primary's underlying host fails at 14:07 on a Friday. The on-call engineer promotes `replica_a`. Total downtime: 4 minutes (good RTO). Then the support tickets arrive: 61 customers were charged but have no order. Reconciliation against the payment provider shows the gap.

Post-incident analysis of the archived WAL shows the replica was **2.4 seconds** behind at the moment of failure, because a marketing email had just gone out and write throughput had spiked. At the prevailing ~26 writes/second of order-related transactions, 2.4 s ≈ 62 transactions. The number matches.

**Phase 2 — the naive fix.** The team sets `synchronous_standby_names = 'replica_a'` with `synchronous_commit = on`. RPO is now 0. Two weeks later, `replica_a`'s host has a network partition. PostgreSQL has no timeout on a synchronous wait, so **every write on the primary blocks**. Checkout, cart updates, and sign-ups all hang. Reads still work, so monitoring based on "is the site up?" shows green while the business is entirely down for 23 minutes.

They have converted a rare, bounded, 62-row data-loss event into a repeatable total write outage. This is the single most common mistake in this topic.

**Phase 3 — the considered fix.**

```
synchronous_standby_names = 'ANY 1 (replica_a, replica_b)'
synchronous_commit        = remote_write      # cluster default
```

- `ANY 1 (of 2)` means either replica satisfies the requirement, so one failing replica no longer blocks writes, and commit latency tracks the *faster* of the two.
- `remote_write` rather than `on` removes the remote `fsync` from the critical path. Both replicas are in different AZs on separate power and network, so losing the leader host does not lose the bytes in a replica's RAM. This is a deliberate, documented reduction in guarantee: it does not survive a *simultaneous* leader-host loss and replica-OS crash. The team wrote that sentence down in the runbook, which is what "making a trade-off" actually looks like.
- Patroni is configured with `synchronous_mode: true` and `synchronous_mode_strict: false`, so if **both** replicas become unavailable, Patroni clears `synchronous_standby_names` and the cluster degrades to asynchronous rather than halting. An alert fires immediately. This is the semi-synchronous behaviour PostgreSQL lacks natively.
- Per-transaction overrides are added, which is where the real win comes from:

| BookNook write | Setting | Reasoning |
|---|---|---|
| `POST /orders`, payments, refunds | `remote_write` (cluster default) | Money. RPO must be 0. |
| `PATCH /cart` | `local` | A lost cart item is annoying, recoverable by the user, and carts are re-derivable from the client. |
| Page-view and search-term logging | `off` | Analytics. Losing 600 ms of rows changes no decision anyone makes. |
| `POST /reviews` | `local` | Rare, and the user can repost. |

Measured result: the p50 write latency for the *whole application* rose by only 0.4 ms, because only ~3 % of writes are order-related and the rest were exempted. The naive configuration had added 1.3 ms to **every** write.

**Phase 4 — the part synchronous replication could never fix.** Three months later, a migration script runs `UPDATE books SET price = price * 0.9` without its `WHERE` clause. The change is faithfully, synchronously, instantly replicated to both standbys with RPO 0. Synchronous replication made the disaster *more* reliable. Recovery comes entirely from PITR: restore the base backup, replay archived WAL to 09:14:52.3, the second before the statement. That is why Section 4 insisted that replication is not a backup.

### Contrasting example — a card-payment ledger

A payment processor's double-entry ledger has the opposite calculus, and seeing why makes the trade-off concrete rather than abstract.

- **Write volume is low**: ~400 ledger commits/second, not 20,000.
- **Each write is financially irreversible**: a lost ledger entry means real money is unaccounted for, and the regulator does not accept "the replica was 2 seconds behind."
- **Latency budget is generous**: the card network's own authorisation round trip is already 300–800 ms, so adding 2.6 ms of cross-AZ synchronous commit is 0.5 % of the user-visible time. In BookNook's checkout, the same 2.6 ms would be ~15 % of the request.
- **Regulatory RPO is contractually zero.**

So the processor runs `synchronous_commit = remote_apply` with `ANY 2 (of 3)` standbys across three AZs, accepts ~3 ms commits, and runs an asynchronous cross-region standby for regional disaster with a documented RPO of ~5 seconds for that scenario only.

Identical database, identical mechanisms, opposite configuration — because the ratio of *cost of a lost write* to *cost of added latency* differs by three orders of magnitude. That ratio, not a preference, is what decides this.

## 8. Code or configuration example

> **Illustrative only — not production-ready.** Connection management, TLS for replication, authentication, replication slots sizing, monitoring, failover orchestration and error handling are simplified or omitted for clarity. Do not copy these values into a real cluster without understanding each one.

### PostgreSQL configuration — the primary

```ini
# ---------------------------------------------------------------------------
# postgresql.conf on the PRIMARY
# ---------------------------------------------------------------------------

wal_level = replica                 # minimum level that supports streaming replication.
                                    # 'logical' only if you also need logical decoding;
                                    # it generates MORE WAL, so do not enable it casually.

max_wal_senders = 10                # one sender process per connected standby, plus
                                    # headroom for pg_basebackup. Too low = a new
                                    # replica silently fails to attach.

max_replication_slots = 10          # replication slots stop the primary recycling WAL
                                    # a standby still needs. Essential for correctness,
                                    # DANGEROUS without monitoring: a dead standby with a
                                    # slot will fill the primary's disk and take it down.

max_slot_wal_keep_size = 64GB       # the safety valve for the line above. Beyond this,
                                    # the slot is invalidated and the standby must be
                                    # rebuilt -- a bad outcome, but better than the
                                    # primary running out of disk.

# --- THE TWO SETTINGS THIS LESSON IS ABOUT --------------------------------

synchronous_commit = remote_write   # ack after the standby has the WAL in its OS cache.
                                    # off          -> no local flush wait (~600 ms window)
                                    # local        -> local flush only  (= ASYNC replication)
                                    # remote_write -> standby has bytes in RAM
                                    # on           -> standby has fsync'd to disk
                                    # remote_apply -> standby has REPLAYED (reads see it)

synchronous_standby_names = 'ANY 1 (replica_b, replica_c)'
                                    # CRITICAL: if this is EMPTY, then remote_write / on /
                                    # remote_apply all silently behave exactly like 'local'.
                                    # PostgreSQL's DEFAULT configuration is ASYNCHRONOUS.
                                    #
                                    # 'ANY 1 (b, c)'  -> wait for the FASTEST of two.
                                    #                    Survives one replica dying.
                                    # 'ANY 2 (b,c,d)' -> stronger durability, still tolerant
                                    #                    of one failure. Waits for 2nd-fastest.
                                    # 'FIRST 1 (b,c)' -> priority order, not latency order.
                                    # 'replica_b'     -> SINGLE POINT OF WRITE FAILURE.
                                    #                    If replica_b hangs, ALL WRITES BLOCK
                                    #                    FOREVER. PostgreSQL has no timeout.

# --- Keep the replication link honest -------------------------------------

wal_sender_timeout = 60s            # declare a standby dead if it stops responding.
                                    # With a sole synchronous standby this is what
                                    # eventually unblocks writes -- which is why you want
                                    # a quorum instead of relying on it.

wal_keep_size = 2GB                 # extra WAL retained even without a slot; smooths
                                    # over brief standby disconnects.

archive_mode = on
archive_command = 'wal-g wal-push %p'   # PITR. This is what actually protects you from
                                        # DELETE-without-WHERE, which replication CANNOT.
```

### PostgreSQL configuration — a standby

```ini
# ---------------------------------------------------------------------------
# postgresql.conf on a STANDBY
# ---------------------------------------------------------------------------

primary_conninfo = 'host=10.0.1.10 port=5432 user=replicator application_name=replica_b'
                                    # application_name MUST match the name used in
                                    # synchronous_standby_names, or this standby will
                                    # never count towards the quorum and you will believe
                                    # you have synchronous replication when you do not.

primary_slot_name = 'slot_replica_b'
hot_standby = on                    # allow read-only queries (Lesson 32).

max_standby_streaming_delay = 30s   # how long WAL replay may be PAUSED to let a
                                    # conflicting read query finish. Replay pause =
                                    # replication lag (Lesson 34), and under
                                    # remote_apply it becomes PRIMARY WRITE LATENCY.

hot_standby_feedback = on           # tell the primary which rows this standby still
                                    # needs, so VACUUM does not remove them.
                                    # TRADE-OFF: prevents query cancellations, but lets a
                                    # long standby query cause table BLOAT on the primary.
```

### SQL — verifying what you actually have

```sql
-- 1. Are we actually synchronous? Run on the PRIMARY.
--    sync_state: 'sync'   = counted toward the quorum
--                'potential' = standby, eligible, but not currently required
--                'async'  = NOT counted; contributes nothing to RPO 0
SELECT application_name,
       client_addr,
       state,                        -- streaming / catchup / startup
       sync_state,
       sync_priority,
       sent_lsn,                     -- how far the primary has SENT
       write_lsn,                    -- stage 3: in standby's OS cache
       flush_lsn,                    -- stage 4: fsync'd on the standby
       replay_lsn,                   -- stage 5: VISIBLE to queries on the standby
       write_lag, flush_lag, replay_lag   -- round-trip times per stage (PG 10+)
FROM pg_stat_replication;

-- 2. The single most important sanity check in this whole lesson.
--    If this returns an empty string, your "synchronous" cluster is ASYNCHRONOUS.
SHOW synchronous_standby_names;

-- 3. Exact unreplicated byte window = your current RPO, in bytes.
--    Measures the gap between what the primary has committed and what the
--    standby has made durable. Works even when the system is idle, unlike
--    any time-based measure (see Lesson 34).
SELECT application_name,
       pg_wal_lsn_diff(pg_current_wal_lsn(), flush_lsn)  AS unflushed_bytes,
       pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) AS unapplied_bytes
FROM pg_stat_replication;

-- 4. Per-transaction override: exempt cheap writes from the synchronous cost.
--    This is where most of the practical win lives.
BEGIN;
SET LOCAL synchronous_commit = 'local';     -- SET LOCAL = this transaction only
INSERT INTO page_views (user_id, path, at) VALUES (4411, '/books/9780141036', now());
COMMIT;                                      -- returns after LOCAL flush only

-- 5. ...and the opposite: force a stronger guarantee for one critical write,
--    even if the cluster default is weaker.
BEGIN;
SET LOCAL synchronous_commit = 'remote_apply';   -- wait until the standby can SEE it
INSERT INTO payments (order_id, amount_cents, provider_ref)
VALUES (90210, 2499, 'ch_3PqX...');
COMMIT;
```

### Python — choosing a durability level per operation

```python
import psycopg
from contextlib import contextmanager

# Illustrative only. A real implementation needs connection pooling, retries with
# backoff and jitter (Lesson 17), metrics, and structured error handling.

# Map business meaning -> durability level. Writing this table down forces the
# team to make an explicit decision for every write path, instead of inheriting
# one global setting that is wrong for 95% of traffic.
DURABILITY = {
    "money":      "remote_write",  # RPO 0: must exist on a second host before ack
    "visible":    "remote_apply",  # RPO 0 AND readable on the sync standby immediately
    "user_data":  "local",         # local fsync only; lost only if the host dies
    "telemetry":  "off",           # may lose ~600 ms of rows on an OS crash
}


@contextmanager
def transaction(conn, criticality: str):
    """Run a transaction at the durability level that matches its business value."""
    level = DURABILITY[criticality]
    with conn.transaction():
        with conn.cursor() as cur:
            # SET LOCAL is scoped to this transaction and reverts automatically.
            # Using plain SET here would silently leak the level onto every later
            # transaction on this POOLED connection -- a classic and very quiet bug
            # that turns "money" writes into "telemetry" writes after one reuse.
            cur.execute("SET LOCAL synchronous_commit = %s", (level,))
            yield cur


def place_order(conn, user_id: int, isbn: str, idempotency_key: str):
    with transaction(conn, "money") as cur:
        # The idempotency key is NOT optional. Synchronous replication guarantees that
        # an ACKNOWLEDGED write is not lost. It does NOT tell the client what happened
        # when the connection drops mid-commit: in PostgreSQL the transaction may well
        # be committed locally even though no ack was ever delivered. The client must
        # treat "no response" as UNKNOWN and retry safely (Lesson 13).
        cur.execute(
            """
            INSERT INTO orders (user_id, isbn, idempotency_key, status)
            VALUES (%s, %s, %s, 'placed')
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING id
            """,
            (user_id, isbn, idempotency_key),
        )
        row = cur.fetchone()
        return row[0] if row else None     # None => this key was already processed


def record_page_view(conn, user_id: int, path: str):
    # Deliberately the weakest level. ~20,000 of these per second; paying 1.3 ms of
    # replication latency each would consume connection-pool capacity for data that
    # nobody would miss. Quantified in Section 9, Part D.
    with transaction(conn, "telemetry") as cur:
        cur.execute(
            "INSERT INTO page_views (user_id, path, at) VALUES (%s, %s, now())",
            (user_id, path),
        )
```

## 9. Numerical example

Every assumption is stated. All figures are **typical/approximate** cloud values, not guarantees; substitute your own measurements before making decisions.

### Stated assumptions

```
A1  Application work per write transaction (parse, plan, execute, locks) = 1.00 ms
A2  Local WAL fsync on cloud NVMe / provisioned gp3                      = 0.30 ms
A3  Standby WAL fsync (same hardware class)                              = 0.30 ms
A4  Standby replay of a small commit record                              = 0.10 ms
A5  Round-trip times (typical):   same-AZ 0.25 ms
                                  cross-AZ 1.00 ms
                                  cross-region (Ireland->Virginia) 70.00 ms
A6  BookNook steady-state write rate                                     = 2,000 tx/s
A7  Average WAL generated per write transaction                          = 300 bytes
A8  Network and CPU are not saturated; queueing delay is excluded
A9  Each stage's cost is additive (true when nothing is saturated, Lesson 14)
```

### Part A — Write latency by configuration

Formula, built directly from the five stages in Section 4:

```
latency = A1 (work) + A2 (local fsync) + [ RTT + remote cost if synchronous ]

where remote cost =  0.00 ms   for remote_write  (no remote fsync)
                     0.30 ms   for on            (A3)
                     0.40 ms   for remote_apply  (A3 + A4)
```

```
(1) ASYNC (local)
    = 1.00 + 0.30 + 0                       = 1.30 ms      [baseline]

(2) SYNC same-AZ, remote_write
    = 1.00 + 0.30 + 0.25 + 0.00             = 1.55 ms      (+0.25 ms, +19%)

(3) SYNC same-AZ, on
    = 1.00 + 0.30 + 0.25 + 0.30             = 1.85 ms      (+0.55 ms, +42%)

(4) SYNC cross-AZ, remote_write
    = 1.00 + 0.30 + 1.00 + 0.00             = 2.30 ms      (+1.00 ms, +77%)

(5) SYNC cross-AZ, on
    = 1.00 + 0.30 + 1.00 + 0.30             = 2.60 ms      (+1.30 ms, +100%)

(6) SYNC cross-AZ, remote_apply
    = 1.00 + 0.30 + 1.00 + 0.40             = 2.70 ms      (+1.40 ms, +108%)

(7) SYNC cross-region, on
    = 1.00 + 0.30 + 70.00 + 0.30            = 71.60 ms     (+70.30 ms, +5,408%)
```

**Reading of the result.** Cross-AZ synchronous roughly doubles commit latency in *relative* terms but costs only 1.3 ms in *absolute* terms — usually irrelevant inside an HTTP request that already takes 80 ms. Cross-region synchronous makes a commit **55× more expensive** and is almost never acceptable for interactive writes.

### Part B — Throughput consequences (Little's Law, Lesson 14)

Little's Law: `L = λ × W`. Here `L` = concurrent write transactions in flight, `λ` = write rate, `W` = write latency. Rearranged: the connections needed to sustain 2,000 writes/second (A6) are `L = λ × W`.

```
(1) ASYNC, W = 1.30 ms   : L = 2,000 × 0.00130 =   2.6 concurrent write transactions
(5) cross-AZ on, W = 2.60: L = 2,000 × 0.00260 =   5.2
(7) cross-region, W=71.6 : L = 2,000 × 0.07160 = 143.2
```

```
Per-connection throughput = 1 / W
  async          : 1 / 0.00130 =   769 tx/s per connection
  cross-AZ on    : 1 / 0.00260 =   385 tx/s per connection
  cross-region   : 1 / 0.07160 =    14 tx/s per connection
```

**Interpretation.** Cross-region synchronous replication does not merely make each write slower; it forces you to hold **143 concurrent database connections** where 3 sufficed. PostgreSQL costs roughly 5–10 MB of memory per backend and degrades on context switching well before 1,000 connections, so you now also need a connection pooler sized for a workload that is doing no extra work whatsoever — it is purely waiting on the speed of light. This is the hidden second cost of synchronous replication and it is the one that actually breaks systems.

### Part C — RPO under asynchronous replication

RPO = the replication lag at the instant of failure, converted into lost transactions.

```
RPO_transactions = lag_seconds × write_rate
RPO_bytes        = lag_seconds × write_rate × WAL_per_tx       (A7)
```

**Scenario C1 — steady state, same-AZ async.** Measured `replay_lag` p50 = 2 ms.
```
RPO_transactions = 0.002 s × 2,000 tx/s = 4 transactions
RPO_bytes        = 4 × 300 B            = 1.2 KB
```
Four transactions. For most businesses, unnoticeable.

**Scenario C2 — steady state, cross-region async.** One-way propagation is 35 ms (half of A5's 70 ms RTT), plus ~10 ms of batching and replay. Measured p50 lag = 45 ms.
```
RPO_transactions = 0.045 s × 2,000 tx/s = 90 transactions
RPO_bytes        = 90 × 300 B           = 27 KB
```

**Scenario C3 — write burst, the case that actually hurts.** A marketing email triggers a 60-second burst. Assumptions: the leader sustains 5,000 writes/s during the burst; the standby's **single-threaded** replay (Lesson 34) manages 3,000 writes/s.
```
Backlog growth rate = 5,000 − 3,000           = 2,000 tx/s
Backlog after 60 s  = 2,000 × 60              = 120,000 transactions
Lag in time         = 120,000 / 3,000 (replay rate)
                                              = 40 seconds
RPO at the end of the burst                   = 40 seconds
RPO_transactions                              = 120,000 transactions
RPO_bytes           = 120,000 × 300 B         = 36 MB of unshipped WAL
Drain time after the burst (at 2,000 tx/s arriving, 3,000 replayed):
                      120,000 / (3,000 − 2,000) = 120 seconds
```

**The conclusion that matters.** The *average* RPO was 4 transactions (C1). The RPO **during the exact window when the system is under the most stress** — i.e. when failure is most likely — was 120,000 transactions, a factor of **30,000× worse**. Quoting an average RPO is therefore meaningless. You must quote a percentile of the lag distribution, and you must alert on it. This is precisely why Lesson 34 exists.

### Part D — The cost of applying synchronous replication indiscriminately

Assumptions: 22,000 writes/s total, of which 2,000 are business-critical (orders, payments, inventory) and 20,000 are telemetry (page views, search terms).

```
Configuration X -- cross-AZ 'on' for EVERYTHING (W = 2.60 ms):
    L = 22,000 × 0.00260 = 57.2 concurrent write transactions
    Added latency vs async = 1.30 ms on all 22,000 writes/s
    Extra connection-seconds per second = 22,000 × 0.00130 = 28.6

Configuration Y -- 'on' for the 2,000 critical, 'off' for the 20,000 telemetry:
    critical : L = 2,000  × 0.00260 =  5.2
    telemetry: W = 1.00 ms (A1 only; 'off' skips even the local fsync)
               L = 20,000 × 0.00100 = 20.0
    Total L = 25.2 concurrent write transactions

Reduction in concurrent write slots: 57.2 → 25.2  = 56% fewer
RPO for critical writes: 0 in BOTH configurations -- the guarantee is unchanged.
```

Configuration Y achieves **identical durability for everything that matters** at 44 % of the concurrency cost. Per-transaction `synchronous_commit` is the highest-leverage setting in this lesson, and most teams never touch it.

### Part E — Tail latency: wait-for-all versus quorum (Lesson 16)

Assumptions: each standby acks in 1.0 ms at p50 but 25 ms at p99 due to checkpoints, WAL recycling and noisy neighbours; standby stalls are independent.

```
'FIRST 2 (b, c)' -- BOTH required. Commit is slow if EITHER is slow:
    P(slow) = 1 − (1 − 0.01)²  = 1 − 0.9801 = 0.0199 = 1.99 %
    Commit p99 ≈ 25 ms, and p98 is also degraded.

'ANY 1 (b, c)'   -- EITHER suffices. Commit is slow only if BOTH are slow:
    P(slow) = 0.01 × 0.01      = 0.0001      = 0.01 %
    Commit p99 ≈ 1 ms. The 25 ms tail is pushed out past p99.99.

Improvement in the probability of a slow commit = 0.0199 / 0.0001 = 199×
```

Same hardware, same durability requirement (the write still exists on a second host), same code. The entire difference is `ANY` versus `FIRST`/all. This is the quorum idea in miniature, and the general form is **Phase 4, Topic 17 — Quorums (R + W > N)**.

## 10. Trade-offs

**Asynchronous replication**
- *Advantages:* lowest and most stable write latency; write availability is completely independent of replica health; adding replicas is free in latency terms; works at any distance, so cross-region standbys are practical.
- *Disadvantages:* non-zero, variable, usually unmonitored RPO; failover is lossy; the loss is worst exactly when load is highest.
- *Operational cost:* low to run, but you **must** monitor and alert on lag percentiles, or you do not know your RPO.
- *Unnecessary to replace when:* writes are reconstructible from an upstream durable log, or the business genuinely tolerates seconds of loss in a rare disaster.

**Synchronous replication (`remote_write`)**
- *Advantages:* RPO = 0 against leader host loss; one network RTT, no remote disk wait; typically only +0.25–1.0 ms within a region.
- *Disadvantages:* does not survive a simultaneous leader-host loss and standby-OS crash; still couples write availability to standby availability.
- *Operational cost:* requires quorum configuration plus an automated fallback, or you have built a write outage.

**Synchronous replication (`on`)**
- *Advantages:* data is on two independent durable devices before ack; the standard "RPO = 0" claim.
- *Disadvantages:* adds a remote `fsync` (~0.3 ms) to every commit; still gives **no visibility guarantee** — a read on that standby may not see the write.
- *Unnecessary complexity when:* the standby is in the same AZ and rack, where a correlated power failure defeats the point anyway.

**Synchronous replication (`remote_apply`)**
- *Advantages:* the only level giving read-your-writes on the synchronous standby; removes an entire class of Lesson 34 bugs.
- *Disadvantages:* highest latency; replay conflicts on the standby directly stall primary writes, so one long analytical query can halt all writes; scales badly with replica count.
- *Operational cost:* needs `max_standby_streaming_delay` tuned to near-zero and analytics kept off that standby.

**Quorum (`ANY k of n`)**
- *Advantages:* removes the slowest replica from the critical path; tolerates failures without losing RPO 0; dramatically better tail.
- *Disadvantages:* needs n ≥ 3 nodes, hence more cost; reasoning about which node is safest to promote becomes subtler.
- *Unnecessary complexity when:* you only have two nodes — then `ANY 1 (of 1)` is just synchronous with a single point of failure wearing a disguise.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| Leader host lost, async replication, 2.4 s lag | Orders vanish after failover; cards charged with no order; 4-minute outage then silent data loss | Payment-provider reconciliation; customer tickets; `pg_wal_lsn_diff` history before the crash | Promote the replica (fast), then manually reconcile the lost window from payment-provider records and idempotency keys. Prevention: synchronous quorum, or a durable queue in front of the write path |
| Sole synchronous standby hangs (`synchronous_standby_names = 'replica_a'`) | **Every write hangs indefinitely.** Reads work, so naive uptime checks stay green while checkout is 100 % broken | Active connections climb while commit rate falls to zero; `pg_stat_activity` shows `wait_event = SyncRep`; write p99 goes vertical | Set `synchronous_standby_names = ''` and reload (`SELECT pg_reload_conf()`) — writes resume instantly. Prevention: `ANY k` quorum plus Patroni with `synchronous_mode_strict: false` |
| `synchronous_standby_names` empty, team believes it is set | No symptom at all — until a failover loses data, possibly years later | `SHOW synchronous_standby_names;` returns empty, or `pg_stat_replication.sync_state = 'async'` for every row | Set it correctly; add a continuous assertion that at least one row has `sync_state = 'sync'`. This is a *silent* failure and deserves a dedicated alert |
| `application_name` on the standby does not match the quorum list | Same silent asynchrony; the standby streams happily but never counts | `sync_state` is `async` despite the name appearing in the config | Fix `primary_conninfo`'s `application_name`; restart the standby |
| `remote_apply` plus a long analytical query on the standby | Checkout latency spikes to seconds, apparently at random, with no corresponding load on the primary | Primary backends in `SyncRep` wait; standby `replay_lag` spikes aligned with BI query start times | Move analytics to a dedicated async replica; lower `max_standby_streaming_delay`; or drop to `on` |
| Cross-region synchronous enabled "for safety" | Every write takes ~72 ms; connection pool exhausts under normal load; cascading timeouts across unrelated endpoints | Write p50 ≈ RTT to the remote region; pool-exhaustion errors; `SyncRep` waits dominate | Move the synchronous standby in-region; keep the cross-region standby asynchronous with a documented RPO |
| Replication slot retained for a dead standby | Site goes fully down when the primary's disk fills; writes fail with "no space left on device" | Disk-free trending down linearly; `pg_replication_slots.active = false` with growing `restart_lsn` distance | Drop the orphaned slot; set `max_slot_wal_keep_size`; alert on slot retention bytes |
| `synchronous_commit = off` chosen for "performance" on order writes | After an OS crash, up to ~600 ms of confirmed orders are missing even though no replica was involved | Crash-recovery log shows the last durable LSN behind the last acknowledged order | Restore the level to at least `local` for critical writes; reconcile the gap. Prevention: per-transaction mapping as in Section 8 |
| Network partition between AZs with `ANY 1 (of 2)` | Writes continue normally (the surviving replica satisfies the quorum); no user impact | `pg_stat_replication` shows one standby gone; lag alert on the partitioned one | Partition heals; the standby catches up from its slot. **This is the quorum working as designed** |
| Failover promotes a standby that was behind another standby | Some writes present on the surviving old standby are absent on the new leader; the old standby cannot re-attach cleanly | LSN comparison across nodes at promotion time; `pg_rewind` required | Always promote the standby with the highest `flush_lsn`; let the cluster manager choose, not a human under pressure |

## 12. Alternatives

| Approach | Durability guarantee | Write latency cost | Main drawback |
|---|---|---|---|
| **Async replication + PITR backups** | RPO = measured lag (ms–s); PITR covers human error | None | Lossy failover; requires lag monitoring to even *know* your RPO |
| **Sync to one named standby** | RPO = 0 vs leader-host loss | +RTT (+ remote fsync for `on`) | Single point of **write** failure; PostgreSQL will block forever |
| **Semi-synchronous (MySQL-style)** | RPO = 0 except when degraded | +RTT normally, 0 when timed out | Weakest exactly when failure is most likely; PostgreSQL lacks it natively |
| **Quorum `ANY k of n`** | RPO = 0 while k nodes survive | +(k-th fastest RTT) | Needs ≥ 3 nodes; more cost; promotion logic is subtler |
| **Consensus replication (Raft/Paxos)** — etcd, CockroachDB, Spanner, YugabyteDB | RPO = 0 **and** agreed leadership, so no split brain | +quorum RTT, typically 1–2 ms in-region | Much more complex; cross-region deployments pay quorum RTT on every write. **Phase 5, Topic 6 — Consensus algorithms** |
| **Storage-level replication** — Aurora, AlloyDB, EBS Multi-Attach | RPO = 0, handled below the database | Low, because the storage layer uses a 4-of-6 style quorum | Vendor lock-in; opaque failure modes; you cannot tune the trade-off |
| **Synchronous disk mirroring (DRBD)** | RPO = 0 at the block layer | +RTT per *write*, not per *commit* — often worse | Block-level, so it replicates corruption too; largely superseded |
| **Durable log in front of the DB** (Kafka/Kinesis + consumer) | RPO = 0 for the *log*; the DB becomes replayable | None on the DB write; the log write has its own cost | Big architectural change; read-after-write becomes harder. **Phase 4, Topic 5 — Message queues**; **Phase 4, Topic 12 — Event sourcing** |
| **Two-phase commit (2PC)** | Atomicity across systems, not durability per se | Very high; blocks on coordinator failure | Solves a different problem; a known availability hazard. **Phase 5, Topic 3 — Distributed transactions** |
| **Multi-leader / active-active** | Writes survive a region loss | Low locally | Conflicts become your problem. **Phase 4, Topic 15 — Multi-leader and leaderless replication** |

The row worth dwelling on is the last-but-two. If your writes arrive via a durable, replicated log that the database merely *consumes*, then a lost database write is not a lost business event — it can be replayed from the log. Many organisations reach RPO ≈ 0 for the things that matter without ever enabling synchronous database replication, simply by making the log the system of record. Whether that is simpler than `synchronous_standby_names = 'ANY 1 (...)'` depends entirely on whether you already have the log.

## 13. When to use it

**Use synchronous replication when:**

1. **The write represents money or a legal obligation.** Payments, ledger entries, refunds, regulatory audit records. If losing a single row requires reconciling with an external party, the 1–2 ms of in-region synchronous commit is trivially worth it.
2. **The acknowledgement is externally visible and irreversible.** A confirmation email, an SMS, a card capture, a physical dispatch instruction. Once an outside party has been told the write happened, you cannot un-tell them, so the write must not evaporate.
3. **The standby is in a different AZ in the same region.** This is the sweet spot: +0.25–1.3 ms buys genuine independence from a single host, rack, and power domain. Almost every system with revenue-bearing writes should do at least this.
4. **Your contract or regulator specifies RPO = 0.** Then there is no decision to make; configure a quorum and budget the latency.
5. **Write volume is low relative to the latency budget.** 400 writes/s inside an 800 ms card-network round trip (Section 7's payment processor) makes 2.6 ms irrelevant.
6. **Specifically `remote_apply`, when a known read must see a just-made write** and you cannot implement LSN tokens in the application. This is the brute-force fix for Lesson 34's first anomaly — correct, expensive, and worth knowing.

**Use asynchronous replication when:**

1. **The replica is in another region.** 70 ms on every write is almost never acceptable; cross-region standbys should be asynchronous with a documented, monitored RPO.
2. **The replica exists for read scaling, not durability** (Lesson 32). Read replicas should never be in the synchronous set — you would be paying durability latency for a performance feature.
3. **The data is reconstructible** — telemetry, caches, derived aggregates, search indexes, session scratch data.
4. **Write availability dominates write durability** for that path: cart updates, draft content, "last seen" timestamps.

## 14. When *not* to use it

**Do not use synchronous replication when:**

1. **You have exactly one standby and no automated fallback.** You have not improved durability so much as built a second way to take the site down, and PostgreSQL's lack of a synchronous timeout means the outage lasts until a human edits a config file. Either run `ANY 1 (of 2)` or more, with a cluster manager that can degrade to async, or stay asynchronous deliberately.
2. **Across regions for interactive writes.** Section 9 Part A/B: 55× latency and 143 connections instead of 3. If you need cross-region RPO 0, the answer is a consensus-based system designed for it (CockroachDB, Spanner, YugabyteDB) with the data placed so that quorums are local — not PostgreSQL synchronous replication stretched across an ocean.
3. **For telemetry, logs, metrics, or cache-like tables.** Section 9 Part D: this was 56 % of the concurrency cost for zero business benefit.
4. **As a substitute for backups.** Synchronous replication replicates `DELETE FROM orders` perfectly. Only PITR and backups protect you from yourself. If you can only afford one, take the backups.
5. **With `remote_apply` on a replica that also serves analytics.** You have coupled your primary's write latency to the slowest BI query anyone writes.

**Do not use asynchronous replication when:**

1. **The business cannot tolerate the burst-time RPO** computed in Section 9 Part C — remember that the honest number is the p99 of the lag distribution, not the median.
2. **You have no lag monitoring.** Asynchronous replication with unmonitored lag means your RPO is an unknown number that you will discover during an incident. If you cannot plot the lag percentiles today, you do not know what you are risking.

## 15. Common mistakes

**Beginner mistakes**
- Assuming `synchronous_commit = on` (the default) means replication is synchronous. It does not, unless `synchronous_standby_names` is also set. The out-of-the-box configuration is asynchronous.
- Believing replication is a backup. It is not: it faithfully replicates destructive mistakes at full speed.
- Confusing RPO (data lost) with RTO (time down), or quoting one when asked for the other.
- Thinking "synchronous" means the replica is immediately readable. Only `remote_apply` provides that; `on` guarantees the bytes are on disk, not that they are visible.
- Setting a single named synchronous standby and calling it high availability, thereby creating a hard write dependency on one machine.
- Quoting average replication lag as the RPO, when the number that matters is the lag percentile during peak write load.
- Enabling synchronous replication globally without measuring which writes actually need it.

**Experienced-engineer mistakes**
- Using `SET synchronous_commit` instead of `SET LOCAL` on a pooled connection, leaking a weak durability level onto subsequent transactions that needed a strong one. This is silent and can persist for the lifetime of the connection.
- Forgetting that `application_name` in `primary_conninfo` must match `synchronous_standby_names` exactly — otherwise the standby streams normally and counts for nothing.
- Choosing `FIRST k` when `ANY k` was wanted, reintroducing the slowest node into the critical path and wondering why p99 commit latency tracks one replica's checkpoint schedule (Section 9 Part E).
- Enabling `remote_apply` and then letting the BI team point dashboards at the synchronous standby, so analytical queries throttle production writes through replay conflicts.
- Creating replication slots without `max_slot_wal_keep_size`, so a dead standby fills the primary's disk and takes down the cluster that replication was meant to protect.
- Treating synchronous replication as sufficient reason to drop idempotency keys. A commit can be durable locally yet unacknowledged; the client still cannot distinguish "failed" from "unknown".
- Setting `synchronous_mode_strict: true` in Patroni without understanding that it will deliberately stop accepting writes when no synchronous standby is available — correct for a ledger, catastrophic for a shop.
- Running a two-node cluster and believing a quorum exists. `ANY 1 (of 1)` is a single point of failure with extra syntax.
- Measuring replication lag only on the primary's `sent_lsn`, which shows the WAL leaving the building and says nothing about whether it was flushed or applied.

## 16. Interview perspective

This topic is a reliable senior/staff-level discriminator, because the surface answer ("sync is safer, async is faster") is one sentence and the real answer is a quantified trade-off.

**How to use it:**

1. **Lead with the acknowledgement point, not with vocabulary.** *"The only difference is when the leader says 'committed' — before shipping the WAL, or after a replica confirms it has it."* That framing immediately shows you understand the mechanism rather than the label.
2. **Define RPO numerically and unprompted.** *"With async cross-AZ at 2 ms lag and 2,000 writes/second, my RPO is about 4 transactions in steady state — but during a write burst where the replica's single-threaded replay falls behind, it was 40 seconds, or 120,000 transactions. So I alert on lag p99, not on average lag."* Giving both numbers is what separates a strong answer from a textbook one.
3. **Volunteer the availability inversion.** *"A single synchronous standby makes the primary's write availability depend on the standby. PostgreSQL has no timeout on that wait, so a hung standby blocks writes indefinitely. I'd use `ANY 1 (of 2)` and a cluster manager that can degrade to async with an alert."* Very few candidates say this, and it reads as operational experience.
4. **Use the tail-latency argument.** *"Waiting for all replicas means my commit latency is the maximum over replicas, so my p99 becomes each replica's p99 — the same amplification we discussed for fan-out in Lesson 16. A quorum turns max into k-th fastest, which drops the probability of a slow commit from ~2 % to ~0.01 %."*
5. **Reach for per-transaction durability.** *"I wouldn't make this a global setting. Payments get `remote_write`; page views get `off`. That cut our concurrent write slots by more than half with no change to the guarantee that matters."*
6. **Distinguish durability from visibility.** *"Note that `on` gets the bytes onto the replica's disk but doesn't make them readable there — that's `remote_apply`. Which is exactly why read-after-write anomalies still happen on a 'synchronous' cluster."* This is the bridge into Lesson 34 and interviewers notice it.
7. **Say what replication does not solve.** *"Neither mode protects against a bad migration or an accidental `DELETE` — those replicate perfectly. That's PITR's job."*
8. **Know the escape hatch.** *"If the requirement is genuinely cross-region RPO 0 at interactive latency, PostgreSQL synchronous replication is the wrong tool; I'd look at a consensus-replicated database with locality-aware quorums."*

**Red flags interviewers watch for:**
- Saying "I'll use synchronous replication for safety" with no mention of the latency cost or the write-availability cost.
- Being unable to state an RPO number, or using RPO and RTO interchangeably.
- Not knowing that the default PostgreSQL setup is asynchronous.
- Proposing cross-region synchronous replication for a consumer web application.
- Claiming replication removes the need for backups.
- Believing a synchronous replica is guaranteed to be readable immediately.
- Treating "add a replica" as automatically improving durability, when an async read replica improves read capacity and barely improves RPO.
- No awareness that the wait is a **maximum** over the required set, and therefore a tail-latency problem.
- Discussing only the happy path, with no answer for "what happens when the synchronous standby stops responding?"

## 17. Knowledge check

Write your answers down before continuing. Do not look anything up first — the point is to find out which parts are solid.

**Conceptual**

1. A team sets `synchronous_commit = on` on their PostgreSQL primary, confirms it with `SHOW synchronous_commit`, and reports to management that the cluster now has RPO = 0. Eight months later a failover loses 1,900 transactions. Explain, mechanically and precisely, what was wrong with their configuration, what the one query is that would have revealed it immediately, and why no error or warning was ever produced.

2. Explain the exact difference in guarantee between `remote_write`, `on`, and `remote_apply`, in terms of the five stages in Section 4. Then describe a specific, realistic failure in which `remote_write` loses data but `on` does not, and a specific, realistic *application-visible bug* that `on` permits but `remote_apply` prevents. State which of the two problems you would normally prioritise fixing, and why.

3. BookNook has three standbys. Configuration A is `FIRST 3 (b, c, d)`; configuration B is `ANY 1 (b, c, d)`. Assume each standby independently has a 1 % chance of being in a slow path (25 ms instead of 1 ms) at any moment. Explain which configuration has the better commit p99 and why, compute the probability of a slow commit under each, and then explain what durability guarantee configuration B gives up relative to A — being precise about what "gives up" means here.

**Practical scenario**

4. BookNook's CTO reads about a competitor losing orders and mandates "zero data loss." An engineer configures `synchronous_standby_names = 'replica_dr'`, where `replica_dr` is an asynchronous disaster-recovery standby in `us-east-1` (70 ms RTT from `eu-west-1`), and sets `synchronous_commit = remote_apply`. The change is deployed on a Tuesday afternoon at low traffic and appears to work. At 18:00, peak traffic arrives: 2,000 writes/second.

   Describe, in order, what happens over the following ten minutes — covering commit latency, connection-pool behaviour, which user-facing endpoints break and in what sequence, and what the monitoring dashboards will and will not show. Then state: (a) the single configuration change that would restore service in under a minute, (b) the correct target architecture that satisfies the CTO's actual requirement, and (c) one thing the CTO's requirement does **not** cover that caused a worse incident in Section 7.

**Calculation**

5. BookNook is considering three topologies for its order-writing path. Use these assumptions: application work per transaction 1.2 ms; local WAL fsync 0.4 ms; standby fsync 0.4 ms; standby replay 0.1 ms; same-AZ RTT 0.3 ms; cross-AZ RTT 1.2 ms; cross-region RTT 80 ms; order write rate 1,500 transactions/second; average WAL per transaction 420 bytes; measured asynchronous replay lag p50 = 6 ms and p99 = 1.8 s.

   (a) Compute the commit latency for: async (`local`); cross-AZ `remote_write`; cross-AZ `on`; cross-AZ `remote_apply`; cross-region `on`. Show each term.
   (b) Using Little's Law, compute the number of concurrent write transactions required to sustain 1,500 writes/second in each of the five cases, and the per-connection throughput.
   (c) For the asynchronous option, compute the RPO in transactions and in bytes at both p50 and p99 lag. State in one sentence which of those two numbers you would put in a design document and why.
   (d) A burst drives the leader to 4,200 writes/second for 90 seconds while the standby replays at only 2,600/second. Compute the backlog in transactions, the resulting lag in seconds at the end of the burst, the RPO in bytes, and how long the standby takes to catch up once the burst ends and the rate returns to 1,500/second.
   (e) Given your numbers, recommend a configuration for BookNook's order path and justify it in no more than four sentences, naming the specific guarantee you are buying and the specific cost you are accepting.

Show every step and state any additional assumptions you make.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 34.

---

**Progress checklist**

Phase 1 — Foundations: **complete** (Lessons 1–17)

Phase 2 — Scaling:
`[x] 1. Vertical scaling` · `[x] 2. Horizontal scaling` · `[x] 3. Stateless application servers` · `[x] 4. Load balancers` · `[x] 5. Layer 4 versus Layer 7` · `[x] 6. Load-balancing strategies` · `[x] 7. Health checks and failover` · `[x] 8. Forward and reverse proxies` · `[x] 9. Caching fundamentals` · `[x] 10. Cache placement and patterns` · `[x] 11. Cache invalidation and eviction` · `[x] 12. Distributed caching` · `[x] 13. CDNs and edge caching` · `[x] 14. Database replication fundamentals` · `[x] 15. Leader–follower replication and read replicas` · `[x] 16. Synchronous versus asynchronous replication`
`[ ] 17. Replication lag and read-after-write consistency` ← next
`[ ] 18–22` not started

Phases 3–5: not started.

**NEXT LESSON: Phase 2, Topic 17 — Replication lag and read-after-write consistency**

---

# Lesson 34 — Replication Lag and Read-After-Write Consistency

*Phase 2, Topic 17*

## 1. Simple definition

**Replication lag** is how far behind the leader a follower is, right now.

Lesson 33 established that an asynchronous follower acknowledges nothing before the client is told "committed", so a follower is always at least slightly behind. Lag is the size of that gap. It can be expressed two ways, and the difference matters enormously:

- **In bytes**: how much WAL the leader has produced that this follower has not yet replayed. This is a *backlog* measurement.
- **In seconds**: how long ago the leader committed the newest transaction the follower has replayed. This is a *staleness* measurement.

**Read-after-write consistency** (also called **read-your-own-writes**) is a guarantee about what one specific user sees:

> *If a user performs a write and is told it succeeded, every subsequent read **by that same user** must reflect that write.*

Note three things in that sentence, all deliberate:

- It says nothing about what *other* users see. They may legitimately see stale data. That is a much weaker and much cheaper promise.
- It is a **session** guarantee, not a global one. It is about one user's causal chain, not about the database's global state.
- It is the single weakest consistency property that users actually notice. Users do not notice that someone else's review took 200 ms to appear. They absolutely notice that *their own* review vanished.

Two more terms, defined now because Section 4 depends on them:

- **LSN (Log Sequence Number)**: PostgreSQL's monotonically increasing position in the WAL stream, written as `0/16B3748`. Every commit has one. "Follower X has replayed up to LSN *L*" is an exact, comparable statement about how current that follower is. LSNs are the mechanism behind the strongest fix in this lesson. MySQL's equivalent is the GTID set.
- **Stale read**: a read that returns a value older than one the same client has already observed or caused. Note that "old" is not the problem; "older than what this client has already seen" is.

## 2. Why it exists

Lesson 32 gave BookNook read replicas and a load balancer in front of them, and the system got faster. Then the bug reports started, and they all had the same shape:

> *"I updated my delivery address, the page said 'Saved', and when I went back to the account page it showed my old address. I changed it three times. Then an hour later both changes were there."*

Nothing is broken. Every component is working exactly as designed:

1. The `PATCH /account/address` went to the **leader**, committed, and returned `200 OK`.
2. The browser redirected to `GET /account`, which took ~90 ms to arrive.
3. The load balancer routed that read to **replica_c** (Lesson 32: reads go to replicas).
4. `replica_c` had not yet replayed the change — it was 140 ms behind at that moment.
5. The user saw their old address.

The user's mental model is *"I saved it, so it is saved."* The system's actual model is *"it is saved on the leader, and will be on this replica shortly."* The mismatch between those two models is the entire subject of this lesson.

This is not an exotic edge case. It is the default behaviour of every read-replica deployment on earth, and it will happen to a percentage of your users that we will compute exactly in Section 9. For BookNook's numbers that percentage is about **5.6 %** of post-write reads — thousands of confused users per day.

It exists as a topic because the fixes are all *trade-offs between consistency, leader load, and latency*, and choosing among them requires knowing your lag distribution. There is no setting called "make it correct" that is free.

## 3. Real-world analogy

**A bank branch network before real-time systems.**

You deposit a cheque at your local branch. The clerk stamps it and tells you it is done. Overnight, the day's transactions are couriered to the other branches, which update their ledgers.

- **Replication lag** = the time between your deposit and the other branches knowing about it.
- **Read-your-own-writes violation**: you walk into a different branch twenty minutes later, ask for your balance, and it is unchanged. You deposited it. You have a stamped receipt. The branch says you did not.
- **Monotonic reads violation**: you check branch B (which has today's update) and see £500. Then you check branch C (which does not) and see £300. Your money appears to have gone backwards in time. This is strictly more alarming to a human than merely seeing stale data, because it breaks the assumption that time moves forward.
- **Consistent prefix violation**: you transfer money *from* savings *to* current. A branch that received the two updates through different couriers shows the credit to current before the debit from savings — so for a moment you appear to have the money twice. Effect visible before cause.
- **Read from the leader for N seconds after a write** = "for the rest of today, please use the branch where you made the deposit."
- **Sticky routing** = "you are assigned to branch B; always use branch B."
- **LSN token** = the clerk gives you a receipt with a sequence number, and any branch will check *"have I processed up to number 84,331 yet?"* before answering you. If not, it waits, or sends you to head office.
- **Waiting for catch-up** = the teller says "hold on, let me process today's courier bag first."

**Where the analogy breaks down:**

| Bank branches | Database replicas |
|---|---|
| You know which branch you are standing in | The user has no idea which replica served them, and it may differ on every single request |
| Lag is a predictable overnight batch | Lag is a *distribution*: 20 ms most of the time, 3 seconds occasionally, 40 seconds during a bulk import |
| A human teller can explain the delay | The user just sees wrong data, with no signal that anything is in flight |
| Branches are few and named | Replicas are autoscaled and ephemeral; sticky routing to a replica that disappears must fail over gracefully |
| Reading the ledger at a branch costs the branch nothing extra | Routing reads back to the leader loads the one node that must not be overloaded — the fix has a direct capacity cost |
| Balance is a single number | A page is dozens of queries, which may hit *different* replicas with *different* lag, producing a page that is internally inconsistent in a way no single replica ever was |
| Your deposit either happened or did not | A write can be durable on three replicas and still invisible on the one you read from, because durability ≠ visibility (Lesson 33) |

That last row connects the two lessons directly. Even a **fully synchronous** cluster at `synchronous_commit = on` exhibits every anomaly in this lesson, because `on` guarantees the WAL is flushed on the standby, not replayed. Only `remote_apply` closes the gap, and only for the standbys in the synchronous set.

## 4. Detailed technical explanation

### What actually causes lag

Replication lag is not a single phenomenon. It has at least six distinct causes with different signatures and different fixes. Diagnosing lag means identifying which one you have.

**1. Write bursts — leader throughput exceeds replica apply throughput.**
The leader commits with N concurrent backends in parallel. The replica replays with (classically) **one** process, serially. If the leader sustains 5,000 writes/s and the replica replays 3,000/s, the backlog grows at 2,000/s for as long as the burst lasts, and then drains at the *difference* between the replay rate and the new arrival rate — which is why a 60-second burst can take several minutes to clear (Lesson 33, Section 9 Part C).
*Signature*: lag rises linearly during the burst, falls linearly afterwards, with a slower fall than rise.

**2. Long-running transactions on the leader.**
A single `UPDATE books SET price = price * 1.05` touching 4 million rows generates one enormous WAL stream, and in PostgreSQL the replica cannot start making it visible until the commit record arrives. A 90-second bulk update is at least a 90-second visibility gap, plus replay time.
*Signature*: a step change in lag, aligned with a batch job or migration.

**3. Single-threaded apply — the structural cause.**
This is the most important one and the least known. PostgreSQL's startup (recovery) process replays WAL serially on a hot standby. The leader generated that WAL using, say, 60 concurrent backends across many CPUs. So **the replica must do with one core what the leader did with sixty** — it is not merely "a bit slower", it is architecturally incapable of matching a highly parallel leader at peak.
In practice the replica survives because replay is cheaper per unit of work than the original execution (no parsing, no planning, no constraint evaluation — it is physical page changes). But the headroom is finite and it disappears exactly at peak. MySQL addressed this with multi-threaded appliers (`replica_parallel_workers` with `LOGICAL_CLOCK` or `WRITESET`); PostgreSQL physical replication remains single-threaded for replay.
*Signature*: the replica's CPU shows one core pinned at 100 % while the rest idle. This is the definitive fingerprint and it is invisible to any dashboard showing *average* CPU.

**4. Network saturation on the replication link.**
WAL volume can be far larger than the logical change size, especially with `full_page_writes` immediately after a checkpoint (a single-byte update can emit an 8 KB page image). Cross-region links, or a shared NIC also serving read traffic, can become the bottleneck.
*Signature*: `sent_lsn` on the primary lags `pg_current_wal_lsn()` — the WAL has not even left the leader. In every other cause, `sent_lsn` is current and `replay_lsn` is behind.

**5. Vacuum and maintenance WAL.**
`VACUUM`, `REINDEX`, and index builds generate large volumes of WAL that is pure overhead from the application's point of view but must still be shipped and replayed. Autovacuum on a large table after a mass delete is a classic cause of an unexplained midnight lag spike.
*Signature*: lag spikes correlate with `pg_stat_progress_vacuum` activity, not with application write volume.

**6. Lock and replication conflicts on the replica.**
This one is counter-intuitive: **reading from a replica can make that replica lag.** If the WAL contains a change that would invalidate a snapshot held by a running query on the standby (for example, a vacuum removing a row version the query still needs), replay must either cancel the query or pause. `max_standby_streaming_delay` controls how long replay will pause. Default is 30 s, so a single long analytics query can stall replay for 30 seconds, producing 30 seconds of lag with zero write-rate change.
`hot_standby_feedback = on` avoids the cancellations by telling the primary not to vacuum rows the standby still needs — at the cost of **table bloat on the primary**. Both options are bad; you choose which badness you prefer.
*Signature*: lag spikes aligned with BI query start times; `pg_stat_database_conflicts` incrementing.

### How to measure lag, and why "seconds behind" lies

There are two honest measurements and one misleading one.

**Byte lag (a backlog measure) — always correct.**

```sql
SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) AS replay_lag_bytes
FROM pg_stat_replication;
```

This is the exact number of WAL bytes the replica has yet to replay. It is zero when the replica is caught up, regardless of how idle the system is. It is the correct signal for alerting on *backlog*.

Its weakness: bytes are not directly meaningful to the business. "We are 400 MB behind" does not translate to an RPO without knowing the write rate, and 400 MB of index-build WAL replays far faster than 400 MB of random row updates.

**Time lag computed on the replica — misleading when idle.**

```sql
-- Run on the STANDBY
SELECT now() - pg_last_xact_replay_timestamp() AS seconds_behind;
```

This computes: *"how long ago was the newest transaction I have replayed originally committed?"*

Now consider an idle leader. At 03:00 the last write happened. At 03:05 nothing has been written at all. The replica is **perfectly caught up** — byte lag is exactly 0. But `pg_last_xact_replay_timestamp()` still returns 03:00, so this query reports **300 seconds behind**, and keeps climbing by one second per second.

This is the lie in the lesson title. "Seconds behind" on an idle system measures *the age of the last write*, not the staleness of the replica. Teams alert on it, get paged at 04:00 for a perfectly healthy system, and then — having learned that the alert is noisy — raise the threshold to 600 seconds and miss a real ten-minute lag event during the next day's peak. The noisy alert does not merely waste time; it actively destroys the signal you needed.

The standard mitigation is to report time lag as zero when there is no backlog:

```sql
SELECT CASE
         WHEN pg_last_wal_receive_lsn() = pg_last_wal_replay_lsn() THEN 0
         ELSE EXTRACT(EPOCH FROM now() - pg_last_xact_replay_timestamp())
       END AS seconds_behind;
```

This is better, but note its own flaw: it compares *received* to *replayed*, so it reports 0 when the replica has replayed everything it has received — even if the leader is unreachable and has produced 10 GB more WAL that never arrived. A disconnected replica reports "0 seconds behind" forever. The only way to catch that is to also monitor `pg_stat_replication` **on the primary**, or the replica's `status`/last-receive time.

**Round-trip lag measured on the primary — the best time-based signal (PostgreSQL 10+).**

```sql
SELECT application_name, write_lag, flush_lag, replay_lag FROM pg_stat_replication;
```

These are `interval` values computed from actual acknowledgement round trips for specific WAL positions: how long it took for a locally flushed record to be written / flushed / replayed on that standby. On an idle system they report `NULL` or the last measured sample rather than climbing without bound. They are the right thing to graph as percentiles.

**The rule to internalise:** alert on **byte lag** for backlog, graph **`replay_lag` percentiles** for user-visible staleness, and treat raw `now() - pg_last_xact_replay_timestamp()` as a diagnostic to be read by a human who knows about the idle-system artefact — never as an alert condition on its own.

One further subtlety: the number you care about for read-after-write is **replay** lag specifically, not write or flush lag. Lesson 33's `synchronous_commit = on` drives `flush_lag` to near zero while leaving `replay_lag` untouched. A cluster can be perfectly synchronous and still fail read-after-write.

### The three anomalies users actually notice

Not all staleness is equally visible. These three are, in increasing order of strangeness.

#### Anomaly 1 — Read-your-own-writes violation

*A user does not see their own just-acknowledged write.*

**BookNook scenario.** Priya edits her delivery address and clicks Save. `PATCH /account/address` goes to the leader, commits at LSN `0/4A21C90`, returns `200`. The browser follows a redirect to `GET /account`, arriving 90 ms later. The LB sends that read to `replica_c`, which is 140 ms behind. Priya sees her old address.

She edits it again. Same thing. She now believes the site is broken and contacts support — who look at the leader, see the correct address, and report "works for me."

This is the anomaly that generates support tickets, because the user has *direct evidence* (a success message) that contradicts what they see.

#### Anomaly 2 — Monotonic reads violation

*A user sees newer data, then older data. Time appears to run backwards.*

**BookNook scenario.** Tom places an order. He opens `GET /orders`; the LB routes it to `replica_a`, which is caught up. He sees order #90210. He hits refresh; this time the LB routes to `replica_b`, which is 900 ms behind because it is mid-checkpoint. **The order disappears.** He refreshes again, lands back on `replica_a`, and it reappears.

Tom's reasonable conclusion is that his order was cancelled and then un-cancelled. He phones support. Note that no individual response was *wrong* — each replica returned a state the database genuinely passed through. The violation is created purely by the *sequence* of replicas he happened to be routed to.

**Monotonic reads** is the guarantee that this cannot happen: if a user has read a value, subsequent reads by that user never return an earlier state. It is a weaker guarantee than read-your-own-writes in one sense (it says nothing about writes) and orthogonal in another (you can have either without the other). Crucially, it is violated by *routing*, not by lag alone — a single replica, on its own, always moves forward.

#### Anomaly 3 — Consistent prefix violation

*A user sees an effect before its cause.*

**Consistent prefix reads** is the guarantee that if writes happen in a causal order, anyone reading them sees them in that order — a reader may see a *prefix* of history, but never a reordered history.

**BookNook scenario.** On a book page, Ana posts a review: *"The translation is dreadful."* Ben replies: *"Agreed, the 2019 edition is better."* Reviews live in the `reviews` table on the main cluster; replies were moved to a separate `review_replies` service with its **own** database and its own replication stream after the reviews table got too hot. A third user loads the page, which fans out to both services. The replies service's replica is caught up; the reviews service's replica is 600 ms behind. The page renders:

```
(no reviews yet)
  └─ Ben: "Agreed, the 2019 edition is better."
```

A reply to nothing. Ben appears to be agreeing with himself.

**The critical nuance, because this is where most explanations are wrong:** with a *single* leader streaming a *single* WAL to a replica, consistent prefix is automatically guaranteed. WAL is replayed strictly in order, so a replica is always some exact earlier point in the leader's history — never a mixture. Consistent prefix violations appear only when there is **more than one replication stream**: separate services, separate databases, sharded data (Lesson 35 — Database partitioning and sharding), or multi-leader setups (Phase 4, Topic 15). The practical implication is that *sharding your database introduces a consistency anomaly that a single leader never had*, and that is a cost people forget to count.

A second route to the same bug within one database: a page that issues two queries, and a connection pool that sends them to two different replicas with different lag. The *database* preserved the prefix; your *application* reassembled a history that never existed. The fix is to pin all queries for one page render to one replica connection.

### The fixes

Five mechanisms, in increasing order of strength and cost.

**Fix 1 — Read from the leader for N seconds after a write.**
After any write, set a flag (cookie, session entry, Redis key) with a TTL of N seconds. While the flag is set, that user's reads go to the leader.
- *Strength*: eliminates read-your-own-writes violations for reads within N seconds, with certainty.
- *Weakness*: N is a guess. Too small and anomalies leak through; too large and leader load grows. Guards nothing after N seconds, which matters if lag can exceed N (and Section 9 shows it does, at the tail).
- *Cost*: directly measurable extra leader read load — computed in Section 9 Part C.
- *When it is right*: the pragmatic default. Simple, no schema changes, covers the overwhelming majority of real cases.

**Fix 2 — Sticky routing by user.**
Hash the user ID to a specific replica, so one user always reads from the same replica.
- *Strength*: gives **monotonic reads** cheaply and reliably, because one replica never goes backwards.
- *Weakness*: does **not** give read-your-own-writes — their sticky replica can still be behind the leader. It also unbalances load if a user is heavy, and needs a defined failover behaviour when that replica dies or is replaced (consistent hashing, Lesson 38).
- *When it is right*: as a complement to Fix 1, not a replacement. Fix 1 + Fix 2 together cover anomalies 1 and 2.

**Fix 3 — Read-your-writes tokens with LSN pinning.**
After a write, capture the leader's current LSN and return it to the client (cookie, header, or session). On each read, pass the token; the router either picks a replica whose `replay_lsn >= token` or falls back to the leader.
- *Strength*: **exact**. Not a heuristic, not a guess. It is correct whether lag is 2 ms or 2 minutes, because it is a direct comparison of positions in the log.
- *Weakness*: requires application and routing changes; the token must travel with the user across page loads, services, and devices; it leaks an internal database position to the client (sign it, or keep it server-side keyed by session); and a user who *writes on their phone and reads on their laptop* has no token, so the guarantee is per-session rather than per-person.
- *Cost*: engineering effort, plus an extra round trip if the chosen replica must be checked.
- *When it is right*: when correctness must be guaranteed rather than probable — financial balances, legal documents, anything where "refresh and it'll appear" is not an acceptable answer.

**Fix 4 — Monotonic reads via session affinity.**
A narrower form of Fix 2: rather than permanently pinning a user to a replica, record in the session the highest LSN this user has *observed*, and reject (or wait on) any replica behind that point.
- *Strength*: guarantees monotonic reads without permanently binding a user to one machine, so load stays balanced and replica replacement is graceful.
- *Weakness*: same token-plumbing cost as Fix 3; must handle the case where no replica qualifies.

**Fix 5 — Wait for the replica to catch up.**
Rather than routing away from a stale replica, make the read wait until that replica has replayed past the required LSN.
- PostgreSQL 18 adds `pg_wal_replay_wait('0/4A21C90', timeout_ms)` which blocks precisely until replay reaches that LSN. Earlier versions poll `pg_last_wal_replay_lsn()`. MySQL has had `WAIT_FOR_EXECUTED_GTID_SET(gtid, timeout)` for years.
- *Strength*: exact, and keeps the read off the leader entirely — the best option for leader capacity.
- *Weakness*: converts a staleness problem into a **latency** problem. If lag is 900 ms, the user waits 900 ms. You must set a timeout and decide the fallback (serve stale with a warning, or redirect to the leader). Under a serious lag event every such read blocks, which can exhaust the connection pool — a staleness incident becomes an availability incident.
- *When it is right*: when lag is normally tiny, leader capacity is the binding constraint, and you have disciplined timeouts (Lesson 17).

**Sixth option, for completeness**: `synchronous_commit = remote_apply` (Lesson 33) solves anomaly 1 at the database layer by refusing to acknowledge the write until the standby can see it. It is exact and requires no application change, which makes it tempting — but it puts replica replay on the *write* critical path for every write, and it only covers the standbys in the synchronous set. Use it when you have few writes and cannot change the application; avoid it when write latency matters.

### Where this is going

Everything in this lesson is a *session guarantee*: a promise about what one client sees, built on top of a storage layer that offers no global ordering promise at all. That is a deliberate and very common engineering position — it is far cheaper than making the whole system strongly consistent, and it covers what users actually perceive.

The formal vocabulary for all of this — linearizability, sequential consistency, causal consistency, eventual consistency, and the session guarantees (read-your-writes, monotonic reads, monotonic writes, writes-follow-reads) — is **Phase 3, Topic 20 — Consistency models**, **Phase 3, Topic 21 — Strong versus eventual consistency**, and **Phase 3, Topic 22 — The CAP and PACELC theorems**. When you meet those, map each definition back to the three concrete anomalies above; the theory is much easier when you already know which user complaint each model prevents.

## 5. Request / data flow

Priya updates her delivery address on BookNook. The platform uses **Fix 1 (leader reads for N seconds)** with **Fix 3 (LSN tokens)** as a backstop for account pages.

1. Browser sends `PATCH /account/address` with the session cookie. The LB routes it to an app server (Lesson 23).
2. The app server classifies this as a write and takes a connection from the **primary** pool. Reads and writes use separate pools; this is what makes routing decisions possible at all (Lesson 32).
3. The transaction commits on the primary at LSN `0/4A21C90`. `synchronous_commit = remote_write` means a standby has the bytes in RAM, but **nothing has been replayed anywhere yet** (Lesson 33, stage 3 of 5).
4. In the same transaction block, the app runs `SELECT pg_current_wal_insert_lsn()` and receives `0/4A21C90`.
5. The app writes two things to the session store (Redis): `last_write_lsn = 0/4A21C90` and `leader_until = now + 1500 ms`.
6. The app returns `200 OK` with `Set-Cookie: bn_lsn=0/4A21C90` (signed, so a client cannot forge a position far in the future and force every read to the leader — a trivial denial-of-service otherwise).
7. The browser redirects. `GET /account` arrives 90 ms later, carrying both the session cookie and `bn_lsn`.
8. The app server's read router checks the session: `now < leader_until` is **true**. The read is routed to the **primary**.
9. The primary returns the new address. Priya sees the change. No anomaly. Total extra cost: one read on the leader.
10. At T+1,700 ms Priya reloads the account page. `leader_until` has expired, so the router now considers replicas.
11. The router consults its replica-health cache, refreshed every 200 ms, which holds each replica's last reported `pg_last_wal_replay_lsn()`: `replica_a = 0/4A21D10`, `replica_b = 0/4A21880`, `replica_c = 0/4A21C95`.
12. It filters to replicas whose `replay_lsn >= 0/4A21C90` (the cookie token). `replica_a` and `replica_c` qualify; `replica_b` is behind and is excluded.
13. Among the qualifying replicas it picks by least connections (Lesson 23) → `replica_c`.
14. `replica_c` serves the read. It is 1,700 ms stale relative to *other users'* writes, which is fine and invisible, but it is guaranteed current with respect to **Priya's** writes, which is what she notices.
15. The response is returned. The app updates the session's `max_observed_lsn` to `replica_c`'s replay position, so subsequent reads can never be served by a replica behind that point — this is Fix 4, and it is what prevents Tom's disappearing-order problem.
16. If **no** replica had qualified at step 12, the router would fall back to the primary, increment a `stale_replica_fallback` counter, and — if that counter rises — fire an alert, because a sustained inability to find a current replica is a lag incident in progress.

Step 16 is the part teams forget. The fallback is also your best *detector*: a metric that counts how often consistency routing had to use the leader tells you about lag in terms of **user impact**, which no lag graph does.

## 6. ASCII diagram

```
ANOMALY 1 — READ-YOUR-OWN-WRITES VIOLATION

  t=0ms    Priya ──PATCH /address──▶ LEADER  [commit @ LSN 0/4A21C90]  ──▶ 200 OK
                                        │
                                        └── WAL streaming (async) ─ ─ ─ ─ ┐
  t=90ms   Priya ──GET /account────▶ replica_c                            │
                                     replay_lsn = 0/4A21880  (140ms behind)
                                        │                                 │
                                        ▼                                 ▼
                                   OLD ADDRESS                      arrives t=140ms
                                   "I literally just saved that."


ANOMALY 2 — MONOTONIC READS VIOLATION (caused by ROUTING, not by lag)

  t=0      Tom places order #90210 on the LEADER
  t=300ms  GET /orders ──▶ replica_a (caught up)      ──▶  [#90210]   ✓ visible
  t=800ms  GET /orders ──▶ replica_b (900ms behind)   ──▶  [        ]  ✗ GONE
  t=1.4s   GET /orders ──▶ replica_a                  ──▶  [#90210]   ✓ back again

            state seen ▲
                       │   ●              ●
                       │        ○              ← time ran BACKWARDS here
                       └──────────────────────▶ real time
            No replica was wrong. The SEQUENCE of replicas was.


ANOMALY 3 — CONSISTENT PREFIX VIOLATION (needs 2+ replication streams)

  reviews-db  leader: [Ana's review]──▶ replica  600ms behind  ✗ not yet replayed
  replies-db  leader: [Ben's reply ]──▶ replica  caught up     ✓ replayed

  page render fans out to BOTH:
        ┌──────────────────────────────────┐
        │  Reviews: (none yet)             │   ← effect visible
        │    └─ Ben: "Agreed, the 2019..." │   ← before cause
        └──────────────────────────────────┘
  NOTE: a SINGLE leader + SINGLE WAL cannot do this — replay is strictly ordered.
  Sharding (Lesson 35) is what introduces this anomaly.


THE FIXES, ON ONE AXIS

  weaker/cheaper ──────────────────────────────────────────▶ stronger/costlier

  do nothing     sticky       leader-for-N-sec    LSN token      wait for
  (eventual)     routing      after a write       pinning        replay
      │             │               │                 │             │
   0 cost      monotonic       RYOW within N      EXACT RYOW    EXACT, no
   anomalies    reads only     (a guess)          + monotonic   leader load,
   visible                     +leader load       +plumbing     but ADDS LATENCY


WHY 'synchronous_commit = on' DOES NOT SAVE YOU  (bridge from Lesson 33)

  LEADER ──WAL──▶ STANDBY:  [write]──▶[flush]──▶[replay]
                               ▲         ▲          ▲
                    remote_write│      on│  remote_apply
                                         │
                      'on' ACKS HERE ────┘   bytes are DURABLE on the standby
                                             but NOT YET VISIBLE to queries.
                      ⇒ A fully synchronous cluster still shows Anomaly 1.
                        Durability ≠ visibility.
```

## 7. Detailed example

### BookNook — from symptom to fix

**Phase 1 — the symptom.** Support receives 40–60 tickets a week with the shape *"it didn't save"* or *"my order disappeared"*. Engineering cannot reproduce it: on a developer machine there is one database, so lag is structurally zero. The bug is **invisible in every environment except production**, which is why it survives to production in the first place.

**Phase 2 — measurement.** The team graphs `replay_lag` percentiles from `pg_stat_replication` over 24 hours:

```
p50  = 20 ms        p90  = 80 ms
p99  = 400 ms       p99.9 = 3,000 ms
peak (nightly catalogue import, 02:00–02:20) = 48,000 ms
```

The p50 is reassuring and completely irrelevant. The distribution has a long tail, and the tail is where the tickets come from. Section 9 turns this distribution into a precise user-impact number: **5.6 %** of post-write reads are stale.

**Phase 3 — the quick fix (Fix 1).** After any write, a signed cookie `bn_leader_until` is set for 1,500 ms; reads with a live cookie go to the primary.

- Tickets fall by roughly 95 % within a day.
- Leader read load rises by 1.75 % (computed in Section 9 Part C) — comfortably within headroom.
- Residual anomalies: users whose redirect takes longer than 1,500 ms (slow mobile connections), and anyone reading during the 02:00 import, when lag is 48 seconds and a 1.5-second window protects nobody.

**Phase 4 — the import problem.** The nightly catalogue import runs as one transaction updating 4 million rows, generating ~6 GB of WAL that a single-threaded replay process cannot keep up with. Three changes:
- Batch the import into 10,000-row chunks with a brief pause between them, so replay keeps pace and the lag ceiling drops from 48 s to ~900 ms.
- Route the import's own verification reads to the primary explicitly.
- Add an alert on replay **byte** lag > 500 MB, not on seconds-behind, precisely because the idle-system artefact in Section 4 had previously made the seconds-based alert untrustworthy and it had been silenced.

**Phase 5 — the account and payment pages (Fix 3 + Fix 4).** For pages where "refresh and it'll appear" is not acceptable — saved addresses, payment methods, order status, returns — the team adds LSN tokens. The router filters replicas by `replay_lsn >= token`, falls back to the primary, and records `max_observed_lsn` per session to guarantee monotonic reads. A `stale_replica_fallback` counter becomes the primary user-impact signal for lag.

**Phase 6 — the sharding trap, noted in advance.** When `reviews` is later split onto its own cluster (Lesson 35), consistent-prefix violations appear for the first time — reviews and replies on independent streams. The fix there is not routing: it is either keeping causally related data in the same shard, or having the replies service refuse to render a reply whose parent review it cannot yet see. Worth noticing now: **the anomaly was introduced by a scaling decision, not by a bug.**

### Contrasting example — a collaborative document editor

The same lag, in a system where the acceptable answers are completely different.

A document editor has multiple users editing one document concurrently, and reads are continuous rather than discrete page loads. Here:

- **Read-your-own-writes is non-negotiable and must be instant.** A user typing cannot wait 90 ms to see their own character, let alone 900. So the client does **not** read its own writes from the server at all: it applies them locally and optimistically, and the server round trip only confirms or corrects. The fix is architectural — move the read-your-writes guarantee to the client — rather than routing-based.
- **Monotonic reads matter more than freshness.** Characters appearing, vanishing, and reappearing would make the editor unusable, so the client maintains a monotonically increasing version vector and *ignores* any server state older than what it has already applied. That is Fix 4, implemented client-side.
- **Consistent prefix is mandatory**, because edits are inherently causal: a delete of text that has not been inserted yet is meaningless. This is handled by operational transformation or CRDTs, not by replication configuration.
- **Stale reads for *other users'* edits are acceptable for a few hundred milliseconds**, which is precisely the guarantee BookNook also relies on.

Same lag, same three anomalies, completely different mechanisms — because the unit of interaction changed from "a page load" to "a continuous stream of causally ordered operations". The transferable insight: **read-after-write is a property of the user's session, so it can be enforced anywhere along that session's path — in the database, in the router, or in the client.** Choosing where is the actual design decision.

## 8. Code or configuration example

> **Illustrative only — not production-ready.** Error handling, connection pooling, token signing and rotation, metrics, replica discovery, timeouts and failover behaviour are simplified or omitted. Do not deploy these as written.

### SQL — measuring lag honestly

```sql
-- ===== Run on the PRIMARY: the authoritative view ==========================
SELECT application_name,
       client_addr,
       state,                                  -- 'streaming' is the healthy state
       sync_state,                             -- sync / potential / async (Lesson 33)
       -- BYTE lag: a true backlog measure. Zero when caught up, even when idle.
       -- This is the number to ALERT on.
       pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) AS replay_lag_bytes,
       -- TIME lag measured from real acknowledgement round trips (PG 10+).
       -- Unlike now() - pg_last_xact_replay_timestamp(), these do NOT climb
       -- forever on an idle system. Graph these as PERCENTILES, not averages:
       -- the p50 is 20 ms and irrelevant; the p99 is what generates tickets.
       write_lag,                              -- stage 3: bytes in standby OS cache
       flush_lag,                              -- stage 4: fsync'd on standby
       replay_lag                              -- stage 5: VISIBLE to standby queries
                                               --          <-- the one this lesson is about
FROM pg_stat_replication
ORDER BY replay_lag_bytes DESC;

-- ===== Run on a STANDBY: the naive query, and why it lies =================
-- On an IDLE leader this climbs by 1 second per second while the standby is
-- perfectly caught up, because it reports the AGE OF THE LAST WRITE, not
-- staleness. Alerting on this directly produces 04:00 pages for a healthy
-- system; the team then raises the threshold and misses the real event.
SELECT now() - pg_last_xact_replay_timestamp() AS naive_seconds_behind;

-- Safer version: report 0 when there is genuinely no backlog to replay.
SELECT CASE
         WHEN pg_last_wal_receive_lsn() = pg_last_wal_replay_lsn() THEN 0
         ELSE EXTRACT(EPOCH FROM now() - pg_last_xact_replay_timestamp())
       END AS seconds_behind;
-- REMAINING FLAW: this compares RECEIVED to REPLAYED. A standby that has lost
-- its connection to the primary has replayed everything it received and will
-- report 0 forever while falling arbitrarily far behind. You MUST also check
-- pg_stat_wal_receiver.status / last_msg_receipt_time, or monitor the primary.

-- ===== The exact position of this standby — the LSN-pinning primitive =====
SELECT pg_last_wal_replay_lsn()  AS replayed_to,   -- compare against a client token
       pg_is_in_recovery()       AS is_standby;    -- true on a replica, false on leader

-- ===== Capturing a token on the PRIMARY right after a write ===============
-- pg_current_wal_insert_lsn() is the position a just-committed transaction is
-- at or before. Any standby whose replay_lsn >= this value is guaranteed to
-- have replayed that write. This comparison is EXACT -- not a heuristic.
SELECT pg_current_wal_insert_lsn() AS write_token;

-- ===== PostgreSQL 18+: wait for replay instead of routing away ============
-- Blocks on the STANDBY until replay reaches the given LSN, or times out.
-- Converts a staleness problem into a LATENCY problem: set the timeout, and
-- decide the fallback, or a lag event becomes a pool-exhaustion event.
-- CALL pg_wal_replay_wait('0/4A21C90', 200);   -- wait up to 200 ms
```

### PostgreSQL configuration — the settings that create or cure lag

```ini
# ---------------------------------------------------------------------------
# On a STANDBY serving reads
# ---------------------------------------------------------------------------

hot_standby = on

max_standby_streaming_delay = 5s
# How long WAL REPLAY may be PAUSED so a conflicting read query can finish.
# Default is 30s, which means ONE long analytics query can create 30 SECONDS
# of replication lag with zero change in write volume. Reading from a replica
# can make that replica lag -- the most counter-intuitive cause in Section 4.
#   LOW  value -> low lag, but read queries get cancelled
#                 ("ERROR: canceling statement due to conflict with recovery")
#   HIGH value -> queries survive, lag spikes
# There is no setting that avoids both. Choose which failure you prefer, and
# put long analytical queries on a SEPARATE replica with a high value here.

hot_standby_feedback = on
# Tells the primary which row versions this standby still needs, so VACUUM
# does not remove them and conflicts largely disappear.
# TRADE-OFF: a long-running query on the standby now BLOATS TABLES ON THE
# PRIMARY. You have moved the pain, not removed it.

wal_receiver_status_interval = 1s
# How often this standby reports its write/flush/replay positions upstream.
# This bounds the FRESHNESS OF YOUR LAG MEASUREMENT -- at the 10s default you
# cannot detect a 2-second lag spike at all. Lower it if you route on LSN.

# ---------------------------------------------------------------------------
# On the PRIMARY
# ---------------------------------------------------------------------------

wal_compression = on
# Compresses full-page images. Full-page writes after a checkpoint can turn a
# 20-byte update into an 8 KB WAL record, which is a frequent cause of
# network-bound lag on cross-region or shared-NIC links.

checkpoint_timeout = 15min
max_wal_size = 8GB
# Infrequent checkpoints -> fewer full-page-write storms -> less WAL volume
# -> less replay for the standby. Trade-off: longer crash recovery on the
# primary, so this directly increases RTO (Lesson 33).

synchronous_commit = remote_apply   # OPTIONAL, for ONE specific purpose:
# the brute-force cure for read-your-own-writes. The commit does not return
# until the synchronous standby has REPLAYED the change, so a read there sees
# it immediately. Correct, requires no application change, and puts standby
# replay on the critical path of EVERY WRITE. Use when writes are rare and you
# cannot change the application. Otherwise prefer the routing fixes below.
```

### Python — a consistency-aware read router

```python
import time
import hmac, hashlib
from dataclasses import dataclass

# Illustrative only: no pooling, no retries, no circuit breaking, no metrics
# export, and replica positions are refreshed naively. Real systems need all
# of those (Lessons 17, 23, 24).


def lsn_to_int(lsn: str) -> int:
    """PostgreSQL LSNs look like '0/4A21C90'. Compare them as 64-bit integers.
    Comparing them as STRINGS is a real and nasty bug: '0/9FFFFFF' > '0/10000000'
    lexicographically, but it is EARLIER in the log."""
    hi, lo = lsn.split("/")
    return (int(hi, 16) << 32) | int(lo, 16)


@dataclass
class Replica:
    name: str
    conn: object
    replay_lsn: int = 0          # refreshed by a background poller
    inflight: int = 0


class ReadRouter:
    # How long after a write this user's reads go to the LEADER (Fix 1).
    # This is a GUESS and must be chosen from the MEASURED replay_lag
    # distribution -- roughly p99 plus a margin. BookNook: p99 = 400 ms,
    # p99.9 = 3 s, so 1.5 s covers almost everything without pinning users to
    # the leader for long. It does NOT cover the nightly-import lag spike;
    # that is handled by fixing the import, not by enlarging this window.
    LEADER_WINDOW_MS = 1500

    def __init__(self, leader, replicas, secret: bytes):
        self.leader = leader
        self.replicas = replicas
        self.secret = secret
        self.fallback_count = 0   # <-- the best USER-IMPACT signal for lag

    # ---- token handling ---------------------------------------------------

    def sign(self, lsn: str) -> str:
        """Tokens MUST be signed. An unsigned token lets any client claim an
        enormous LSN, which no replica can ever satisfy, forcing every read to
        the leader -- a trivial denial-of-service against your primary."""
        mac = hmac.new(self.secret, lsn.encode(), hashlib.sha256).hexdigest()[:16]
        return f"{lsn}.{mac}"

    def verify(self, token: str) -> int:
        try:
            lsn, mac = token.rsplit(".", 1)
        except ValueError:
            return 0
        expected = hmac.new(self.secret, lsn.encode(), hashlib.sha256).hexdigest()[:16]
        if not hmac.compare_digest(mac, expected):
            return 0                       # forged or corrupt: ignore, do not error
        return lsn_to_int(lsn)

    # ---- the write path ---------------------------------------------------

    def execute_write(self, session: dict, sql: str, params: tuple):
        with self.leader.cursor() as cur:
            cur.execute(sql, params)
            # Capture the log position reached by this write, in the SAME
            # transaction, so the token cannot be older than the write.
            cur.execute("SELECT pg_current_wal_insert_lsn()")
            lsn = cur.fetchone()[0]

        # Fix 1: send this user's reads to the leader for a short window.
        session["leader_until_ms"] = time.monotonic() * 1000 + self.LEADER_WINDOW_MS
        # Fix 3: the exact position any replica must have reached to serve
        # this user correctly. Unlike the time window, this is not a guess.
        session["write_token"] = self.sign(lsn)
        return lsn

    # ---- the read path ----------------------------------------------------

    def pick_connection(self, session: dict):
        now_ms = time.monotonic() * 1000

        # Fix 1 -- recent writer: go straight to the leader. Simple and exact
        # for the window's duration.
        if now_ms < session.get("leader_until_ms", 0):
            return self.leader, "leader:recent_write"

        # The position this session REQUIRES a replica to have reached:
        #  - its own last write        (Fix 3: read-your-own-writes)
        #  - the newest state it has already SEEN (Fix 4: monotonic reads --
        #    this is what stops Tom's order from disappearing on refresh)
        required = max(
            self.verify(session.get("write_token", "")),
            session.get("max_observed_lsn", 0),
        )

        # Only replicas that have replayed PAST that point are eligible.
        eligible = [r for r in self.replicas if r.replay_lsn >= required]

        if not eligible:
            # No replica is current enough. Serve correctly from the leader and
            # COUNT IT. A rising fallback rate is lag expressed as USER IMPACT,
            # which no lag graph gives you. Alert on this, not just on bytes.
            self.fallback_count += 1
            return self.leader, "leader:no_fresh_replica"

        # Among eligible replicas, balance normally (Lesson 23).
        chosen = min(eligible, key=lambda r: r.inflight)
        return chosen.conn, f"replica:{chosen.name}"

    def execute_read(self, session: dict, sql: str, params: tuple):
        conn, route = self.pick_connection(session)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            # Record how current the data we just showed the user was, so no
            # later read may serve anything OLDER. Without this step, routing
            # alone reintroduces the monotonic-reads violation.
            cur.execute(
                "SELECT CASE WHEN pg_is_in_recovery() "
                "THEN pg_last_wal_replay_lsn() ELSE pg_current_wal_insert_lsn() END"
            )
            session["max_observed_lsn"] = max(
                session.get("max_observed_lsn", 0), lsn_to_int(cur.fetchone()[0])
            )
        return rows, route
```

## 9. Numerical example

### Stated assumptions

```
B1  BookNook measured replay_lag distribution over 24 h (excluding the import window):
        p50  =    20 ms
        p90  =    80 ms
        p99  =   400 ms
        p99.9 = 3,000 ms
B2  After a write, the browser redirects and the follow-up GET arrives
        D = 120 ms after the commit acknowledgement.
        (40 ms network + 60 ms client render/navigation + 20 ms LB and app overhead
         — typical for a mid-range mobile device on 4G.)
B3  Reads are routed to replicas uniformly; no consistency routing yet.
B4  A read is STALE if, at the instant it is served, that replica's lag exceeds the
        time elapsed since the commit. (The replica must have replayed past the
        write's LSN; equivalently, lag < elapsed time.)
B5  Lag at any instant is drawn from the distribution in B1, independently of the write.
B6  BookNook serves 50,000 writes/day, each followed by at least one read,
        and 4,000,000 reads/day in total.
B7  The leader can sustain its current write load plus up to 10% additional read QPS
        before latency degrades.
```

### Part A — Modelling the lag tail

The distribution is only given at four points, so interpolate. Tail probabilities of this shape are well approximated by a power law, `P(lag > x) = C · x^(−a)`, which is fitted from two known points.

Using p90 and p99 — i.e. `P(lag > 80 ms) = 0.10` and `P(lag > 400 ms) = 0.01`:

```
Take the ratio of the two tail probabilities and of the two thresholds:

    a = ln(0.10 / 0.01) / ln(400 / 80)
      = ln(10) / ln(5)
      = 2.302585 / 1.609438
      = 1.4307

Check the fit at p99:  0.10 × (400/80)^(−1.4307)
                     = 0.10 × 5^(−1.4307)
                     = 0.10 × e^(−1.4307 × 1.609438)
                     = 0.10 × e^(−2.3026)
                     = 0.10 × 0.1000
                     = 0.0100  ✓ matches the given p99
```

The model is therefore `P(lag > x) = 0.10 × (x / 80)^(−1.4307)` for x ≥ 80 ms.

### Part B — Probability a post-write read is stale, with no fix at all

The read arrives D = 120 ms after the commit (B2). By B4 it is stale if lag > 120 ms:

```
P(stale) = P(lag > 120 ms)
         = 0.10 × (120 / 80)^(−1.4307)
         = 0.10 × 1.5^(−1.4307)
         = 0.10 × e^(−1.4307 × ln 1.5)
         = 0.10 × e^(−1.4307 × 0.405465)
         = 0.10 × e^(−0.58010)
         = 0.10 × 0.55986
         = 0.05599
         ≈ 5.6 %
```

In daily terms (B6):

```
Stale post-write reads per day = 50,000 × 0.05599 = 2,799  ≈ 2,800 users/day
```

**2,800 users a day see "it didn't save."** That is the number that produces 40–60 support tickets a week — because only a small fraction of affected users bother to report it, which is exactly why this bug persists for months.

### Part C — Fix 1: read from the leader for N seconds after a write

With a leader window of N, a post-write read is stale only if it arrives *after* the window **and** lag still exceeds its elapsed time.

Additional assumption **B8**: the distribution of post-write read arrival times is such that 92 % arrive within 500 ms and 8 % arrive later (users who leave the tab, slow devices, background refreshes). For the late arrivals, use P(lag > 500 ms) as a conservative bound, since a read arriving even later needs even more lag to be stale.

```
Reads inside the window (0–500 ms): routed to the LEADER
    P(stale) = 0 exactly. Not "small" — zero, by construction.

Reads after the window (8% of post-write reads):
    P(lag > 500 ms) = 0.10 × (500/80)^(−1.4307)
                    = 0.10 × 6.25^(−1.4307)
                    = 0.10 × e^(−1.4307 × 1.832581)
                    = 0.10 × e^(−2.62185)
                    = 0.10 × 0.072596
                    = 0.0072596

    P(stale overall) = 0.08 × 0.0072596 = 0.000581 = 0.058 %

Stale reads per day = 50,000 × 0.000581 = 29 per day
Reduction: 2,800 → 29  =  96× improvement
```

**Cost of the fix — extra leader read load.** Additional assumption **B9**: each write is followed on average by 1.4 reads within the 500 ms window.

```
Reads redirected to the leader per day = 50,000 × 1.4 = 70,000
As a fraction of all reads (B6)        = 70,000 / 4,000,000 = 0.0175 = 1.75 %
```

Against B7's 10 % headroom, 1.75 % is comfortably affordable. **One cookie, 96× fewer anomalies, 1.75 % more leader reads.** This is the best return on effort in the lesson and it is why Fix 1 is the pragmatic default.

### Part D — What a naive fix buys instead

A common alternative is to add an artificial client-side delay before the post-write read, rather than routing. Suppose a 300 ms delay is added, so D = 420 ms:

```
P(stale) = 0.10 × (420/80)^(−1.4307)
         = 0.10 × 5.25^(−1.4307)
         = 0.10 × e^(−1.4307 × 1.658228)
         = 0.10 × e^(−2.37232)
         = 0.10 × 0.093297
         = 0.00933 = 0.93 %

Stale reads per day = 50,000 × 0.00933 = 467
```

Compare the two fixes honestly:

```
No fix            : 2,800 stale/day,  0 ms added latency
300 ms delay      :   467 stale/day,  300 ms added to EVERY post-write page load
Leader-for-500 ms :    29 stale/day,  0 ms added latency, +1.75% leader reads
```

The delay is **16× worse than the routing fix** *and* makes every user wait. It is a very common instinct and it is strictly dominated. Worth remembering as a general principle: adding a sleep to paper over a race is almost always both slower and less correct than addressing the routing.

### Part E — Monotonic reads: probability of time running backwards

Assumption **B10**: 3 replicas; a user's consecutive reads are routed independently; lag on different replicas is independent (optimistic — in reality checkpoints correlate, which makes the real number somewhat worse).

A user reads at T, then again at T + 600 ms. A monotonic-reads violation requires the second read to be served by a replica that has *not* replayed as far as what the first read showed.

```
P(second read lands on a DIFFERENT replica) = 1 − 1/3 = 0.667

P(the first replica was current)  = 1 − P(lag > 600 ms)
    P(lag > 600) = 0.10 × (600/80)^(−1.4307)
                 = 0.10 × 7.5^(−1.4307)
                 = 0.10 × e^(−1.4307 × 2.014903)
                 = 0.10 × e^(−2.88273)
                 = 0.10 × 0.055974 = 0.005597
    so P(first current) = 0.9944

P(the second replica is behind that point) ≈ 0.005597

P(violation per read pair) ≈ 0.667 × 0.9944 × 0.005597
                           = 0.003713 = 0.37 %
```

Assumption **B11**: 600,000 refresh-style read pairs per day.

```
Violations per day = 600,000 × 0.003713 = 2,228 per day
```

**Note what this shows.** The monotonic-reads violation rate (0.37 %) is an order of magnitude *lower* than the read-your-own-writes rate (5.6 %), yet it produces a comparable number of daily incidents because it applies to a much larger volume of reads. And its user-visible severity is higher — "my order vanished" is more alarming than "my edit didn't save." The fix (Fix 4, `max_observed_lsn` in the session) costs nothing extra once the token plumbing from Fix 3 exists, which is the argument for implementing Fixes 3 and 4 together.

### Part F — Fix 5: waiting for replay, and why the timeout is mandatory

If instead of routing away, each read waits for the replica to catch up:

```
Expected wait, given the read must wait at all (lag > 120 ms):
    conditional mean of the tail. For a power law with a = 1.4307 and
    threshold x0 = 120 ms, E[lag | lag > x0] = x0 × a / (a − 1)
                                              = 120 × 1.4307 / 0.4307
                                              = 120 × 3.3218
                                              = 399 ms

Unconditional added latency = P(stale) × E[wait | stale]
                            = 0.05599 × 399 ms
                            = 22.3 ms  average
```

22 ms of average added latency looks acceptable. Now check what happens during the nightly import, when lag is 48 seconds:

```
Every post-write read waits up to 48 s unless a timeout stops it.
At 50,000 writes/day the import window is low traffic, say 30 writes/minute
= 0.5 writes/s, each blocking a connection for up to 48 s:
    L = λ × W = 0.5 × 48 = 24 connections held, continuously, doing nothing.
If the pool is 20 connections: TOTAL OUTAGE for reads during the import.
```

**A staleness problem became an availability problem.** This is why Fix 5 requires a hard timeout (say 200 ms) and an explicit fallback. Without one, it is strictly more dangerous than doing nothing.

## 10. Trade-offs

**Do nothing (accept eventual consistency)**
- *Advantages:* zero complexity, zero leader load, maximum read scalability, no token plumbing.
- *Disadvantages:* 5.6 % of post-write reads are wrong (Section 9 Part B); the resulting support load is continuous and the bug is unreproducible outside production.
- *Unnecessary to fix when:* the data is genuinely non-interactive — analytics dashboards, recommendation feeds, "people also bought", trending lists. Nobody writes to these and then immediately reads them.

**Fix 1 — leader reads for N seconds after a write**
- *Advantages:* one cookie, no schema change, 96× fewer anomalies, no added latency.
- *Disadvantages:* N is a guess calibrated against a distribution that changes; fails completely when lag exceeds N; adds leader read load proportional to write rate.
- *Operational cost:* you must re-derive N whenever the lag distribution shifts, and alert when p99 lag approaches N.

**Fix 2 — sticky routing by user**
- *Advantages:* monotonic reads essentially for free; good cache locality on the replica.
- *Disadvantages:* no read-your-own-writes guarantee at all; load imbalance; needs consistent hashing (Lesson 38) so that losing a replica does not remap everyone; conflicts with least-connections balancing (Lesson 23).

**Fix 3 / Fix 4 — LSN tokens and observed-position pinning**
- *Advantages:* exact, not probabilistic; correct at any lag; covers read-your-own-writes and monotonic reads with the same mechanism; the fallback counter becomes an excellent user-impact metric.
- *Disadvantages:* real engineering work; tokens must be signed and must travel across services; cross-device sessions are not covered; adds a position-tracking query or a background poller.
- *Operational cost:* a replica-position cache refreshed every ~200 ms, which must itself be monitored.

**Fix 5 — wait for replica catch-up**
- *Advantages:* exact, and keeps reads off the leader entirely, which preserves the capacity benefit that replicas exist for.
- *Disadvantages:* converts staleness into latency; under a lag event it holds connections and can exhaust the pool (Section 9 Part F); requires PostgreSQL 18 for the clean primitive, or polling before that.

**`synchronous_commit = remote_apply` (Lesson 33)**
- *Advantages:* fixes anomaly 1 at the database layer with zero application change.
- *Disadvantages:* every write pays replica replay latency; only covers synchronous standbys; replay conflicts on the standby stall primary writes.
- *Unnecessary complexity when:* write volume is high — the routing fixes cost far less.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| Replica 140 ms behind serves a post-write read | "I saved it and it didn't save." User edits repeatedly, creating duplicate work and sometimes duplicate records | `replay_lag` p99 graph correlated with support tickets; a `stale_replica_fallback` counter if consistency routing exists | Leader reads for N seconds after a write (Fix 1); LSN tokens for pages where correctness is required |
| User's consecutive reads hit replicas with different lag | An order, review, or message appears, disappears on refresh, then reappears. More alarming than simple staleness | Hard to detect server-side without session instrumentation; usually found via support tickets or client-side version checks | Track `max_observed_lsn` per session and exclude replicas behind it (Fix 4); or sticky routing (Fix 2) |
| Nightly bulk import as one transaction | For ~20 minutes nightly, all replica reads are up to 48 s stale; overnight users see yesterday's catalogue | Byte-lag alert; one core pinned at 100 % on the replica while others idle (the single-threaded-replay fingerprint) | Batch the import into chunks with pauses; route import verification reads to the leader; alert on byte lag, not seconds |
| Long analytics query on a read replica | Unrelated users see stale data for up to `max_standby_streaming_delay`; or the analyst's query is cancelled with "conflict with recovery" | `pg_stat_database_conflicts` rising; lag spikes aligned with BI schedules rather than write volume | Dedicate a separate replica to analytics with a high delay setting; keep user-facing replicas at a low one |
| Alerting on `now() - pg_last_xact_replay_timestamp()` | Nobody is paged for the real event, because the alert was silenced months ago after firing every night on an idle system | Alert history shows repeated 03:00–05:00 firings with zero backlog | Alert on byte lag; use the `CASE` form or `replay_lag` for time-based graphs |
| Replica loses its connection to the primary | Replica reports "0 seconds behind" while serving data that is minutes or hours old; users see a frozen site | `pg_stat_wal_receiver` empty or `last_msg_receipt_time` stale; the primary shows the standby missing from `pg_stat_replication` | Health checks must verify *connectivity*, not just liveness (Lesson 24), and eject the replica from the read pool |
| Unsigned LSN token accepted from the client | A malicious or buggy client sends a far-future LSN; no replica ever qualifies; 100 % of that session's reads go to the leader | Leader read QPS rises sharply; `stale_replica_fallback` spikes for a small number of sessions | Sign tokens with HMAC and reject invalid ones silently; cap the acceptable token to the primary's current LSN |
| Page fans out to two services with independent replication streams | A reply renders above a review that does not exist yet; a comment count disagrees with the number of comments shown | User reports of "impossible" page states; hard to reproduce | Keep causally related data in one shard; or have the dependent service suppress rendering when the parent is not yet visible |
| LSN token compared as a string rather than as an integer | Sporadic, baffling staleness — `'0/9FFFFFF'` sorts after `'0/10000000'` lexicographically but is earlier in the log | Anomalies cluster around WAL positions where the hex digit count changes | Parse LSNs into 64-bit integers before comparing (Section 8) |
| Fix 5 (wait for replay) deployed without a timeout | During a lag event every post-write read blocks; the connection pool exhausts; the site goes fully down | Pool-exhaustion errors coinciding with a lag spike; connections in a waiting state | Hard timeout (~200 ms) plus an explicit fallback to the leader or to a stale response with a UI hint |

## 12. Alternatives

| Approach | Guarantee provided | Cost | Main drawback |
|---|---|---|---|
| **Accept eventual consistency** | None | Zero | 5.6 % of post-write reads are wrong; continuous support load |
| **All reads from the leader** | Read-your-writes, monotonic reads, consistent prefix — everything | Loses the entire benefit of replicas | Caps read throughput at one machine; replicas become idle standbys only |
| **Leader reads for N seconds after a write (Fix 1)** | Read-your-writes within N | ~1.75 % extra leader reads | N is a guess; fails when lag > N |
| **Sticky routing by user (Fix 2)** | Monotonic reads | Load imbalance | No read-your-writes guarantee |
| **LSN tokens + observed-position pinning (Fix 3/4)** | Read-your-writes **and** monotonic reads, exactly | Engineering effort; token plumbing | Per-session, so cross-device reads are uncovered |
| **Wait for replica catch-up (Fix 5)** | Read-your-writes, exactly | Added latency when stale | Can exhaust the pool during a lag event without a timeout |
| **`synchronous_commit = remote_apply`** | Read-your-writes on the sync standby | Replica replay on every write's critical path | Write latency and replay-conflict coupling (Lesson 33) |
| **Write-through cache the user reads from** | Read-your-writes via the cache, not the database | Cache infrastructure | Cache and replica can disagree; adds an invalidation problem (Lessons 27–28) |
| **Optimistic client-side update** | The user sees their own write instantly | Client complexity; must reconcile on error | Shows data that may not have committed; needs rollback-on-failure UX |
| **Single-node or consensus-replicated database** — CockroachDB, Spanner, YugabyteDB | Linearizable reads; all three anomalies impossible | Higher write latency; operational complexity | Solves it by not having asynchronous replicas. **Phase 5, Topic 6 — Consensus algorithms** |
| **CRDTs / operational transformation** | Convergence and causal ordering without a leader | Significant design constraints on data types | Only applies to data that can be expressed as mergeable operations. **Phase 4, Topic 16 — Conflict resolution** |

The row worth dwelling on is **optimistic client-side update**, because it is how most modern front-ends actually solve anomaly 1, and it sidesteps the server question entirely: the client already knows what it wrote, so it renders that, and only reconciles if the server disagrees. It is cheap and effective — and it is also how teams accidentally ship a UI that cheerfully shows a write that was later rejected. If you use it, the error path is the whole design.

## 13. When to use it

Apply explicit read-after-write handling when:

1. **A user writes and is immediately shown the result.** Profile edits, address changes, posting a review, placing an order, uploading an avatar, changing a password. This is the overwhelming majority of cases and the default answer is Fix 1.
2. **The data is financial or legal.** Balances, payment methods, invoices, consent records. Use exact LSN pinning (Fix 3), not a time window — a 1,500 ms guess is not a guarantee, and "refresh and it'll appear" is not an acceptable answer for money.
3. **A write is followed by a redirect, which is the dominant web pattern.** `POST` → `302` → `GET` is *designed* to produce a read immediately after a write, which is precisely the anomaly window. If your framework does this (and it does), you need Fix 1 at minimum.
4. **An item can appear and disappear across refreshes.** Order lists, message threads, notification counts. Add Fix 4 (`max_observed_lsn`) — this is the anomaly users find most alarming.
5. **A workflow has steps that depend on the previous step's write.** Multi-page checkout, onboarding wizards, anything where step 3 reads what step 2 wrote. Here lag causes a *functional* failure, not merely a cosmetic one.
6. **Two services render one page and their data is causally related.** Reviews and replies, posts and comment counts, orders and shipments. Guard against consistent-prefix violations explicitly, because sharding (Lesson 35) will make this worse, not better.

## 14. When *not* to use it

1. **Read-only, non-causal data.** Bestseller lists, recommendations, trending searches, aggregate dashboards, "customers also viewed". No user wrote these, so no user can fail to see their own write. Adding consistency routing here spends leader capacity for zero benefit.
2. **Data that is already deliberately stale.** Anything served from a CDN (Lesson 30) or a cache with a 60-second TTL (Lesson 28) is *minutes* stale by design. Routing the database read to the leader to shave 200 ms of staleness off a value that will then sit in a cache for a minute is theatre.
3. **When the real requirement is stronger than session consistency.** If two *different* users must see the same value at the same instant — inventory for the last copy of a book, seat selection, auction bids — read-after-write does not help at all, because it only constrains one session. You need a serialising mechanism: conditional updates, row locks, or a database with linearizable reads (Phase 3, Topic 21).
4. **When the write volume makes leader read-redirection unaffordable.** If 40 % of requests are writes, Fix 1 sends a large share of reads back to the leader and you have quietly rebuilt a single-node system. Use LSN filtering across replicas (Fix 3) instead, so reads stay distributed.
5. **When the underlying problem is a lag bug.** If p99 lag is 8 seconds because of an unbatched nightly import, fix the import. Consistency routing applied to a broken replication pipeline just moves all your traffic onto the leader and hides the real defect until it causes something worse.

## 15. Common mistakes

**Beginner mistakes**
- Assuming replicas are "basically instant" and writing code that reads back immediately after writing.
- Testing against a single local database, where lag is structurally zero, so the bug cannot exist in any environment except production.
- Adding `sleep(300)` before the post-write read. Section 9 Part D: 16× worse than routing *and* it slows every user.
- Alerting on `now() - pg_last_xact_replay_timestamp()` and then silencing the alert when it fires nightly on an idle system — destroying the signal you needed.
- Quoting the *median* lag. The p50 is 20 ms and generates no tickets; the tail generates all of them.
- Believing `synchronous_commit = on` fixes this. It guarantees the WAL is flushed on the standby, not replayed. Durability is not visibility.
- Sending all reads to the leader "for safety", which converts a scaled read tier back into a single node and wastes the replicas entirely.
- Thinking read-after-write means other users see the write too. It does not, and that is exactly why it is affordable.

**Experienced-engineer mistakes**
- Comparing LSNs as strings. `'0/9FFFFFF' > '0/10000000'` lexicographically but is earlier in the log, producing sporadic, nearly undebuggable staleness.
- Accepting an unsigned LSN token from the client, enabling a one-line denial-of-service against the primary by claiming a far-future position.
- Implementing read-your-own-writes but not monotonic reads, then being puzzled by "my order disappeared when I refreshed" — a different anomaly needing the `max_observed_lsn` half of the fix.
- Setting `max_standby_streaming_delay = 30s` on a user-facing replica and not realising that a single analytics query can therefore create 30 seconds of lag with no change in write volume.
- Enabling `hot_standby_feedback = on` to stop query cancellations, then spending a month diagnosing unexplained table bloat on the *primary*.
- Leaving `wal_receiver_status_interval` at its 10-second default while routing reads on LSN comparisons, so routing decisions use positions up to 10 seconds out of date.
- Deploying wait-for-catch-up without a timeout, so a lag event becomes a connection-pool-exhaustion outage (Section 9 Part F).
- Trusting a replica that reports "0 seconds behind" while disconnected from the primary, because the naive query compares *received* to *replayed*.
- Splitting a service into two databases for scaling reasons and not realising that consistent-prefix guarantees, which a single WAL provided for free, have just been given up.
- Treating the nightly import's 48-second lag as a monitoring threshold to be raised rather than a workload to be batched.
- Measuring lag only on the primary's `sent_lsn`, which shows WAL leaving the building and nothing about replay.

## 16. Interview perspective

This is one of the highest-signal topics in distributed-systems interviewing, because almost every candidate proposes read replicas and almost none volunteer the consequence.

**How to use it:**

1. **Volunteer the anomaly when you introduce replicas.** *"I'll add read replicas for the read-heavy traffic — which immediately creates a read-after-write problem, since a user who updates their address and is redirected will often hit a replica that hasn't replayed the change yet."* Saying this *before* being asked is the single strongest move available on this topic.
2. **Name all three anomalies and distinguish them.** Read-your-own-writes (my write is missing), monotonic reads (data went backwards), consistent prefix (effect before cause). Most candidates know the first; naming the second and third with concrete examples is a clear seniority signal.
3. **Know that consistent prefix is free with a single leader.** *"With one WAL stream, replay is strictly ordered, so a replica is always an earlier point in history, never a mixture. Consistent prefix only breaks once I shard or split services — so sharding introduces an anomaly I didn't previously have."* This is subtle, correct, and rarely said.
4. **Give the staircase of fixes with costs.** *"Cheapest is reading from the leader for a second or so after a write — one cookie, and in our case it removed about 96 % of the anomalies for 1.75 % more leader reads. If I need a guarantee rather than a heuristic, I pin on LSN: capture the WAL position after the write and only use replicas that have replayed past it."*
5. **Quantify.** *"Our replay lag p50 was 20 ms but p99 was 400 ms, and the post-write read arrives about 120 ms later, so roughly 5–6 % of post-write reads were stale — a few thousand users a day."* A concrete number with the arithmetic behind it is memorable.
6. **Explain why the median is useless here.** The metric that matters is the lag distribution's tail, plus a counter of how often consistency routing had to fall back to the leader — lag expressed as user impact.
7. **Connect it to Lesson 33 deliberately.** *"Note that even full synchronous replication doesn't fix this, because `on` only guarantees the standby flushed the WAL, not replayed it. Only `remote_apply` closes that gap, and it puts replay on every write's critical path."*
8. **Know the boundary of the guarantee.** *"Read-your-own-writes is a session guarantee. If the requirement is that two different users see the same value simultaneously — last copy in stock, seat booking — this doesn't help and I need a serialising mechanism instead."*
9. **Name where it leads.** Session guarantees, causal consistency, linearizability, CAP/PACELC — showing you know this is a specific point on a larger map.

**Red flags interviewers watch for:**
- Adding read replicas with no mention of staleness at all.
- Saying "eventually consistent" as if it were a reassurance rather than a description of a user-visible defect.
- Proposing a fixed sleep or a client-side delay as the fix.
- Believing synchronous replication makes replicas immediately readable.
- Not distinguishing read-your-own-writes from global consistency, and therefore over-engineering towards strong consistency when a cookie would do.
- Being unable to say *how* they would measure lag, or quoting only an average.
- No awareness that reading from a replica can itself cause that replica to lag (replay conflicts).
- Proposing "just read from the leader" for everything, undoing the whole point of the replica tier, without acknowledging the capacity consequence.
- No fallback plan when no replica is fresh enough.

## 17. Knowledge check

Write your answers down before continuing.

**Conceptual**

1. A replica reports `now() - pg_last_xact_replay_timestamp() = 412 seconds`, and its byte lag measured on the primary is exactly 0. Explain precisely why both numbers are correct simultaneously, what the system must have been doing for this to occur, and what a team typically does next that causes them to miss a genuine ten-minute lag event two months later. Then state which two metrics you would actually alert on and what each one would fail to catch on its own.

2. Explain the difference between a read-your-own-writes violation, a monotonic-reads violation, and a consistent-prefix violation, with one concrete BookNook scenario each. Then explain why a single PostgreSQL leader streaming one WAL to its replicas cannot produce a consistent-prefix violation, and name two specific architectural changes that would introduce one.

3. BookNook runs `synchronous_commit = on` with one synchronous standby in another AZ, and its engineers conclude that read-after-write anomalies are therefore impossible on that standby. Explain, using the five replication stages from Lesson 33, exactly why they are wrong, what setting would make them right, and two distinct costs of that setting — one affecting write latency and one that could let a read query on the standby stall writes on the primary.

**Practical scenario**

4. BookNook adds a "Resend confirmation email" button to the order page. The flow is: `POST /orders/{id}/resend` writes a row to `email_jobs` on the leader and returns `202`; the page then polls `GET /orders/{id}/emails` every 500 ms to show the status; a worker reads `email_jobs` from a **read replica**, sends the email, and writes the result back to the leader.

   Current measurements: replay lag p50 = 20 ms, p99 = 400 ms, p99.9 = 3 s. The worker polls the replica every 200 ms.

   Describe every consistency anomaly in this design, identifying which actor experiences each one and what the user-visible symptom is — including at least one that causes **duplicate emails to be sent** and at least one where the user sees a status that goes backwards. Then propose a corrected design, stating for each change which specific fix from Section 4 you are applying, what guarantee it provides, and what it costs. Finally, explain why the worker's read is the most dangerous read in this system and why adding retries to it would make things worse rather than better.

**Calculation**

5. A different service, BookNook Reviews, has a measured replay-lag distribution of p90 = 150 ms and p99 = 900 ms. Post-write reads arrive 200 ms after the commit acknowledgement. The service handles 120,000 review writes per day and 9,000,000 reads per day. Each write is followed on average by 1.2 reads within 800 ms. Assume a read is stale if the lag at that instant exceeds the time elapsed since the commit, and model the tail as `P(lag > x) = P90 × (x / 150)^(−a)`.

   (a) Fit the exponent `a` from the two given percentiles, showing every step.
   (b) Compute the probability that a post-write read is stale with no fix, and the number of affected reads per day.
   (c) The team proposes a leader-read window of 800 ms. Assume 94 % of post-write reads arrive within that window and 6 % arrive later; bound the late arrivals using `P(lag > 800 ms)`. Compute the residual stale-read probability, the residual stale reads per day, and the improvement factor.
   (d) Compute the additional leader read load as a percentage of total reads. The leader has 8 % read headroom — does this fit? Show the comparison.
   (e) Compute the leader-read window that would be required to achieve fewer than 50 stale reads per day, assuming the same 94/6 arrival split applies at any window length. Comment in two sentences on whether that window is a sensible engineering choice and what you would do instead.

Show every step and state any additional assumptions you make.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 35.

---

**Progress checklist**

Phase 1 — Foundations: **complete** (Lessons 1–17)

Phase 2 — Scaling:
`[x] 1. Vertical scaling` · `[x] 2. Horizontal scaling` · `[x] 3. Stateless application servers` · `[x] 4. Load balancers` · `[x] 5. Layer 4 versus Layer 7` · `[x] 6. Load-balancing strategies` · `[x] 7. Health checks and failover` · `[x] 8. Forward and reverse proxies` · `[x] 9. Caching fundamentals` · `[x] 10. Cache placement and patterns` · `[x] 11. Cache invalidation and eviction` · `[x] 12. Distributed caching` · `[x] 13. CDNs and edge caching` · `[x] 14. Database replication fundamentals` · `[x] 15. Leader–follower replication and read replicas` · `[x] 16. Synchronous versus asynchronous replication` · `[x] 17. Replication lag and read-after-write consistency`
`[ ] 18. Database partitioning and sharding` ← next
`[ ] 19–22` not started

Phases 3–5: not started.

**NEXT LESSON: Phase 2, Topic 18 — Database partitioning and sharding**

