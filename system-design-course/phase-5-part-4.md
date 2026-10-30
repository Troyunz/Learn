# Lesson 102 — Design Video Streaming

*Phase 5, Topic 10*

## 1. Simple definition

**The problem.** Let a creator upload a video once and have millions of viewers watch it smoothly on devices ranging from a 4K television to a phone on a train — with fast start, no stalling, correct subtitles, and without leaking the content to pirates.

**Why this is three systems wearing one name.** A video platform is:

1. **An ingest and transcoding pipeline** — CPU-bound, batch, spiky, and expensive to get wrong because a re-encode of the whole catalogue costs six figures.
2. **A storage and packaging system** — the same video exists in 6–15 renditions, sliced into segments, in two packaging formats, several times over for DRM and audio tracks.
3. **A delivery system** — egress-dominated, CDN-shaped, where **bandwidth is 90 %+ of the bill** and the only lever that matters is the cache hit ratio.

The lesson is not "add a queue and a CDN". It is that the **cost and quality of a video product are decided by the bitrate ladder and the cache key**, both of which are decided long before a single viewer presses play.

Terms defined before use:

- **Rendition / bitrate ladder**: the set of (resolution, bitrate) versions encoded from one master (240p…4K).
- **Segment**: a 2–6 s slice of a rendition — the unit of download, caching and switching.
- **Manifest (playlist)**: the index telling the player which segments exist and at what bitrates (HLS `.m3u8`, DASH `.mpd`).
- **ABR (adaptive bitrate)**: the player's choice of rendition per segment based on measured throughput and buffer.
- **Packaging**: wrapping encoded segments into a delivery format (HLS/DASH, fMP4/TS).
- **Per-title encoding**: choosing the ladder for each title from its own complexity instead of one ladder for everything.
- **Glass-to-glass latency**: capture to display — the metric live streaming is judged on.
- **DRM**: key-based content protection with licence delivery per playback session.

The one sentence to carry out of this lesson: **transcoding decides the cost of storage and the ceiling of quality; the cache key decides 90 % of the bandwidth bill; and the player's buffer decides whether any of it feels good — so design those three, and the rest is plumbing.**

## 2. Why it exists

**Because egress dwarfs everything.** BookNook Video serves 12 M viewing hours/month. At an average 4 Mbit/s that is **21.6 PB/month of egress**, which at £0.02/GB is **£432,000/month** — against **£37,000/month** for the entire 1.7 PB catalogue in storage. A single percentage point of CDN cache hit ratio is worth **£4,300/month**; a bitrate ladder that is 20 % too generous is worth **£86,000/month**. Nothing else in the system is close.

**Because a re-encode is a one-way decision.** The ladder you choose at upload time determines what every future viewer receives. Change it and you re-encode the back catalogue — for 200,000 hours that is **£180,000 of compute and 9 days** (§9 step 3). That is why per-title encoding, codec choice and segment length are worth agonising over *before* the first upload, and why "we'll optimise later" is a decision to pay twice.

**Because the player, not the server, delivers quality.** Rebuffering and slow start are the metrics users feel — industry studies consistently find each additional second of startup time costs roughly 6 % of engagement, and a rebuffer costs far more. No amount of server optimisation helps if the player's ABR algorithm switches too aggressively or the segments are 10 seconds long.

**Because live and VOD are different products with different physics.** VOD can afford 10-day encodes and 6-second segments. Live must encode in real time, which makes the encode cost per hour of content **~40× higher** and forces a hard choice between latency and scale: 2-second segments triple the request rate and halve the cache efficiency.

**And because content protection is a business requirement with a technical shape.** Every playback session needs a licence; keys must rotate; a leaked key must not unlock the catalogue. Piracy is not solved by DRM but by raising its cost — and the mechanisms for that (short-lived signed URLs, device binding, watermarking) all live in this design.

## 3. Real-world analogy

**A master negative, a print shop, and a projectionist.**

**The master is the source of truth.** It is huge, expensive, and never shipped. It exists so that every future print can be made. That is the mezzanine file — and treating it as the thing you serve is the first mistake a video platform makes.

**The print shop makes prints in every gauge the cinemas can project.** 35 mm for the big houses, 16 mm for the small ones, a digital file for the rest. Each print is a **rendition**, and the set is the **ladder**. The shop does not make one print and let the small cinema squint: the difference in cost between a 4K print and a 240p print is enormous, and shipping the wrong one wastes money in both directions.

**The good print shop measures the film before choosing its gauges.** A film that is mostly dialogue on a static set needs a lower bitrate to look the same as a film full of confetti and fireworks. That is **per-title encoding**, and it is where 20–30 % of the bandwidth bill is won or lost.

**The projectionist switches reels as the projector struggles.** When the audience is watching a grainy 16 mm copy because the 35 mm reel jammed, and the 35 mm becomes available again, the projectionist cuts back. That is **ABR**: the player chooses per segment, and the *buffer* is what gives it the luxury of choosing well. Too small a buffer and the switch happens mid-scene, which is a visible glitch.

**And the print shop films every screening.** A camcorder in the audience produces a copy the shop cannot stop, but the shop can make each print **slightly, invisibly different**, so a leaked copy can be traced back to the screening it came from. That is forensic watermarking, and it is the only anti-piracy measure that survives the analogue hole.

The analogy's instructive point: **the shop's economics are dominated by the trucks, not the presses** — the presses run once, the trucks run every night. Egress is the trucks.

## 4. Detailed technical explanation

### 4.1 Requirements

**Functional.**

| # | Requirement |
|---|---|
| F1 | Upload a master up to 20 GB; resume interrupted uploads |
| F2 | Transcode to an adaptive ladder (240p–2160p) with multi-channel audio and subtitles |
| F3 | Serve VOD on HLS (and DASH where required), with DRM on premium content |
| F4 | Start playback in < 2 s p95; rebuffering < 0.5 % of watch time |
| F5 | Live streaming with a 6–10 s glass-to-glass target for events |
| F6 | Playback analytics per session (start time, rebuffers, bitrate switches) |
| F7 | Region-aware delivery, and lawful geo-restriction |
| F8 | Thumbnails, a scrubbing preview (trick-play) sprite, and captions |

**Non-functional.**

| # | Requirement | Number |
|---|---|---|
| N1 | Catalogue | 200,000 hours |
| N2 | Viewing | 12 M hours/month |
| N3 | CDN hit ratio | ≥ 95 % (each point is £4.3k/month) |
| N4 | Encode latency | VOD p95 < 90 min for a 90-min title; live < 8 s |
| N5 | Availability | 99.95 % playback start |
| N6 | Cost | egress ≤ £0.008 per viewing hour at 1080p |
| N7 | Protection | Premium titles: multi-DRM + signed URLs + watermarking |

### 4.2 Capacity estimation

```
  CATALOGUE STORAGE (the ladder's fault)
      per hour of content, at the ladder in §4.5:
        240p  0.30 Mbit/s →  135 MB/h
        360p  0.60        →  270 MB/h
        480p  1.20        →  540 MB/h
        720p  2.50        → 1.13 GB/h
        1080p 5.00        → 2.25 GB/h
        2160p 16.0        → 7.20 GB/h
        ──────────────────────────────
        total ladder      → 11.5 GB/h
      + audio (3 × 128 kbit/s) 0.18 GB/h + subtitles 2 MB/h
      ⇒ 11.7 GB/h × 200,000 h = 2.34 PB
      per-title encoding measured at −25 % ⇒ 1.76 PB
      + mezzanine masters (12 GB/h × 200k) = 2.4 PB on cold storage
      ⇒ ~£37,000/month of storage (standard) + £9,600 (archive masters)

  EGRESS (the dominant line)
      average viewing bitrate measured: 4.0 Mbit/s (mix of devices)
      ⇒ 1.80 GB per viewing hour
      12,000,000 h/month × 1.80 GB = 21.6 PB/month = 21.6 M GB
      at £0.02/GB ⇒ £432,000/month
      ⇒ 88 % of the total bill. EVERY design decision should be evaluated
        against this number.

  ENCODE COMPUTE
      per hour of content, all renditions: 42 CPU-minutes at H.264 1080p
      (measured with a 4× speedup from chunked parallelism on 2 s GOPs)
      ⇒ 200,000 h × 0.7 h = 140,000 CPU-hours once for the catalogue
      new content: 400 h/month of uploads × 0.7 = 280 CPU-hours/month
      ⇒ encode is a ONE-OFF cost; egress is FOREVER. Budget accordingly.

  REQUEST RATE
      6 s segments ⇒ each viewer requests 600 segments/hour
      ⇒ 12 M h/month = 4,000 h/viewing-hour… in requests:
        12,000,000 h × 600 = 7.2 B segment requests/month = 2,778/s
        peak 11,000/s
      ⇒ with a 95 % CDN hit ratio, origin sees 139/s at peak. The CDN
        absorbs the storm; the origin is a rounding error.
```

### 4.3 API

```
POST /v1/videos
     { "title": "…", "tier": "standard|premium", "drm": "widevine|fairplay|none",
       "subtitles": ["en", "hi"], "size_bytes": 8_912_345_678 }
  →  202 { "video_id": "v_…",
           "upload_urls": ["<presigned PUT part 1>", …],   # 64 MB parts
           "part_size": 67108864, "expires_at": "…" }

POST /v1/videos/{id}/complete
  →  202 { "status": "queued_for_transcode", "job_id": "j_…" }
GET  /v1/videos/{id}/status
  →  200 { "status": "transcoding|packaging|ready|failed",
           "renditions_done": 4, "renditions_total": 6, "eta": "…" }

GET  /v1/videos/{id}/playback
     Authorization: Bearer <user>
  →  200 { "hls": "https://cdn.bn.example/v/v_…/master.m3u8?exp=…&sig=…",
           "dash": "…", "drm": { "type": "widevine",
                                "license_url": "…", "token": "…" } }
     the URL expires in 6 h and is bound to a session id, so a copied URL
     leaks a session, not the catalogue.

POST /v1/live/streams       → 202 { "ingest_url": "rtmp://…", "key": "…" }
GET  /v1/live/{id}/playback  → 200 { hls_low_latency: "…", dvr_window_s: 7200 }

POST /internal/packaging/{video_id}/manifest      # pipeline → CDN origin push
```

### 4.4 Data model

```sql
CREATE TABLE videos (
  id            UUID PRIMARY KEY,
  owner_id      BIGINT NOT NULL,
  title         TEXT NOT NULL,
  duration_ms   BIGINT,
  tier          SMALLINT NOT NULL,          -- standard | premium
  status        SMALLINT NOT NULL,          -- uploaded|encoding|ready|failed
  mezzanine_key TEXT NOT NULL,             -- cold storage
  complexity    REAL,                      -- measured per-title encode score
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE renditions (                  -- one row per (video, rung, codec)
  video_id      UUID NOT NULL,
  rung_id       SMALLINT NOT NULL,         -- 0=240p … 5=2160p
  codec         SMALLINT NOT NULL,         -- h264|h265|av1
  width         INT, height INT, bitrate_kbps INT,
  segment_seconds SMALLINT NOT NULL,
  master_key    TEXT NOT NULL,             -- s3://bn-video/{id}/{rung}/h264/
  bytes         BIGINT,
  PRIMARY KEY (video_id, rung_id, codec)
);

CREATE TABLE playback_sessions (           -- the quality dataset
  session_id    UUID PRIMARY KEY,
  video_id      UUID NOT NULL,
  user_id       BIGINT,
  device_class  SMALLINT,
  started_at    TIMESTAMPTZ NOT NULL,
  startup_ms    INT,                       -- time to first frame
  watched_ms    BIGINT,
  rebuffer_ms   BIGINT, rebuffer_count INT,
  switches      INT,                       -- ABR switches (quality metric)
  avg_bitrate_kbps INT,
  ended_at      TIMESTAMPTZ
) PARTITION BY RANGE (started_at);         -- daily; the analytics source of truth

CREATE TABLE drm_keys (                    -- never in the same trust zone as
  video_id UUID, key_id BYTEA, period INT, -- the content
  wrapped_key BYTEA,                       -- wrapped by the KMS master key
  PRIMARY KEY (video_id, key_id)
);
```

**The content layout on the origin — the cache key lives here:**

```
  s3://bn-video/v/{video_id}/{codec}/{rung}/seg/{segment_index:06d}.m4s
  s3://bn-video/v/{video_id}/master.m3u8
  ⇒ IMMUTABLE PATHS. Nothing is ever overwritten; a re-encode produces a new
    video_id (v2) or a new codec/rung path. That is what allows
    `Cache-Control: public, max-age=31536000, immutable` and a 95 %+ hit
    ratio. A path that changes breaks caching; a cache that must revalidate
    costs 10× the requests.
```

### 4.5 Transcoding: the ladder, per-title encoding, and cost control

```
  THE NAIVE LADDER: one fixed set of rungs for every title.
      measured waste: 34 % of the bits are unnecessary on 71 % of the
      catalogue (talking-head interviews encoded at the bitrate a fireworks
      show needs).

  PER-TITLE ENCODING (chosen):
    1. encode a 90-second probe at 3 bitrates, measure VMAF/SSIM
    2. fit the rate-quality curve for this title
    3. choose the ladder that keeps quality ≥ target at the lowest bitrate
    ⇒ ladder per title, typically 20–34 % fewer bits for equal perceptual
      quality; BookNook measured −25 % overall (§9 step 2).
    cost: 4 minutes of extra encode per hour of content — trivial against
    the egress it saves.

  CHUNKED PARALLEL ENCODE:
      split at 2 s GOP boundaries ⇒ N workers encode N segments independently
      ⇒ wall-clock encode time divided by the worker count (measured 4× on
        12 workers for a 90-minute title: 84 min → 21 min)
      ⚠ segment boundaries must be keyframe-aligned; a non-aligned split
        produces visible artefacts at every join, and it is not obvious in a
        thumbnail review — spot-check joins at 3 random points, always.

  CODEC CHOICE (a cost/coverage trade, measured):
      H.264   universal, 100 % of devices   — baseline
      H.265   −38 % bits, 78 % of devices   — strong default for premium
      AV1     −52 % bits, 41 % of devices, 3× encode cost
      ⇒ pack H.264 + H.265 for everything; AV1 only where the audience
        supports it and the title is popular enough to amortise the encode.
        BookNook: AV1 on the top 4,000 titles = 61 % of viewing hours,
        saving 31 % of their egress for 9 % more encode.
```

### 4.6 Delivery: the cache key is 90 % of the bill

```
  THE REQUEST SHAPE
      11,000 segment requests/s at peak, 6 s segments
      ⇒ a popular title's 600 segments are requested by thousands of viewers
        within minutes; with 6 s segments and a 3-viewer/minute arrival rate,
        each segment is requested ~200 times before it ages out of the hot set.

  THE CACHE KEY is the full path: video_id + codec + rung + segment_index.
      a 95 % hit ratio at the edge follows from:
        · immutable paths (no revalidation, no purge)
        · long TTL (30 days) with no invalidation needed
        · popularity skew: the top 2,000 titles are 74 % of playback, and the
          top 40,000 are 96 %
        · segment-level caching (not whole-file), so a viewer who watches 3
          minutes caches 30 segments and a viewer who jumps caches only what
          they watch

  MULTI-CDN (measured): two CDNs, 70/30 split by measured quality per region,
      with automatic failover on a 2× error-rate deviation. Cost: 3 % more per
      GB, and 0.4 s of startup latency avoided during the one CDN incident
      that happens per year.

  ⚠ THE EGRESS TRAP: serving a title from the ORIGIN to the CDN costs the
    same money as serving it to a viewer. A 90 % hit ratio therefore does not
    save 90 % of egress — it saves the difference between CDN-per-GB
    (£0.02) and origin-per-GB (£0.09 with egress fees)… which means a hit
    ratio DROP is not linear in cost — it multiplies by 4.5× per unfilled
    request. Model it that way or the bill will surprise you (§9 step 1).
```

### 4.7 ABR and the quality metrics that matter

```
  THE PLAYER'S JOB
    · measure throughput on each segment download
    · keep a buffer target (measured optimum 24–30 s on broadband, 12 s on
      mobile, where a too-large buffer increases startup time and data cost)
    · choose the next rung: throughput-based rules over-react to a single
      slow segment; buffer-based rules (BOLA-style) are measurably better
    · start LOW and ramp: the first segment at 480p starts in 0.9 s; starting
      at 1080p takes 2.4 s on the median connection

  THE FOUR METRICS (they are the product)
    startup time      p50 0.9 s, p95 1.9 s (target ≤ 2 s)
    rebuffer ratio    rebuffered_ms / watched_ms, target < 0.5 %
    average bitrate   the quality the user actually got
    switch rate       rung changes per minute; > 4 is perceived as "flickering"
    ⇒ optimising average bitrate alone degrades rebuffering; the four must be
      traded off deliberately, and measured per device class and per country.
```

### 4.8 Live: the physics that cannot be optimised away

```
  LIVE IS NOT VOD WITH A SHORTER DELAY:
      encode must happen in real time ⇒ 1 hour of live needs 1 hour of
      encode capacity, not 21 minutes; and it must be PROVISIONED, because
      you cannot queue a live stream.
      segment duration defines the latency floor:
        10 s segments → 20–30 s glass-to-glass, best cache efficiency
         6 s segments → 12–18 s, the default
         2 s segments → 6–8 s (low-latency HLS), 3× the request rate and a
                        lower cache hit ratio (fewer viewers per segment
                        before it expires)
      ⇒ AT 2 s SEGMENTS THE ORIGIN LOAD IS 3× AND THE CDN HIT RATIO FALLS
        FROM 95 % TO 88 % — a 2.4× egress cost increase for 10 seconds of
        latency. State that trade explicitly; most stakeholders want both.

  LIVE CAPACITY: a 500,000-concurrent event at 3 Mbit/s = 1.9 Tbit/s of
      egress for 2 hours = 1.7 PB = £34,000 for one event. Pre-selling ads
      against a capacity reservation is how this is paid for.
```

### 4.9 Protection, and what DRM actually buys

- **Multi-DRM** (Widevine/FairPlay/PlayReady) with per-session licences; keys never in the same trust zone as the content, wrapped by a KMS.
- **Key rotation** every 2–10 minutes on premium titles, so a single leaked key unlocks a segment, not a film.
- **Signed, short-lived URLs bound to a session**, so a shared link expires and is traceable.
- **Forensic watermarking** per session (a few bits in the pixel domain), because DRM raises the cost of piracy but does not stop a screen recording — the only defence against the analogue hole is attribution.
- **The honest sentence for a design review:** "DRM protects the licence path, not the pixels. Budget for watermarking and takedowns; do not claim DRM stops piracy."

## 5. Request / data flow

```
 UPLOAD → PLAYBACK, end to end
 ─────────────────────────────────────────────────────────────────────────
 1. creator      POST /v1/videos → 64 MB multipart presigned PUTs
                 (the app never touches the bytes — L98's lesson, applied)
 2. ingest       parts assembled in object storage; master verified
                 (checksum, container parse, duration, audio tracks)
 3. probe        90 s probe encode at 3 bitrates → complexity score → ladder
 4. transcode    chunked at 2 s GOPs across 12 workers per rung, 6 rungs,
                 2 codecs ⇒ 84 min of content encodes in 21 min wall-clock
 5. package      segments + manifests (HLS + DASH), fMP4, CENC-encrypted for
                 premium; thumbnails and a trick-play sprite
 6. publish      push the manifest and a warm set of segments to the CDN
                 origin; status → ready
 7. viewer       GET /playback → signed HLS URL + DRM token
 8. player       fetch master.m3u8, pick the initial rung (measured: start at
                 480p), fetch 3 segments ahead, begin playing at 0.9 s
 9. steady       one segment per 6 s per viewer; ABR adjusts per segment;
                 the CDN serves 95 % of them
10. analytics    each segment fetch reports (session, rung, bytes, timing);
                 aggregated hourly into the quality dashboard

 PLAY A POPULAR TITLE FROM A COLD EDGE
 ─────────────────────────────────────────────────────────────────────────
 the first viewer in a region triggers 600 segment fetches from the origin
 over the title's duration; the 400th viewer in that region is served
 entirely from the edge.
 ⇒ the "warm-up" problem for a launch: pre-push the first 10 segments and
   the manifest to every PoP for a scheduled premiere (measured: startup
   p95 1.9 s → 0.8 s for the premiere, and 41 % less origin egress in the
   first 10 minutes).
```

## 6. ASCII diagram

```
  CREATOR                                                    VIEWERS
     │ upload (presigned, direct)                               │
     ▼                                                          ▼
 ┌─────────┐    ┌──────────────────────────┐        ┌────────────────────┐
 │ object  │───►│ TRANSCODE PIPELINE       │        │ CDN EDGE (multi-   │
 │ storage │    │ · probe → per-title      │        │ CDN, 95 % hit)     │
 │ mezz.   │    │   ladder                 │        │ · immutable paths  │
 └─────────┘    │ · chunked at 2 s GOPs    │        │ · 30-day TTL       │
                │ · 6 rungs × 2 codecs     │        │ · segment-level    │
                │ · H.264 + H.265 (+AV1    │        └─────────┬──────────┘
                │   on top 4k titles)      │                  │ 5 % miss
                └───────────┬──────────────┘                  ▼
                            ▼                        ┌────────────────────┐
                ┌──────────────────────────┐         │ ORIGIN (object      │
                │ PACKAGER                 │────────►│ storage + manifest  │
                │ · HLS + DASH, fMP4       │         │ service)            │
                │ · CENC DRM (premium)     │         └────────────────────┘
                │ · thumbnails, sprites    │
                └───────────┬──────────────┘         ┌────────────────────┐
                            │                        │ PLAYER (ABR)       │
                            ▼                        │ · start 480p       │
                ┌──────────────────────────┐         │ · buffer 24–30 s   │
                │ PUBLISHER → CDN warm-up  │         │ · buffer-based ABR │
                └──────────────────────────┘         └────────────────────┘

   WHERE THE MONEY GOES (12 M viewing hours/month):
     egress        21.6 PB × £0.02      = £432,000   ← 88 %
     storage       1.76 PB + masters     = £46,600
     encode        amortised + 280 CPU-h = £2,300
     CDN requests  2,778/s → 8.3 B/month = £4,100
     DRM licences  1.9 M sessions        = £3,800
     ─────────────────────────────────────────────────────
     total                               ≈ £488,800/month

   THE LADDER IS A COST DECISION, NOT A QUALITY ONE:
     1080p  5.0 Mbit/s ─┐
     720p   2.5         ├─ each rung's share of viewing hours × its bitrate
     480p   1.2         │  IS the egress bill. Adding a rung costs storage ×1
     360p   0.6         │  and saves egress only if viewers switch DOWN to
     240p   0.3 ────────┘  it, which they do only if the player is ABR-sane.
```

## 7. Detailed example

**BookNook Video**: author interviews, book trailers, and recorded book-club sessions.

```
  measured:
    200,000 hours of catalogue, 400 hours/month of new uploads
    12 M viewing hours/month, 6 s segments, 95.4 % CDN hit ratio
    average viewing bitrate 4.0 Mbit/s (device mix: 44 % TV, 31 % mobile,
    25 % desktop)
    startup p50 0.9 s / p95 1.9 s · rebuffer ratio 0.31 % · 1.4 switches/min
```

**The ladder decision, and what it changed:**

```
  v1 ladder (fixed, 7 rungs, no per-title encoding):
      storage 2.9 PB, average viewing bitrate 5.1 Mbit/s
      egress 12 M × 2.30 GB = 27.6 PB = £552,000/month
  v2 (per-title ladder, 6 rungs, H.265 for premium, AV1 on the top 4,000):
      storage 1.76 PB + masters
      average viewing bitrate 4.0 Mbit/s (−22 %)
      egress £432,000/month
  ⇒ £120,000/month saved, at a one-off cost of £180,000 of compute to
    re-encode the catalogue and 9 days of pipeline time. Payback: 1.5 months.
  ⚠ and the re-encode broke 12 titles: their segment boundaries were not
    keyframe-aligned, so the chunked encode produced audible glitches at the
    joins. Detection came from a viewer report, not from a test. See §11.4.
```

**The origin-egress surprise (the £61,000 month):**

```
  month 1: the CDN hit ratio was 95.4 % — exactly on target — and the bill
  was £61,000 ABOVE forecast.
  cause: the 4.6 % of misses were not uniformly distributed. A new-release
  launch drove cache misses on the same 600 segments from 40 PoPs
  simultaneously, and each miss costs 4.5× a hit (origin egress £0.09/GB
  versus CDN £0.02/GB).
  the arithmetic: 4.6 % of 21.6 PB = 993 TB of origin egress
      at £0.09/GB = £89,400 (versus £19,900 if it had been served by the CDN)
  fixes: (a) pre-warm the first 10 segments + manifest to every PoP for
  scheduled releases; (b) request collapsing at the origin (10 concurrent
  requests for the same segment ⇒ 1 origin fetch); (c) a per-pop hit-ratio
  alert, not a global average — the global number hid a 61 % hit ratio in
  the 6 PoPs serving the launch region.
  ⇒ after: launch-window hit ratio 99.1 %, egress back to forecast.
```

**The live event, and the honest latency trade:**

```
  a 500,000-concurrent author event, 2 hours:
      at 3 Mbit/s ⇒ 1.35 PB of egress ⇒ £27,000 for one event
      encode: 6 rungs × 8 workers provisioned for 3 h = £1,100
      low-latency mode (2 s segments) was requested by the product team:
        glass-to-glass 7 s instead of 16 s, but the hit ratio fell to 88 %
        and egress rose to 1.46 PB ⇒ +£2,200 for 9 seconds of latency
      decision: enable low-latency only for the interactive Q&A segment of
      the event (the last 20 minutes), and use 6 s segments for the rest.
      ⇒ 9 seconds where it mattered, at 12 % of the cost of applying it
        everywhere. THAT is the kind of decision the arithmetic enables.
```

## 8. Code or configuration example

```python
# ── Per-title ladder selection from a probe encode ──────────────────────
LADDER_RUNGS = [(240, 0.30), (360, 0.60), (480, 1.20),
                (720, 2.50), (1080, 5.00), (2160, 16.0)]   # (height, Mbit/s)

def choose_ladder(probe: ProbeResult, target_vmaf=93.0) -> list[Rung]:
    """probe.curve maps rung → measured VMAF for this title's content."""
    rungs = []
    for height, base_kbps in LADDER_RUNGS:
        if height > probe.max_height:
            continue
        # per-title adjustment: simple content needs fewer bits for the same
        # perceived quality; complex content needs more
        factor = probe.bitrate_factor(height)      # measured, 0.7 … 1.25
        kbps = round(base_kbps * 1000 * factor)
        if probe.vmaf_at(height, kbps) < target_vmaf - 1.0:
            kbps = round(kbps * 1.15)             # do not drop below quality
        rungs.append(Rung(height=height, bitrate_kbps=kbps))
    return rungs

# ── Chunked parallel encode: the 2 s GOP rule that must never be broken ─
async def transcode(mezz: Mezzanine, rung: Rung, codec: Codec):
    gop = await probe_gop_structure(mezz)         # keyframe positions
    if not gop.is_regular(seconds=2.0):
        # force a conform pass first: misaligned splits produce join glitches
        mezz = await conform(mezz, keyframe_every_s=2.0)
    chunks = split_at_keyframes(mezz, gop, every_s=2.0)
    await asyncio.gather(*[
        encode_chunk(ch, rung, codec) for ch in chunks   # 12 workers
    ])
    await concat_and_verify(chunks, spot_check_joins=3)  # §4.5
```

```hcl
# ── The manifest and segment paths: immutability is the whole cache story ─
# master playlist — short TTL, because it is small and may be re-published
resource "aws_s3_object" "master" {
  bucket       = "bn-video"
  key          = "v/${var.video_id}/master.m3u8"
  cache_control = "public, max-age=60"
}

# segments — LONG TTL, IMMUTABLE. A path never changes content.
resource "aws_s3_object" "segment" {
  for_each     = local.segments
  bucket       = "bn-video"
  key          = "v/${var.video_id}/${each.value.codec}/${each.value.rung}/seg/${each.value.index}.m4s"
  cache_control = "public, max-age=31536000, immutable"
  # ⚠ re-encodes MUST write to a new video_id (v2) or a new codec/rung path.
  #   Overwriting a segment under the same path poisons every edge cache for
  #   30 days and is invisible until users report artefacts.
}
```

```javascript
// ── Player: start low, buffer-based ABR, and report the four metrics ────
const player = new Player({
  manifest: hlsUrl,
  startLevel: 2,                  // 480p: 0.9 s startup vs 2.4 s at 1080p
  abr: {
    strategy: "buffer-based",     // measured better than throughput-based
    bufferTargetSec: isMobile ? 12 : 26,
    maxSwitchesPerMinute: 4,      // beyond this, users perceive flicker
    minRung: 0, maxRung: 5,
  },
});

player.on("segmentloaded", (e) => analytics.push({
  session: SESSION_ID, rung: e.level, bytes: e.bytes,
  downloadMs: e.durationMs, bufferMs: player.bufferLengthMs,
}));
player.on("rebufferstart", () => analytics.push({ session: SESSION_ID,
                                                  event: "rebuffer" }));
```

```yaml
# ── Operational rails: quality is a per-pop, per-device question ────────
cdn:
  providers: [primary, secondary]
  split: [0.7, 0.3]
  failover: { error_rate_deviation: 2.0, window_s: 120 }
  prewarm_on_publish: { segments: 10, populations: all }
  request_collapsing: true
quality_slo:
  startup_p95_ms: 2000
  rebuffer_ratio_max: 0.005
  switches_per_minute_max: 4
alerts:
  - alert: PopHitRatioLow
    expr: min by (pop) (cdn_hit_ratio) < 0.90
    for: 10m
    severity: page          # the GLOBAL average hides a regional launch miss
  - alert: OriginEgressSpike
    expr: rate(origin_egress_bytes[30m]) > 2 * on() group_left rate(origin_egress_bytes[7d] offset 7d)
    severity: page          # a collapsing or pre-warm regression
  - alert: TranscodeJoinArtefacts
    expr: transcode_join_spotcheck_failures > 0
    severity: ticket        # caught before publish, not by a viewer
  - alert: LiveLatencyBreach
    expr: live_glass_to_glass_seconds > 10
    severity: page
```

## 9. Numerical example

BookNook Video: 12 M viewing hours/month, 200,000-hour catalogue.

**Step 1 — egress, the number everything else answers to.**

```
  average viewing bitrate 4.0 Mbit/s
      ⇒ 4.0 / 8 = 0.5 MB/s ⇒ 1.80 GB per hour of viewing
  12,000,000 h × 1.80 GB = 21,600,000 GB = 21.6 PB/month
      at CDN £0.02/GB               = £432,000
      at origin £0.09/GB (the misses) see below
  origin share at a 95.4 % hit ratio = 4.6 % = 993,600 GB × £0.09 = £89,400
  ⇒ TOTAL EGRESS £521,400/month, of which 17 % is the 4.6 % of misses.
  ⇒ A HIT RATIO OF 95 % IS NOT "ALMOST ALL". In cost terms, 4.6 % of
    requests cost 17 % of the bill. Optimise the misses, not the hits.
```

**Step 2 — per-title encoding, in pounds.**

```
  fixed ladder: average viewing bitrate 5.1 Mbit/s → 2.30 GB/h
      12 M h × 2.30 = 27.6 PB → £552,000/month
  per-title ladder + H.265 + selective AV1: 4.0 Mbit/s → 1.80 GB/h
      12 M h × 1.80 = 21.6 PB → £432,000/month
  saving £120,000/month; re-encode cost £180,000 one-off
  ⇒ payback 1.5 months, then £1.44 M/year forever. The only reason teams do
    not do this is that the re-encode is a scary project — which is exactly
    why the ladder decision belongs at design time, not at optimisation time.
```

**Step 3 — storage, and why the ladder's shape matters less than you think.**

```
  per hour of content: 11.5 GB of ladder output
  200,000 h ⇒ 2.30 PB; per-title −25 % ⇒ 1.76 PB
      at £0.021/GB-month = £37,000/month
  masters: 12 GB/h × 200,000 = 2.4 PB on archive tier £0.004/GB = £9,600
  ⇒ STORAGE IS 8 % OF THE BILL. Doubling the ladder would cost £37,000/month
    more — significant, but an order of magnitude less than the £120,000 the
    bitrate change saved. THE BITRATE IS THE COST LEVER; THE RUNG COUNT IS
    NOT. Teams routinely optimise the wrong one.
```

**Step 4 — segment length: the three-way trade.**

```
  segment 6 s: requests 600/viewing-hour · hit ratio 95.4 % · live latency 16 s
  segment 2 s: requests 1,800/viewing-hour (3×) · hit ratio ~88 % ·
               live latency 7 s
  ⇒ VOD egress penalty at 2 s segments:
       misses 12 % instead of 4.6 % ⇒ origin egress 2.59 PB × £0.09 =
       £233,000 instead of £89,400 ⇒ +£143,600/month
  ⇒ AND 3× the request volume: 2,778/s → 8,334/s at peak (still fine, but
    the per-request CDN cost triples: £12,300/month)
  ⇒ FOR VOD, 6 s IS CORRECT and 2 s is a £155,000/month mistake.
    For LIVE, 2 s is a product requirement, so the cost is justified — but
    ONLY for the live path. That is why segment length is a per-path setting.
```

**Step 5 — the encode cost, honestly bounded.**

```
  catalogue re-encode: 200,000 h × 0.7 CPU-h/h = 140,000 CPU-hours
      at £0.032/CPU-hour (spot) = £4,480 — trivially cheap
      wall clock: 140,000 CPU-h / 2,000 parallel vCPUs = 70 h = 3 days
      BookNook measured 9 days because of rate limits and a conform pass
      on 12% of titles (§7).
  new content: 400 h/month × 0.7 = 280 CPU-h = £9/month
  ⇒ ENCODE IS NOT THE COST PROBLEM. Budget generously for quality here
    (a 15 % higher bitrate for one problematic title is pennies) and
    fiercely for egress.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **Fixed ladder** | Simple, predictable | 25–34 % more bits on most content | Tiny catalogues, uniform content |
| **Per-title ladder** | −25 % egress | Probe encode, complexity in the pipeline | **Always above ~1,000 hours** |
| **H.264 only** | Universal coverage | 38 % more bits than H.265 | Legacy device support matters most |
| **H.264 + H.265** | −38 % bits for 78 % of devices | Two encodes, two storage sets | **The default** |
| **AV1 everywhere** | −52 % bits | 3× encode cost, 41 % coverage | Only for the most-watched titles |
| **6 s segments** | Best cache efficiency | 12–18 s live latency | VOD, and live where latency is not the point |
| **2 s segments** | 6–8 s live latency | 3× requests, 88 % hit ratio | Live events, interactive Q&A only |
| **Immutable segment paths** | 95 %+ hit ratio, no invalidation | A re-encode needs a new path | **Always** |
| **Origin-served misses uncollapsed** | Simple | 4.6 % of requests = 17 % of the bill | Never |
| **Single CDN** | Simpler contracts | One incident takes you down | Small catalogues |
| **Multi-CDN** | Regional quality, failover | 3 % more per GB, two integrations | Global audiences |
| **DRM everywhere** | Protection | Complexity, licence cost, device coverage | Premium only — it costs more than it saves on free content |
| **Watermarking** | Attribution after a leak | Per-session processing cost | Any premium content |

## 11. Failure scenarios

**11.1 — The launch-driven origin bill.** A global average 95.4 % hit ratio hid a 61 % ratio in six PoPs and cost £61,000 in one month (§7). **Fix:** per-PoP hit ratio alerts, pre-warm on publish, request collapsing at the origin.

**11.2 — Overwritten segments.** A re-encode wrote to the same paths; every edge served a mix of old and new segments for 30 days, and 41 titles had visible glitches at segment joins. **Fix:** immutable paths, a new `video_id` per encode generation, and a publish-time assertion that no path is being reused.

**11.3 — Non-keyframe-aligned chunked encode** (§7). 12 titles had audible artefacts at joins. **Fix:** a conform pass to force regular 2 s GOPs, plus an automated spot-check of 3 joins that fails the job before publish.

**11.4 — Segment-length change breaking the cache.** A team set 2 s segments globally "for better latency" and added £155,000/month of egress (§9 step 4). **Fix:** segment length is a per-path configuration with a cost model attached to the pull request template.

**11.5 — ABR thrash.** A throughput-based ABR on a congested mobile network switched rungs 11 times a minute; users described "flickering". **Fix:** buffer-based ABR, a switch-rate cap, and switch rate as a first-class quality metric.

**11.6 — DRM outage.** The licence service was unavailable for 6 minutes; premium titles would not start anywhere. **Fix:** a licence-service SLO of four nines, a cached short-lived licence where policy allows, and a documented "free-tier fallback" for non-premium content so an outage degrades rather than stops.

**11.7 — Live stream that could not be provisioned.** A 500,000-concurrent event arrived with 40 minutes' notice; the pipeline had 12 workers, and the encode fell behind, producing a corrupted manifest for 9 minutes. **Fix:** capacity reserved per event, a smoke test at 2× expected concurrency, and a pre-encoded fallback state ("please stand by") that is itself a valid stream.

**11.8 — Captions out of sync.** Subtitles were packaged with a hard-coded offset, but one title's master had a non-zero start offset; captions drifted 4 s. **Fix:** per-title subtitle timing is derived from the mezzanine's actual start time, and verified by sampling.

## 12. Alternatives

| Instead of | Consider | When |
|---|---|---|
| Own transcoding | A managed transcoding service | Volume is low or spiky; you need many codecs |
| Own packaging | The transcoder's packaged output | You do not need DRM or multi-format |
| Own CDN math | A CDN with volume pricing + a live-quality SLA | You are not a video company |
| AV1 | H.265 | Coverage matters more than bits |
| A 6-rung ladder | 4 rungs (240/480/1080/2160) | Mobile-first audiences converge on few rungs |
| Per-title encoding | A simpler 2-tier complexity classification | Pipeline maturity is low |
| Live streaming | Premiere-then-VOD | The event tolerates a 5-minute delay |
| DRM | Signed URLs + watermarking | The content is not premium |

## 13. When to use it

- **Per-title encoding and selective codecs** for any catalogue above a few thousand hours.
- **Immutable segment paths with long TTLs** — always, without exception.
- **Multi-CDN** once a single CDN incident would be visible to customers.
- **Short segments** only where the latency requirement justifies the cost.
- **DRM and watermarking** on premium content, with the limitations stated.
- **Per-PoP quality and hit-ratio monitoring**, not global averages.

## 14. When *not* to use it

- **A custom pipeline for a handful of videos** — a managed service is cheaper.
- **AV1 for a long-tail catalogue** — the encode cost never amortises.
- **DRM on free content** — the licence path costs more than the leakage.
- **2-second segments for VOD** — £155,000/month for latency nobody notices.
- **A 12-rung ladder** — the storage and packaging complexity buys nothing.
- **A global hit-ratio target** without a per-PoP floor — it hides the outage.

## 15. Common mistakes

1. **Optimising storage instead of bitrate** (§9 step 3).
2. **A fixed ladder** and a catalogue-wide re-encode later (§9 step 2).
3. **Mutable segment paths** (§11.2).
4. **A global cache hit ratio** with no per-PoP floor (§11.1).
5. **No request collapsing** at the origin (§11.1).
6. **Segment length chosen for latency on a VOD path** (§9 step 4).
7. **Non-keyframe-aligned chunked encoding** (§11.3).
8. **Throughput-based ABR** with no switch-rate cap (§11.5).
9. **Starting playback at the highest rung** (2.4 s vs 0.9 s startup).
10. **No pre-warm for scheduled launches** (§11.1).
11. **No capacity reservation for live events** (§11.7).
12. **Treating DRM as anti-piracy** rather than licence control (§4.9).
13. **Storing masters in the hot tier** — they belong in archive.
14. **No per-device quality breakdown**, so a mobile-only regression looks like noise.

## 16. Interview perspective

**What is actually being tested.** Whether you know that video is an **egress** problem, that the bitrate ladder is the dominant cost lever, and that the cache key and player buffer decide quality. Strong candidates: per-title encoding, immutable paths, per-PoP hit ratios, startup-low-then-ramp, and the live/VOD segment trade. Weak candidates: "transcode to several resolutions and put it on a CDN".

**Answers that land well:**

- *"Where does the money go?"* — "Egress, 88 % of it. Twelve million viewing hours a month at four megabits is twenty-one point six petabytes, four hundred and thirty thousand pounds. The catalogue in storage is thirty-seven thousand. So the ladder and the cache key are the two decisions that matter, and both are made before anyone presses play."
- *"How do you choose the bitrate ladder?"* — "Per title, from a probe encode. A talking-head interview and a fireworks show need very different bitrates for the same perceived quality, and a fixed ladder wastes about a third of the bits on seventy percent of the catalogue. Per-title encoding saved us twenty-two percent of average bitrate — a hundred and twenty thousand a month — for a one-off hundred-and-eighty-thousand-pound re-encode. Payback in six weeks."
- *"Why do you care so much about cache hit ratio?"* — "Because a miss costs four and a half times a hit: origin egress versus CDN. At a 95.4 % hit ratio, 4.6 % of requests were 17 % of the bill. And the global number hid a 61 % ratio in the six PoPs serving a launch region — which cost us sixty-one thousand pounds in one month. So we alert per PoP, pre-warm on publish, and collapse concurrent requests at the origin."
- *"Live streaming — what changes?"* — "Physics. You cannot queue a live encode, so capacity is provisioned per event. And segment duration becomes a three-way trade: two-second segments give seven-second latency but triple the request count and drop the hit ratio to 88 %, which for the whole catalogue would be a hundred and fifty-five thousand a month. We enable it only for the interactive part of an event."
- *"What about DRM?"* — "It protects the licence path, not the pixels. We rotate keys every few minutes and bind signed URLs to a session, and we watermark per session, because the only defence against a screen recording is attribution."

**Common interview tasks:** estimate storage, egress and encode cost; design the pipeline; choose a ladder; design the cache/packaging layout; explain ABR and the quality metrics; design live; explain DRM's limits.

**Red flags interviewers watch for:**

- No egress arithmetic.
- Mutable segment paths or a cache that revalidates.
- A single fixed ladder with no cost discussion.
- No ABR or quality metrics.
- Live treated as VOD.
- DRM described as stopping piracy.
- No per-PoP monitoring.
- Optimising storage when egress is 88 % of the bill.

## 17. Knowledge check

**Conceptual**

1. Explain why egress, not storage, dominates a video platform's cost, and compute the egress for 12 M viewing hours at 4 Mbit/s. Then explain why a cache miss costs more than 1/0.046 of a hit.
2. Explain per-title encoding: what is measured, how the ladder is chosen, and why the saving is expressed as a bitrate rather than a file size. Then explain why the rung count matters far less than the bitrate.
3. Explain the segment-length trade for VOD and for live, with the cache and request-rate arithmetic for 6 s versus 2 s. Then state the decision rule.

**Practical scenario**

4. Design BookNook Video: 200,000-hour catalogue, 12 M viewing hours/month, 44 % TV viewers, 31 % mobile, DRM on premium titles, live events up to 500,000 concurrent, a 2 s startup target and a 0.5 % rebuffer target. Give the requirements and capacity tables, the ladder design with per-title encoding, the codec policy, the pipeline with its parallelism and verification, the packaging and cache-key design, the multi-CDN and pre-warm strategy, the ABR and player configuration, the live architecture with its latency trade, the DRM and watermarking design, the quality dashboard, the cost model, and the alerts. Then state what you would do differently for a mobile-only audience of 5 M users.

**Calculation**

5. 12 M viewing hours/month. (a) Compute the egress in PB at 4.0 Mbit/s and at 5.1 Mbit/s. (b) Compute the monthly cost at £0.02/GB for each and the difference. (c) Given a 95.4 % hit ratio and an origin price of £0.09/GB, compute the origin egress and its share of the total bill. (d) Compute the saving from raising the hit ratio to 99 %, and state what it implies for the origin.

6. A 200,000-hour catalogue. (a) Compute storage for a ladder totalling 11.5 GB per hour. (b) Apply a 25 % per-title saving and compute the new figure. (c) Compute the one-off encode cost for the catalogue at 0.7 CPU-hours per content hour and £0.032/CPU-hour, and the wall-clock time at 2,000 parallel vCPUs. (d) Compute the monthly egress saving from a 22 % bitrate reduction at 12 M viewing hours, and state the payback period.

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
  - [x] 9. Design a news feed (L101)
  - [x] **10. Design video streaming (L102) ← you are here**
  - [ ] 11. Design ride sharing (L103)
  - [ ] 12. Design e-commerce and inventory (L104)
  - [ ] 13. Design payment processing (L105)
  - [ ] 14. Design a metrics and logging platform (L106)

**NEXT LESSON: Phase 5, Topic 11 — Design ride sharing**

---

---

# Lesson 103 — Design Ride Sharing

*Phase 5, Topic 11*

## 1. Simple definition

**The problem.** A rider asks for a trip from A to B. Within seconds, find the best available driver nearby, offer it to them, get an acceptance, and then track the driver's live location through pickup and drop-off — while never assigning one driver to two riders, and never over-promising an arrival time.

**Why it is the design where one invariant dominates.** Every other property of a ride-sharing system can degrade gracefully except one: **a driver must be on at most one active trip at any instant.** Two riders told "your driver is 3 minutes away" when it is the same driver is not a latency bug, it is a stranded person on a street corner. That invariant is a **single-writer problem** (L78, L91 §7.2), and the whole dispatch design is built around enforcing it under concurrency, network partitions and retries.

Terms defined before use:

- **Geo cell**: a small area (H3/S2/geohash cell) used to index moving drivers.
- **Candidate set**: the drivers within a radius of the pickup, retrieved from the geo index.
- **Offer**: a time-limited invitation to a specific driver, with a TTL.
- **Claim / assignment**: the atomic transition making a driver's state `en_route`.
- **Fencing**: a token that lets the system reject a stale claim from a partitioned dispatcher (L78 §4.5).
- **ETA**: estimated time of arrival, from a routing service with live and historical traffic.
- **Surge multiplier**: a price multiplier for a cell reflecting demand over supply.
- **Trip state machine**: `requested → matched → en_route → arrived → in_trip → completed | cancelled`.
- **Location firehose**: the continuous stream of driver positions (the highest-volume write in the system).

The one sentence to carry out of this lesson: **dispatch is a geo-query followed by a single-writer claim — the query is an index problem, the claim is a correctness problem, and the two must never be mixed, because the one thing a rider cannot tolerate is a promise that turns out to be false.**

## 2. Why it exists

**Because the invariant is enforced, not hoped for.** With 200,000 active couriers and 42 match attempts per second at peak, a naive "read availability, then write assignment" races: BookNook measured a **0.4 % double-assignment rate** in its first version — 180 stranded deliveries per day. The fix is not a lock around the whole dispatcher (that serialises everything) but a **conditional single-row update** plus a lease.

**Because the location firehose is the real write load.** 200,000 drivers × one position every 4 seconds = **50,000 writes/s**, against 14 trip requests/s. That is a 3,500:1 ratio: the system is 99.97 % a telemetry ingestion problem and 0.03 % a matching problem. Getting that ratio backwards produces an architecture that is expensive and slow in the wrong places.

**Because ETA accuracy *is* the product.** A rider accepts a "3 minutes away" and plans around it: they wait outside, they leave the office, they choose not to hail something else. Measured: BookNook's ETA p50 error was 2.1 minutes with no traffic model, and 41 % of riders who saw an ETA over 8 minutes cancelled after 3 minutes of waiting. A traffic-aware ETA cut p50 error to 38 seconds and cancellations by 19 %.

**Because two-sided marketplaces need explicit supply signals.** Price (surge) is the only fast lever on supply; it must be computed per small area, smoothed to avoid shocks, and bounded so that the product does not become a lottery. That is a data problem with an ethics dimension, and it belongs in the design.

**And because the safety and audit trail must survive everything.** A trip has a location trail, a driver identity, a fare breakdown and a dispute path. That trail must be durable, append-only and searchable years later — while the location firehose itself is ephemeral (a position older than a few minutes is useless for matching). **Two different retention policies for what looks like the same data** is a distinction most first drafts miss.

## 3. Real-world analogy

**A taxi rank with a dispatcher and an "engaged" light.**

The dispatcher has a board showing every cab in the city. The board is only as fresh as the last radio call — a car that has not called in for four minutes is *probably* where you last heard it. That is the **location firehose and its staleness bound**: a position is a fact with a timestamp, and its usefulness decays.

When a fare arrives, the dispatcher looks at the board for the nearest cabs, then **radios one**. The cab has a fixed window to answer, and while the offer is open the dispatcher does not promise that cab to anyone else. That is the **offer with a TTL** — designed so that a silent cab does not hold up the queue forever.

**And the "engaged" light is the invariant.** Once a cab accepts, the light goes on and it is off the board until the drop-off. The dispatcher physically cannot give it a second fare. Two dispatchers (two radio rooms) both looking at the same board is where the analogy earns its place: **unless the light is set by the cab itself, atomically, one of them will double-book.**

The analogy's last and most important lesson: **the dispatcher is not the one who decides.** The cab does — by taking the fare and setting the light. The system's job is to make that decision atomic and to reconcile the board afterwards, not to maintain a perfect model of the world. Every ride-sharing design that treats the dispatcher's in-memory view as authoritative eventually double-books a car.

## 4. Detailed technical explanation

### 4.1 Requirements

**Functional.**

| # | Requirement |
|---|---|
| F1 | Request a trip from a pickup to a drop-off, with a quoted fare and ETA |
| F2 | Match to a nearby available driver within 60 s p99 |
| F3 | Live tracking from acceptance through pickup to drop-off |
| F4 | Cancel (rider before pickup; driver before acceptance) with a policy |
| F5 | Fare computed from distance, time and a surge multiplier, quoted up front |
| F6 | Driver earnings, ratings, and a trip history |
| F7 | Safety: trip sharing, an SOS path, and a location trail retained for audit |
| F8 | Dispatch must never assign one driver to two active trips |

**Non-functional.**

| # | Requirement | Number |
|---|---|---|
| N1 | Match latency | p50 < 10 s, p99 < 45 s |
| N2 | Location freshness | ≤ 4 s from device to the geo index |
| N3 | ETA accuracy | p50 error < 60 s, p90 < 3 min |
| N4 | Scale | 200,000 active drivers, 1.2 M trips/day, 40 cities |
| N5 | Double-assignment | **zero** (structural, not statistical) |
| N6 | Availability | 99.95 % for trip requests during a regional outage |
| N7 | Retention | Live positions 5 min; trip trail 7 years |

### 4.2 Capacity estimation

```
  LOCATION FIREHOSE
      200,000 active drivers × 1 position / 4 s = 50,000 writes/s
      position payload 48 B (lat, lon, heading, speed, accuracy, ts)
      ⇒ 2.4 MB/s ingress; at peak (evening) 1.6× = 80,000/s
      ⇒ IN-MEMORY geo index (per city), with a 5-minute history to a
        time-series store, and NO durable row per position.

  TRIP REQUESTS
      1,200,000 trips/day = 13.9/s average, peak × 3 = 42/s
      as a fraction of all writes: 42 / 80,000 = 0.05 %
      ⇒ the matching service is tiny; the geo service is the fleet.

  GEO INDEX SIZE
      H3 resolution 8 ≈ 0.74 km² per cell
      40 cities × ~1,400 cells = 56,000 cells
      200,000 drivers spread unevenly: a dense downtown cell may hold 400,
      a suburban cell 2
      ⇒ 200,000 driver records × ~120 B in memory = 24 MB of live state —
        trivially small. The cost is INGEST THROUGHPUT, not memory.

  CANDIDATES PER MATCH
      3-cell ring (k=1, 7 cells) at res 8 covers ~2.2 km radius...
      measured: to reach a 3 km pickup radius in dense areas you need k=2
      (19 cells) in the suburbs and k=1 in the centre
      ⇒ candidate counts measured: p50 34, p95 118, p99 210
      ⇒ ETA must be computed for the top ~12 by great-circle distance only
        (a routing call is 8 ms; 210 calls is 1.7 s — far too slow).

  TRIP TRAIL
      1.2 M trips/day × 25 min × 1 position/5 s = 360 M positions/day
      at 48 B = 17 GB/day = 6.3 TB/year, retained 7 years = 44 TB
      ⇒ a columnar/time-series store, and the reason the retention split
        (N7) exists.
```

### 4.3 API

```
POST /v1/trips/quote
     { "pickup": {lat, lon}, "dropoff": {lat, lon}, "product": "standard" }
  →  200 { "quote_id": "q_…", "fare": {"amount": 1240, "currency": "GBP",
             "surge": 1.4, "breakdown": {...}},
           "eta_pickup_s": 210, "eta_trip_s": 1_140,
           "expires_at": "…" }             # a quote is valid for 120 s

POST /v1/trips
     Idempotency-Key: <uuid>
     { "quote_id": "q_…" }
  →  202 { "trip_id": "t_…", "state": "requested" }        # async matching

WS  /v1/trips/{id}/stream          # state changes, driver location, ETA
  ←  { "state": "matched", "driver": {...}, "vehicle": {...},
       "eta_pickup_s": 180 }
  ←  { "driver_location": {lat, lon, heading}, "eta_pickup_s": 150 }
  ←  { "state": "arrived" } → { "state": "in_trip" } → { "state": "completed",
       "fare_final": {...} }

POST /v1/trips/{id}/cancel   { "reason": "…" }
POST /v1/drivers/offers/{offer_id}   { "action": "accept"|"decline" }
POST /internal/locations     # driver app → geo service, batched, 4 s
```

### 4.4 Data model

```sql
-- The trip: a state machine with an append-only event log.
CREATE TABLE trips (
  id             UUID PRIMARY KEY,
  rider_id       BIGINT NOT NULL,
  driver_id      BIGINT,                 -- NULL until claimed
  state          SMALLINT NOT NULL,      -- 0 requested … 6 completed
  pickup         GEOGRAPHY(POINT, 4326) NOT NULL,
  dropoff        GEOGRAPHY(POINT, 4326) NOT NULL,
  quote_id       UUID NOT NULL,
  surge          NUMERIC(3,2) NOT NULL,
  fare_quoted_pence INT,
  fare_final_pence  INT,
  requested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  matched_at     TIMESTAMPTZ, picked_up_at TIMESTAMPTZ,
  completed_at   TIMESTAMPTZ, cancelled_at TIMESTAMPTZ,
  cancel_reason  TEXT,
  claim_token    BIGINT                  -- monotonic fencing token (L78)
) PARTITION BY RANGE (requested_at);

CREATE TABLE trip_events (               -- append-only; the audit trail
  trip_id     UUID NOT NULL,
  seq         BIGINT NOT NULL,
  event       SMALLINT NOT NULL,
  actor       SMALLINT NOT NULL,         -- rider|driver|system
  at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  payload     JSONB,
  PRIMARY KEY (trip_id, seq)
);

-- THE INVARIANT, expressed as a constraint the database enforces:
-- a driver may have at most one trip in a non-terminal state.
CREATE UNIQUE INDEX one_active_trip_per_driver
  ON trips (driver_id)
  WHERE state IN (1, 2, 3, 4);           -- matched|en_route|arrived|in_trip
  -- ⚠ this index is the last line of defence. The DISPATCH path must also
  --   be atomic, because a constraint violation on the write path is a 500
  --   to a rider, not a recovery.

-- Driver state in the operational store (mirrored from the geo index)
CREATE TABLE drivers (
  id            BIGINT PRIMARY KEY,
  city_id       INT NOT NULL,
  state         SMALLINT NOT NULL,        -- offline|available|offered|engaged
  current_trip  UUID,                     -- the single active trip
  claim_token   BIGINT NOT NULL DEFAULT 0,
  last_location GEOGRAPHY(POINT, 4326),
  last_seen_at  TIMESTAMPTZ
);

CREATE TABLE offers (                     -- time-limited invitations
  id            UUID PRIMARY KEY,
  trip_id       UUID NOT NULL,
  driver_id     BIGINT NOT NULL,
  expires_at    TIMESTAMPTZ NOT NULL,     -- 10 s
  state         SMALLINT NOT NULL,        -- offered|accepted|declined|expired
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 4.5 The geo index: cells, rings, and the staleness bound

```
  WHY CELLS AND NOT A SPATIAL DATABASE
      "SELECT * FROM drivers WHERE ST_DWithin(location, $1, 3000)" on
      200,000 rows at 50,000 updates/s is impossible: every position update
      would churn a spatial index, and every query would scan.
  ⇒ instead: an in-memory map from H3 cell → set of available drivers,
      updated on each position write (an O(1) move between two sets).

  THE INDEX
      cell_key = H3(cell=res 8)          # ≈ 0.74 km²
      drivers_in_cell: map[cell] → linked set of driver ids (+ a small
                       struct: location, heading, last_seen)
      on a position update:
          old_cell = index.cell_of(driver)
          new_cell = h3(lat, lon, 8)
          if old_cell != new_cell: move between sets         # O(1)
          else: update the struct in place                    # O(1)
      ⇒ 80,000 updates/s is a hash-map workload, one shard per city, and it
        fits on a few nodes per region.

  THE QUERY
      candidates = union(drivers_in_cell[c] for c in k_ring(center_cell, k))
      choose k adaptively: start k=1 (7 cells, ~2.2 km), widen to k=2
      (19 cells) if fewer than 20 candidates, then k=3 (37 cells).
      measured: p50 candidates 34 (k=1 suffices 81 % of the time), p99 210.
      STALENESS FILTER: drop any driver whose last_seen_at is older than
      15 s (a driver whose app died is not available).
      ⇒ THE STALENESS BOUND IS A CORRECTNESS REQUIREMENT: without it, a
        driver who went offline 4 minutes ago is a candidate, and every offer
        to them expires — measured 11 % of offers wasted, and a p99 match
        time of 71 s.
```

### 4.6 The claim: single-writer, fenced, and atomic

```
  THE BUG WE MUST NOT SHIP (measured in v1: 0.4 % of trips):
      dispatcher A: SELECT ... WHERE state='available' → driver 7741
      dispatcher B: SELECT ... WHERE state='available' → driver 7741
      A: UPDATE drivers SET state='engaged', current_trip=t1
      B: UPDATE drivers SET state='engaged', current_trip=t2   ← overwrites
      ⇒ two riders, one driver, and driver 7741's app has one trip.
  Kind: a read-then-write race (L51 §4.3), across a network.

  THE FIX (three layers, all needed):
    (1) CONDITIONAL UPDATE — the claim is a single atomic statement:
          UPDATE drivers
             SET state = 'engaged', current_trip = $trip,
                 claim_token = claim_token + 1
           WHERE id = $driver AND state = 'available'
             AND last_seen_at > now() - interval '15 seconds'
          RETURNING claim_token;
        ⇒ 0 rows affected = someone else won; the offer simply expires.
        ONE row, ONE statement, no transaction spanning two services.
    (2) UNIQUE INDEX on active trips per driver (§4.4) as the backstop that
        makes the invariant true even if a code path forgets (1).
    (3) FENCING TOKEN — the trip carries the claim_token, and every later
        state transition (`arrived`, `in_trip`, `completed`) is conditional
        on the token matching:
          UPDATE trips SET state=4 WHERE id=$t AND claim_token=$tok
        ⇒ a dispatcher that was partitioned, retried, and now believes it
          owns the driver CANNOT transition a trip it no longer owns.

  THE OFFER PROTOCOL, with numbers
      · rank candidates by great-circle ETA (a cheap heuristic), take the top
        12, compute routing ETAs for those 12 only (12 × 8 ms = 96 ms)
      · offer IN PARALLEL to the top 3 for 10 s, first acceptance wins
          — measured better than sequential offers: p50 match 7 s vs 26 s,
            at the cost of 1.9 declined offers per match (drivers tolerate it;
            riders do not tolerate a 26-second match)
      · on acceptance, the conditional UPDATE decides; the other two offers
        are cancelled by an event, and if they also accepted (a race inside
        the 10 s window) the conditional update already rejected them
      · no candidate accepted ⇒ widen the ring, raise surge, or fail the
        request with an honest message after 45 s
```

### 4.7 ETA, and why it must be honest

```
  THREE ESTIMATORS, in increasing accuracy and cost:
      great-circle / 30 km/h        ±4.2 min p50 error   (free; for ranking)
      routing API (static traffic)  ±2.1 min             (8 ms, £0.004/call)
      routing + live + historical   ±38 s                (14 ms, £0.006/call)
  ⇒ use the cheap one for RANKING 200 candidates, the accurate one for the
    TOP 12 and for the rider-facing promise.

  THE PRODUCT CONSEQUENCE, measured:
      ETA error p50 2.1 min → 38 s:
        cancellations 19 % lower
        "driver was late" complaints 1,100/week → 210/week
        driver-side "unfair rating" appeals 340/week → 90/week
  ⇒ ETA accuracy is not a routing-feature, it is the trust mechanism. Budget
    for it: 42 requests/s × 2 calls × £0.006 = £0.50/s ≈ £1,300/month, and
    it paid for itself in reduced support load alone.
```

### 4.8 Surge: a control system with an ethics surface

```
  COMPUTATION (per H3 res-7 cell ≈ 5.2 km², every 2 minutes):
      demand  = open requests in the last 10 min in the cell
      supply  = available driver-minutes in the last 10 min in the cell
      ratio   = demand / max(supply, 1)
      multiplier = clamp(1 + 0.6 × log2(max(ratio, 1)), 1.0, 3.0)
      smoothing: multiplier = 0.65 × previous + 0.35 × target
          ⇒ prevents the "price jumps while I am looking at it" shock, which
            measured 6.1 % of quotes being re-quoted higher within 30 s
      floors/caps: never above 3.0; never below 1.0; no surge for essential
          medical trips (a policy switch, not a technical one)

  WHAT IT IS FOR: in the short run, price is the only lever that moves supply
  toward demand. In the long run, it is a signal to drivers about where to go.
  ⇒ and it has a measurable downside: BookNook's surge experiment at a 2.8×
    cap showed a 34 % drop in ride requests in the surged cell, of which 61 %
    never re-requested within 24 hours. THE DESIGN DECISION IS THEREFORE
    "surge to 1.8× and accept a longer match time, or surge to 2.8× and lose
    demand" — and that is a business call, documented with numbers, not a
    threshold buried in code.
```

### 4.9 The trip state machine and the audit trail

```
  requested ──► matched ──► en_route ──► arrived ──► in_trip ──► completed
      │            │            │           │           │
      └─ cancelled ┘            └─ cancelled┘           └─ cancelled (policy)
  · every transition is written to trip_events (append-only) AND applied to
    trips (the current state). The event log is the truth for disputes; the
    row is the truth for queries.
  · transitions are conditional on the expected current state AND the claim
    token, so a duplicated or out-of-order message cannot skip a state:
        UPDATE trips SET state=3, picked_up_at=now()
        WHERE id=$t AND state=2 AND claim_token=$tok
  · the location trail is written every 5 s during a trip, to a separate
    store with a 7-year retention (N7), because a safety review may need it
    in a year — while the live index forgets in 5 minutes.
  ⇒ THE TWO RETENTIONS ARE A DESIGN DECISION: sending the trail to the live
    index would make a 5-minute working set cost 44 TB.
```

## 5. Request / data flow

```
 A TRIP, END TO END
 ─────────────────────────────────────────────────────────────────────────
 t+0      rider: POST /quote → fare + ETA (routing + surge for the cell)  [140 ms]
 t+0      rider: POST /trips (idempotency key) → 202, state=requested
 t+40 ms  matcher: centre cell = H3(pickup, 8); k=1 ring → 27 candidates
          filter last_seen ≤ 15 s → 22 available
          great-circle sort → top 12; routing ETA for 12 → 96 ms
          offer to the top 3 IN PARALLEL, TTL 10 s
 t+3.1 s  driver 7741 accepts → conditional UPDATE wins (claim_token 41)
          other two offers cancelled; the trip moves to `matched`
 t+3.2 s  rider's socket receives `matched` + driver details + ETA 180 s
 t+9 s    driver starts moving; positions every 4 s update the geo index and
          the rider's ETA every 10 s
 t+186 s  driver arrives: state=arrived (conditional on token 41)
 t+205 s  rider boards: state=in_trip
 t+1,350 s drop-off: fare_final = distance × rate × surge; state=completed;
          the driver's `one_active_trip_per_driver` slot frees; the driver
          becomes `available` in the geo index within 4 s
 ⇒ p50 request→matched 7 s, p99 38 s. The whole matching path is 140 ms of
   work; the rest is driver response time, which is a human delay.

 FAILURE: no acceptance in 10 s
     widen to k=2 → 61 candidates → offer to the next 3 → up to 3 waves
     ⇒ measured: 1.6 % of requests need a second wave, 0.2 % a third,
        0.04 % fail with "no drivers nearby" and a surge-honest message.
```

## 6. ASCII diagram

```
  DRIVER APPS (200,000)                    RIDER APPS
        │ position every 4 s                    │ quote / request
        ▼                                      ▼
  ┌──────────────────────┐            ┌────────────────────────┐
  │ LOCATION INGEST      │            │ TRIP SERVICE           │
  │ 50k–80k writes/s     │            │ · quote (routing+surge)│
  │ · per-city shard     │            │ · trip state machine   │
  │ · batched per driver │            │ · sockets to riders    │
  └──────────┬───────────┘            └───────────┬────────────┘
             │ in-memory only                     │ match request
             ▼                                    ▼
  ┌──────────────────────┐            ┌────────────────────────┐
  │ GEO INDEX (per city) │◄───────────┤ MATCHER                │
  │ H3 res 8: 0.74 km²   │ candidates │ · k-ring expansion     │
  │ cell → driver set    │            │ · last_seen ≤ 15 s     │
  │ 24 MB of live state  │            │ · great-circle rank    │
  │ staleness 15 s       │            │ · ETA for top 12 only  │
  └──────────────────────┘            └───────────┬────────────┘
                                                  │ offer to top 3,
        ┌─────────────────────┐                   │ TTL 10 s, parallel
        │ 5-min position ring │◄──────────────────┘
        │ (feeds trip trail)  │
        └─────────────────────┘
                                                  ▼
  ┌───────────────────────────────────────────────────────────────┐
  │ THE CLAIM — one atomic statement, and it is the whole invariant│
  │  UPDATE drivers SET state='engaged', current_trip=$t,          │
  │         claim_token = claim_token + 1                           │
  │   WHERE id=$d AND state='available' AND last_seen_at > now()-15s│
  │   RETURNING claim_token;                                        │
  │  + UNIQUE INDEX (driver_id) WHERE state IN (1,2,3,4)  ← backstop │
  │  + fencing token on every later transition                       │
  └───────────────────────────────────────────────────────────────┘

  THE FUEL GAUGE:
     location writes   50,000/s  ██████████████████████████ 99.95 %
     trip requests         42/s  ▏                          0.05 %
  ⇒ optimise the firehose (batching, in-memory, per-city shards), not the
    matching path. A candidate who shards the matcher has misread the load.
```

## 7. Detailed example

**BookNook Courier**: same-day book delivery by independent couriers in 40 cities.

```
  measured:
    200,000 active couriers, 1.2 M deliveries/day, 40 cities
    50,000 position writes/s average, 80,000/s peak
    match p50 7 s, p99 38 s
    ETA p50 error 38 s (after the traffic model), 2.1 min before
    v1 double-assignment rate 0.4 % = 180 stranded deliveries/day
```

**The double-assignment incident and the fix:**

```
  2025-05, v1 with read-then-write dispatch and two dispatcher replicas:
    · 180 deliveries/day assigned to a courier who was already on a delivery
    · the second rider waited 11 minutes on average before re-booking
    · 41 % of affected riders did not order again within 30 days
    · cost: ~£74,000/month of lost repeat business, plus support load
  fix, in the order it was applied:
    1. the conditional UPDATE (§4.6) — eliminated 99.6 % of cases
    2. the UNIQUE INDEX on active trips — eliminated the remainder, which
       came from a retry path that wrote `matched` without checking
    3. fencing tokens — eliminated a 1-in-40,000 case during a 40-second
       network partition between the two dispatcher zones (L65 §4.6)
  measured after: zero double-assignments in 14 months, and the constraint
  violation counter has been flat at 0 since (it is alerted on at > 0).
```

**The stale-candidate problem (the quieter bug):**

```
  symptom: 11 % of offers were sent to couriers whose app had been killed;
  every one expired after 10 s, so p99 match time was 71 s.
  cause: the geo index decayed only when a position arrived; a courier who
  went offline simply stopped updating, and remained "available" forever.
  fix: `last_seen_at > now() − 15 s` in BOTH the candidate query and the
  conditional claim, plus an explicit `offline` signal from the app on
  shutdown, plus a sweeper that removes couriers unseen for 5 minutes.
  measured after: wasted offers 11 % → 0.4 %, p99 match 71 s → 38 s.
  ⇒ the cheapest latency fix in the whole system was a WHERE clause.
```

**The surge shock, and the policy that replaced it:**

```
  2025-11: surge without smoothing. Quotes could jump between the quote
  screen and the confirm screen.
    · 6.1 % of quotes were re-quoted higher within 30 s
    · social media activity ("surge lottery"), 2,400 mentions, 14 press
      pickups, 1 regulatory enquiry
  fix: (a) smoothing (0.65 previous + 0.35 target), (b) a 90-second quote
  lock at the quoted multiplier, (c) a 3.0 hard cap, (d) an explicit no-surge
  policy for medical-essential deliveries.
  measured after: re-quotes 6.1 % → 0.2 %, mentions → 60/quarter.
  ⇒ AND the business cost, measured honestly: the smoothing delayed supply
    response, so match time in surged cells rose 4 s. That is the trade, and
    it was accepted explicitly.
```

## 8. Code or configuration example

```python
# ── The claim: one atomic statement, and it decides everything ──────────
CLAIM_SQL = """
UPDATE drivers
   SET state = 'engaged', current_trip = $2,
       claim_token = claim_token + 1
 WHERE id = $1
   AND state = 'available'
   AND last_seen_at > now() - interval '15 seconds'
RETURNING claim_token
"""

async def claim_driver(driver_id: int, trip_id: UUID) -> int | None:
    """Returns the fencing token, or None if this dispatcher lost the race."""
    async with db.transaction():
        tok = await db.fetchval(CLAIM_SQL, driver_id, trip_id)
        if tok is None:
            return None
        # the trip row records the token so later transitions can be fenced
        updated = await db.execute(
            """UPDATE trips SET driver_id=$2, state=1, matched_at=now(),
                                claim_token=$3
               WHERE id=$1 AND state=0""", trip_id, driver_id, tok)
        if updated == "UPDATE 0":          # trip was cancelled mid-claim
            raise ClaimAborted()            # the transaction rolls back the
        return tok                          # driver claim too — atomically

# ── Every later transition is fenced: a partitioned dispatcher cannot act ─
async def transition(trip_id: UUID, expect_state: int, new_state: int, tok: int):
    n = await db.execute(
        """UPDATE trips SET state=$3,
                  picked_up_at = CASE WHEN $3=4 THEN now() ELSE picked_up_at END,
                  completed_at = CASE WHEN $3=6 THEN now() ELSE completed_at END
           WHERE id=$1 AND state=$2 AND claim_token=$4""",
        trip_id, expect_state, new_state, tok)
    if n == "UPDATE 0":
        metrics.inc("dispatch.stale_transition")   # alerted; must stay ~0
        return False
    await events.append(trip_id, new_state)        # append-only audit trail
    return True
```

```python
# ── Candidate retrieval: rings, staleness, and cheap ranking ────────────
async def candidates(pickup: LatLon, city: City, limit=12):
    centre = h3.latlng_to_cell(pickup.lat, pickup.lon, 8)
    for k in (1, 2, 3):
        cells = h3.grid_disk(centre, k)
        found = geo_index.drivers_in(cells, city)
        found = [d for d in found if d.last_seen_age_s <= 15]   # §4.7
        if len(found) >= 20 or k == 3:
            break
    # great-circle is free; routing is 8 ms ⇒ only the top 12 get a real ETA
    found.sort(key=lambda d: haversine(pickup, d.location))
    top = found[:12]
    etas = await asyncio.gather(*[routing.eta(d.location, pickup) for d in top])
    return sorted(zip(top, etas), key=lambda x: x[1])[:limit]
```

```python
# ── Parallel offers: three at once, one winner, fenced by the claim ─────
async def match(trip):
    cand = await candidates(trip.pickup, trip.city)
    for wave in range(3):
        offers = await create_offers(trip, cand[wave*3:(wave+1)*3], ttl_s=10)
        winner = await first_acceptance(offers, timeout_s=10)   # race
        if winner:
            tok = await claim_driver(winner.driver_id, trip.id)
            if tok:                       # we won the atomic claim
                await cancel_offers(offers, except_=winner)
                await notify_rider(trip, winner, tok)
                return
            # someone else claimed this driver narrowly: try the next best
        await expire_offers(offers)
    await fail_no_drivers(trip)           # honest message, surge-aware
```

```yaml
# ── The rails: invariants as alerts, and surge as policy ────────────────
dispatch:
  geo_resolution: 8
  candidate_radius_rings: [1, 2, 3]
  staleness_seconds: 15
  offer_ttl_seconds: 10
  parallel_offers: 3
  max_waves: 3
  match_deadline_seconds: 45
surge:
  cell_resolution: 7
  window_minutes: 10
  smoothing: 0.65
  cap: 3.0
  quote_lock_seconds: 90
alerts:
  - alert: DoubleAssignmentDetected
    expr: increase(dispatch_constraint_violations_total[5m]) > 0
    severity: page            # the invariant; must be structurally impossible
  - alert: StaleTransition
    expr: rate(dispatch_stale_transition_total[5m]) > 0.1
    severity: page            # a partitioned dispatcher is acting
  - alert: WastedOfferRate
    expr: rate(dispatch_offers_expired_total[5m]) / rate(dispatch_offers_total[5m]) > 0.05
    severity: ticket          # staleness bound or app-level offline signal broken
  - alert: MatchLatencyBreach
    expr: histogram_quantile(0.99, rate(dispatch_match_seconds_bucket[5m])) > 45
    severity: page
  - alert: EtaErrorHigh
    expr: histogram_quantile(0.5, rate(eta_error_seconds_bucket[1h])) > 60
    severity: ticket          # the promise is drifting
```

## 9. Numerical example

BookNook Courier: 200,000 couriers, 1.2 M trips/day, 40 cities.

**Step 1 — the load ratio that dictates the architecture.**

```
  position writes   200,000 × (1/4 s)          = 50,000/s  (peak 80,000/s)
  trip requests     1,200,000 / 86,400         = 13.9/s   (peak 42/s)
  ratio                                        = 3,597 : 1
  ⇒ 99.97 % of the write load is telemetry. Any design that stores positions
    durably before indexing them is wrong by two orders of magnitude:
       durable write per position: 50,000 rows/s ⇒ 4.3 B rows/day ⇒ ~600 GB
       /day of time-series that is 99.99 % never queried again.
    ⇒ the position goes to an in-memory index first, and to durable storage
      only when it is part of a TRIP trail (§4.9).
```

**Step 2 — the match funnel, wave by wave.**

```
  candidates: p50 34, p95 118, p99 210
  offers per match: 3 (parallel) × waves
  measured acceptance: 71 % of offers are accepted within 10 s by a courier
      in the top 3; 22 % need wave 2; 6 % wave 3; 1 % fail
  ⇒ expected offers per successful match:
      0.71 × 3 + 0.22 × 6 + 0.06 × 9 + … ≈ 2.13 + 1.32 + 0.54 = 3.99
      i.e. ~4 offers per match, of which 1.9 are declined or expire.
  at 42 matches/s peak: 168 offers/s ⇒ 42 courier interactions/s.
  ⇒ THE COST OF PARALLEL OFFERS IS 1.9 WASTED DRIVER ATTENTION PER TRIP.
    That is accepted deliberately: sequential offers would make p50 match
    26 s instead of 7 s, and riders tolerate declines far less than delay.
```

**Step 3 — the geo index, sized.**

```
  cities 40; H3 res 8 cells per city ≈ 1,400 average (a city is not square;
  measured 1,100–3,900) ⇒ 56,000 cells
  couriers 200,000, distributed with a 6× density ratio between downtown and
  suburbs ⇒ a downtown cell holds up to 400, a suburban cell 1–2
  ⇒ total live state: 200,000 × 120 B = 24 MB per REGION (not per city —
    a whole region's active couriers fit in one process's RAM).
  ⇒ the geo index is not a scaling problem at all; it is a hotness problem
    per city. 40 cities × 2 replicas × a 200 MB process = trivial cost.
  THE CONSTRAINT IS INGEST: 80,000 updates/s into a per-city single writer.
    ⇒ shard ingest by driver_id % 4 within a city, with each shard owning a
      partition of the cell sets, and a query fanning out to the 4 shards
      (4 × 19 cells, 0.4 ms each) — measured p99 query 2.9 ms.
```

**Step 4 — ETA accuracy pays for itself, in numbers.**

```
  ROUTING CALL VOLUME, counted properly (candidates do NOT get routing):
      candidate ranking          great-circle only          = 0 calls
      top-12 ETAs per match      42 matches/s × 12          =   504/s
      rider promise              reuses the winner's ETA    =     0
      en-route refreshes         in-progress trips ÷ 30 s
          in-progress = 42/s × 18 min = 45,360 trips
          refreshes   = 45,360 / 30  = 1,512/s
      ─────────────────────────────────────────────────────────────
      total                                                 ≈ 2,016/s
                                                    = 174 M calls/day

  AT A PER-CALL VENDOR PRICE OF £0.006:  174 M × £0.006 = £1,044,000/DAY
      ⇒ absurd by three orders of magnitude. The vendor pricing model is an
        ARCHITECTURAL CONSTRAINT, and it must be checked before the design
        is committed.

  SELF-HOSTED ROUTING (pre-computed contraction hierarchies + a live
  traffic overlay, refreshed every 2 min):
      2,016 calls/s × 14 ms of CPU = 28.2 cores ⇒ 32 cores with headroom
      ≈ £1,760/month, plus a graph-refresh job
  ⇒ £1,044,000/day versus £58.7/day: SELF-HOSTING IS ROUGHLY 18,000×
    CHEAPER at this volume. The break-even is around 3 calls/s, which is why
    every real ride-sharing platform self-hosts its routing.
  ⇒ AND the accuracy is the same, because the model is ours either way:
    great-circle for ranking (±4.2 min), routing for the promise (±38 s with
    live traffic, ±2.1 min without).
  ⇒ THE PRODUCT PAYOFF of accuracy, measured: p50 ETA error 2.1 min → 38 s
    cut cancellations 19 % and "driver was late" complaints from 1,100/week
    to 210/week — which at a £3.10 handling cost is £2,758/week saved, i.e.
    £11,900/month against a £1,760/month routing bill.
```

**Step 5 — the retention split, in terabytes.**

```
  live positions:       200,000 couriers × 21,600 positions/day… = 4.3 B/day
      kept 5 minutes in the index → RAM only → 0 TB of durable storage
  trip trail:           1.2 M trips × 25 min × 12 positions/min = 360 M/day
      48 B each = 17 GB/day = 6.3 TB/year → 44 TB at 7 years
      at £0.021/GB-month = £930/month, and it is a COMPLIANCE asset
  ⇒ sending the live firehose to durable storage as well would be
    4.3 B × 48 B = 206 GB/day = 75 TB/year = 527 TB at 7 years = £11,000/
    month for data nobody will ever query. TWO RETENTION POLICIES, ONE
    DESIGN DECISION.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **In-memory geo index** | 80k updates/s, sub-3 ms queries | Rebuild on restart; no history | **Always** for live matching |
| **Spatial database for matching** | One system, durable | Cannot sustain the update rate | Tiny cities, or batch assignment |
| **Conditional update for claiming** | Zero double-assignments | Needs the invariant expressed in SQL | **Always** |
| **Distributed lock for claiming** | Familiar | Latency, lease expiry, fencing needed anyway | Only with a consensus store |
| **Parallel offers (3)** | p50 7 s instead of 26 s | 1.9 wasted driver interactions per trip | Accepted whenever riders value speed |
| **Sequential offers** | Fairer to drivers; fewer declines | p50 match 26 s | Driver supply is scarce and riders patient |
| **Great-circle ranking** | Free, 0.2 ms | ±4 min ETA error | Candidate ranking only |
| **Routing for top 12 only** | ±38 s ETA at sane cost | Two-stage ranking complexity | Always |
| **Self-hosted routing** | Unbounded call volume | Ops burden, graph updates | > ~10 calls/s |
| **Per-call routing API** | No ops | Cost scales with matches | Tiny volume |
| **Surge with smoothing** | No price shocks | Delayed supply response (+4 s match) | **Always** |
| **Unsmoothed surge** | Fastest supply response | Quote churn, press, regulators | Never |
| **Two retention policies** | 44 TB instead of 527 TB | Two stores | Always |

## 11. Failure cases

**11.1 — Double assignment** (§7). 180 stranded deliveries/day, £74,000/month of lost repeat business. **Fix:** conditional update, a unique index backstop, and fencing tokens; alert at any constraint violation.

**11.2 — Stale candidates** (§7). Dead apps stayed "available"; wasted offers 11 %, p99 match 71 s. **Fix:** a `last_seen` bound in both the query and the claim, an explicit offline signal, and a sweeper.

**11.3 — Geo index loss on restart.** A node restart emptied a city's index; matches failed for 90 s until positions re-arrived. **Fix:** warm the index from the last 60 s of the position ring buffer, and only accept traffic when ≥ 90 % of recent drivers are indexed.

**11.4 — Surge shock** (§7). 6.1 % quote churn, press coverage, a regulator enquiry. **Fix:** smoothing, a 90-second quote lock, a cap, and an explicit no-surge policy category.

**11.5 — The dispatcher partition.** Two zones split for 40 s; both offered, one claimed, and a stale dispatcher then tried to mark the trip `arrived`. **Fix:** fencing tokens, which made the stale transition a no-op; the counter alerted and was investigated.

**11.6 — Trip state skip.** A duplicated `in_trip` message from a driver app moved a trip from `arrived` to `in_trip` twice, double-billing a rider. **Fix:** transitions conditional on the expected state (not just the target), and an idempotency key on driver events.

**11.7 — ETA promise made by the wrong estimator.** The rider screen used the great-circle ETA while the internal records used routing; riders saw 3 minutes and the system expected 8. **Fix:** one ETA source for the promise, stored on the trip for later comparison (`eta_quoted` vs `actual`), and the error is a monitored metric.

**11.8 — City-level cascade.** One city's ingest shard saturated at 80,000/s, so positions lagged 40 s, so candidates were stale, so offers expired, so match time hit 3 minutes. **Fix:** per-shard backpressure that drops to 8-second positions (accepting lower accuracy) rather than falling behind (L76).

## 12. Alternatives

| Instead of | Consider | When |
|---|---|---|
| Real-time dispatch | Scheduled/pre-booked dispatch | Deliveries, not immediate rides |
| Own geo index | A managed geospatial service | Low volume, or no sharding needs |
| Own routing | A managed routing API | Under ~10 calls/s |
| Parallel offers | A broadcast-to-all-available model | Very dense supply, tiny markets |
| Surge | Fixed pricing with queueing/waitlists | Regulated markets, or brand-sensitive products |
| Real-time push | Batch assignment in waves | Courier logistics with wide time windows |

## 13. When to use it

- **An in-memory cell index** for any high-frequency moving-object workload.
- **A conditional single-row update plus a fencing token** for every "assign exactly one worker to one job" problem — it generalises to delivery, field service and hospital scheduling.
- **Parallel time-boxed offers** whenever human response time dominates latency.
- **Two retention policies** whenever a firehose and a compliance trail share a data shape.

## 14. When *not* to use it

- **A distributed lock for claiming** — a conditional update is simpler and needs fencing anyway.
- **Routing API calls for candidate ranking** — the call volume is orders of magnitude too high (§9 step 4).
- **Durable storage for every position** — 600 GB/day of data nobody reads.
- **Unsmoothed surge** — the product damage is measurable (§11.4).
- **A spatial database on the hot matching path**.
- **Surge as a growth strategy** — 61 % of surged-away riders never returned (§4.8).

## 15. Common mistakes

1. **Read-then-write claiming** (§11.1).
2. **No fencing token**, so a partitioned dispatcher can act on a stale claim (§11.5).
3. **No staleness bound** on candidates (§11.2).
4. **Durable writes for the position firehose** (§9 step 5).
5. **Optimising the matcher when the load is 99.97 % telemetry** (§9 step 1).
6. **Routing API calls per candidate** (§9 step 4).
7. **Two ETA sources** for promise and record (§11.7).
8. **Unsmoothed surge** (§11.4).
9. **State transitions conditional on the target but not the source** (§11.6).
10. **Treating the dispatcher's view as authoritative** (§3).
11. **One retention policy for firehose and trail** (§9 step 5).
12. **No per-city backpressure**, so a lagging ingest shard degrades matching (§11.8).
13. **No warm-up after a geo index restart** (§11.3).
14. **No `eta_quoted` vs `actual` metric**, so ETA drift is invisible.

## 16. Interview perspective

**What is actually being tested.** Whether you can express the **single-writer invariant** as an atomic operation, whether you know the write load is telemetry rather than matching, and whether ETA/pricing are treated as product-critical systems. Strong candidates: conditional update + unique index + fencing, cell index with a staleness bound, parallel time-boxed offers, pre-computed routing. Weak candidates: "use a spatial index and a lock".

**Answers that land well:**

- *"How do you assign a driver?"* — "One atomic statement: update the driver row to engaged where it is currently available and seen in the last fifteen seconds, returning a fencing token. Zero rows updated means someone else won and the offer just expires. That is backed by a unique index so a driver can have at most one non-terminal trip, and every later transition is conditional on the token, so a dispatcher that was partitioned and retried cannot touch a trip it no longer owns. Before that, we double-booked 0.4 % of trips — a hundred and eighty stranded deliveries a day."
- *"What's the hard part of scaling it?"* — "It is not matching. Two hundred thousand couriers sending a position every four seconds is eighty thousand writes a second; trip requests are forty-two. So the design is an in-memory cell index with O(1) moves between cells, no durable row per position, and a five-minute ring buffer — because storing the firehose would be six hundred gigabytes a day that nobody ever queries. The trip trail is stored for seven years, but that is a different data set at seventeen gigabytes a day."
- *"How do you get ETAs right?"* — "Two-stage. Great-circle distance ranks two hundred candidates for free; only the top twelve get a real routing ETA. And the routing engine is self-hosted, because at seven hundred calls a second a per-call vendor API would cost more than the annual revenue of the feature — the difference was about six hundred times. Accuracy matters because the promise is the product: going from two minutes of p50 error to thirty-eight seconds cut cancellations nineteen percent."
- *"Surge?"* — "A control system, with an ethics surface. Computed per five-square-kilometre cell every two minutes, smoothed so quotes do not jump under the rider's finger, capped at three, with a ninety-second quote lock and a no-surge policy for essential deliveries. And we measured the downside honestly: at a 2.8 multiplier, 34 % of requests in the surged cell disappeared, and 61 % of those riders never came back within a day. So we cap lower and accept a slightly longer match."

**Common interview tasks:** design matching; enforce the one-driver invariant; design the geo index; compute the firehose; design offers and timeouts; design ETA and surge; design the trip state machine and audit trail; find the bottleneck.

**Red flags interviewers watch for:**

- Read-then-write assignment.
- No fencing or idempotency on state transitions.
- A spatial database on the hot path.
- No staleness handling for driver positions.
- Durable storage for every position.
- Surge with no smoothing, cap or policy.
- No ETA accuracy metric.
- Optimising the matcher while ignoring the firehose.

## 17. Knowledge check

**Conceptual**

1. Express the ride-sharing invariant as an atomic database operation, and explain why it is a single-writer problem rather than a distributed-lock problem. Then explain the three layers of defence and what each catches that the others miss.
2. Explain the cell-based geo index: the update, the query, the ring expansion, and the staleness bound. Then explain why a spatial database cannot serve the same workload at 80,000 updates/s.
3. Explain the two-stage ETA design and why costing forces it. Then explain why ETA accuracy is a product mechanism rather than a routing feature, with the measured consequences.

**Practical scenario**

4. Design BookNook Courier: 200,000 couriers, 1.2 M deliveries/day, 40 cities, a match target of p99 45 s, ETA p50 error under 60 s, no double assignments, and 7-year trip retention. Give the requirements and capacity tables, the geo index design with sharding and warm-up, the offer protocol with its arithmetic, the claim and fencing design, the state machine, the ETA pipeline with its cost, the surge policy with its measured downside, the two retention policies, the alerts, and the incident replays. Then state what you would change if the couriers were salaried employees rather than independent contractors.

**Calculation**

5. 200,000 couriers sending a position every 4 s. (a) Compute writes/s at average and at 1.6× peak. (b) Compute the durable storage per day if every position were written at 48 B, and the 7-year total. (c) Compute the in-memory footprint for 120 B per driver, and state whether it fits on one node. (d) Compute the ratio of position writes to trip requests at 1.2 M trips/day.

6. A matcher offers to three couriers in parallel with a 10 s TTL. 71 % of matches are accepted in wave 1, 22 % in wave 2, 6 % in wave 3, and 1 % fail. (a) Compute the expected offers per successful match. (b) Compute offers/s at 42 matches/s. (c) Compare with sequential offers where the first offer is accepted 71 % of the time but each wave costs a full 10 s: compute p50 match time. (d) State the trade and which you would ship, with the measured justification.

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
  - [x] 9. Design a news feed (L101)
  - [x] 10. Design video streaming (L102)
  - [x] **11. Design ride sharing (L103) ← you are here**
  - [ ] 12. Design e-commerce and inventory (L104)
  - [ ] 13. Design payment processing (L105)
  - [ ] 14. Design a metrics and logging platform (L106)

**NEXT LESSON: Phase 5, Topic 12 — Design e-commerce and inventory**

---

---

# Lesson 104 — Design E-Commerce and Inventory

*Phase 5, Topic 12*

## 1. Simple definition

**The problem.** Let people browse a catalogue, put things in a basket, pay, and have the goods arrive — while **never selling an item you do not have**, never charging for an order you cannot fulfil, and surviving a flash sale that sends 30× normal traffic at a single product.

**Why it is the capstone of the course.** Every earlier lesson appears here at once: caching and CDN (Phase 2), transactions and isolation (Phase 3), sagas and idempotency and outbox (Phase 4), and the design patterns of this phase. The interesting part is not the catalogue — that is a cache — it is the **reservation**: a small piece of state that turns "I might buy this" into "this is held for you", and which must be correct under concurrency, failure, abandonment and flash sales.

Terms defined before use:

- **SKU**: a specific sellable item (a title, edition, format).
- **Stock ledger**: the authoritative count of what is physically available, ideally as a sequence of movements rather than a single number.
- **Reservation / hold**: a time-bounded claim on stock, created at checkout and released on expiry, cancellation or fulfilment.
- **Oversell**: accepting orders for more stock than exists — the one failure that costs money, trust and support time simultaneously.
- **Oversell floor**: the invariant `available − reserved ≥ 0`, enforced atomically.
- **Cart**: a durable, mutable wish list (not a reservation).
- **Flash sale**: a demand spike concentrated on a few SKUs, the event that breaks naive designs.
- **Virtual queue / waiting room**: admitting buyers at a rate the inventory and payment paths can sustain.
- **Allocation**: the step where a reservation becomes a physical pick (warehouse, batch, delivery slot).

The one sentence to carry out of this lesson: **the catalogue is a cache, the cart is a wish list, and only the reservation is a promise — so the entire design is about making the reservation atomic, time-bounded, and reversible, and about admitting no more traffic than the inventory path can honestly serve.**

## 2. Why it exists

**Because the invariant is financial.** BookNook ships books; an oversold title means a refund, an apology, a support ticket and — for a pre-order or a signed edition — a customer who will not come back. The measured cost of an oversell is **£9.40 per affected order** (refund handling, contact, goodwill credit) plus a 14 % repeat-purchase loss among affected customers. That is why the answer to "how do you handle contention?" is not "add a queue" but "make the decrement atomic and the reservation explicit".

**Because contention is concentrated, not uniform.** BookNook sells 3.1 M SKUs, but on a sale day **4 SKUs** can carry 61 % of orders. A design that is correct at 400 requests/s of browse traffic and naive at the row level will pass every load test and fail on the first signed-edition drop (§7).

**Because carts and stock are different things and get confused constantly.** A cart holding an item reserves nothing: two customers can hold the same last copy in their carts for a week. Making the cart reserve stock would create a denial-of-service against inventory (100,000 abandoned carts holding the last 40 copies). The correct design keeps carts free and reserves only at checkout, briefly.

**Because checkout is a distributed transaction and must be treated as one.** Stock in one service, payment in another, fulfilment in a third. Phase 4 said: do not use 2PC across those; use a **saga with a late pivot** (L84). E-commerce is the canonical saga, and getting the pivot point wrong either refunds too many people or blocks too much stock.

**And because the read path is where the traffic is.** 400 requests/s of browse at peak against 60/s of checkout: the catalogue must be cacheable, the stock display must be allowed to be slightly stale (with an honest UI), and only the checkout path needs the strong invariant. **Applying the strongest consistency everywhere is how a shop becomes slow and expensive**.

## 3. Real-world analogy

**A bookshop with a numbered stockroom and paper hold slips.**

The **shelf** is the catalogue: everyone can look, pick up, read the blurb, and put the book back. It is a *view* — it does not promise anything, and it can be a minute out of date. That is the cached product page.

The **reservation slip** is the promise. When you decide to buy, the bookseller writes a slip with your order number and the book's stock number, and puts that copy **behind the counter** with your name on it. Now it is yours — for a limited time. If you do not come back within the hour, the slip expires and the book returns to the shelf. That is the reservation with a TTL, and the hour is a designed number, not a guess.

**And the stockroom ledger is the truth.** The bookseller does not count books from memory: every arrival and every removal is written in a ledger, and the count is the sum. When the ledger says zero, no slip is written — the bookseller says "we're out", and that moment is **atomic** because only one person holds the pen. If two assistants write slips simultaneously from the same last copy, exactly one must fail, and that is the single-writer rule.

The analogy's crucial detail: **the slip is what makes the flash sale survivable.** When 500 people want the 40 signed copies, the shop does not let them all queue at the counter; it hands out numbered tickets to the first 40, and everyone else is told honestly, immediately, that they missed it. That is the waiting room and the atomic decrement: **the shop's promise rate is bounded by its truth rate.**

## 4. Detailed technical explanation

### 4.1 Requirements

**Functional.**

| # | Requirement |
|---|---|
| F1 | Browse a catalogue of 3.1 M SKUs with search, filters and facets |
| F2 | Add to a durable cart (across devices, for 90 days) |
| F3 | Checkout: reserve stock, take payment, create the order, and hand off to fulfilment |
| F4 | Never oversell: `available − reserved ≥ 0` at all times |
| F5 | Release reservations on expiry, cancellation, payment failure or non-fulfilment |
| F6 | Show stock status honestly (in stock / low stock / pre-order / out of stock) |
| F7 | Support flash sales with a bounded, honest admission rate |
| F8 | Full auditability of every stock movement |

**Non-functional.**

| # | Requirement | Number |
|---|---|---|
| N1 | Browse latency | p99 < 200 ms (cached), < 400 ms on a miss |
| N2 | Checkout latency | p99 < 2.5 s end to end |
| N3 | Throughput | 400 rps browse, 60 rps checkout at peak; 1,200 rps in a flash sale |
| N4 | Oversell | **zero** |
| N5 | Reservation TTL | 15 minutes (measured against payment completion) |
| N6 | Availability | 99.95 % browse; checkout degrades to a queue rather than failing |
| N7 | Audit | Every stock movement retained 7 years |

### 4.2 Capacity estimation

```
  CATALOGUE
      3,100,000 SKUs; average product page 8 KB of JSON
      browse 400 rps at peak ⇒ 3.2 MB/s; 97 % served from CDN/edge cache
      ⇒ origin browse load 12 rps. Nothing.

  CART
      carts 400 M rows (active 90 days): 400 M × 400 B = 160 GB
      writes: 40 rps average, 210 rps at peak (add/remove/quantity)
      ⇒ one modest Postgres cluster; carts are user-partitioned.

  CHECKOUT
      60 orders/s at peak = 5.2 M orders/day (BookNook's realistic peak day)
      each order: 1 stock decrement per SKU (avg 2.3 SKUs), 1 payment auth,
      1 order row, 1 outbox event, 1 fulfilment handoff
      ⇒ 138 stock decrements/s at peak; 1,200 rps in a flash sale won't map
        to 1,200 decrements/s because the waiting room caps admission (N3)
        — the flash-sale design converts a request spike into a bounded
        admission rate (§4.8).

  STOCK
      3.1 M SKUs × (on_hand, reserved, version) ≈ 96 B = 300 MB
      ⇒ THE ENTIRE STOCK TABLE FITS IN MEMORY. This is the fact that makes
        flash-sale design tractable: keep the availability counters in a
        fast store, and make the durable ledger the audit trail.

  ORDERS
      5.2 M/day × 1.1 KB = 5.7 GB/day = 2.1 TB/year, retained 7 years
      ⇒ partitioned by month, archived after 12.
```

### 4.3 API

```
GET  /v1/products/{sku}                 → cached 60 s at the edge, with
                                          availability as a coarse status
GET  /v1/products?q=&facets=            → cached 300 s, personalised results
                                          only where the user is signed in

POST /v1/cart/items     { "sku": "…", "qty": 1 }        # NOT a reservation
POST /v1/cart/items/{sku}  { "qty": 3 }
DELETE /v1/cart/items/{sku}
GET  /v1/cart

POST /v1/checkout                       # one call, one idempotency key
     Idempotency-Key: <uuid>
     { "cart_version": 41, "payment_method": "pm_…",
       "delivery": { … } }
  →  202 { "order_id": "o_…", "state": "reserving|paying|confirmed|failed",
           "reservation_expires_at": "…" }
  →  409 { "code": "insufficient_stock", "skus": ["…"], "alternative": "…" }

WS  /v1/orders/{id}/stream              # state transitions, so the UI can
                                        # show "reserved → authorising → done"

POST /v1/orders/{id}/cancel
GET  /v1/stock/{sku}                    # internal: on_hand, reserved, version
POST /internal/stock/receive            { "sku": "…", "qty": 40, "ref": "GRN-…" }
```

### 4.4 Data model

```sql
-- THE STOCK COUNTER: small, hot, and the only row that must be atomic.
CREATE TABLE stock (
  sku         TEXT PRIMARY KEY,
  on_hand     INT NOT NULL CHECK (on_hand >= 0),
  reserved    INT NOT NULL DEFAULT 0 CHECK (reserved >= 0),
  version     BIGINT NOT NULL DEFAULT 0,
  CHECK (on_hand - reserved >= 0)          -- the oversell floor, in the schema
);

-- THE RESERVATION: a promise with an expiry, always tied to an order.
CREATE TABLE reservations (
  id          UUID PRIMARY KEY,
  order_id    UUID NOT NULL,
  sku         TEXT NOT NULL,
  qty         INT NOT NULL CHECK (qty > 0),
  state       SMALLINT NOT NULL,           -- held|committed|released|expired
  expires_at  TIMESTAMPTZ NOT NULL,        -- now() + 15 min
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (order_id, sku)                   -- one reservation per order per SKU
);
CREATE INDEX res_expiry ON reservations (expires_at) WHERE state = 0;

-- THE LEDGER: append-only, the audit trail and the reconciliation source.
CREATE TABLE stock_movements (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  sku         TEXT NOT NULL,
  delta       INT NOT NULL,                -- +40 received, −1 sold, −1 reserved?
  kind        SMALLINT NOT NULL,           -- receive|reserve|release|sale|return
  ref_type    SMALLINT NOT NULL,           -- order|goods_receipt|adjustment
  ref_id      UUID,
  at          TIMESTAMPTZ NOT NULL DEFAULT now()
) PARTITION BY RANGE (at);

-- Carts: user-partitioned, mutable, and NOT a reservation.
CREATE TABLE carts (
  user_id     BIGINT NOT NULL,
  sku         TEXT NOT NULL,
  qty         INT NOT NULL,
  added_at    TIMESTAMPTZ NOT NULL,
  updated_at  TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (user_id, sku)
);

-- Orders: the saga's state machine.
CREATE TABLE orders (
  id            UUID PRIMARY KEY,
  user_id       BIGINT NOT NULL,
  state         SMALLINT NOT NULL,     -- created|reserved|paid|confirmed|
                                        -- failed|cancelled|fulfilled|refunded
  total_pence   INT NOT NULL,
  payment_intent TEXT,
  idempotency_key TEXT NOT NULL UNIQUE,
  saga_step     SMALLINT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
) PARTITION BY RANGE (created_at);
```

### 4.5 The reservation: five candidate designs, one chosen

```
  A. CHECK-THEN-UPDATE (the naive one)
      SELECT on_hand, reserved FROM stock WHERE sku=$1;
      -- application decides in Python/Java
      UPDATE stock SET reserved = reserved + $qty WHERE sku=$1;
      ⇒ under concurrency, two requests both read "1 left" and both succeed:
        a guaranteed oversell. NEVER.

  B. ROW LOCK
      SELECT ... FOR UPDATE; UPDATE ...; COMMIT;
      ⇒ correct, but serialises every buyer of a hot SKU on one row lock.
        Measured on a contended row: 1 / 0.9 ms = 1,100 reservations/s in
        theory; BookNook measured 480/s under a realistic mix, with p99
        checkout 3.9 s and lock timeouts at 1,400 concurrent buyers.

  C. CONDITIONAL DECREMENT (CHOSEN — the workhorse)
      UPDATE stock SET reserved = reserved + $qty, version = version + 1
       WHERE sku = $1 AND on_hand - reserved >= $qty
      ⇒ 0 rows affected = insufficient stock, immediately and honestly.
        One statement, no read, no lock held across application code.
        Measured: 4,100 reservations/s on one SKU (8.5× the row-lock design)
        because the lock is held for microseconds inside the statement.

  D. RESERVATION TABLE ONLY (no counter)
      INSERT the reservation, then SUM(qty) per SKU to compute availability.
      ⇒ the SUM makes every checkout O(open reservations), which in a flash
        sale is 60,000 rows. Rejected for the hot path; retained as the
        RECONCILIATION check (the counter must equal the sum).

  E. EVENT-SOURCED LEDGER ONLY
      availability = SUM(movements). Correct and audit-perfect, but replaying
      a 7-year ledger per checkout is absurd — so the ledger is the SOURCE OF
      TRUTH and the counter is a MATERIALISED VIEW of it, rebuilt on drift.
      ⇒ this is what BookNook runs: C for the write path, E for the truth,
        and a nightly reconciliation that asserts C == E.
```

### 4.6 The checkout saga, with the pivot placed correctly

```
  THE SEVEN STEPS (BookNook's checkout, from L84):
    1. VALIDATE   cart version, prices re-checked, address, fraud pre-screen
    2. RESERVE    conditional decrement per SKU (this is the entry to the
                  critical section; compensation = release)
    3. PRICE      re-price with current promotions; if it moved, re-confirm
    4. AUTHORISE  payment authorisation (not capture)            ← near-pivot
    5. COMMIT     reservations become `committed`; stock moved from
                  `reserved` to sold
    6. CAPTURE    payment capture                                ← PIVOT
    7. FULFIL     create the pick, hand off to the warehouse

  THE PIVOT IS AFTER CAPTURE (step 6), and the design's entire cost profile
  follows from that placement:
    · everything before the pivot is cheap to undo (release a reservation,
      void an authorisation)
    · everything after it is expensive (a refund, a cancellation notice,
      a warehouse pick that has already started)
  ⇒ NEVER capture payment before reserving stock: that inverts the pivot and
    turns every stock-out into a refund.
  MEASURED compensation rate: 2.9 % of checkouts (payment declines,
  abandonment, price-change re-confirmations) = 1,740/month at 60/s peak
  days. Each compensation costs £0.31 in payment-gateway fees plus a support
  fraction ⇒ ~£780/month. Acceptable.
```

### 4.7 The reservation TTL: derived, not guessed

```
  WHAT THE TTL MUST COVER:
      median time from reservation to payment capture = 47 s
      p95 = 3 min 10 s; p99 = 6 min 40 s; p99.9 = 14 min 20 s (3-D Secure
      redirects, slow cards, users switching apps)
  WHAT THE TTL COSTS:
      every minute of TTL holds stock that could be sold: on a hot SKU with
      40 units and 60 buyers/minute, a 15-minute TTL can lock all 40 units
      behind 40 abandoned checkouts for up to 15 minutes.
  THE TRADE, measured at three TTLs on a 2,000-unit limited edition:
      5 min  → 11.4 % of checkouts expired before payment (refunded/retried);
               stock idle time 3.1 min average
      15 min → 1.2 % expired; stock idle 6.8 min   ← CHOSEN
      30 min → 0.1 % expired; stock idle 14.2 min; 41 % of the run sold out
               with units still reserved and unsold at the end
  ⇒ 15 minutes covers p99.9 at a stock-idle cost of under 7 minutes. THAT is
    the arithmetic a TTL needs; "15 minutes feels right" is not a design.
```

### 4.8 Flash sales: convert a request spike into an admission rate

```
  THE PROBLEM: 40 units, 180,000 people arriving in 90 seconds.
    · 2,000 requests/s of "add to cart + checkout" against 40 units
    · without admission control: 179,960 honest failures, a database
      saturated on one row, and a site that looks broken to everyone —
      including people browsing unrelated titles.
  THE DESIGN (three layers):
    1. WAITING ROOM (front door): a token-based queue in front of the sale
       page. Admission rate = min(inventory_path_capacity, payment_capacity)
         inventory path: 4,100 reservations/s ⇒ not the constraint
         payment path:   180 authorisations/s  ⇒ THE CONSTRAINT
       ⇒ admit 150/s, hand out a signed 5-minute entry token, and show
         queue position and an honest estimate.
    2. PER-USER AND PER-SKU LIMITS (L95): 1 unit per user per SKU for
       limited editions; 10/s per IP; 3 checkouts per user per minute.
    3. HONEST SOLD-OUT SIGNAL: when `on_hand - reserved = 0`, flip a fast
       flag that the CDN serves immediately (a 1-second propagation), so
       99 % of the crowd is told "sold out" without touching the database.
  ⇒ MEASURED at the 2026-03 signed-edition drop:
      without a waiting room (the 2025-11 drop): site-wide p99 8.4 s, 41 %
      of unrelated browse requests failed, and 34 oversells (from a retry
      path that bypassed the conditional decrement — see §11.5)
      with the waiting room: p99 240 ms for browse, checkout p99 1.9 s,
      zero oversells, and a mean queue wait of 3 min 40 s with a published
      estimate that was accurate to ±40 s.
```

### 4.9 What may be stale, and what may not

| Data | Staleness allowed | Why |
|---|---|---|
| Product page content | 5 min | Nobody notices a description edit |
| Price on the product page | **0** (re-checked at checkout) | Consumer law; a wrong price is a legal problem |
| "In stock" badge | 60 s | A UI hint, not a promise |
| Stock count displayed | 60 s (or a coarse bucket) | Exact counts leak competitive information |
| **Availability at reservation** | **0** | The invariant |
| Order total | 0 | Re-priced inside the saga |
| Search results | 300 s | Repricing an index costs more than it earns |
| Recommendations | 15 min | Personalisation tolerates staleness by design |

```
  ⇒ THE RULE: EVERY READ THAT DOES NOT CREATE A PROMISE MAY BE STALE.
    Reservations and prices are the only zero-staleness paths, and saying so
    explicitly is what allows the catalogue to be a CDN-cached view while
    checkout stays correct.
```

## 5. Request / data flow

```
 BROWSE (400 rps peak)
 ─────────────────────────────────────────────────────────────────────────
 edge cache (97 %) → product JSON + availability status (60 s TTL)
 miss → catalogue service → read replica / search index → 120 ms
 ⇒ zero database writes on the browse path; nothing here is contended.

 CHECKOUT (60 rps peak), the seven-step saga
 ─────────────────────────────────────────────────────────────────────────
 t+0      POST /checkout (idempotency key) → order row, state=created
 t+40 ms  step 1 validate: cart version match, price re-check (authoritative)
 t+90 ms  step 2 RESERVE: for each of 2.3 SKUs:
              UPDATE stock SET reserved = reserved + qty
               WHERE sku=$1 AND on_hand - reserved >= qty
              ⇒ 0 rows ⇒ HTTP 409 with the offending SKUs, cart preserved
 t+140 ms outbox: stock.reserved events (L85) — never a dual write
 t+400 ms step 4 AUTHORISE payment (£X, intent id)   [p99 1.8 s, 3-D Secure]
 t+2.2 s  step 5 COMMIT reservations; write stock_movements rows
 t+2.4 s  step 6 CAPTURE   ← THE PIVOT
 t+2.5 s  step 7 FULFIL: create the pick, publish order.confirmed
 ⇒ p50 1.4 s, p99 2.5 s. The payment call is 72 % of the latency and it is
   the one step that cannot be optimised away.

 EXPIRY (the part that must never be forgotten)
 ─────────────────────────────────────────────────────────────────────────
 every 30 s: for reservations where state=held and expires_at < now():
     UPDATE stock SET reserved = reserved - qty WHERE sku=...
     UPDATE reservations SET state=expired
     append stock_movements(release)
 ⇒ measured: 2.4 % of reservations expire, releasing 3,100 units/day — units
   that would otherwise be permanently unsellable.
```

## 6. ASCII diagram

```
   ┰ BROWSE (400 rps) ─────────────────────────────────────────────┐
   │ CDN edge (97 %) → catalogue API → search index / read replica  │
   │ availability badge: 60 s stale, and LABELLED as a hint          │
   └────────────────────────────────────────────────────────────────┘

   ┰ CHECKOUT (60 rps) ───────────────────────────────────────────┐
   │                                                                │
   │  cart ──► SAGA ORCHESTRATOR (state machine, per order)          │
   │              │                                                  │
   │      1 validate ─► 2 RESERVE ─► 3 price ─► 4 AUTHORISE          │
   │                        │                       │                │
   │                        ▼                       ▼                │
   │                 ┌─────────────┐          ┌─────────────┐        │
   │                 │ STOCK       │          │ PAYMENTS    │        │
   │                 │ conditional │          │ (L105)      │        │
   │                 │ decrement   │          └─────────────┘        │
   │                 │ on_hand -   │                                 │
   │                 │ reserved ≥0 │          5 COMMIT ─► 6 CAPTURE  │
   │                 └──────┬──────┘                │      (PIVOT)   │
   │                        │                       ▼                │
   │                 ┌──────▼──────┐          ┌─────────────┐        │
   │                 │ LEDGER      │          │ FULFILMENT  │        │
   │                 │ append-only │          │ pick, ship  │        │
   │                 │ = the truth │          └─────────────┘        │
   │                 └──────┬──────┘                                 │
   │                        │ nightly reconciliation: counter == Σ   │
   │                        ▼                                        │
   │                 ┌──────────────┐    ┌───────────────────────┐   │
   │                 │ EXPIRY SWEEPER│    │ WAITING ROOM          │   │
   │                 │ 30 s, releases│    │ admits 150/s (payment │   │
   │                 │ 3,100 units/d │    │ capacity), not 2,000/s│   │
   │                 └──────────────┘    └───────────────────────┘   │
   └────────────────────────────────────────────────────────────────┘

   THE ONE STATEMENT THAT MATTERS:
     UPDATE stock SET reserved = reserved + $qty
      WHERE sku = $1 AND on_hand - reserved >= $qty
     ⇒ 0 rows = sold out, honestly, in microseconds, under any concurrency.

   THE FLASH-SALE FUNNEL (40 units, 180,000 arrivals):
     180,000 arrive
       └─ waiting room admits 150/s → 13,500 through in 90 s
            └─ availability flag flips at unit 40 → 99 % see "sold out"
                 └─ 40 conditional decrements succeed, 0 oversells
```

## 7. Detailed example

**BookNook's storefront**: 3.1 M titles, 5.2 M orders on a peak day, signed-edition drops twice a year.

```
  measured:
    400 rps browse peak (97 % CDN), 60 rps checkout peak
    2.3 SKUs per order, 47 s median reservation→capture
    2.9 % of checkouts compensate, 2.4 % of reservations expire
    480 reservations/s under the old row-lock design at p99 3.9 s
    4,100 reservations/s with the conditional decrement
```

**The oversell incident (November, the one that produced the ledger):**

```
  2025-11-14, first signed-edition drop: 400 units, 90,000 people.
    · the checkout path used SELECT ... FOR UPDATE, which was correct
    · BUT the "buy now" fast path (added 3 weeks earlier) used
      check-then-update with no lock at all
    · 34 oversells: 34 customers paid for a book that did not exist
    · cost: 34 refunds + £60 goodwill each + 34 × 14 % repeat-purchase loss
      ⇒ £9.40 handling + £60 goodwill = £2,362 direct, plus ~£1,900 of
        lifetime value, plus a week of "are you a real shop" comments
  fixes:
    1. every stock decrement goes through ONE function — no exceptions, no
       fast paths (enforced by an architecture test that greps for direct
       UPDATEs on `stock` outside the inventory module)
    2. the CHECK constraint `on_hand - reserved >= 0` as a backstop, so a
       bug becomes a failed transaction rather than a corrupt count
    3. a reconciliation job that compares the counter with the ledger nightly
       and alerts on any non-zero drift
  ⇒ zero oversells since, across two drops and 1.1 M orders.
```

**The cart-reservation proposal that was rejected, with numbers:**

```
  proposal: "hold stock when an item is added to the cart, so customers do
  not lose their basket."
  arithmetic:
    carts active at any time: 1.4 M
    average items per cart: 2.7
    ⇒ 3.8 M units would be held
    BookNook's total on-hand across the catalogue: 2.1 M units
    ⇒ THE CARTS WOULD HOLD 181 % OF ALL PHYSICAL STOCK. The shop would appear
      sold out permanently while the warehouse was full.
  the honest alternative: reserve at checkout for 15 minutes, show "low stock"
  honestly in the cart, and accept that 1.9 % of checkouts hit a stock-out —
  measured, and cheaper than the alternative by every measure.
  ⇒ THIS IS THE MOST COMMON E-COMMERCE DESIGN MISTAKE, and it is arithmetic,
    not opinion.
```

**The flash sale that worked, measured end to end:**

```
  2026-03 drop: 2,000 units, 310,000 arrivals in 6 minutes.
    waiting room admitted 150/s ⇒ 54,000 admitted over 6 min
    of those, 41,000 reached checkout, 2,000 succeeded
    sold out at 4 min 20 s; the availability flag flipped at 4 min 21 s
    browse p99 during the event: 240 ms (unaffected — separate paths)
    checkout p99: 1.9 s
    oversells: 0
    queue wait: mean 3 min 40 s, published estimate accurate to ±40 s
    revenue: £148,000 in 4 minutes 20 seconds
  ⇒ THE MEASURE THAT MATTERS MOST: 0 oversells AND 0 unrelated-user failures.
    The 2025-11 event had 34 oversells and 41 % browse failures.
```

## 8. Code or configuration example

```python
# ── The ONLY stock decrement in the codebase. No fast paths. ─────────────
RESERVE_SQL = """
UPDATE stock
   SET reserved = reserved + $2, version = version + 1
 WHERE sku = $1
   AND on_hand - reserved >= $2
RETURNING version
"""

async def reserve(conn, order_id: UUID, sku: str, qty: int, ttl_s=900):
    async with conn.transaction():
        v = await conn.fetchval(RESERVE_SQL, sku, qty)
        if v is None:
            raise InsufficientStock(sku)         # 409, honest, no partial state
        await conn.execute(
            """INSERT INTO reservations
                 (id, order_id, sku, qty, state, expires_at)
               VALUES ($1,$2,$3,$4,0, now() + ($5 || ' seconds')::interval)
               ON CONFLICT (order_id, sku) DO UPDATE
                 SET qty = reservations.qty + EXCLUDED.qty""",
            new_id(), order_id, sku, qty, ttl_s)
        await conn.execute(
            """INSERT INTO stock_movements (sku, delta, kind, ref_type, ref_id)
               VALUES ($1,$2,2,1,$3)""", sku, qty, order_id)   # kind=reserve
    return v

# ── Reservation expiry: batched, idempotent, and it must never be skipped ─
async def expire_reservations():
    rows = await db.fetch("""
        SELECT id, order_id, sku, qty FROM reservations
         WHERE state = 0 AND expires_at < now()
         ORDER BY expires_at
         LIMIT 5000
         FOR UPDATE SKIP LOCKED""")            # parallel sweepers, no contention
    for r in rows:
        async with db.transaction():
            await db.execute(
                """UPDATE stock SET reserved = reserved - $2, version = version + 1
                    WHERE sku = $1""", r["sku"], r["qty"])
            n = await db.execute(
                """UPDATE reservations SET state = 3
                    WHERE id = $1 AND state = 0""", r["id"])
            if n == "UPDATE 1":                # only ledger it if WE expired it
                await db.execute(
                    """INSERT INTO stock_movements (sku, delta, kind, ref_type,
                                                    ref_id)
                       VALUES ($1,$2,3,1,$3)""", r["sku"], -r["qty"],
                    r["order_id"])
        await order_saga.on_reservation_expired(r["order_id"], r["sku"])
```

```python
# ── Booking the saga: reserve → authorise → commit → capture (pivot) ─────
async def checkout(order: Order, cart: Cart, payment_method: str):
    # steps 1–2: validate and reserve (compensable)
    reserved = []
    try:
        for line in cart.lines:
            await reserve(db, order.id, line.sku, line.qty)
            reserved.append(line)
        # step 3: authoritative re-price; if it moved, ask the user again
        price = await pricing.reprice(cart)
        if price.total_pence != cart.quoted_pence:
            raise PriceChanged(price)           # compensate: release all
        # step 4: authorise (not capture) — reversible
        auth = await payments.authorise(price.total_pence, payment_method,
                                        idempotency_key=order.id)
        await order.advance(order.id, "authorised", auth.id)
        # step 5: commit reservations (stock becomes sold)
        await commit_reservations(db, order.id)
        # step 6: THE PIVOT — capture
        await payments.capture(auth.id)         # after this, compensations
        await order.advance(order.id, "confirmed")   # are refunds, not voids
        # step 7: fulfil
        await fulfilment.create_pick(order.id, reserved)
    except InsufficientStock as e:
        await release_all(db, order.id)          # compensate what we took
        raise HTTPException(409, {"code": "insufficient_stock", "sku": e.sku})
    except PriceChanged as e:
        await release_all(db, order.id)
        raise HTTPException(409, {"code": "price_changed", "new": e.total})
    except Exception:
        # unknown failure BEFORE the pivot: release and void the authorisation
        await compensate_pre_pivot(order, reserved)
        raise
```

```python
# ── The waiting room: admission is bounded by the PAYMENT path, not stock ─
class WaitingRoom:
    RATE_PER_SECOND = 150          # = payment authorisation capacity
    def __init__(self): self.queue = deque(); self.tokens = {}

    async def join(self, user_id) -> Position:
        self.queue.append(user_id)
        return Position(position=len(self.queue), eta_s=len(self.queue) / 150)

    async def admit(self):
        for _ in range(self.RATE_PER_SECOND):
            if not self.queue: break
            u = self.queue.popleft()
            self.tokens[u] = sign_entry_token(u, ttl_s=300)   # 5 min to buy
        # tokens are handed to clients via the socket; the checkout POST
        # requires a valid entry token, so nobody can bypass the queue
```

```yaml
# ── Rails: the invariant, the TTL, and the reconciliation ───────────────
inventory:
  reserve_statement_only_in_module: inventory      # enforced by a CI check
  reservation_ttl_seconds: 900
  expiry_sweep_seconds: 30
  reconciliation: { schedule: "0 2 * * *", alert_on_drift_units: 0 }
  max_units_per_user_per_sku: 10
flash_sale:
  waiting_room: { admission_per_second: 150, entry_token_ttl_s: 300 }
  sold_out_flag_propagation_s: 1
  per_user_limit: 1
alerts:
  - alert: OversellInvariantViolation
    expr: increase(stock_check_constraint_violations_total[5m]) > 0
    severity: page          # must be structurally impossible; if it fires,
                            # a code path has bypassed the module
  - alert: StockCounterLedgerDrift
    expr: abs(stock_counter_minus_ledger_units) > 0
    severity: page          # the nightly reconciliation found drift early
  - alert: ReservationExpiryBacklog
    expr: count by () (reservations WHERE state=0 AND expires_at < now()-60s) > 100
    severity: page          # stock is stranded: units nobody can buy
  - alert: CheckoutCompensationRateHigh
    expr: rate(saga_compensations_total[10m]) / rate(saga_started_total[10m]) > 0.06
    severity: ticket        # > 2× the 2.9 % baseline
  - alert: PaymentLatencyBreach
    expr: histogram_quantile(0.99, rate(payment_authorise_seconds_bucket[5m])) > 2
    severity: page          # the pivot is at risk; TTL may need to stretch
```

## 9. Numerical example

BookNook: 3.1 M SKUs, 60 orders/s peak, 15-minute reservations.

**Step 1 — why the conditional decrement is 8.5× the row-lock design.**

```
  row lock (SELECT FOR UPDATE): the lock is held from the SELECT until COMMIT,
  including the application's work in between. Measured hold time per
  reservation: 2.1 ms ⇒ capacity 476 reservations/s on ONE SKU.
  conditional decrement: the row lock is held only inside the UPDATE
  statement — measured 0.24 ms ⇒ 4,170 reservations/s on one SKU.
  ⇒ 8.8×. AND the failure mode is better: the row-lock design produced
    120 lock-timeout errors per 1,000 buyers at 1,400 concurrency, while the
    conditional decrement produces a clean "0 rows affected".
  ⇒ at 60 orders/s × 2.3 SKUs = 138 decrements/s, EITHER design has capacity.
    The difference appears in exactly one place: a flash sale, where 40 units
    are wanted by 180,000 people. That is the day the 8.8× matters.
```

**Step 2 — the reservation TTL, in units stranded and sales lost.**

```
  2,000-unit limited edition, 60 buyers/minute sustained:
      5-min TTL:  reserved-and-abandoned at any time ≈ 60 × 5 × 0.012 = 3.6
                  units; expired checkouts 11.4 % ⇒ 228 buyers must retry
      15-min TTL: ≈ 60 × 15 × 0.012 = 10.8 units held; expired 1.2 % ⇒ 24
      30-min TTL: ≈ 21.6 units held; expired 0.1 % ⇒ 2
  ⇒ and the failure that matters at 30 minutes: the run sold out with 21 units
    reserved and unsold, i.e. 1 % of the run was never sold in the window.
    At £74 contribution each: £1,554 of foregone revenue, versus £0 at 5
    minutes but 228 disappointed buyers.
  ⇒ 15 MINUTES IS THE ARGUMENT'S RESULT, not a default: it strands ~11 units
    (£814 of temporarily unsellable stock) to avoid 204 retries. The retries
    cost more in support and abandonment than the stranded units.
```

**Step 3 — the cart-reservation arithmetic, which kills the feature.**

```
  active carts 1,400,000 × 2.7 items = 3,780,000 units that WOULD be held
  total on-hand across the catalogue:  2,100,000 units
  ⇒ 181 % of physical stock. The shop would present as sold out while the
    warehouse was full.
  ⇒ and even restricting to "only the last 40 units of a scarce SKU" would
    hold 40 units for every abandoned cart, meaning the LAST COPY of a book
    could be held hostage for 90 days by a shopper who never returns.
    Measured on a pilot: cart-holds on the 20 scarcest titles raised
    stock-out-to-abandonment conversion failures from 1.9 % to 31 %.
```

**Step 4 — the flash-sale admission rate, derived from the payment path.**

```
  payment authorisations: 180/s sustained (measured, with a provider cap)
  ⇒ admit 150/s (83 % of payment capacity, leaving room for organic traffic)
  40-unit drop, 180,000 arrivals in 90 s:
      admitted in 90 s = 150 × 90 = 13,500
      of admitted, 76 % reach checkout = 10,260 checkouts
      of those, the first 40 succeed within 1.2 s; the rest get 409 sold-out
  ⇒ without the waiting room, 180,000 × 76 % = 136,800 checkouts in 90 s =
    1,520/s against 180/s of payment capacity ⇒ 88 % would fail at the
    payment step, with money already authorised for some ⇒ refunds.
  ⇒ THE WAITING ROOM PREVENTS REFUNDS, not just overload. That is the point
    people miss: an admission rate above the payment path's capacity
    manufactures refunds.
```

**Step 5 — the total cost of getting it wrong, one drop.**

```
  2025-11 (no waiting room, a fast path bypassing the module):
      34 oversells                     £2,362 direct + ~£1,900 LTV
      41 % of unrelated browse failed  ≈ 180,000 lost sessions × 1.9 % conv
                                         × £74 = £253,000 of foregone revenue
      p99 8.4 s site-wide              2 enterprise accounts complained
  2026-03 (waiting room + single decrement path):
      0 oversells, browse p99 240 ms, £148,000 revenue in 4 min 20 s
  ⇒ THE COST OF THE BUG WAS ~£257,000, AND THE FIX WAS 3 WEEKS OF WORK.
    This is the arithmetic that justifies the whole reservation subsystem.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **Conditional decrement** | 4,100 reservations/s, no read, honest failure | A constraint you must not bypass | **Always** |
| **Row lock (FOR UPDATE)** | Familiar, correct | 8.8× slower; lock timeouts under load | Low contention only |
| **Check-then-update** | Simplest code | **Guaranteed oversell** | Never |
| **Reservation with TTL** | Honest promises; recoverable stock | Strands stock; needs a sweeper | **Always at checkout** |
| **Cart holds stock** | "Your basket is safe" | Holds 181 % of physical stock (§9 step 3) | Never |
| **15-min TTL** | 1.2 % expiries | ~11 units stranded on a hot SKU | **Chosen** |
| **5-min TTL** | Fast stock recycling | 11.4 % of checkouts expire | Payment is one-tap |
| **Ledger + counter** | Audit + speed | Nightly reconciliation | **Always** |
| **Counter only** | Simple | No audit; drift is invisible | Never for physical goods |
| **Cached availability badge** | CDN-able browse | 60 s stale; must be labelled a hint | **Always for browse** |
| **Zero-staleness stock display** | "Accurate" | Kills caching; leaks counts | Never |
| **Waiting room** | Prevents refunds and protects browse | Queue UX; published wait estimate must be honest | Any drop/scarce launch |
| **No waiting room** | Simpler | Overload, refunds, unrelated failures (§9 step 5) | Plenty of stock |
| **Late pivot (after capture)** | Cheap compensations | Reversible until capture | **Always** |

## 11. Failure scenarios

**11.1 — Oversell through a fast path** (§7). 34 oversells, £4,300 direct, £253,000 of collateral browse failure. **Fix:** one decrement function, a CI check that forbids direct writes, and a CHECK constraint backstop.

**11.2 — Cart-reservation proposal** (§9 step 3). Would have held 181 % of physical stock. **Fix:** reserve only at checkout, and label cart stock as a hint.

**11.3 — Reservation expiry sweeper stalled.** A deploy stopped the sweeper for 6 hours; 41,000 units stayed reserved and unsold, and a bestseller showed "out of stock" for an afternoon. **Fix:** an alert on the sweep backlog, expiry as a database-driven schedule (not an in-process cron), and a manual "release all expired" runbook.

**11.4 — Price changed mid-saga.** A promotion ended between cart and payment; 1,100 orders were charged a different price than quoted. **Fix:** step 3 re-prices authoritatively and raises `PriceChanged` before authorisation, with a re-confirmation UI.

**11.5 — Retry path bypassing the module** (§7). The "buy now" endpoint wrote stock directly. **Fix:** module-private writes, plus the constraint, plus a repository-level check in CI.

**11.6 — Stock counter drift.** A bulk import updated `on_hand` without a ledger row; the counter was 1,400 units higher than the ledger across 61 SKUs. **Fix:** every change writes a movement in the same transaction, and the nightly reconciliation alerts on any drift.

**11.7 — Flash sale payment refunds** (§9 step 4). Admitting 1,520 checkouts/s against 180/s of payment capacity manufactured refunds. **Fix:** an admission rate derived from payment capacity, and a sold-out flag with 1 s propagation.

**11.8 — Cache serving a sold-out product as available.** The product page was cached for 5 minutes with "in stock", and 4,100 people hit 409s at checkout. **Fix:** the availability badge is a separate, short-TTL, labelled hint — and the checkout's 409 message is actionable ("we've just sold out; here are 3 similar titles"), which converted 19 % of the disappointed buyers.

## 12. Alternatives

| Instead of | Consider | When |
|---|---|---|
| Reservations at checkout | Pay-first with automatic refund on stock-out | Refunds are cheap and stock-outs are rare |
| Reservations | Auction / lottery for scarce items | Demand exceeds supply by >100× |
| Own inventory service | A managed commerce platform | You are not a retailer |
| Conditional decrement | A serialised queue per SKU | You have a fast, durable queue and low volume |
| Waiting room | Randomised ballot before the sale opens | Fairness matters more than speed |
| Real-time stock display | Coarse buckets ("only 3 left") | You want urgency without leaking counts |
| Ledger + counter | Ledger only, with a periodic materialised view | Very low write volume |

## 13. When to use it

- **A reservation subsystem** for any physical-good checkout with contention.
- **The conditional decrement** for every bounded resource: stock, seats, slots, licences, coupons.
- **A ledger alongside a counter** whenever the number is money-adjacent or audited.
- **A waiting room** for any launch where demand exceeds supply by more than ~10×.
- **Explicit staleness budgets per read path**, published in the API documentation.

## 14. When *not* to use it

- **Reservations for digital goods** with unlimited supply — there is nothing to hold.
- **Cart-level holds** (§9 step 3).
- **Reservations longer than the payment path's p99.9** — they strand stock.
- **Zero-staleness stock display on the browse path** — cache-killing and count-leaking.
- **A waiting room when stock is plentiful** — you are adding friction to a problem you do not have.
- **2PC across inventory and payment** — use the saga (L84).

## 15. Common mistakes

1. **Check-then-update** for stock (§11.1).
2. **Multiple decrement code paths**, one of which forgets (§11.1, §11.5).
3. **Cart holds** (§11.2).
4. **No expiry sweeper**, or a sweeper with no backlog alert (§11.3).
5. **No re-price before authorisation** (§11.4).
6. **Counter without a ledger** (§11.6).
7. **Admission rate not derived from payment capacity** (§11.7).
8. **Long-lived cached availability** with no honesty label (§11.8).
9. **Capturing payment before reserving stock** — inverting the pivot.
10. **TTL chosen by feel** rather than against the payment p99.9 (§4.7).
11. **A 409 that is a dead end** instead of offering alternatives.
12. **Reservation TTL longer than the fulfilment cut-off**, so a warehouse pick races an expiry.
13. **No reconciliation**, so drift is discovered by a customer.
14. **Treating the cart as the source of truth for price** — the cart is a wish list.

## 16. Interview perspective

**What is actually being tested.** Whether you can turn an invariant into an **atomic statement**, whether you distinguish the cart from the reservation, and whether you can size a TTL and an admission rate from measurements. Strong candidates: conditional decrement, reservation with a derived TTL, late pivot, ledger + counter, waiting room bounded by payment capacity. Weak candidates: "use a transaction and a lock" with no contention analysis.

**Answers that land well:**

- *"How do you prevent overselling?"* — "One statement: update the stock row, adding to reserved, where on-hand minus reserved is at least the quantity. Zero rows updated means sold out, and it is honest and immediate. That is eight and a half times the throughput of a `SELECT FOR UPDATE` because the lock is held for microseconds inside the statement rather than across the application's work. Then a check constraint as a backstop, a single code path that is the only place allowed to write stock, and a nightly reconciliation against an append-only ledger, because a counter with no ledger cannot be trusted."
- *"Why not reserve stock when it goes in the cart?"* — "Because it is arithmetically impossible. We have one point four million active carts averaging two point seven items, which is three point eight million units, against two point one million units of total stock. The carts would hold a hundred and eighty-one percent of everything we own and the shop would present as sold out with a full warehouse. The cart is a wish list; the reservation is a promise; only the promise is bounded."
- *"How long is a reservation?"* — "Fifteen minutes, and that comes from measurement: our payment p99.9 is fourteen minutes twenty because of three-D Secure redirects. Five minutes expires eleven percent of legitimate checkouts; thirty minutes strands twenty-one units of a two-thousand-unit run and loses about fifteen hundred pounds of sales. Fifteen strands eleven units and loses twenty-four checkouts, which is the cheapest point."
- *"What happens in a flash sale?"* — "You convert a request spike into an admission rate — and the rate comes from the payment path, not from stock. We can do four thousand reservations a second but only a hundred and eighty payment authorisations, so we admit a hundred and fifty a second through a waiting room with a signed five-minute entry token. Without it, ninety percent of buyers would be authorised then refunded, because stock ran out mid-flight. That is the part people miss: admitting too many people manufactures refunds."

**Common interview tasks:** design browse versus checkout; prevent oversell; design the reservation lifecycle; place the saga pivot; derive the TTL; design a flash sale; design the ledger and reconciliation; decide what may be stale.

**Red flags interviewers watch for:**

- Read-then-write stock handling.
- Reserving in the cart.
- No expiry mechanism.
- Payment captured before stock reserved.
- No ledger, only a counter.
- A waiting room with an admission rate from nowhere.
- Zero-staleness stock display on the browse path.
- No re-price step.
- A 409 with no recovery path.
- No numbers: no TTL derivation, no contention figures.

## 17. Knowledge check

**Conceptual**

1. Express the oversell invariant as an atomic statement, and explain why it is faster than a row lock. Then explain the three defences and what each one catches.
2. Explain why the cart must not reserve stock, with the arithmetic. Then explain what may legitimately be stale on each read path and why.
3. Explain the seven-step checkout saga and justify the pivot's placement. Then explain what changes about compensations before and after the pivot, in cost.

**Practical scenario**

4. Design BookNook's commerce path: 3.1 M SKUs, 400 rps browse, 60 rps checkout, 2.3 SKUs per order, a 180/s payment capacity, two scarcer drops a year with 100× demand, and a 7-year audit requirement. Give the requirements and capacity tables, the API, the schema with the constraint and the ledger, the reservation design with a derived TTL, the saga with its pivot and compensations, the flash-sale waiting room with its admission arithmetic, the browse caching with explicit staleness budgets, the reconciliation job, the alerts, and the incident replays. Then state what changes if the goods are digital licences with instant delivery.

**Calculation**

5. A hot SKU has 40 units. (a) At 60 buyers/minute and a 15-minute TTL, compute the expected units held by abandoned checkouts at steady state if 1.2 % of checkouts expire. (b) Compute the same for a 5-minute and a 30-minute TTL. (c) If the run is 2,000 units and 21 are stranded at the end, compute the revenue lost at £74 contribution. (d) State the TTL you would choose and the reasoning in one sentence.

6. Checkout capacity: 180 payment authorisations/s, 4,100 reservations/s. (a) Compute which is binding. (b) Compute the admission rate you would set at 83 % of the binding capacity. (c) For 180,000 arrivals in 90 seconds, compute how many are admitted and how many checkouts occur at a 76 % reach rate. (d) Compute how many refunds you avoid compared with admitting everyone, given that only 40 units exist.

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
  - [x] 9. Design a news feed (L101)
  - [x] 10. Design video streaming (L102)
  - [x] 11. Design ride sharing (L103)
  - [x] **12. Design e-commerce and inventory (L104) ← you are here**
  - [ ] 13. Design payment processing (L105)
  - [ ] 14. Design a metrics and logging platform (L106)

**NEXT LESSON: Phase 5, Topic 13 — Design payment processing**

---
