# Lesson 9 — HTTP/1.1 versus HTTP/2 versus HTTP/3

*Phase 1, Topic 9*

## 1. Simple definition

**HTTP** is the request/response language of the web (Lesson 5). But the *words* of that language — `GET /books/123`, `200 OK`, `Content-Type: application/json` — are separate from the *way those words are packed onto the wire*. The three versions in this lesson keep almost all the same words and change the packing.

- **HTTP/1.1** (1997) sends requests and responses as **plain ASCII text**, one request at a time per TCP connection. A connection is a queue: request 1 must fully finish before request 2 may start.
- **HTTP/2** (2015) sends the same information as **binary frames** over a single TCP connection, and interleaves many requests at once on that one connection. This is called **multiplexing**.
- **HTTP/3** (2022) keeps HTTP/2's model but replaces TCP with **QUIC**, a new transport built on UDP, so that one lost packet no longer stalls every request sharing the connection.

Three terms to pin down before we go further, because the whole lesson hangs on them:

- **Connection**: one TCP (or QUIC) pipe between client and server. Expensive to create (Lesson 8 — handshakes).
- **Stream**: one logical request/response pair inside a connection. HTTP/1.1 has one per connection. HTTP/2 and HTTP/3 have many.
- **Head-of-line (HOL) blocking**: when the item at the front of a queue is stuck, everything behind it waits — even if those items were ready. This single idea explains the entire evolution from 1.1 → 2 → 3.

## 2. Why it exists

In 1996 an average web page was one HTML document and perhaps four images. HTTP/1.0 opened a fresh TCP connection per file, used it once, and closed it. That was tolerable.

Today a typical e-commerce product page pulls **60–120 separate resources**: HTML, 3–6 CSS files, 10–30 JavaScript bundles, 20–60 images, 2–5 fonts, a handful of analytics beacons, and several JSON API calls. (Figure is typical for 2024-era retail sites; your mileage varies.)

Try that with HTTP/1.0's one-file-per-connection model:

```
80 resources × (TCP handshake 1 RTT + TLS handshake 1 RTT + request 1 RTT)
= 80 × 3 RTT = 240 round trips
At a typical 50 ms RTT = 12 seconds before you count a single byte of payload.
```

Every version since has been an attack on that number:

1. **HTTP/1.1 added keep-alive** — reuse the connection, so you pay the handshake once instead of 80 times.
2. **Browsers added 6 parallel connections per origin** — do 6 things at once instead of 1.
3. **HTTP/2 added multiplexing** — do 80 things at once on *one* connection.
4. **HTTP/3 replaced TCP with QUIC** — make those 80 things genuinely independent, so one dropped packet doesn't freeze the other 79.

The theme: **protocol versions exist to remove round trips and remove queues.** Nothing more exotic than that.

## 3. Real-world analogy

A **supermarket checkout**.

- **HTTP/1.1 without keep-alive**: you queue, pay for one item, leave the store, walk back in, queue again for the next item. Absurd — that's a new connection per request.
- **HTTP/1.1 with keep-alive**: you stay at the till and pay for items one at a time, but the cashier will not start scanning item 2 until item 1 is fully bagged and handed over. One slow item (a price check) holds up everything behind it. That is **HTTP-level head-of-line blocking**.
- **HTTP/1.1 with 6 connections**: the store opens 6 tills for you. Better, but you can only ever be served 6 items at a time, and each till still blocks internally.
- **HTTP/2**: one cashier, but they scan all 80 items, send them down a shared conveyor belt in small interleaved chunks, and you reassemble your bags at the end. Massively faster — *as long as the conveyor belt never jams*.
- **HTTP/3**: 80 separate mini-belts running side by side in the same machine. If one jams, the other 79 keep moving.

**Where the analogy breaks down**

| Supermarket | Real HTTP |
|---|---|
| A jammed belt is obvious and someone fixes it | A lost TCP packet is invisible; the receiver just stops delivering data upward and waits ~1 RTT for a retransmit, with no error anywhere |
| The belt's speed is fixed | Network capacity is discovered dynamically by congestion control, which *slows down* when it sees loss (Lesson 8) |
| Items are independent | Resources have dependencies — CSS and fonts block rendering, while a footer image does not. This is why HTTP/2 **prioritisation** exists |
| Opening more tills always helps | Opening more TCP connections makes each one's congestion control blinder, and 6 connections competing for the same link can be *slower* than 1 well-fed connection |
| You see your total at the end | The browser does not know a resource's size until headers arrive, so it cannot plan the order perfectly |

The last-but-one row is the subtle one. More parallelism is not monotonically better, because each TCP connection independently probes for bandwidth and independently backs off.

## 4. Detailed technical explanation

### 4.1 HTTP/1.1 — text, keep-alive, and a failed experiment

An HTTP/1.1 request is literally text terminated by CRLF:

```
GET /books/9781492052203 HTTP/1.1\r\n
Host: booknook.com\r\n
User-Agent: Mozilla/5.0 ...\r\n
Cookie: session=abc123; ab_test=variant_b; ...\r\n
\r\n
```

**Keep-alive (persistent connections).** HTTP/1.1 made `Connection: keep-alive` the default. The connection stays open after a response, so subsequent requests skip the TCP handshake (1 RTT) and TLS handshake (1–2 RTT). This is the single largest win in the version's history. The cost: both ends must agree where a response *ends*, which is why `Content-Length` or `Transfer-Encoding: chunked` is mandatory on a keep-alive connection.

**Pipelining, and why it failed.** HTTP/1.1 allowed a client to send request 2 before response 1 arrived. Responses, however, had to come back **in the same order** — because the responses are just concatenated bytes with no identifiers, so order is the only way to match them up. Consequences:

1. If response 1 is slow (a 300 ms database query), responses 2–10 sit finished in the server's buffer, unsendable. Classic HOL blocking, now *worse* than no pipelining, because the client cannot re-route request 5 to another connection.
2. Many intermediaries (proxies, antivirus middleboxes, old load balancers) mis-handled pipelined requests, sometimes serving response 2 to request 1 — a response-splitting security hazard.
3. There was no way to cancel a single in-flight request without killing the connection.

Every major browser shipped pipelining disabled by default. It is effectively dead. **The lesson: ordering without identifiers is a dead end — and that is exactly the problem HTTP/2's stream IDs solve.**

**The 6-connections-per-origin limit.** RFC 2616 suggested 2 connections per origin; browsers settled on **6** (Chrome, Firefox, Safari — approximate, and configurable). An *origin* is the triple `(scheme, host, port)`, so `https://booknook.com` and `https://img.booknook.com` are different origins with separate pools. Why a limit at all? To stop one tab from opening 200 sockets and (a) exhausting server file descriptors, (b) drowning the link in uncoordinated congestion-control loops.

**Domain sharding — a former workaround, now an anti-pattern.** If the limit is 6 per origin, split your assets across `img1.booknook.com`, `img2.booknook.com`, `img3.booknook.com` and you get 18 parallel connections. In 2012 this measurably improved page loads. Today it is actively harmful:

- Each extra hostname costs a **DNS lookup** (Lesson 4: 20–120 ms on a cold cache), a **TCP handshake**, and a **TLS handshake**.
- Under HTTP/2 it *destroys* the benefit of a single multiplexed connection and splits your congestion window across N connections.
- It defeats HPACK header compression, which is per-connection.

If you see sharding in a codebase in 2026, it is legacy. Remove it when you move to HTTP/2.

### 4.2 HTTP/2 — binary framing and multiplexing

HTTP/2 keeps methods, status codes, headers and bodies identical. It changes the wire encoding to a **binary framing layer**.

Everything becomes a **frame**: a 9-byte header (length, type, flags, stream ID) plus a payload. Frame types that matter:

| Frame | Purpose |
|---|---|
| `HEADERS` | Carries the HPACK-compressed request or response headers |
| `DATA` | Carries body bytes |
| `SETTINGS` | Connection-level parameters, e.g. max concurrent streams, initial window size |
| `WINDOW_UPDATE` | Flow control credit, per stream and per connection |
| `RST_STREAM` | Cancel one request without touching the connection |
| `GOAWAY` | "I'm shutting down; streams above ID N were not processed" — enables clean draining |
| `PING` | Liveness and RTT measurement |

**Streams.** Every request/response pair gets an odd-numbered **stream ID** (client-initiated streams are odd, server-initiated even). Because every frame carries its stream ID, frames from different requests can be **interleaved arbitrarily** on the wire and reassembled correctly at the other end. Ordering no longer matters — identifiers replaced it. That is precisely what pipelining lacked.

Default `SETTINGS_MAX_CONCURRENT_STREAMS` is typically 100–128 (nginx defaults to 128; this is tunable and is a real production knob).

**HPACK header compression.** HTTP headers are grotesquely repetitive: the same 800-byte `Cookie`, `User-Agent`, `Accept`, and `Referer` block on all 80 requests for a page. HPACK uses:

- A **static table** of 61 common header name/value pairs (`:method: GET` is index 2 — one byte on the wire).
- A **dynamic table** built up per connection: once `cookie: session=abc...` has been sent, later requests reference it by index.
- **Huffman coding** for literal strings that aren't in either table.

Real-world effect: a repeated 800-byte header block typically shrinks to **20–60 bytes**. Over 80 requests that is roughly 64 KB → 4 KB. (Why a custom algorithm rather than gzip? Because gzip over a stream of attacker-influenced secrets enabled the **CRIME** attack; HPACK's table-index design avoids compressing secrets against attacker-controlled text in the same way.)

**Prioritisation.** The client can tell the server "stream 7 (the CSS) matters more than stream 23 (a footer image)" using dependencies and weights. In practice the original RFC 7540 priority tree was so complex and so badly implemented by servers that browsers largely abandoned it; RFC 9218 ("Extensible Prioritization", the `priority` header with urgency `u=0..7` and incremental `i`) replaced it and is what HTTP/3 uses. **Teaching model:** priorities are hints. **Production reality:** do not rely on them; control order by controlling what you request and when.

**Server push, and why it was deprecated.** The idea: when the client asks for `/index.html`, the server proactively pushes `/style.css` before being asked, saving a round trip. It failed for three reasons:

1. **The server does not know the client's cache.** It pushes a 200 KB CSS file the browser already had — pure waste. ("Cache digests" were proposed to fix this and never shipped.)
2. **It competes for bandwidth** with the HTML the browser actually needs to parse.
3. **Measurement showed near-zero or negative benefit** at scale. Chrome removed support in version 106 (2022); Fastly and others published data showing it rarely helped.

The replacement is **`103 Early Hints`** (RFC 8297): the server sends a cheap informational response — `103 Early Hints` with `Link: </style.css>; rel=preload` — *while* it is still generating the real `200`. The client, which *does* know its own cache, decides whether to fetch. This is the correct division of responsibility: the server gives information, the client makes the decision.

### 4.3 The key insight: two layers of head-of-line blocking

This is the single most important paragraph in the lesson, and the thing interviewers probe.

There are **two** places a queue can stall:

- **HTTP-layer HOL blocking**: one request must wait for another *at the HTTP level*. HTTP/1.1 has this (one response at a time per connection). **HTTP/2 fixes it** with streams.
- **TCP-layer HOL blocking**: TCP guarantees *in-order delivery of one byte stream* (Lesson 8). If packet #500 is lost, packets #501–#600 may have arrived safely and be sitting in the receiver's kernel buffer, but the kernel **will not hand them to the application** until #500 is retransmitted and arrives — roughly 1 RTT later. The kernel has no idea those bytes belonged to 15 different HTTP streams.

So: **HTTP/2 multiplexes on top of a transport that refuses to be multiplexed.** One lost packet stalls *all* concurrent streams. On a clean network HTTP/2 is excellent. On a lossy network (mobile, congested Wi-Fi, 3 % loss), HTTP/2 can be *worse* than HTTP/1.1 with 6 connections — because with 6 connections, a loss only stalls 1/6 of your traffic.

You cannot fix this inside TCP. TCP's in-order byte stream is baked into every operating system kernel on earth and into middleboxes that would drop anything unfamiliar. Hence HTTP/3.

### 4.4 HTTP/3 and QUIC

**QUIC** is a transport protocol that provides what TCP provides (reliability, ordering, congestion control, flow control) plus TLS 1.3, implemented **in user space on top of UDP**.

Why UDP? Not because anyone wants UDP's unreliability — QUIC re-implements reliability itself. UDP is chosen because it is the only way to deploy a new transport across the existing internet: routers, NATs and firewalls already pass UDP, whereas a genuinely new IP protocol number would be dropped by middleboxes worldwide. (SCTP tried that and is undeployable on the public internet.) Running in user space also means QUIC can be upgraded by shipping a new browser/server binary instead of waiting a decade for OS kernels.

What QUIC gives you:

1. **Independent streams.** Each stream has its own sequence space and its own reassembly. A lost packet carrying stream 7's data blocks *only stream 7*. Streams 1–6 and 8–80 are delivered immediately. **This eliminates transport-layer HOL blocking** — the entire point of HTTP/3.
2. **Fewer handshake round trips.** TCP+TLS 1.3 costs 2 RTT for a fresh connection (1 for TCP SYN/SYN-ACK, 1 for TLS). QUIC merges transport and crypto handshakes into **1 RTT**.
3. **0-RTT resumption.** On a repeat connection to a server you've spoken to before, the client can send application data **in the very first packet**, using a cached pre-shared key. 0 RTT of setup. Caveat, and it matters: 0-RTT data is **replayable** by an attacker who captures the packet, so it must only carry **idempotent, safe** requests — `GET`s, not `POST /orders` (Lesson 17 — idempotency). Servers must enforce this; most restrict 0-RTT to safe methods automatically.
4. **Connection migration.** A TCP connection is identified by the 4-tuple `(src IP, src port, dst IP, dst port)`. Walk out of your house and your phone switches Wi-Fi → 5G, your IP changes, and **every TCP connection dies** — new handshakes, new TLS, reloaded video buffer. QUIC identifies connections by a **Connection ID** carried in the packet, independent of IP. The connection survives the network change; in-flight requests continue.
5. **Always encrypted.** There is no unencrypted QUIC. Even most transport metadata (packet numbers, ACK ranges) is encrypted, which stops middleboxes from "helpfully" rewriting it and ossifying the protocol.
6. **QPACK** replaces HPACK. Same idea, but redesigned so that header-table updates don't reintroduce HOL blocking across independent streams.

**When HTTP/3 does *not* help** — say this in an interview and you will stand out:

- **On a clean, low-loss, low-latency network** (a data centre, a wired office LAN, service-to-service traffic), there is nothing to fix. HTTP/2 already achieves full multiplexing, and HTTP/3's user-space packet handling costs more **CPU** — historically 2–3× the CPU per byte versus kernel TCP, improving with GSO/offload but still a real cost at high throughput.
- **For a single large download** (one 2 GB file). There is only one stream; there is no inter-stream blocking to eliminate. You get the handshake saving and nothing else.
- **When you are bandwidth-bound, not latency-bound.** If the link is saturated, no framing change creates capacity.
- **When the bottleneck is elsewhere** — a 900 ms database query, a 4 MB unminified JavaScript bundle, or 40 sequential API calls. Protocol upgrades cannot fix an architecture problem. Fix payload size and request count first.
- **On networks that block or throttle UDP.** Some corporate firewalls and a few mobile carriers do. Clients therefore must implement **happy eyeballs**-style fallback to HTTP/2 over TCP, which means you must keep TCP working anyway.

## 5. Request / data flow

A browser loading `https://booknook.com/books/9781492052203` and discovering 80 sub-resources, over **HTTP/3 with fallback**, start to finish:

1. **DNS**: resolve `booknook.com` → `203.0.113.10`, and also query the **HTTPS record (RR type 65)**, which may contain `alpn="h3,h2"`, telling the client HTTP/3 is available without a prior visit (Lesson 4).
2. **Protocol decision**: if the client has a cached `Alt-Svc: h3=":443"; ma=86400` header or an HTTPS RR, it races a QUIC attempt against a TCP attempt. Otherwise it connects over TCP and learns about h3 for *next* time.
3. **QUIC handshake**: client sends an Initial packet containing the TLS 1.3 ClientHello; server replies with its half; **1 RTT** and the connection is secure and usable. (A repeat visit with a cached ticket sends the `GET` inside the first flight — 0 RTT.)
4. **ALPN**: inside the TLS handshake, Application-Layer Protocol Negotiation settles on the string `h3`. (Over TCP it would be `h2` or `http/1.1`.) ALPN is why no extra round trip is spent choosing a version.
5. **Stream 0**: client opens a bidirectional stream, sends a QPACK-compressed `HEADERS` frame: `:method: GET`, `:path: /books/9781492052203`, `:authority: booknook.com`.
6. **Server** routes it, queries PostgreSQL, renders HTML (say 12 ms), and sends `HEADERS` (status 200) then one or more `DATA` frames on the same stream.
7. **Browser parses incrementally.** As the HTML preload scanner finds `<link rel=stylesheet>`, `<script src>`, `<img src>`, it issues more requests — **immediately, without waiting** — opening streams 4, 8, 12, 16 … up to the server's `max_concurrent_streams`.
8. **Prioritisation**: the browser marks CSS and fonts `u=0` (most urgent) and below-the-fold images `u=5, i` (incremental), so the server interleaves `DATA` frames accordingly.
9. **A packet is lost.** The packet carried frames for stream 36 only. QUIC delivers all other streams' data to the browser without delay; stream 36 waits ~1 RTT for a retransmit. **Under HTTP/2, all 80 streams would have waited.**
10. **Flow control** operates at two levels — per stream and per connection — so one enormous response cannot consume all of the receiver's memory.
11. **The user walks outside; Wi-Fi → 5G.** The client's IP changes. It sends packets with the same **Connection ID** from the new address; the server validates the new path (an anti-amplification challenge) and continues. No handshake, no stall.
12. **Idle**: the connection stays open (typically 30 s idle timeout, tunable) for subsequent navigations and XHR/fetch calls.
13. **Shutdown**: on deploy, the server sends `GOAWAY` with the highest stream ID it processed, so the client knows exactly which requests to safely retry elsewhere.

## 6. ASCII diagram

```
HTTP/1.1 (keep-alive, 6 connections/origin) — 80 assets
  conn1 |--REQ a--|<--RES a-->|--REQ g--|<--RES g-->|--REQ m--|...  (serial per conn)
  conn2 |--REQ b--|<--RES b-->|--REQ h--|<--RES h-->|...
  conn3 |--REQ c--|<-------- slow RES c (300ms) -------->|  <-- d,e,f queued behind it
  conn4 |--REQ d--|...                                       HTTP-layer HOL blocking
  conn5 ...        6 handshakes paid. 6 congestion windows competing.
  conn6 ...

HTTP/2 — ONE TCP connection, frames interleaved by stream ID
  app:    [s1 HDR][s3 HDR][s5 HDR] ... 80 requests sent immediately
  wire:   | s1 DATA | s5 DATA | s3 DATA | s1 DATA | s7 DATA | s3 DATA | ...
                                  ^
                           packet LOST here
  kernel: TCP holds EVERY later byte until the retransmit arrives (~1 RTT)
  result: s1, s3, s5, s7 ... all 80 streams stall together
          +---------------- TCP-layer HOL blocking -----------------+

HTTP/3 over QUIC — ONE UDP connection, independent per-stream reassembly
  wire:   | s1 | s5 | s3 | s1 | s7 | s3 | ...
                        ^
                 packet LOST (carried s3 only)
  QUIC:   s1,s5,s7,...  delivered to the app IMMEDIATELY
          s3            waits ~1 RTT for its own retransmit
  result: 1 stalled stream instead of 80.   <-- the whole point of HTTP/3

Handshake cost to first application byte (typical 50 ms RTT)
  HTTP/1.1+TLS1.3 : TCP 1-RTT + TLS 1-RTT = 2 RTT = 100 ms   (× per connection!)
  HTTP/2  +TLS1.3 : TCP 1-RTT + TLS 1-RTT = 2 RTT = 100 ms   (once)
  HTTP/3  (QUIC)  : combined              = 1 RTT =  50 ms   (once)
  HTTP/3  0-RTT   : data in first packet  = 0 RTT =   0 ms   (safe methods only)
```

## 7. Detailed example

### BookNook

BookNook's product page `/books/9781492052203` pulls, on a cold cache:

- 1 HTML document, 34 KB
- 4 CSS files, 90 KB total
- 11 JavaScript bundles, 520 KB total
- 58 images (cover art, related-title thumbnails, rating stars, logos), 1.1 MB total
- 3 WOFF2 fonts, 120 KB
- 3 JSON API calls (stock, personalised recommendations, review summary)

**80 resources, ~1.86 MB.** All on `booknook.com` except images, which the 2016-era team put on `img1/img2/img3.booknook.com` for sharding.

On **HTTP/1.1**, the browser opens 6 connections to `booknook.com` and 6 to each image shard — 24 connections, 24 TCP handshakes, 24 TLS handshakes. Chrome's waterfall shows a distinctive staircase: six bars, then six more, then six more.

Migrating to **HTTP/2** was a two-line nginx change (`listen 443 ssl; http2 on;`) — but the first deploy made the page *slower*. Why? Sharding. Three image hostnames meant three separate HTTP/2 connections, three separate HPACK dynamic tables, three independent congestion windows, and three extra DNS+TLS setups. **Collapsing the shards back onto `booknook.com` was the actual win.** Measured effect (illustrative but typical of real migrations): p75 Largest Contentful Paint 2.9 s → 1.8 s.

Later, BookNook enabled **HTTP/3**. The gain on office Wi-Fi was small (~60 ms, mostly the saved handshake RTT). The gain on **mobile 4G in a train carriage** — the company's second-largest traffic segment in India — was large: p95 LCP fell from 7.4 s to 4.6 s, because that traffic has 1–3 % packet loss and frequent cell handovers. Connection migration also cut "checkout failed, please retry" support tickets, because a cart submission no longer died when the user's phone switched from Wi-Fi to mobile data on the way out of a café.

### Contrasting example — a live video platform

A live-streaming service has the opposite profile. There is essentially **one** resource: a continuous sequence of 2-second media segments. Multiplexing 80 streams is irrelevant; there are never 80 streams.

What it gets from HTTP/3 is different:

- **Connection migration** — viewers watching on a phone while moving between networks don't rebuffer.
- **0-RTT** — a segment request after a brief network blip starts instantly instead of after 100 ms of handshake, which directly reduces the live playback buffer.
- **Unreliable datagrams** (a QUIC feature, used by WebTransport) — for ultra-low-latency interactive streams where a late frame is worthless anyway, exactly as in Lesson 8's UDP discussion.

What it does **not** get: throughput. A saturated 25 Mbps link delivers 25 Mbps regardless of protocol. The platform still had to buy CDN capacity (Phase 2, Topic 14).

**The general rule to carry away: HTTP/3's benefit is proportional to (number of concurrent streams) × (packet loss rate) + (frequency of network changes).** When all three are near zero, stay on HTTP/2 and spend your effort elsewhere.

## 8. Code or configuration example

This configuration **illustrates how the three versions are enabled and negotiated**. It is not production-ready: no rate limiting, no security headers, no cache policy, no real certificate management, no tuned worker/connection limits.

```nginx
# /etc/nginx/conf.d/booknook.conf  -- ILLUSTRATIVE, not production-ready.

server {
    # TCP listener. HTTP/1.1 and HTTP/2 both ride on this socket; which one is
    # used is decided by ALPN inside the TLS handshake -- no extra round trip.
    listen 443 ssl;
    listen [::]:443 ssl;

    # UDP listener for QUIC. Note "quic", and note it is a DIFFERENT socket:
    # HTTP/3 traffic never touches the TCP listener above. Firewalls must allow
    # inbound UDP/443 or every HTTP/3 attempt silently fails and falls back.
    listen 443 quic reuseport;
    listen [::]:443 quic reuseport;

    server_name booknook.com;

    # HTTP/2 on the TCP listener (nginx >= 1.25 syntax).
    http2 on;
    # HTTP/3 on the QUIC listener.
    http3 on;

    # QUIC mandates TLS 1.3. Leaving 1.2 enabled only affects the TCP path.
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_certificate     /etc/ssl/booknook.crt;
    ssl_certificate_key /etc/ssl/booknook.key;

    # Advertise HTTP/3 to clients that arrived over TCP. "ma=86400" = remember
    # for 24h. Without this header (or an HTTPS DNS record), a first-time visitor
    # has no way to know h3 exists and will always use h2.
    add_header Alt-Svc 'h3=":443"; ma=86400' always;

    # Max simultaneous streams per HTTP/2 connection. Too low -> the browser
    # queues requests again, re-creating HTTP/1.1-style blocking. Too high ->
    # one client can pin a lot of worker memory. 128 is the nginx default.
    http2_max_concurrent_streams 128;

    # Close idle keep-alive connections after 65s. Must be LONGER than the
    # load balancer's idle timeout, otherwise nginx closes mid-request and the
    # LB reports spurious 502s -- a classic, maddening production bug.
    keepalive_timeout 65s;

    location /assets/ {
        root /var/www;

        # Long-lived immutable caching. Content-hashed filenames make this safe.
        # Cheaper than ANY protocol upgrade: the fastest request is the one the
        # browser never sends. Fix caching before you fix protocols.
        add_header Cache-Control "public, max-age=31536000, immutable";
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000;

        # IMPORTANT: this is the CLIENT side of nginx. nginx speaks HTTP/1.1 to
        # the upstream app by default, which is usually correct -- on a loopback
        # or in-datacentre link there is ~0 loss and ~0.2 ms RTT, so HTTP/2's
        # multiplexing buys nothing and HTTP/3 would only add CPU cost.
        proxy_http_version 1.1;
        proxy_set_header Connection "";   # enables upstream keep-alive reuse

        # Never omit timeouts on an outbound call (Lesson 17).
        proxy_connect_timeout 2s;
        proxy_read_timeout 10s;
    }
}

# Redirect plaintext. HTTP/3 cannot be reached without TLS at all.
server {
    listen 80;
    server_name booknook.com;
    return 308 https://$host$request_uri;   # 308 preserves the method and body
}
```

Verify which protocol you actually got:

```bash
# -w prints the negotiated version AFTER the transfer. Don't trust config, measure.
curl -s -o /dev/null -w '%{http_version} %{time_total}s\n' https://booknook.com/

# Force HTTP/3 (requires a curl built with QUIC support, e.g. --with-openssl-quic).
curl --http3-only -s -o /dev/null -w '%{http_version}\n' https://booknook.com/

# See the Alt-Svc advertisement that teaches a client about h3.
curl -sI https://booknook.com/ | grep -i alt-svc
```

## 9. Numerical example

**The question: how long to load BookNook's 80-asset page under each protocol?**

**Assumptions — every one of these is a choice, and you should state them aloud in an interview:**

| Assumption | Value | Why |
|---|---|---|
| RTT (client ↔ server) | 50 ms | Typical for a same-country mobile/broadband user |
| Bandwidth | 20 Mbps = 2.5 MB/s | Typical 2024 mobile/home downlink |
| HTML document | 34 KB | From Section 7 |
| Other 80 assets, average | 23 KB each → 1,840 KB total | From Section 7 |
| Server think time, HTML | 10 ms | DB query + render |
| Server think time, static asset | 2 ms | Served from disk/page cache |
| Request headers, uncompressed | 800 bytes | Cookies + UA + Accept, realistic for a logged-in user |
| TLS version | 1.3 → 1 RTT | Lesson 7 |
| Packet loss (baseline) | 0 % | We add loss later |
| HTTP/1.1 connections | 6 per origin, single origin | Sharding removed |
| MSS (bytes per packet) | 1,460 | Standard Ethernet MTU minus headers |

**Step 0 — common to all: fetch the HTML.**

```
TCP handshake                    = 1 RTT = 50 ms   (HTTP/1.1, HTTP/2)
TLS 1.3 handshake                = 1 RTT = 50 ms   (HTTP/1.1, HTTP/2)
QUIC combined handshake          = 1 RTT = 50 ms   (HTTP/3)
Request → first response byte    = 1 RTT = 50 ms
Server think                     =         10 ms
HTML transfer 34 KB / 2.5 MB/s   =         13.6 ms  → round to 14 ms

HTTP/1.1 & HTTP/2 HTML time = 50 + 50 + 50 + 10 + 14 = 174 ms
HTTP/3 HTML time            =      50 + 50 + 10 + 14 = 124 ms
HTTP/3 with 0-RTT (repeat)  =           50 + 10 + 14 =  74 ms
```

**Step 1 — HTTP/1.1, 80 assets over 6 connections.**

Five more connections must be opened (handshakes overlap each other, so we pay 100 ms once, in parallel):

```
Extra connection setup (parallel) = 2 RTT = 100 ms
Rounds needed = ceil(80 assets / 6 connections) = ceil(13.33) = 14 rounds

Per asset within a round:
  request→first byte                 = 1 RTT        = 50 ms
  server think                       =                 2 ms
  transfer 23 KB at 2.5MB/s ÷ 6 conns sharing the link
      per-connection share = 2.5 MB/s / 6 = 0.4167 MB/s = 426.7 KB/s
      23 KB / 426.7 KB/s                 =                54 ms
  per-asset total                                      = 106 ms

Asset phase = 14 rounds × 106 ms = 1,484 ms

Header overhead (no compression):
  80 × 800 B = 64,000 B = 62.5 KB uploaded.
  On a typical 5 Mbps uplink = 625 KB/s → 62.5/625 = 100 ms of UPLOAD time,
  which also delays the requests themselves. Add ~100 ms.

HTTP/1.1 total = 174 (HTML) + 100 (conns) + 1,484 (assets) + 100 (headers)
               = 1,858 ms  ≈ 1.86 s
```

**Step 2 — HTTP/2, one connection, all 80 requests issued at once.**

```
No extra handshakes                                   = 0 ms
All 80 requests fly out together, then:
  first response byte                   = 1 RTT       = 50 ms
  server think (parallel, not serial)   =               2 ms
  transfer ALL 1,840 KB over the full link
      1,840 KB / 2,560 KB/s             =             719 ms

Header overhead with HPACK:
  first request ~800 B, the other 79 ~50 B each (dynamic-table indices)
  total = 800 + 79×50 = 4,750 B = 4.6 KB  (vs 62.5 KB) → ~7 ms upload

HTTP/2 total = 174 + 50 + 2 + 719 + 7 = 952 ms  ≈ 0.95 s
Improvement over HTTP/1.1 = (1858 - 952) / 1858 = 48.8 % faster
```

Notice **what actually changed**: HTTP/1.1 spent 14 × 50 ms = 700 ms purely on sequential round trips and 100 ms on redundant headers. HTTP/2 spent 50 ms on round trips and 7 ms on headers. The transfer time (719 ms) is bandwidth and is *identical* in both — it is the floor neither protocol can beat.

**Step 3 — HTTP/3 at 0 % loss.**

```
Same as HTTP/2 except the handshake is 1 RTT shorter:
HTTP/3 total (fresh)  = 124 + 50 + 2 + 719 + 7 = 902 ms
HTTP/3 total (0-RTT)  =  74 + 50 + 2 + 719 + 7 = 852 ms

Gain over HTTP/2 = 50 ms fresh (5.3 %), 100 ms on a repeat visit (10.5 %)
```

**On a clean network, HTTP/3 buys you one round trip. That's it.** If someone claims HTTP/3 made their site "twice as fast" on a good connection, be sceptical.

**Step 4 — now add 2 % packet loss (a congested mobile cell).**

```
Total payload = 1,840 KB + 34 KB = 1,874 KB = 1,918,976 bytes
Packets       = 1,918,976 / 1,460 = 1,315 packets
Expected losses = 1,315 × 0.02 = 26.3 → ~26 lost packets

HTTP/2 (TCP): EVERY loss stalls the single byte stream for the whole
connection until retransmission. With fast retransmit, a recovery costs
roughly 1 RTT = 50 ms. Losses are spread over time and partially overlap;
assume a conservative 50 % overlap:
    added stall = 26 × 50 ms × 0.5 = 650 ms
    HTTP/2 under loss ≈ 952 + 650 = 1,602 ms

Also: TCP congestion control halves the window on each loss event,
so the effective throughput drops further. Ignore that here (it makes
HTTP/2 look better than reality).

HTTP/3 (QUIC): each loss stalls only its own stream. A stalled image
delays THAT image, not the page. Only losses hitting render-blocking
resources (HTML, 4 CSS, 3 fonts = 8 of 80 streams ≈ 10 % of bytes) delay
the visible page:
    page-blocking losses = 26 × 0.10 = 2.6 → ~3
    added stall = 3 × 50 ms = 150 ms
    HTTP/3 under loss ≈ 902 + 150 = 1,052 ms

Advantage of HTTP/3 over HTTP/2 at 2 % loss = (1602 - 1052)/1602 = 34 % faster
```

**Summary table**

| Scenario | HTTP/1.1 | HTTP/2 | HTTP/3 |
|---|---|---|---|
| Clean network, cold | 1,858 ms | 952 ms | 902 ms |
| Clean network, repeat visit | 1,858 ms | 952 ms | 852 ms |
| 2 % packet loss | ~2,400 ms* | 1,602 ms | 1,052 ms |
| Bytes of request headers | 62.5 KB | 4.6 KB | 4.6 KB |
| TCP/QUIC connections | 6 | 1 | 1 |

\* HTTP/1.1 under loss degrades too, but only 1/6 of traffic stalls per loss event; estimated, not derived.

**The punchline nobody expects:** every column has a 719 ms transfer floor. Cutting the JavaScript bundle from 520 KB to 150 KB saves `370 KB / 2,560 KB/s = 145 ms` **in every single column**, costs no infrastructure change, and compounds with the protocol gain. **Payload reduction beats protocol upgrades.** Say that in an interview.

## 10. Trade-offs

**HTTP/1.1**

- *Advantages*: universally supported; trivially debuggable with `telnet`/`tcpdump` (it's text); simple to implement; no multiplexing bugs; works through every middlebox on earth.
- *Disadvantages*: HOL blocking; 6-connection ceiling; uncompressed, hugely repetitive headers; handshake cost multiplied by connection count.
- *Operational cost*: more sockets per client, so higher file-descriptor and memory usage on the server.

**HTTP/2**

- *Advantages*: removes HTTP-layer HOL blocking; one connection per origin (far fewer sockets, fewer handshakes); HPACK saves real bytes on header-heavy APIs; `RST_STREAM` lets a browser cancel a single request cheaply; `GOAWAY` enables graceful draining during deploys.
- *Disadvantages*: TCP-layer HOL blocking remains and is *concentrated* onto one connection; binary wire format means you need tooling (`nghttp`, Wireshark) to debug; prioritisation is effectively unreliable; makes domain sharding harmful rather than neutral.
- *Operational cost*: load balancers must understand h2 end-to-end or you get protocol downgrade surprises; long-lived connections make deploys and connection draining a real design concern (and they break naive round-robin load balancing — see Section 15).

**HTTP/3**

- *Advantages*: no transport HOL blocking; 1-RTT and 0-RTT handshakes; connection migration; mandatory encryption; protocol evolution no longer blocked on OS kernels or middleboxes.
- *Disadvantages*: higher CPU per byte (user-space packet processing, ~2–3× TCP historically); UDP is blocked or deprioritised on some networks so you must keep a TCP path alive forever; much younger tooling; 0-RTT replay is a genuine security footgun; debugging requires `qlog`/`qvis` rather than familiar TCP tools.
- *Operational cost*: two listeners (TCP + UDP) and two code paths to monitor; firewall and cloud-LB support for UDP/443 is not universal; per-connection state in user space has different memory characteristics than kernel sockets.

**When each becomes unnecessary complexity**: deploying HTTP/3 on an internal, wired, east-west service mesh is complexity for no gain — the loss rate is ~0 and the RTT is 0.2 ms. Use HTTP/2 there (gRPC already does, Lesson 11).

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| UDP/443 blocked by a corporate firewall | Nothing visible — a ~200 ms slower first load | Server-side: h3 request share drops for an ASN. Client-side: QUIC attempt times out | Browser races QUIC and TCP and falls back to HTTP/2; the client remembers the failure and stops trying h3 for that origin |
| Packet loss spike on a mobile cell (HTTP/2) | Page freezes mid-render, then everything appears at once | p95/p99 LCP rises while the server's own latency metrics stay flat — the classic "server is fine, users complain" signature (Lesson 16) | Loss subsides; TCP retransmits. Real fix: move that traffic to HTTP/3 |
| `http2_max_concurrent_streams` set too low (e.g. 8) | Page loads at roughly HTTP/1.1 speed despite "having HTTP/2" | Waterfall shows queued requests; server logs show stream-limit stalls | Raise the setting and redeploy; monitor memory per connection |
| Load balancer idle timeout < origin keep-alive timeout | Sporadic 502s on perhaps 0.1 % of requests, impossible to reproduce | 502 rate correlates with request inter-arrival gaps near the timeout value | Make the origin's `keepalive_timeout` strictly greater than the LB's idle timeout |
| 0-RTT replay attack | A duplicated action — e.g. a coupon redeemed twice | Duplicate-request detection; anomalous repeat rate on a single endpoint | Restrict 0-RTT to safe/idempotent methods; enforce idempotency keys (Lesson 17) |
| Network switch (Wi-Fi → 5G) on TCP | "Connection lost", video rebuffers, upload restarts | Client error events spike at handover; correlates with IP change | Application-level retry — or use HTTP/3, where connection migration makes it a non-event |
| Server sends `GOAWAY` during a deploy | Nothing, if handled correctly | Client-side retry metrics tick up briefly | Client retries streams above the last-processed ID on a new connection |
| Domain sharding left in place after an HTTP/2 migration | Page is *slower* after an "optimisation" | Compare connection count and LCP before/after; look for multiple origins in the waterfall | Consolidate assets onto one origin |
| Middlebox mangles h2 frames | Random connection resets on one ISP | Errors cluster by ASN/geography | Fall back to HTTP/1.1 for that path; or move to HTTP/3, whose encryption prevents mangling |

## 12. Alternatives

| Approach | What it is | Good at | Bad at | Use when |
|---|---|---|---|---|
| **HTTP/1.1** | Text, one request at a time per connection | Universality, debuggability, simple proxies | Concurrency, header bloat | Legacy clients; simple internal APIs; server-to-server on a LAN |
| **HTTP/2** | Binary frames, multiplexed over TCP | Many concurrent requests on clean networks; gRPC's transport | Lossy networks; one connection = one failure domain | Most web traffic today; all internal gRPC |
| **HTTP/3 / QUIC** | HTTP/2 semantics over UDP-based QUIC | Lossy/mobile networks; network changes; handshake cost | CPU cost; UDP-hostile networks; single-stream downloads | Public-facing sites with significant mobile traffic |
| **WebSocket** | Persistent bidirectional frames after an HTTP upgrade | Server-initiated push, chat, live dashboards | No request/response semantics, no caching, scaling stateful connections (Lesson 2) | Real-time bidirectional data |
| **Server-Sent Events (SSE)** | One-way text stream over plain HTTP | Simple server→client push; auto-reconnect built in | Server→client only; HTTP/1.1 connection-limit issues | Notifications, live order status |
| **WebTransport** | Streams *and* unreliable datagrams over QUIC | Games, low-latency media, replacing WebSocket on h3 | Young; browser/server support still maturing | Latency-critical interactive apps |
| **gRPC** | RPC framing on HTTP/2 (Lesson 11) | Typed, streaming, efficient service-to-service calls | Not natively browser-callable | Internal microservice communication |
| **Raw TCP / custom protocol** | Your own framing | Maximum control, minimal overhead | You re-implement everything, no ecosystem | Database wire protocols, specialised infrastructure |

## 13. When to use it

1. **Use HTTP/3 for public, consumer-facing traffic with meaningful mobile share.** If >30 % of your users are on cellular networks or in regions with 1 %+ loss, HTTP/3's elimination of transport HOL blocking plus connection migration is a measurable, user-visible win — as the Step-4 calculation showed (34 % faster at 2 % loss).
2. **Use HTTP/2 for internal service-to-service RPC.** Data-centre links have ~0 loss and sub-millisecond RTT, so QUIC's advantage evaporates while its CPU cost does not. gRPC chose HTTP/2 for exactly this reason (Lesson 11).
3. **Use HTTP/2 (or 3) whenever a page needs many small resources from one origin.** Dashboards, image grids, icon-heavy apps. The more concurrent requests, the bigger the multiplexing win.
4. **Use HTTP/1.1 deliberately for simple, low-volume internal APIs and for anything that must traverse ancient middleware.** A cron job calling an internal endpoint twice an hour gains nothing from h2 and loses debuggability.
5. **Enable HTTP/3 when you have long-lived requests and mobile users** — file uploads, long polling, video — because connection migration turns "network change = failure" into "network change = nothing happened".

## 14. When *not* to use it

1. **Don't deploy HTTP/3 to fix a payload problem.** If your page ships 4 MB of JavaScript or makes 40 sequential API calls, the protocol is not your bottleneck. In the Section 9 numbers, 719 ms of a 902 ms load was raw bytes on the wire. Shrink the bytes first; it is cheaper and helps every protocol.
2. **Don't run HTTP/3 on internal, high-throughput east-west links.** You pay 2–3× CPU per byte to solve a loss problem you don't have. For a service pushing 5 Gbps between racks, that is real money and real latency.
3. **Don't use HTTP/2 or HTTP/3 as a substitute for WebSocket/SSE when you need server-initiated messages.** Server push is deprecated and was never a general messaging mechanism. Use SSE or WebSocket, or an async channel (Phase 4, Topic 5 — Message queues).
4. **Don't shard domains under HTTP/2 or HTTP/3.** It fragments the connection, the congestion window, and the header-compression table. What helped in 2012 hurts now.
5. **Don't enable 0-RTT on endpoints with side effects.** A replayed 0-RTT `POST /orders` creates a duplicate order. Restrict it to safe methods until you have idempotency keys (Lesson 17).

## 15. Common mistakes

**Beginner mistakes**

- Believing HTTP/2 "replaced" HTTP/1.1's semantics. Methods, status codes, headers and caching rules are **identical**; only the encoding changed. If you understand Lessons 5 and 6, you understand HTTP/2's semantics already.
- Thinking HTTP/3 "uses UDP, so it's unreliable". QUIC rebuilds reliability in user space. It is as reliable as TCP; it just isn't *ordered across unrelated streams*, which is the entire point.
- Expecting a protocol upgrade to fix a slow database. Protocols move bytes; they don't make your query faster.
- Enabling `http2 on;` and never verifying. Always measure with `curl -w '%{http_version}'` — a load balancer in front of you may be terminating h2 and speaking HTTP/1.1 to your origin anyway.
- Confusing **parallel connections** with **concurrency**. Six connections do not give you six times the bandwidth; they give you six independent queues sharing the same pipe.
- Leaving domain sharding in place after migrating, then concluding "HTTP/2 didn't help".

**Experienced-engineer mistakes**

- **Forgetting that HTTP/2 breaks naive load balancing.** With HTTP/1.1, each request can go to a different backend. With HTTP/2, a client opens **one** long-lived connection and all its requests pin to **one** backend. A new backend added during a traffic spike receives *zero* traffic from existing clients. Fixes: L7 load balancing that balances per-stream, or periodic `GOAWAY`-driven connection recycling (`max_connection_age`). This bites every gRPC deployment (Phase 2, Topic 7).
- Relying on HTTP/2 priorities. They're advisory, inconsistently implemented, and were redesigned in RFC 9218. Control the order of what you *request* instead.
- Mismatched keep-alive timeouts between LB and origin, producing an untraceable trickle of 502s.
- Setting `max_concurrent_streams` very high to "improve throughput", then discovering one abusive client can allocate hundreds of per-stream buffers — a memory-exhaustion DoS. (See also the 2023 **HTTP/2 Rapid Reset** attack, CVE-2023-44487, where attackers opened and immediately `RST_STREAM`'d streams to force unbounded server work. Mitigation: rate-limit stream creation, not just requests.)
- Enabling 0-RTT globally without auditing which endpoints are safe to replay.
- Not instrumenting per-protocol metrics. You cannot tell whether h3 helped if your dashboards don't split latency by negotiated protocol and by network type.
- Assuming HTTP/3 is on because the config says so — if your cloud load balancer doesn't forward UDP/443, every client silently uses h2.

## 16. Interview perspective

This topic appears as a question about *why*, never *what*. Patterns that land well:

- **Lead with the two layers of HOL blocking.** "HTTP/2 fixed HTTP-layer head-of-line blocking with streams, but it still runs on TCP, which enforces one ordered byte stream — so a single lost packet stalls all streams. HTTP/3 moves to QUIC so each stream reassembles independently." That one sentence demonstrates you understand the whole evolution.
- **Quantify before recommending.** "At 50 ms RTT, HTTP/1.1 spends 14 sequential round trips on 80 assets — 700 ms of pure latency. HTTP/2 spends one. That's where the win comes from."
- **Name when it does *not* help.** Clean data-centre links, single large downloads, bandwidth-bound workloads. This is the fastest way to signal real experience.
- **Mention the load-balancing consequence.** "Long-lived h2 connections pin clients to backends, so I'd set a max connection age and use L7 balancing." Interviewers for infrastructure roles love this.
- **Point at payload before protocol.** "Before I enable h3 I'd check bundle size and request count — in my estimate, 75 % of the load time was raw bytes."
- **Know the deprecations.** Pipelining (replaced by multiplexing), server push (replaced by 103 Early Hints), domain sharding (now an anti-pattern), RFC 7540 priorities (replaced by RFC 9218).

**Red flags interviewers watch for:** saying HTTP/3 is "unreliable because it's UDP"; claiming HTTP/2 eliminates all head-of-line blocking; proposing domain sharding; being unable to name a case where a newer version doesn't help; recommending a protocol upgrade without ever mentioning payload size or request count; not knowing that HTTP/2 and HTTP/3 keep identical HTTP semantics.

## 17. Knowledge check

Answer these before moving on. Write a line or two for each.

**Conceptual**

1. Explain the difference between HTTP-layer and TCP-layer head-of-line blocking. For each, state which protocol version eliminates it and by what mechanism. Then explain why HTTP/2 can be *slower* than HTTP/1.1 with 6 connections on a 3 %-loss network.
2. HTTP/2 server push was removed from Chrome, and `103 Early Hints` was introduced. Explain what information the server lacked that made push a bad bet, and why moving the decision to the client fixes it.
3. QUIC runs over UDP but is reliable. Explain (a) why UDP was chosen rather than a brand-new IP protocol, and (b) what specifically QUIC had to re-implement that TCP already provided.

**Practical scenario**

4. BookNook has just migrated to HTTP/2. p75 page load got 5 % *worse*, not better. The team's assets are served from `img1`, `img2` and `img3.booknook.com`; the nginx config sets `http2_max_concurrent_streams 8;`; and the AWS ALB in front has a 60-second idle timeout while nginx's `keepalive_timeout` is 65 s. List every problem you can see, explain the mechanism by which each one hurts, rank them by expected impact, and say what you would measure to confirm each diagnosis before changing anything.

**Calculation**

5. A BookNook competitor serves a page of **120 assets averaging 18 KB**, plus a 40 KB HTML document, to users with **RTT 80 ms**, **bandwidth 12 Mbps**, server think time **3 ms** per asset and **15 ms** for the HTML, TLS 1.3, and 6 connections per origin for HTTP/1.1.
   (a) Compute total load time for HTTP/1.1, HTTP/2 and HTTP/3 (fresh connection), showing every step.
   (b) What fraction of the HTTP/2 time is pure bandwidth that no protocol can remove?
   (c) At **1.5 % packet loss** with a 1,460-byte MSS, how many packets are lost, and what stall does that add under HTTP/2 if each loss costs 1 RTT and 50 % of recoveries overlap?
   (d) The team can either enable HTTP/3 or cut total asset bytes by 35 %. Which saves more milliseconds at 0 % loss, and which at 1.5 % loss? State every assumption you add.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 10.

---

**Progress checklist**

Phase 1:
- `[x] 1. Client-server architecture`
- `[x] 2. Stateful versus stateless servers`
- `[x] 3. IP addresses, ports, sockets, domain names`
- `[x] 4. DNS resolution and DNS caching`
- `[x] 5. HTTP requests and responses`
- `[x] 6. HTTP methods, headers, cookies, sessions, status codes`
- `[x] 7. HTTPS and TLS`
- `[x] 8. TCP versus UDP`
- `[x] 9. HTTP/1.1 vs HTTP/2 vs HTTP/3`
- `[ ] 10. REST APIs`
- `[ ] 11. RPC and gRPC`
- `[ ] 12. GraphQL`
- `[ ] 13. Synchronous versus asynchronous communication`
- `[ ] 14. Latency, throughput, bandwidth, concurrency`
- `[ ] 15. Availability, reliability, durability, fault tolerance`
- `[ ] 16. Average latency vs p50/p95/p99, tail latency`
- `[ ] 17. Idempotency, retries, timeouts, exponential backoff`

**NEXT LESSON: Phase 1, Topic 10 — REST APIs**

---

# Lesson 10 — REST APIs

*Phase 1, Topic 10*

## 1. Simple definition

**An API (Application Programming Interface)** is the contract one program offers another: the list of things you may ask for, how to ask, and what you get back. BookNook's browser, its iOS app, and a partner's price-comparison crawler all talk to the same API.

**REST (REpresentational State Transfer)** is an *architectural style* for such APIs, described by Roy Fielding in his 2000 doctoral thesis. Its central idea:

> Model your system as a set of **resources** (nouns) identified by **URIs**, and manipulate them using the **uniform, standard methods** of HTTP (verbs), exchanging **representations** (JSON, XML, HTML) of those resources.

Three definitions you need immediately:

- **Resource**: a *thing* in your domain that is worth naming — a book, an order, a customer's cart. Not an action.
- **URI (Uniform Resource Identifier)**: the name/address of that resource, e.g. `/books/9781492052203`. (A URL is a URI that also tells you how to reach it.)
- **Representation**: a concrete serialisation of the resource at a moment in time — the JSON document you actually receive. The resource is the abstract concept; the JSON is a *representation* of it. That distinction is where the "R" in REST comes from, and it is why `Accept: application/json` versus `Accept: application/xml` can return different bytes for the same URI.

The honest headline, stated up front: **the overwhelming majority of APIs called "REST" are not REST.** They are HTTP+JSON APIs that use nouns in URLs and a few HTTP verbs. That is fine and usually correct — but you should know the difference, because interviewers ask.

## 2. Why it exists

Before REST, remote APIs were mostly **SOAP** (Simple Object Access Protocol) or **XML-RPC**. A SOAP call looked like this:

```
POST /BookService HTTP/1.1
Content-Type: text/xml; charset=utf-8
SOAPAction: "http://booknook.com/GetBookDetails"

<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body><GetBookDetails><isbn>9781492052203</isbn></GetBookDetails></soap:Body>
</soap:Envelope>
```

Everything is a `POST` to one endpoint. Observe the consequences:

1. **No caching.** `POST` is non-cacheable by definition, so no browser, proxy, or CDN can cache anything. Every read hits your origin.
2. **No meaningful URLs.** Every request targets `/BookService`, so logs, metrics, rate limits and access rules cannot distinguish "read a book" from "delete all books".
3. **No standard error semantics.** Errors hide inside the XML body; the HTTP status is `200 OK` even for failures, so every piece of generic infrastructure believes everything is fine.
4. **Heavy tooling.** You needed a WSDL file and a code generator to make a single call.

REST's bet was: **HTTP is already a complete, well-understood application protocol with verbs, status codes, caching, content negotiation, and authentication. Stop tunnelling your own protocol inside it; use it as designed.**

What you get for free by doing that:

- Any CDN, proxy or browser can cache your `GET`s (Phase 2, Topic 14).
- Load balancers can route by path; WAFs can block by path; rate limiters can bucket by path.
- `curl`, browsers, Postman, and every HTTP library already work — no code generation required.
- Monitoring tools understand `5xx` without being taught your error format.

That is a huge amount of free infrastructure, and it is the real reason REST won.

## 3. Real-world analogy

**A public library with a strict filing system.**

- Every item has a permanent **shelf address** (the URI): `/floor-2/fiction/kleppmann-m/designing-data-intensive`. The address names the *thing*, never the action.
- There is a small, fixed set of **actions** anyone may perform, and everyone already knows them: *look at* (GET), *add a new item* (POST), *replace an item* (PUT), *amend a label* (PATCH), *remove* (DELETE).
- You don't need a librarian to explain a new section. Once you know the addressing scheme and the five actions, you can use any section of the library — this is the **uniform interface**.
- The librarian doesn't remember you between visits. Each request must carry your library card (**statelessness**, Lesson 2).
- Some items are marked "photocopy freely, valid for a year" and others "reference only, check each time" — **cache-control**.

**Where the analogy breaks down**

| Library | Real REST API |
|---|---|
| Shelf addresses are physical, so one book is in exactly one place | The same resource can be legitimately reachable at several URIs (`/books/123`, `/authors/7/books/123`, `/me/cart/items/9`), which creates real cache-invalidation headaches |
| The catalogue card tells you where related books are | Fielding's REST requires the *response itself* to carry links to what you can do next (HATEOAS). Almost no real API does this; clients hardcode URLs instead |
| Actions on items are independent | A single business operation ("check out") may touch cart, inventory, payment and order — which maps badly onto "one resource, one verb" |
| Removing a book is instant and obvious | A `DELETE` whose response is lost leaves the client unsure whether it happened. The second `DELETE` returns 404 — did *I* delete it, or someone else? (Lesson 17) |
| Nobody asks the librarian for 40 books at once to assemble one page | A REST client routinely needs 40 round trips to build one screen — the problem Lessons 11 and 12 exist to solve |

The last two rows are the motivation for everything that follows in this phase.

## 4. Detailed technical explanation

### 4.1 Resources and URI design

**Rule 1 — URIs name nouns, not verbs.** The verb is the HTTP method.

| Bad | Good | Why |
|---|---|---|
| `POST /createBook` | `POST /books` | The method already says "create" |
| `GET /getBookById?id=123` | `GET /books/123` | Identity belongs in the path |
| `POST /books/123/delete` | `DELETE /books/123` | Standard method = standard semantics everywhere |
| `GET /searchBooks?q=sql` | `GET /books?q=sql` | Search is a filtered view of the collection |

**Rule 2 — plural collection nouns, then an identifier.**

```
/books                      collection of all books
/books/9781492052203        one book, identified by ISBN
/books/9781492052203/reviews   that book's reviews (a sub-collection)
/reviews/8812                  a review, addressable on its own
```

Note that `/books/{isbn}/reviews` and `/reviews/{id}` can both exist. The first is a *scoped collection*; the second is the canonical address of one item.

**Rule 3 — nest at most one level.** `/customers/42/orders/991/items/3/book/reviews/12` is unusable. Once a resource has its own ID, give it a top-level URI: `/order-items/3`.

**Rule 4 — lowercase, hyphenated, no file extensions, no trailing slash.** `/gift-cards`, not `/GiftCards`, `/gift_cards`, or `/giftCards.json`. Use `Accept` headers for format, not `.json`.

**Rule 5 — the few legitimate "verb" endpoints.** Some operations genuinely are not CRUD on a noun: `POST /orders/991/cancel`, `POST /sessions` (login), `POST /payments/55/refunds`. Purists object; practitioners accept these as **controller resources**. The pragmatic test: *could this be modelled as creating a resource?* `POST /orders/991/cancellations` creates a cancellation record and is both RESTful and auditable. Prefer that when the action is worth remembering.

**Rule 6 — IDs should be opaque to the client.** Whether `991` is a bigint, a UUID or a ULID is your business. Clients that parse your IDs will break when you change them. Also: sequential integer IDs leak business volume (a competitor orders twice and learns your daily order count) and enable enumeration attacks. Prefer UUIDv7 or ULID for public IDs.

### 4.2 The six REST constraints

Fielding defined REST as six constraints. An API is only "RESTful" if it satisfies them.

| # | Constraint | What it means | Do real APIs follow it? |
|---|---|---|---|
| 1 | **Client–server** | Separate UI concerns from data storage concerns; they evolve independently | Yes, universally |
| 2 | **Stateless** | Every request contains everything needed to understand it; the server stores no client session context (Lesson 2) | Mostly yes — tokens instead of server sessions |
| 3 | **Cacheable** | Responses must declare themselves cacheable or not, so clients and intermediaries can reuse them | Partially — many APIs send no cache headers at all |
| 4 | **Uniform interface** | Resource identification, manipulation via representations, self-descriptive messages, and **HATEOAS** | Rarely in full — HATEOAS is almost always missing |
| 5 | **Layered system** | A client cannot tell whether it's talking to the origin or to a proxy/LB/CDN/gateway | Yes, by accident — this is how the internet works |
| 6 | **Code-on-demand** (optional) | Server may ship executable code to the client (e.g. JavaScript) | Rare for APIs; the web itself does it |

Because constraint 4 is routinely violated, **most "REST APIs" are more accurately "HTTP APIs" or "JSON-over-HTTP"**. Fielding himself wrote a famous 2008 blog post titled *"REST APIs must be hypertext-driven"* complaining about exactly this. In practice the industry settled on a useful subset. Know the gap; don't be pedantic about it in a design review.

### 4.3 The Richardson Maturity Model

Leonard Richardson's four-level ladder is the standard way to describe how "RESTful" an API actually is.

| Level | Name | Description | Example |
|---|---|---|---|
| **0** | The Swamp of POX | One URI, one method, everything tunnelled in the body | `POST /api` with `{"action":"getBook","isbn":"..."}`. SOAP lives here |
| **1** | Resources | Many URIs, still mostly one method | `POST /books/123/get`, `POST /books/123/delete` |
| **2** | HTTP verbs | Correct methods + correct status codes per resource | `GET /books/123` → 200, `DELETE /books/123` → 204. **~95 % of production "REST" APIs stop here** |
| **3** | Hypermedia controls (HATEOAS) | Responses include links telling the client what it can do next | Below |

**HATEOAS** = *Hypermedia As The Engine Of Application State*. A Level-3 response:

```json
{
  "id": 991,
  "status": "awaiting_payment",
  "total_paise": 128000,
  "_links": {
    "self":   { "href": "/orders/991" },
    "pay":    { "href": "/orders/991/payments", "method": "POST" },
    "cancel": { "href": "/orders/991/cancellations", "method": "POST" }
  }
}
```

The promise: the client never hardcodes URLs or business rules. When the order ships, the `cancel` link simply disappears, and the client's "Cancel" button vanishes without a client release. Clients discover the API by following links from a single root URI, exactly as a human browses a website.

**Why HATEOAS is rare — be specific, don't just say "it's hard":**

1. **Clients ignore it.** Writing a generic link-following client is genuinely difficult; it's far easier to write `fetch('/orders/' + id)`. Developers do the easy thing, so the links go unused and become dead weight.
2. **It doesn't remove the coupling it claims to.** The client still has to know what `"pay"` *means* and what fields to send. You've moved the coupling from URL structure to link-relation names, not eliminated it.
3. **Payload cost.** Links can be 30–50 % of a small response's bytes. On a 50-item list that's significant.
4. **Tooling gap.** OpenAPI, code generators, and typed clients all assume fixed paths. HATEOAS fights the ecosystem.
5. **The actual problem it solves is rarely the binding constraint.** Most teams control both client and server and can coordinate a deploy.

Where HATEOAS *does* earn its keep: long-lived public APIs with uncontrolled third-party clients, and workflow-heavy domains where valid next-actions change with state (payments, insurance claims, government services). The **Stripe** and **PayPal** APIs include links; most internal APIs don't.

### 4.4 Collections: pagination, filtering, sorting

**Never return an unbounded collection.** `GET /books` on a 50-million-row table will either OOM your server or time out. Always paginate, always have a default page size (typically 20–50) and a hard maximum (typically 100–200).

**Offset pagination** — the obvious design:

```
GET /books?limit=20&offset=0
GET /books?limit=20&offset=20
→ SQL: SELECT ... ORDER BY created_at DESC LIMIT 20 OFFSET 20
```

*Advantages*: trivially simple; you can jump to page 500 directly; you can show "page 7 of 243".

*Two serious problems*:

1. **Deep pagination is O(offset).** The database cannot teleport to row 100,000. It must walk the index and **discard** 100,000 rows before returning 20. The cost grows linearly with the page number. Quantified in Section 9.
2. **Drift.** If a new book is inserted while the user is on page 1, then page 2 re-shows an item from page 1 (and an item is silently skipped if something is deleted). On a feed with writes, users see duplicates.

**Cursor (keyset) pagination** — the production design:

```
GET /books?limit=20
→ { "items": [...], "next_cursor": "eyJjcmVhdGVkX2F0IjoiMjAyNi0wMS0xMVQwOToxNDoyMloiLCJpZCI6ODgxMn0" }

GET /books?limit=20&cursor=eyJjcmVhdGVkX2F0...
→ SQL: SELECT ... WHERE (created_at, id) < ('2026-01-11T09:14:22Z', 8812)
       ORDER BY created_at DESC, id DESC LIMIT 20
```

The cursor is an opaque (usually base64-encoded) copy of the sort key of the last row returned. The database uses the index to **seek** directly to that position — O(log n) regardless of depth.

Critical detail: the cursor must include a **tiebreaker** (`id`) because `created_at` is not unique. Comparing the tuple `(created_at, id)` guarantees a total order; comparing only `created_at` silently drops or duplicates rows sharing a timestamp. This is a genuinely common production bug.

*Trade-off*: you cannot jump to "page 500", and you cannot show a total count cheaply. For infinite-scroll feeds and API consumers iterating everything, that's fine. For an admin table with numbered pages, offset is acceptable — because admins are few and their tables are small.

**Filtering and sorting.** Keep it boring and explicit:

```
GET /books?genre=databases&min_price=20000&max_price=80000&sort=-published_at,title
```

- `-field` = descending, bare `field` = ascending; comma-separated for multi-key.
- **Allow-list** every filterable and sortable field. Never pass user strings into SQL `ORDER BY` — that's an injection vector — and never let a client sort by an unindexed column, which turns a 2 ms query into a 4-second full table scan.
- Don't invent a query language in your query string (`?filter=price>200 AND genre="db"`). You'll end up writing a parser, and you'll have built a worse GraphQL (Lesson 12).

### 4.5 Versioning

Your API is a contract. Breaking it breaks other people's software — possibly a mobile app version that 8 % of users will never upgrade.

**What counts as breaking:** removing a field, renaming a field, changing a type (`"42"` → `42`), adding a required request field, tightening validation, changing an error code, changing default pagination size. **Non-breaking:** adding an optional request field, adding a response field (*if* clients ignore unknown fields — document that requirement loudly).

| Strategy | Example | Advantages | Disadvantages | Used by |
|---|---|---|---|---|
| **URI path** | `/v1/books`, `/v2/books` | Obvious; browsable; trivially cacheable; easy to route at the LB; easy to log | "Not RESTful" (same resource, two URIs); version sprawl across every endpoint; encourages whole-API version bumps | Twitter, GitHub (historically), most internal APIs |
| **Query parameter** | `/books?version=2` | Easy to default; no routing change | Easy to forget; pollutes caching keys; mixes versioning with filtering | Some legacy APIs |
| **Custom header** | `X-API-Version: 2` | URIs stay clean and canonical | Invisible in a browser; `curl` needs extra flags; caches must `Vary` on it or they'll serve a v1 body to a v2 client | Azure, some enterprise APIs |
| **Media type / content negotiation** | `Accept: application/vnd.booknook.v2+json` | Fielding-approved; versions *representations*, which is what actually changes; per-resource granularity | Verbose; poor tooling; easy to misconfigure caches | GitHub (`application/vnd.github+json`), early Heroku |
| **Date-based pinning** | `Stripe-Version: 2026-01-11` | Fine-grained; the account is pinned at signup so nothing ever breaks; server translates between versions | Server must maintain a chain of transformations forever — real engineering investment | Stripe |

**Practical recommendation**, stated with its dependency: *if* you control all clients (internal services, or a web app you deploy), prefer **additive, never-break evolution** with no version at all — add fields, never remove, deprecate with headers and metrics. *If* you have third-party or mobile clients you cannot force-upgrade, use **URI path versioning** for its operational simplicity, keep version count ≤ 2, and publish a sunset date using the `Sunset` and `Deprecation` HTTP headers (RFC 8594).

### 4.6 Errors: RFC 9457 `application/problem+json`

The two failure modes to avoid: (a) returning `200 OK` with `{"error": "..."}` — this lies to every proxy, monitor and retry policy in the path; (b) inventing a bespoke error shape per endpoint.

The standard (RFC 9457, which obsoletes RFC 7807) defines `application/problem+json`:

```json
{
  "type": "https://booknook.com/problems/insufficient-stock",
  "title": "Insufficient stock",
  "status": 409,
  "detail": "Only 2 copies of 9781492052203 remain; 5 were requested.",
  "instance": "/orders/991",
  "isbn": "9781492052203",
  "available": 2,
  "requested": 5,
  "trace_id": "0af7651916cd43dd8448eb211c80319c"
}
```

- `type` — a stable URI identifying the *kind* of problem. **Clients branch on this, not on `detail`.**
- `title` — short, human-readable, same for all occurrences of this type.
- `status` — must match the HTTP status code.
- `detail` — human-readable, specific to *this* occurrence. Never parse it.
- `instance` — which resource had the problem.
- Extension members (`available`, `requested`) — machine-readable specifics so the UI can say something useful.
- `trace_id` — include it always; it's how a support ticket becomes a log query (Phase 5, Topic 4 — Distributed tracing).

Status-code discipline, briefly (full treatment was Lesson 6): `400` malformed, `401` not authenticated, `403` authenticated but not allowed, `404` not found, `409` conflict with current state, `422` syntactically valid but semantically wrong, `429` rate limited (always with `Retry-After`), `500` our bug, `503` temporarily down (with `Retry-After`). **Never return 200 for an error.**

### 4.7 PATCH semantics

`PUT` **replaces** the entire resource. If you `PUT` a book with only `{"price_paise": 29900}`, a correct server sets every other field to its default or null. This surprises people constantly.

`PATCH` applies a *partial modification*, but the spec deliberately does not say how. Two standard formats:

**JSON Merge Patch** (RFC 7386), `Content-Type: application/merge-patch+json`:

```json
{ "price_paise": 29900, "promo_tag": null }
```

Rules: present keys are set; `null` means **delete this field**; absent keys are untouched. Simple and widely used. Limitation: because `null` means delete, you **cannot set a field to null**, and you cannot modify one element of an array — you must send the whole array.

**JSON Patch** (RFC 6902), `Content-Type: application/json-patch+json`:

```json
[
  { "op": "replace", "path": "/price_paise", "value": 29900 },
  { "op": "add",     "path": "/tags/-",      "value": "bestseller" },
  { "op": "test",    "path": "/version",     "value": 7 }
]
```

An ordered list of operations. The `test` op gives you **optimistic concurrency** — the whole patch fails if `version` isn't 7. More powerful, more verbose, harder for clients.

**The idempotency trap:** `PUT` and `DELETE` are idempotent (applying twice = applying once). `PATCH` is **not necessarily** idempotent — `{"op":"add","path":"/tags/-","value":"sale"}` appends a tag every time you run it. Plan retries accordingly (Lesson 17).

**Concurrency:** prefer `ETag` + `If-Match` for updates. Server returns `ETag: "v7"` on `GET`; client sends `If-Match: "v7"` on `PATCH`; server returns **412 Precondition Failed** if someone else changed it first. This is lost-update prevention using nothing but standard HTTP, and it's the single most under-used REST feature.

### 4.8 Bulk endpoints and idempotency keys

**Bulk operations.** 500 individual `POST`s cost 500 round trips. Options:

- `POST /books/bulk` with an array body, returning **207 Multi-Status** with a per-item result array. Partial success is the norm; make it explicit.
- A **batch job resource**: `POST /import-jobs` returns `202 Accepted` with `Location: /import-jobs/77`; the client polls or receives a webhook. Correct when the work takes longer than an HTTP timeout.

Rules: cap batch size (e.g. 100 items), return per-item status with the item's index or key, never let one bad item fail the other 99 silently, and decide explicitly whether the batch is transactional (all-or-nothing) or best-effort — then document it.

**Idempotency keys.** The ambiguity problem from Lesson 1: the client sends `POST /orders`, the response is lost, the client retries, and now there are two orders. The fix:

```
POST /orders
Idempotency-Key: 7f1b0c9e-5a4d-4c2a-9f0b-2c8c0a1e7b33
```

The server stores the key with the result of the first execution. A retry with the same key **returns the stored response instead of re-executing**. Keys are per-endpoint, scoped to the caller, and expire (Stripe uses 24 hours). Full treatment, including what to do when a retry arrives *while* the first is still running, is **Lesson 17 — idempotency, retries, timeouts, exponential backoff**.

### 4.9 The two problems REST cannot solve

These set up Lessons 11 and 12, so read them carefully.

**Over-fetching.** `GET /books/9781492052203` returns the full representation: title, description (4 KB), all 23 contributor records, full publisher details, 15 category tags. The mobile "recently viewed" strip needs exactly **three** fields: title, cover URL, price. You download 12 KB to use 180 bytes — **98.5 % waste**, paid for in bytes, battery and parse time on a phone. REST's uniform interface is precisely what forbids the client from saying "just these fields". (You can bolt on `?fields=title,cover_url,price` — "sparse fieldsets" — but now you've broken caching, your SQL layer, and the uniformity you started with.)

**Under-fetching / N+1 round trips.** To render one BookNook order confirmation screen:

```
GET /orders/991                      → order with 5 item references
GET /order-items/{id}   × 5          → each item has a book_id
GET /books/{isbn}       × 5          → each book has an author_id
GET /authors/{id}       × 5          → author names
GET /customers/42                    → shipping name
GET /addresses/77                    → shipping address
                                     = 18 round trips
```

At 60 ms RTT on mobile, if the client naively waterfalls these, that is over a second of pure latency before a single pixel is correct. HTTP/2 multiplexing (Lesson 9) helps a lot — the parallel groups of 5 collapse into one RTT each — but the **dependency chain** (order → item → book → author) is inherently sequential and no transport fixes it.

These two problems are the entire motivation for:
- **Lesson 11 (RPC/gRPC)**: design a purpose-built endpoint that returns exactly the composed result, with an efficient binary encoding.
- **Lesson 12 (GraphQL)**: let the client declare the exact shape it wants in one request.

## 5. Request / data flow

A `POST /orders` from BookNook's web app, start to finish:

1. **Client builds the request.** Method `POST`, URI `/v1/orders`, headers: `Content-Type: application/json`, `Accept: application/json`, `Authorization: Bearer eyJ...`, `Idempotency-Key: <uuid v4 generated once and reused across retries>`. Body carries **intent only**: `{"items":[{"isbn":"9781492052203","quantity":2}],"address_id":77}` — no prices (Lesson 1's trust boundary).
2. **DNS, TCP/QUIC, TLS, ALPN** (Lessons 3, 4, 7, 9). The connection is usually already open and reused.
3. **CDN / edge.** Sees `POST`, knows it is non-cacheable and non-safe, forwards to origin. A `GET /books/123` would have been answered here.
4. **API gateway / load balancer.** Terminates TLS, routes by path prefix `/v1/orders` to the orders service, applies global rate limiting by API key (returning `429` + `Retry-After` if exceeded), and injects a `traceparent` header if absent.
5. **Authentication.** The service validates the bearer token's signature and expiry. Invalid → `401` with `WWW-Authenticate`. Valid but this user may not order on behalf of customer 42 → `403`.
6. **Idempotency check.** Look up `(api_key, endpoint, idempotency_key)` in Redis/Postgres. **Hit** → return the stored response verbatim, status and all; stop here. **Miss** → insert a row in state `in_progress` (uniquely constrained, so a concurrent duplicate fails fast with `409`).
7. **Validation / deserialisation.** Parse JSON against a schema. Unknown fields → ignore (forward compatibility) or reject (strictness) — pick one and document it. Malformed JSON → `400`. Valid JSON, invalid business values (quantity 0, unknown address) → `422` with a `problem+json` body listing each field error.
8. **Business logic.** Begin a database transaction. Re-read prices **server-side**. Check and decrement stock with `UPDATE books SET stock = stock - 2 WHERE isbn = $1 AND stock >= 2` — the `AND` makes it safe under concurrency. Zero rows updated → roll back, return `409 Conflict` with `type: .../insufficient-stock`.
9. **Create the order**, commit, and record the idempotency result.
10. **Build the response.** `201 Created`, `Location: /v1/orders/991`, `ETag: "1"`, `Cache-Control: no-store` (it's private and mutable), body = the created order's representation.
11. **Client handles it.** `2xx` → success path. `429`/`503` → wait `Retry-After`, retry with the **same** idempotency key. `5xx` → retry with exponential backoff and jitter. `4xx` (other) → do **not** retry; it will fail identically.
12. **Observability.** Log structured fields (method, route *template* `/v1/orders` not the expanded path — otherwise your metrics cardinality explodes), status, latency, trace ID. Emit a metric labelled by route and status class.

## 6. ASCII diagram

```
RESOURCE MODEL                          URI SPACE
  Customer 42 ──owns──> Order 991 ──has──> OrderItem ──refs──> Book ──by──> Author
                                                                  │
  /customers/42                                                   └──has──> Review

  /customers/42/orders          collection scoped to a customer
  /orders/991                   canonical address of one order
  /orders/991/cancellations     controller resource (POST = cancel)
  /books/9781492052203          canonical address of one book
  /books/9781492052203/reviews  sub-collection
  /reviews/8812                 canonical address of one review

METHOD x RESOURCE MATRIX
                 /books (collection)         /books/{isbn} (item)
  GET      200 + paginated list          200 + representation | 404
  POST     201 + Location header         405 Method Not Allowed
  PUT      405 (don't replace a set)     200/204 full replace  | 201 upsert
  PATCH    405                           200/204 partial update| 412 if ETag stale
  DELETE   405                           204 No Content        | 404

PAGINATION: why deep offset hurts
  OFFSET 100000 LIMIT 20
     index: [row0][row1]...............[row99999][row100000..100019][...]
            |<----- 100,000 rows READ AND THROWN AWAY ----->|<-kept->|
     cost grows LINEARLY with page number

  CURSOR (created_at,id) < ('2026-01-11T09:14:22Z', 8812) LIMIT 20
     index:  B-tree seek ──────────────────────────────> [20 rows]
            |<-- O(log n), SAME cost for page 1 and page 5000 -->|

THE TWO PROBLEMS REST CANNOT FIX
  Over-fetch:   GET /books/123  ->  12 KB returned, 180 bytes used  (98.5% waste)
  Under-fetch:  order -> items -> books -> authors
                  GET      GET x5   GET x5   GET x5   = sequential dependency chain
                  |<-- 4 round trips MINIMUM, no transport can remove them -->|
                                          |
                        Lesson 11 (gRPC): one purpose-built composed endpoint
                        Lesson 12 (GraphQL): one request, client-declared shape
```

## 7. Detailed example

### BookNook

BookNook v1's public API, as it would actually be written:

```
GET    /v1/books?genre=databases&sort=-rating&limit=20          200 + cursor
GET    /v1/books/9781492052203                                  200 + ETag
GET    /v1/books/9781492052203/reviews?limit=10                 200
POST   /v1/books/9781492052203/reviews                          201 + Location
GET    /v1/customers/42/orders?limit=20&cursor=...              200
POST   /v1/orders            (Idempotency-Key required)         201 + Location
GET    /v1/orders/991                                           200 + ETag
PATCH  /v1/orders/991        (If-Match required)                200 | 412
POST   /v1/orders/991/cancellations                             201 | 409
POST   /v1/carts/abc/items                                      201
DELETE /v1/carts/abc/items/9                                    204
```

Design decisions worth defending in a review:

- **`GET /v1/books/{isbn}` carries `Cache-Control: public, max-age=300, stale-while-revalidate=600`.** Book metadata changes rarely. A CDN now absorbs the vast majority of catalogue reads — this one header removes more origin load than any amount of server tuning.
- **Stock is *not* in the cached book representation.** It changes every few seconds; caching it for 5 minutes would show "In stock" for sold-out books. It lives at `GET /v1/books/{isbn}/availability` with `Cache-Control: no-store`. **Split resources by rate of change, not by what feels like one object.** This is one of the highest-leverage REST design ideas and almost nobody teaches it.
- **Cancellation is a created resource, not a `POST /orders/991/cancel` verb**, so there is a durable, addressable record of who cancelled and why (`GET /v1/cancellations/5521`).
- **Orders are append-mostly.** The only `PATCH`-able fields are `shipping_address_id` and `gift_message`, and only while `status = "awaiting_payment"`. Everything else is a state transition via a sub-resource.
- **Search is deliberately a different resource:** `GET /v1/search?q=...` returns mixed types (books, authors, collections) with relevance scores. Forcing it into `/books` would have been dishonest about what it returns.

**The failure that taught them pagination.** The partner integration team used `GET /v1/books?limit=100&offset=N` to sync the full 1.2 M-row catalogue nightly. Early pages took 15 ms. By offset 900,000 a single page took **4.2 seconds**, the job ran for hours, and it held a database connection the whole time, degrading everyone. Moving to cursor pagination brought every page to a flat ~8 ms. Numbers in Section 9.

### Contrasting example — the Stripe API

Stripe is the most-studied REST API in the industry, and it diverges from textbook REST in instructive ways:

- **Date-based versioning.** Your account is pinned to the API version in effect when you signed up (`2026-01-11`). Stripe maintains a chain of response transformers so old integrations never break. Enormously expensive to build; the right call for a payments company where a broken integration means lost money.
- **Mandatory idempotency keys on all `POST`s.** Charging a card twice is not a bug, it's a lawsuit.
- **Cursor pagination only** (`starting_after`, `ending_before`). No offsets anywhere, because customers iterate millions of charges.
- **Expandable objects**: `GET /v1/charges/ch_123?expand[]=customer&expand[]=invoice.subscription` inlines related objects. This is a pragmatic, bounded answer to under-fetching — a tiny slice of GraphQL's power, with none of its complexity. Note what they *didn't* do: they didn't let you pick arbitrary field subsets, so caching and response shape stay predictable.
- **`application/x-www-form-urlencoded` request bodies**, not JSON — a historical choice that made `curl` examples trivial and persists for compatibility.
- **Some links, limited HATEOAS.** Level 2.5, not Level 3.

The lesson: a world-class REST API is **Level 2 plus deliberate, well-chosen exceptions** — not a purist Level 3.

## 8. Code or configuration example

This **illustrates REST resource design, pagination, conditional requests, and problem+json**. It is not production-ready: no authentication, no rate limiting, no connection pooling, in-memory data, no real idempotency store, no tests.

```python
# booknook_api.py -- ILLUSTRATIVE ONLY, not production-ready.
from fastapi import FastAPI, Response, Request, Query, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import base64, json, hashlib
from typing import Optional

app = FastAPI()

# ---------- Problem Details (RFC 9457) --------------------------------------
def problem(status: int, type_: str, title: str, detail: str, **extra):
    """Every error in this API has ONE shape. Clients branch on `type`,
    never on `detail` (which is free text and may be reworded any time)."""
    body = {"type": f"https://booknook.com/problems/{type_}", "title": title,
            "status": status, "detail": detail, **extra}
    # The media type matters: it tells generic clients this is a standard
    # problem document, not an application payload that happened to fail.
    return JSONResponse(body, status_code=status,
                        media_type="application/problem+json")

# ---------- Cursor helpers ---------------------------------------------------
def encode_cursor(created_at: str, id_: int) -> str:
    """The cursor is OPAQUE to clients. Base64 is not security -- it is a signal
    that says 'do not parse or construct this yourself'. Including the row id
    as a tiebreaker is mandatory: created_at is not unique, and without the
    tiebreaker rows sharing a timestamp are silently skipped or duplicated."""
    return base64.urlsafe_b64encode(
        json.dumps({"c": created_at, "i": id_}).encode()).decode()

def decode_cursor(cursor: str):
    try:
        d = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return d["c"], d["i"]
    except Exception:
        # A malformed cursor is the CLIENT's error -> 400, never 500.
        raise HTTPException(status_code=400, detail="malformed cursor")

# Field allow-list. NEVER interpolate a user string into ORDER BY: that is SQL
# injection, and it also lets a client sort by an unindexed column and turn a
# 2 ms query into a 4-second table scan.
SORTABLE = {"created_at", "price_paise", "rating", "title"}

# ---------- Collection: GET /v1/books ---------------------------------------
@app.get("/v1/books")
def list_books(
    # Hard cap on limit. Without `le=100` a client can request limit=1000000
    # and OOM the process -- an availability bug disguised as a feature.
    limit: int = Query(20, ge=1, le=100),
    cursor: Optional[str] = None,
    genre: Optional[str] = None,
    sort: str = "-created_at",
):
    desc = sort.startswith("-")
    field = sort.lstrip("-")
    if field not in SORTABLE:
        return problem(400, "invalid-sort", "Invalid sort field",
                       f"'{field}' is not sortable.", allowed=sorted(SORTABLE))

    # KEYSET pagination. The WHERE clause compares a TUPLE so the database can
    # seek through the composite index (created_at, id) instead of counting and
    # discarding `offset` rows. Cost is O(log n) at page 1 and at page 50,000.
    #   SELECT * FROM books
    #   WHERE ($1::text IS NULL OR genre = $1)
    #     AND ($2::timestamptz IS NULL OR (created_at, id) < ($2, $3))
    #   ORDER BY created_at DESC, id DESC
    #   LIMIT $4 + 1          -- fetch one EXTRA row purely to learn whether a
    #                         -- next page exists, without a COUNT(*) query
    rows = fake_query(limit + 1, cursor, genre, field, desc)
    has_more = len(rows) > limit
    rows = rows[:limit]

    # Envelope, not a bare array. A bare JSON array has nowhere to put
    # pagination metadata later, and historically was a JSON-hijacking risk.
    return {
        "items": rows,
        "page": {
            "limit": limit,
            "has_more": has_more,
            "next_cursor": encode_cursor(rows[-1]["created_at"], rows[-1]["id"])
                           if has_more and rows else None,
        },
    }

# ---------- Item: GET /v1/books/{isbn} with conditional requests ------------
@app.get("/v1/books/{isbn}")
def get_book(isbn: str, response: Response,
             if_none_match: Optional[str] = Header(None)):
    book = BOOKS.get(isbn)
    if book is None:
        return problem(404, "book-not-found", "Book not found",
                       f"No book with ISBN {isbn}.", isbn=isbn)

    # A strong ETag derived from content. Any change to the body changes the tag.
    etag = '"' + hashlib.sha256(
        json.dumps(book, sort_keys=True).encode()).hexdigest()[:16] + '"'

    # Conditional GET: if the client already has this exact version, reply 304
    # with NO BODY. Saves ~12 KB per request and all serialisation work.
    if if_none_match == etag:
        return Response(status_code=304, headers={"ETag": etag})

    response.headers["ETag"] = etag
    # Catalogue data is public and slow-changing -> let the CDN serve it.
    # stale-while-revalidate lets the CDN return a slightly stale copy
    # INSTANTLY while refreshing in the background: no user waits on our origin.
    response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=600"
    return book

# NOTE: stock is deliberately NOT in the above response. It changes every few
# seconds and must never be cached for 5 minutes. Split resources by RATE OF
# CHANGE, not by what intuitively "feels like" one object.
@app.get("/v1/books/{isbn}/availability")
def availability(isbn: str, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {"isbn": isbn, "in_stock": BOOKS[isbn]["stock"] > 0,
            "stock": BOOKS[isbn]["stock"]}

# ---------- Create: POST /v1/orders -----------------------------------------
class OrderItemIn(BaseModel):
    isbn: str
    # ge=1 rejects 0 and negatives. A negative quantity in a naive pricing
    # routine produces a NEGATIVE total -- i.e. you pay the attacker.
    quantity: int = Field(ge=1, le=20)

class OrderIn(BaseModel):
    # INTENT ONLY. No unit_price, no total, no discount. The client cannot be
    # trusted with anything that affects money (Lesson 1, trust boundary).
    items: list[OrderItemIn] = Field(min_length=1, max_length=50)
    address_id: int

@app.post("/v1/orders", status_code=201)
def create_order(order: OrderIn, response: Response,
                 idempotency_key: Optional[str] = Header(None)):
    # Without this key, a lost response + client retry = two real orders and
    # two real charges. Full treatment in Lesson 17.
    if not idempotency_key:
        return problem(400, "missing-idempotency-key", "Idempotency-Key required",
                       "POST /v1/orders requires an Idempotency-Key header.")

    if idempotency_key in IDEMPOTENCY:
        stored = IDEMPOTENCY[idempotency_key]
        # Replay the ORIGINAL response byte-for-byte, including its status code.
        # The retry must be indistinguishable from the first call.
        return JSONResponse(stored["body"], status_code=stored["status"])

    total = 0
    for item in order.items:
        book = BOOKS.get(item.isbn)
        if book is None:
            return problem(422, "unknown-isbn", "Unknown ISBN",
                           f"{item.isbn} is not in the catalogue.", isbn=item.isbn)
        if book["stock"] < item.quantity:
            # 409 = the request is valid but conflicts with current server state.
            # Extension members let the UI say something USEFUL instead of "error".
            return problem(409, "insufficient-stock", "Insufficient stock",
                           f"Only {book['stock']} copies remain.",
                           isbn=item.isbn, available=book["stock"],
                           requested=item.quantity)
        total += book["price_paise"] * item.quantity   # price read SERVER-SIDE

    created = {"id": 991, "status": "awaiting_payment", "total_paise": total}
    IDEMPOTENCY[idempotency_key] = {"status": 201, "body": created}

    # 201 Created MUST carry Location: the canonical URI of the new resource.
    response.headers["Location"] = f"/v1/orders/{created['id']}"
    response.headers["Cache-Control"] = "no-store"   # private + mutable
    return created
```

Exercising it:

```bash
# Conditional GET: second call returns 304 with an empty body.
ETAG=$(curl -sI localhost:8000/v1/books/9781492052203 | grep -i '^etag' | cut -d' ' -f2 | tr -d '\r')
curl -s -o /dev/null -w '%{http_code} %{size_download}B\n' \
     -H "If-None-Match: $ETAG" localhost:8000/v1/books/9781492052203
# -> 304 0B

# Retrying with the same key returns the SAME order, not a second one.
KEY=$(uuidgen)
for i in 1 2; do
  curl -s -X POST localhost:8000/v1/orders -H 'Content-Type: application/json' \
       -H "Idempotency-Key: $KEY" \
       -d '{"items":[{"isbn":"9781492052203","quantity":2}],"address_id":77}'
  echo
done
```

## 9. Numerical example

### Part A — deep offset pagination, quantified

**Assumptions**

| Assumption | Value | Justification |
|---|---|---|
| Table size | 1,200,000 books | BookNook's catalogue |
| Index | B-tree on `(created_at DESC, id DESC)` | Required for either pagination scheme |
| Cost to walk one index entry | 0.4 µs | Typical for a warm in-memory B-tree on modern hardware |
| Cost to fetch + serialise one returned row | 60 µs | Heap lookup + JSON encoding |
| Page size | 100 rows | Partner sync job |
| Network + framework overhead | 3 ms per request | Fixed |

**Offset pagination.** The engine must traverse `offset + limit` index entries and discard the first `offset`:

```
Page 1    (offset 0):
   index walk = (0 + 100)      × 0.4 µs =      40 µs = 0.04 ms
   row cost   = 100            × 60 µs  =   6,000 µs = 6.00 ms
   total      = 0.04 + 6.00 + 3.00                   = 9.04 ms

Page 1,000 (offset 99,900):
   index walk = (99,900 + 100) × 0.4 µs =  40,000 µs = 40.0 ms
   row cost   =                                         6.00 ms
   total      = 40.0 + 6.0 + 3.0                     = 49.0 ms

Page 9,000 (offset 899,900):
   index walk = (899,900 + 100) × 0.4 µs = 360,000 µs = 360 ms
   row cost   =                                          6.00 ms
   total      = 360 + 6 + 3                           = 369 ms
```

(In the real incident the number was ~4.2 s rather than 369 ms, because the query also sorted on an unindexed column and the working set exceeded RAM, forcing disk reads. The *shape* — linear growth with offset — is the point.)

**Full-catalogue sync with offset pagination.** 1,200,000 / 100 = **12,000 pages**. The index-walk cost for page *k* is `(k × 100) × 0.4 µs`. Summing:

```
Total index walks = 0.4 µs × 100 × (1 + 2 + ... + 12,000)
                  = 0.4 µs × 100 × (12,000 × 12,001 / 2)
                  = 0.4 µs × 100 × 72,006,000
                  = 0.4 µs × 7,200,600,000
                  = 2,880.24 seconds           = 48.0 minutes

Row + overhead cost = 12,000 pages × (6 ms + 3 ms) = 108,000 ms = 1.8 minutes

TOTAL ≈ 49.8 minutes, nearly all of it discarding rows.
```

**Cursor pagination.** Each page is a B-tree seek — `log₂(1,200,000) ≈ 20.2` node comparisons — then a sequential scan of 100 entries:

```
Per page:
   seek        = 21 comparisons × 0.4 µs  =     8.4 µs ≈ 0.008 ms
   index scan  = 100            × 0.4 µs  =    40   µs = 0.04  ms
   row cost    = 100            × 60 µs   = 6,000   µs = 6.00  ms
   overhead    =                                         3.00  ms
   total                                                ≈ 9.05 ms   (CONSTANT)

Full sync = 12,000 pages × 9.05 ms = 108,600 ms = 1.81 minutes
```

**Result: 49.8 minutes → 1.8 minutes, a 27.5× speedup, from changing a WHERE clause.** And the per-page worst case drops from 369 ms to 9 ms, so the job no longer holds a connection long enough to hurt other queries.

```
Page number:     1        1,000     5,000     9,000     12,000
Offset  (ms):    9.0      49.0      209.0     369.0     489.0     ← linear
Cursor  (ms):    9.05     9.05      9.05      9.05      9.05      ← flat
```

### Part B — over-fetching cost on mobile

**Assumptions**: the "recently viewed" strip shows 10 books; full representation 12 KB each; the strip needs `title` (40 B), `cover_url` (90 B), `price_paise` (6 B) plus JSON punctuation ≈ **180 B** per book; mobile downlink 8 Mbps = 1,000 KB/s; JSON parse throughput ≈ 50 MB/s on a mid-range phone.

```
Full representations:
   bytes    = 10 × 12 KB = 120 KB
   transfer = 120 KB / 1,000 KB/s          = 120 ms
   parse    = 120 KB / 50,000 KB/s         =   2.4 ms
   total                                   = 122.4 ms

Minimal representations:
   bytes    = 10 × 180 B = 1.8 KB
   transfer = 1.8 / 1,000                  =   1.8 ms
   parse                                   =   0.04 ms
   total                                   =   1.84 ms

Waste  = (120 - 1.8) / 120 = 98.5 % of bytes
Saving = 122.4 - 1.84 = 120.6 ms per strip render
```

Now scale it. **Assumption**: 10,000 DAU (Lesson 1), 6 strip renders per user per day, egress billed at $0.09/GB:

```
Requests/day  = 10,000 × 6 = 60,000
Wasted bytes  = 60,000 × (120 - 1.8) KB = 60,000 × 118.2 KB = 7,092,000 KB
              = 6.76 GB/day = 205.6 GB/month
Cost          = 205.6 × $0.09 = $18.50/month
```

$18.50 is trivial — **and that is the correct conclusion at BookNook's scale**. Do not adopt GraphQL for this. But multiply DAU by 1,000 (10 M DAU, a mid-size consumer app):

```
205.6 GB × 1,000 = 205,600 GB/month = 205.6 TB → $18,504/month in egress alone
```

Plus 120 ms of extra latency on every render, which at consumer scale measurably reduces conversion. **That** is when Lesson 12 becomes a business case rather than a preference. Always name the scale at which a trade-off flips.

### Part C — under-fetching round trips

**Assumptions**: RTT 60 ms (mobile), server processing 8 ms per request, 18 requests in the dependency chain from Section 4.9, arranged in 4 sequential levels (order → items → books → authors) plus 2 independent calls.

```
HTTP/1.1, 6 connections, naive sequential client:
   18 requests × (60 ms RTT + 8 ms) = 18 × 68 = 1,224 ms

HTTP/2, parallel within each level (Lesson 9):
   level 1: /orders/991                        = 68 ms
   level 2: 5 × /order-items  (parallel)       = 68 ms
   level 3: 5 × /books        (parallel)       = 68 ms
   level 4: 5 × /authors      (parallel)       = 68 ms
   (the 2 independent calls overlap level 2)   =  0 ms
   total                                       = 272 ms

Single composed endpoint (Lesson 11 or 12):
   1 request, server does 4 fast in-datacentre joins at 2 ms each
   = 60 ms RTT + 8 ms + 8 ms = 76 ms
```

**1,224 ms → 272 ms → 76 ms.** HTTP/2 removed the *parallelisable* waste; only changing the API shape removed the *dependency chain*. That is precisely why Lessons 11 and 12 exist, and it is the cleanest possible justification for them.

## 10. Trade-offs

**Advantages**

- **Free infrastructure.** HTTP caching, CDNs, proxies, load balancers, WAFs, rate limiters, browser dev tools, `curl`, Postman — all of it works on day one, with no adapters.
- **Uniform and learnable.** An engineer who has used one REST API can use yours within minutes.
- **Debuggable.** You can reproduce any bug by pasting a URL into a browser or a `curl` line into a ticket.
- **Independent evolution.** Additive changes don't break clients; resources can be versioned, deprecated, or split independently.
- **Cacheability is the big one.** A `GET /books/{isbn}` with a 5-minute TTL served from a CDN costs ~0 and takes ~15 ms. No other benefit comes close at read-heavy scale.
- **Statelessness** means any server can handle any request, which makes horizontal scaling trivial (Lesson 2, Phase 2).

**Disadvantages**

- **Over-fetching and under-fetching** (Section 4.9, quantified in Section 9).
- **No type safety.** JSON has no schema on the wire. A server renaming a field breaks clients at *runtime*, in production, on a Sunday. OpenAPI helps but is documentation, not enforcement.
- **Chatty for composite views**, especially on high-latency mobile links.
- **Verbose encoding.** JSON repeats every field name in every object (quantified in Lesson 11).
- **Awkward for workflows.** "Transfer money from A to B" is not a noun, and pretending it is produces strained designs.
- **No built-in streaming or server push.** You bolt on SSE, WebSocket, or polling.

**Operational costs**

- Versioning and deprecation are forever work: tracking which clients use v1, chasing them, running two code paths.
- Documentation drifts from reality unless generated from code.
- Cache invalidation across multiple URIs that represent the same underlying entity.
- Someone must own and police URI conventions, or the API becomes a patchwork of three teams' habits.

**When it becomes unnecessary complexity:** a purely internal, single-consumer, high-volume service call — where you control both sides, need low latency and typed contracts, and will never use a browser or CDN. That is gRPC's home turf (Lesson 11).

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| Deep offset pagination on a large table | Admin page or sync job hangs; later pages take seconds | Slow-query log shows query time growing with `OFFSET`; p99 on one route climbs while p50 is flat | Switch to cursor pagination; add the composite index; cap maximum offset |
| Unbounded `GET /books` (no default limit) | Browser tab freezes; app OOMs | Response size histogram has a huge tail; server memory spikes on specific requests | Enforce a default and a hard max `limit`; return `400` above it |
| Breaking change shipped without a version bump | Old mobile app crashes or shows blank screens | Client error-reporting spikes for one app version; 4xx/parse errors rise | Roll back; re-add the field as deprecated; ship a versioned endpoint |
| `200 OK` returned for a logical error | Retries never happen; failures are invisible; data silently wrong | Error-rate dashboards show 0 % while support tickets rise — the worst possible signature | Return correct status codes; add contract tests asserting status per scenario |
| No idempotency key on `POST /orders` | Duplicate orders and double charges after a network blip | Duplicate-detection job; customer complaints; payment-processor duplicate alerts | Require `Idempotency-Key`; deduplicate server-side (Lesson 17) |
| Client sorts by an unindexed column | One specific filter combination times out | `EXPLAIN` shows a sequential scan; p99 spikes for one query-string shape | Allow-list sortable fields; add indexes; reject the rest with `400` |
| Cache TTL too long on volatile data | "In stock" shown for a sold-out book; order fails at checkout | Rise in `409 insufficient-stock` at checkout; mismatch between page and DB | Split volatile fields into a `no-store` sub-resource; shorten TTL; use ETags |
| Lost update (two concurrent `PATCH`es) | One user's edit silently disappears | Audit log shows two writes, one overwritten; user reports "my change vanished" | `ETag` + `If-Match`, returning `412`; or a `version` column with `test` in JSON Patch |
| Missing `Retry-After` on `429`/`503` | Clients hammer a struggling service, deepening the outage | Request rate *rises* during an incident — a retry storm | Send `Retry-After`; clients use exponential backoff **with jitter** (Lesson 17) |
| Metrics labelled with expanded paths (`/books/978…`) | Dashboards and alerts stop loading | Monitoring system reports high metric cardinality / is itself overloaded | Label with the route *template*, not the concrete URI |

## 12. Alternatives

| Approach | What it is | Advantages | Disadvantages | Best for |
|---|---|---|---|---|
| **REST / HTTP-JSON** | Resources + HTTP verbs | Caching, ubiquity, debuggability, zero tooling | Over/under-fetching, untyped, chatty | Public APIs, CRUD, anything a browser or CDN touches |
| **gRPC** (Lesson 11) | Binary protobuf RPC over HTTP/2 | Typed contracts, code generation, 40–70 % smaller payloads, bidirectional streaming | Not browser-native, no HTTP caching, needs a proxy for the web | Internal service-to-service, high volume, polyglot teams |
| **GraphQL** (Lesson 12) | Client-specified query language over one endpoint | Kills over/under-fetching, one round trip, strong schema | HTTP caching breaks, N+1 resolvers, complexity attacks, heavy infrastructure | Many distinct clients, deeply nested data, rapid UI iteration |
| **SOAP** | XML envelopes + WSDL | Formal contracts, WS-Security, transactions | Enormously verbose, no caching, heavy tooling | Legacy enterprise, banking, government integrations |
| **JSON-RPC** | Minimal method-call-over-JSON | Dead simple, transport-agnostic | No caching, no HTTP semantics, no types | Blockchain nodes, simple internal RPC |
| **OData** | REST plus a standard query language | Rich filtering/`$expand` without a custom parser | Complex, Microsoft-centric, easy to DoS yourself | Enterprise data services, Power BI-style consumers |
| **tRPC** | End-to-end typed RPC for TypeScript | Zero codegen, perfect type inference | TypeScript-only on both ends | Full-stack TS monorepos |
| **Webhooks / events** (Phase 4, Topic 5) | Server→client push over HTTP | Removes polling entirely | Delivery guarantees, retries, signature verification | Notifying partners of state changes |

## 13. When to use it

1. **Public or partner-facing APIs.** Third-party developers already know REST, can explore it with `curl`, and need no code generation. GitHub, Stripe, Twilio, Shopify — all REST-first for exactly this reason.
2. **Read-heavy, cacheable resources.** BookNook's catalogue is read ~100× more than it's written. `Cache-Control: public, max-age=300` on `GET /books/{isbn}` lets a CDN absorb the vast majority of traffic. No other API style gives you this for free.
3. **CRUD-shaped domains.** When your operations genuinely are create/read/update/delete on nouns — products, users, posts, tickets — REST's mapping is natural and the code almost writes itself.
4. **Anything a browser touches directly.** Browsers speak HTTP natively with caching, cookies, CORS, and conditional requests. REST uses all of it.
5. **When operational simplicity matters more than peak efficiency.** Debuggable in production with one `curl`, understandable by a new hire in an hour, works with every observability tool you already pay for.
6. **Webhooks and integrations**, where the other side may be a no-code tool, a Zapier connector, or a PHP script from 2014.

## 14. When *not* to use it

1. **High-volume internal service-to-service calls.** 50,000 RPS between two services you own: JSON parsing costs real CPU, payloads are ~2× larger than protobuf, and you gain nothing from caching because the data is per-request. Use gRPC (Lesson 11).
2. **Clients with wildly different data needs.** If a watch app, a TV app, a web app, and a partner integration all need different field subsets and different nesting from the same backend, REST forces you to either over-fetch, build a dozen bespoke endpoints, or invent sparse fieldsets. GraphQL (Lesson 12) or a BFF is the better answer.
3. **Real-time, bidirectional, or streaming data.** Live order tracking, chat, collaborative editing. Polling a REST endpoint every second is wasteful and still laggy. Use WebSocket, SSE, or gRPC streaming.
4. **Deeply nested, variable-shape queries.** "Give me this author, their books, each book's top 3 reviews, and each reviewer's badge" is 20+ REST round trips and one GraphQL query.
5. **Workflow/command-heavy domains.** If 80 % of your operations are verbs that don't map to resources ("reconcile ledger", "simulate pricing", "rebalance portfolio"), you'll fight the model constantly. RPC is the honest fit.

## 15. Common mistakes

**Beginner mistakes**

- **Verbs in URIs**: `/getBooks`, `/createOrder`, `/deleteUser?id=5`. The method is the verb.
- **`GET` with side effects.** `GET /orders/991/cancel` will be cancelled by a CDN prefetch, a browser preload, a link checker, or a crawler. `GET` and `HEAD` must be **safe** (no observable state change). This has caused real production incidents — most famously the 2005 Google Web Accelerator deleting admin-panel rows that used `GET` links.
- **Returning `200` for errors** with `{"success": false}` in the body. Every proxy, retry policy, alert, and SLO now believes you're healthy.
- **Returning a bare JSON array** at the top level — no room for pagination metadata later.
- **No pagination at all**, or pagination with no maximum limit.
- **Using `PUT` for partial updates**, silently nulling every field the client didn't send.
- **Leaking internal errors**: stack traces, SQL text, or table names in the `detail` field. That's reconnaissance for an attacker.
- **Inconsistent naming** across endpoints: `created_at` here, `createdAt` there, `creation_date` elsewhere. Pick one convention and enforce it with a linter (Spectral).

**Experienced-engineer mistakes**

- **Offset pagination that survives to production scale.** It works beautifully in testing with 500 rows and falls over at 2 million. Cursor from the start costs an extra hour.
- **Cursor pagination without a tiebreaker column**, silently dropping rows that share a timestamp. Nobody notices until an auditor does.
- **Versioning everything at once.** Bumping the whole API to `/v2` to change one endpoint forces every client to migrate everything. Prefer additive evolution and per-resource versioning.
- **Chasing HATEOAS purity** on an internal API where you control both sides, paying real cost for a benefit you structurally cannot use.
- **Building a query language in the query string** (`?filter=price>100,genre~db`) — you are now maintaining a parser, a planner, and a DoS surface. Either keep filters simple or adopt GraphQL deliberately.
- **Exposing database rows as resources 1:1.** Your API then cannot survive a schema refactor, and you leak internal structure forever. The API is a contract; the schema is an implementation detail.
- **Caching authenticated responses without `Vary: Authorization`**, so a shared cache serves user A's data to user B. This is a catastrophic, resume-generating bug.
- **No `Retry-After` on `429`/`503`**, producing retry storms that convert a brownout into an outage.
- **Allowing unbounded `?expand=` / `?include=` depth**, letting one client request a join that reads your entire database in a single HTTP call.
- **High-cardinality metric labels** from unparameterised paths, which takes down your *monitoring* during an incident.

## 16. Interview perspective

REST is assumed knowledge; you are being graded on *judgment*, not vocabulary.

- **Design the resource model out loud before writing any endpoints.** "The nouns are Book, Author, Review, Cart, Order, Payment. Orders are immutable after payment, so state changes become sub-resources." That framing earns more credit than a perfect URI list.
- **Volunteer the pagination strategy with its reason.** "Cursor pagination with `(created_at, id)` as the key, because offset pagination is O(offset) and this table will reach millions of rows." Then mention the tiebreaker. Few candidates do, and it is immediately recognisable as experience.
- **Split resources by rate of change.** "Book metadata is cacheable for 5 minutes at the CDN; stock is `no-store` on a separate availability endpoint." This single idea shows you understand caching as a design input rather than a header you add later.
- **Name the trust boundary.** "The order request carries ISBN and quantity only; prices are server-side."
- **Bring up idempotency before you're asked.** "`POST /orders` requires an `Idempotency-Key` so a retried request can't create a duplicate order."
- **Be honest about Richardson Level 2.** "This is Level 2 — correct verbs and status codes. I'd skip HATEOAS because we control all clients and the link payload wouldn't be used." Nuance beats dogma.
- **Know when to leave REST.** "Internal service-to-service I'd use gRPC for typed contracts and smaller payloads; for the mobile home screen I'd consider a BFF, because it currently needs 18 round trips."
- **Quantify.** "18 round trips at 60 ms is 1.2 seconds sequential, 272 ms with HTTP/2, 76 ms with one composed endpoint."

**Red flags interviewers watch for:** verbs in URIs; `GET` with side effects; returning 200 with an error body; no pagination; offset pagination defended at million-row scale; no versioning or deprecation plan; no mention of caching; claiming an API is "RESTful" while being unable to name a single one of the six constraints; proposing GraphQL or gRPC with no stated reason; ignoring the duplicate-order problem entirely.

## 17. Knowledge check

**Conceptual**

1. An engineer says "our API is fully RESTful". Name the six REST constraints, state which one is violated by nearly every production API, and explain concretely what that violation costs you — and why almost every team accepts that cost anyway.
2. Explain why offset pagination's cost grows with page number while cursor pagination's does not, in terms of what the database physically does with the index. Then explain why a cursor built only from `created_at` is dangerous, and what you must add.
3. BookNook caches `GET /v1/books/{isbn}` for 5 minutes at the CDN, and the response includes a `stock` field. Describe the user-visible bug this creates, explain why shortening the TTL to 30 seconds is the wrong fix, and give the design that is correct.

**Practical scenario**

4. BookNook is launching a public partner API. Partners must be able to: sync the full 1.2 M-book catalogue nightly, look up single books, place orders, and be told when an order ships. Design it: list your resources and URIs, pick and justify a pagination strategy, pick and justify a versioning strategy, specify your error format, say which endpoints require an `Idempotency-Key` and why, state your cache headers per endpoint, and explain how partners learn about shipping without polling. Then name the single part of your design most likely to cause an incident in year one, and say how you'd detect it early.

**Calculation**

5. BookNook's mobile home screen renders a "For You" grid of **24 books**. Each full book representation is **9 KB**; the grid needs **210 bytes** per book. Mobile downlink is **6 Mbps**; RTT is **70 ms**; server processing is **9 ms** per request. The screen currently issues 1 request for the recommendation list (returning 24 ISBNs) and then 24 individual `GET /v1/books/{isbn}` calls.
   (a) Over HTTP/1.1 with 6 connections, how long does the screen take? Show the rounds.
   (b) Over HTTP/2 with full multiplexing, how long?
   (c) If a single composed endpoint returned only the needed 210 bytes per book, how long?
   (d) At 40,000 DAU with 3 home-screen renders per user per day and egress at $0.09/GB, what is the monthly egress cost of the current design versus the composed design?
   (e) Based on (a)–(d), would you adopt GraphQL? Justify with numbers, and state the DAU at which your answer changes.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 11.

---

**Progress checklist**

Phase 1:
- `[x] 1. Client-server architecture`
- `[x] 2. Stateful versus stateless servers`
- `[x] 3. IP addresses, ports, sockets, domain names`
- `[x] 4. DNS resolution and DNS caching`
- `[x] 5. HTTP requests and responses`
- `[x] 6. HTTP methods, headers, cookies, sessions, status codes`
- `[x] 7. HTTPS and TLS`
- `[x] 8. TCP versus UDP`
- `[x] 9. HTTP/1.1 vs HTTP/2 vs HTTP/3`
- `[x] 10. REST APIs`
- `[ ] 11. RPC and gRPC`
- `[ ] 12. GraphQL`
- `[ ] 13. Synchronous versus asynchronous communication`
- `[ ] 14. Latency, throughput, bandwidth, concurrency`
- `[ ] 15. Availability, reliability, durability, fault tolerance`
- `[ ] 16. Average latency vs p50/p95/p99, tail latency`
- `[ ] 17. Idempotency, retries, timeouts, exponential backoff`

**NEXT LESSON: Phase 1, Topic 11 — RPC and gRPC**

---

# Lesson 11 — RPC and gRPC

*Phase 1, Topic 11*

## 1. Simple definition

**RPC (Remote Procedure Call)** is a style of API in which you call a function that happens to execute on another machine. The code *looks* like a local call:

```python
book = catalog.GetBook(isbn="9781492052203")   # runs on a different server
```

No URLs, no HTTP verbs, no status codes in your business code. You call a **procedure** (a function/method) with **arguments** and get a **return value** or an **error**.

Compare the two mental models:

| | REST | RPC |
|---|---|---|
| Organising idea | **Nouns.** "Here are my resources; use standard verbs on them." | **Verbs.** "Here are my functions; call them." |
| Example | `POST /orders/991/cancellations` | `OrderService.CancelOrder(order_id=991, reason=...)` |
| Adding capability | Add a resource or a sub-resource | Add a method |

**gRPC** is Google's modern open-source RPC framework (2015). Its three defining choices:

1. **Protocol Buffers (protobuf)** as the Interface Definition Language and binary wire format.
2. **HTTP/2** as the transport (Lesson 9) — giving multiplexing, streaming and header compression for free.
3. **Code generation** — the `.proto` file is compiled into real client and server classes in 11+ languages.

Terms to define now:

- **IDL (Interface Definition Language)**: a language-neutral file describing your methods and message types. It is the *contract*, and it is machine-readable — unlike REST documentation.
- **Stub** (client side) / **skeleton** or **service base** (server side): generated code that makes a network call look like a function call.
- **Marshalling** (a.k.a. **serialisation**): turning in-memory objects into bytes. **Unmarshalling / deserialisation** is the reverse.

## 2. Why it exists

Two separate motivations, decades apart.

**Motivation 1 (1980s): programmers already know how to call functions.** Sun RPC (1984), DCE/RPC, CORBA (1991), Java RMI (1997), XML-RPC (1998), SOAP (1999) all pursued the same dream — make the network invisible so distributed programming feels like normal programming. (We'll see in Section 4.2 why the dream is a trap.)

**Motivation 2 (2010s): JSON-over-HTTP is expensive at internal scale.** Google ran thousands of services exchanging billions of messages. At that volume, three REST costs become unacceptable:

1. **Payload size.** JSON repeats every field name in every object. A 50-book list repeats `"price_paise"` 50 times. Protobuf sends a 1-byte field tag.
2. **CPU.** JSON parsing is string scanning, allocation, and number parsing. Protobuf decoding is mostly pointer arithmetic over length-prefixed fields — typically **3–10× faster**.
3. **No contract enforcement.** A team renames `price` to `price_paise`; six downstream services break in production because nothing checked. With protobuf, the build fails.

Add a fourth that only appears at scale: **streaming**. REST has no native way to say "send me results as they're computed" or "here's a continuous feed of events in both directions". gRPC has four streaming modes built in.

So gRPC's pitch is narrow and honest: **for service-to-service calls inside your own infrastructure, where you control both ends and the browser is not involved, you can have typed contracts, 40–70 % smaller payloads, much lower CPU, and streaming — by giving up HTTP cacheability and human-readable debugging.**

## 3. Real-world analogy

**A specialist trade desk versus a public-service counter.**

REST is the **public counter at a post office**. Anyone can walk up. There are standard forms (HTTP methods), standard windows (URIs), posted rules everyone already knows, and a notice board showing which services are available. Slow-ish, verbose, universally accessible.

RPC/gRPC is a **direct phone line between two traders who have agreed a script in advance**. Both sides hold the same printed script (the `.proto` file). A trader says "`BUY 200 AAPL LMT 187.25`" — terse, unambiguous, instantly understood, because the meaning of every position in that sentence was agreed beforehand. An outsider overhearing it learns nothing. Changing the script requires both desks to get the new version.

**Where the analogy breaks down**

| Trade desk | Real RPC |
|---|---|
| If the line goes dead, you know immediately and can shout across the room | A dead gRPC connection may hang silently until a deadline fires. Without a deadline it may hang **forever** |
| Both desks obviously update the script together | Services deploy independently and at different times. Old and new schema versions **must** interoperate in both directions — the central problem of Section 4.5 |
| A spoken instruction either arrives or doesn't | A request may arrive, execute, and have its *response* lost. The caller cannot distinguish that from "never arrived" (Lesson 17) |
| The script is short and stable | Real protos grow to hundreds of messages across dozens of teams and need governance, linting and review |
| A phone call is a phone call | A local function call costs ~1 ns; the same call over the network costs ~1,000,000 ns. Making them *look* identical in code is exactly the trap in Section 4.2 |

## 4. Detailed technical explanation

### 4.1 How an RPC actually works

The illusion is built from six mechanical steps:

1. **The client calls a stub.** `catalog.GetBook(isbn="...")` — ordinary local method call on generated code.
2. **The stub marshals** the arguments into bytes using the agreed encoding.
3. **The transport sends** those bytes (for gRPC: as HTTP/2 `DATA` frames on a new stream).
4. **The server's dispatcher** reads the bytes, **unmarshals** them into a request object, and looks up which method was named.
5. **Your real function runs** and returns a response object, which is marshalled back.
6. **The stub unmarshals** the reply and returns it — or raises an exception/returns an error.

Everything hard lives in the gaps: what if step 3 never arrives? What if step 5 takes 40 seconds? What if the server is running a newer version of the contract than the client?

### 4.2 Why "make remote look local" is a trap

This is the most important conceptual section in the lesson. The classic reference is Waldo et al., *"A Note on Distributed Computing"* (1994), whose thesis is blunt: **local and remote calls differ in ways that cannot be hidden, and hiding them produces fragile systems.**

Four unhideable differences:

- **Latency.** A local call is ~1 ns. A same-datacentre RPC is ~0.5–1 ms — roughly **1,000,000× slower**. Code written as if calls are free (a loop calling `GetBook` 500 times) is fine locally and catastrophic remotely.
- **Partial failure.** A local call either returns or the whole process dies — you and the callee fail together. A remote call can fail *while the callee succeeds*. There is no local equivalent, and no API can hide it.
- **Memory access.** Local calls can pass pointers and share mutable objects. Remote calls copy everything. Semantics genuinely differ.
- **Concurrency.** A remote callee is shared by many callers simultaneously, with no shared lock discipline.

The canonical checklist is **the 8 Fallacies of Distributed Computing** (L. Peter Deutsch, plus James Gosling). Each is a false assumption that naive RPC code makes:

1. **The network is reliable.** It is not — packets drop, links flap, switches reboot. Every call needs a retry policy and an idempotency story.
2. **Latency is zero.** It is not — ~0.5 ms in-rack, ~150 ms cross-continent. Chatty interfaces die on this.
3. **Bandwidth is infinite.** It is not — a 10 MB response × 5,000 RPS saturates a 100 Gbps link.
4. **The network is secure.** It is not — assume an attacker on the path; use mTLS even internally.
5. **Topology doesn't change.** It does — pods are rescheduled, IPs are reused, services move regions. Never cache a resolved IP forever (Lesson 4).
6. **There is one administrator.** There isn't — the service you call is owned by another team with its own deploy schedule and its own incident.
7. **Transport cost is zero.** It isn't — serialisation burns CPU, cross-AZ traffic is billed per GB, TLS handshakes cost round trips.
8. **The network is homogeneous.** It isn't — different MTUs, proxies, load balancers, protocol versions and middleboxes sit between you and the callee.

**The practical rule: a good RPC framework does not hide the network — it makes network concerns explicit and ergonomic.** This is precisely why gRPC puts **deadlines**, **status codes**, **cancellation**, **retries** and **interceptors** in the foreground rather than hiding them. CORBA and Java RMI tried to hide them and are largely dead.

### 4.3 Protocol Buffers: the IDL and the wire format

A `.proto` file defines messages and services:

```protobuf
syntax = "proto3";
package booknook.catalog.v1;

message Book {
  string isbn        = 1;    // "1" is the FIELD NUMBER, not a default value
  string title       = 2;
  string author      = 3;
  int64  price_paise = 4;
  int32  stock       = 5;
  bool   in_stock    = 6;
  float  rating      = 7;
}
```

**The field number is the whole trick.** On the wire, protobuf sends a **tag** = `(field_number << 3) | wire_type` as a varint, followed by the value. Field *names* never appear on the wire. That is where the size win comes from — and it is also why field numbers must never change.

Wire types (there are six; these three carry almost everything):

| Wire type | Meaning | Used by |
|---|---|---|
| 0 | Varint — variable-length integer, 7 bits of payload per byte | `int32`, `int64`, `bool`, `enum` |
| 2 | Length-delimited — a varint length, then that many bytes | `string`, `bytes`, nested messages, packed repeated fields |
| 5 | 32-bit fixed | `float`, `fixed32` |

**Varints** encode small numbers in few bytes: 0–127 in 1 byte, 128–16,383 in 2, up to 16,383–2,097,151 in 3. So `stock = 7` costs 1 tag byte + 1 value byte = **2 bytes total**, versus JSON's `"stock":7,` = 10 bytes.

Caveat worth knowing: negative numbers in `int32`/`int64` are encoded as 10-byte varints (sign extension). Use `sint32`/`sint64`, which zig-zag encode, when values are commonly negative.

### 4.4 gRPC over HTTP/2

A gRPC call is an HTTP/2 request with a strict shape:

```
:method = POST
:scheme = https
:path   = /booknook.catalog.v1.CatalogService/GetBook   <- package.Service/Method
content-type = application/grpc+proto
grpc-timeout = 250m                                      <- the DEADLINE, 250 ms
te = trailers
```

Each message is framed as: **1 byte** (compressed flag) + **4 bytes** (big-endian length) + the protobuf bytes. Multiple messages = streaming.

The response ends with HTTP/2 **trailers** — headers sent *after* the body:

```
grpc-status = 0
grpc-message = OK
```

Trailers are why gRPC needs HTTP/2: a server can stream 10,000 messages and *then* report failure, without having guessed the outcome before sending the first byte. HTTP/1.1 cannot do this cleanly, which is the root of the gRPC-Web limitation in Section 4.7.

**The four call modes:**

| Mode | Signature | Example at BookNook |
|---|---|---|
| **Unary** | `rpc GetBook(GetBookRequest) returns (Book)` | Fetch one book. 95 % of real calls |
| **Server streaming** | `rpc ListBooks(ListBooksRequest) returns (stream Book)` | Stream 1.2 M catalogue rows to a sync job without buffering them all in memory |
| **Client streaming** | `rpc BulkUpsert(stream Book) returns (UpsertSummary)` | Publisher uploads 200,000 records; one summary comes back |
| **Bidirectional streaming** | `rpc Track(stream Position) returns (stream Eta)` | Delivery app pushes GPS positions, receives recalculated ETAs continuously |

Streaming is not a niche feature; it is the thing REST genuinely cannot do without bolting on SSE or WebSocket.

**Deadlines, and why they beat timeouts.** A *timeout* is "I'll wait 2 seconds." A **deadline** is an absolute point in time — "this must complete by 14:02:31.250" — that is **propagated** to every downstream call:

```
Gateway sets deadline = now + 500 ms
  └─ OrderService receives deadline (480 ms left after network hop)
       ├─ CatalogService receives deadline (470 ms left)
       └─ PricingService receives deadline (460 ms left)
```

If the gateway's client has already given up, every downstream service learns the deadline has passed and **cancels immediately**, freeing threads, connections and database handles instead of computing a result nobody will read. With plain timeouts, each hop restarts its own clock, so a 3-hop chain of "2 second timeouts" can take 6 seconds while the original caller left after 2. **Deadline propagation is the single most valuable operational feature of gRPC**, and it's the thing people most often forget to configure. gRPC best practice: **always set a deadline**; a call with no deadline can hang until the connection dies.

**Status codes.** gRPC defines 17 canonical codes. The ones that matter and their retry semantics:

| Code | # | Meaning | Retry? |
|---|---|---|---|
| `OK` | 0 | Success | — |
| `CANCELLED` | 1 | Caller gave up | No |
| `INVALID_ARGUMENT` | 3 | Client sent bad data | **No** — it will fail identically |
| `DEADLINE_EXCEEDED` | 4 | Ran out of time | Maybe — only if idempotent |
| `NOT_FOUND` | 5 | No such entity | No |
| `ALREADY_EXISTS` | 6 | Duplicate create | No (often means your retry *worked*) |
| `PERMISSION_DENIED` | 7 | Authenticated, not allowed | No |
| `RESOURCE_EXHAUSTED` | 8 | Rate limited / out of quota | **Yes, with backoff** |
| `FAILED_PRECONDITION` | 9 | System state is wrong (e.g. out of stock) | No, until state changes |
| `ABORTED` | 10 | Concurrency conflict | **Yes, at a higher level** |
| `UNIMPLEMENTED` | 12 | Method doesn't exist | No |
| `INTERNAL` | 13 | Our bug | No (retrying a bug reproduces it) |
| `UNAVAILABLE` | 14 | Transient — server down, connection lost | **Yes, with backoff + jitter** |
| `UNAUTHENTICATED` | 16 | No/invalid credentials | No, until re-auth |

Note how much more actionable this is than HTTP's `500`. `UNAVAILABLE` says "retry"; `INTERNAL` says "don't". Encoding retry-safety into the status code is a deliberate, excellent design decision.

**Interceptors** are gRPC's middleware: functions wrapping every call on client or server. Standard uses: authentication, structured logging, metrics, distributed tracing header propagation (Phase 5, Topic 4), retries, circuit breaking (Phase 4, Topic 14), and payload-size limits. Because they're uniform, you implement cross-cutting concerns **once** rather than per handler.

### 4.5 Schema evolution — the rules that keep you alive

Services deploy independently. At any moment, an old client may call a new server and vice versa. Protobuf is designed for this, *if* you follow the rules.

**Rule 1 — field numbers are permanent identity.** The name is for humans; the number is the contract. Renaming `price` → `price_paise` while keeping `= 4` is **wire-compatible** and safe. Changing `= 4` to `= 9` is a **breaking change** that silently misinterprets data.

**Rule 2 — never reuse a deleted field's number.** If field 4 held `price_paise` (int64) and you delete it, then later assign `4` to `discount_code` (string), an old client still sending an int64 in field 4 will have its bytes interpreted as a string length prefix. You get garbage, or a crash, with **no error** — protobuf has no field-name check to catch it. This is the single most dangerous protobuf mistake.

**Rule 3 — use `reserved` to make it impossible.** The compiler then rejects reuse:

```protobuf
message Book {
  reserved 4, 11 to 15;                 // these numbers can NEVER be used again
  reserved "price", "legacy_sku";       // these NAMES can never be used again
  string isbn = 1;
}
```

**Rule 4 — in proto3, all fields are optional on the wire.** An old server receiving a field it doesn't know **preserves the unknown bytes** (since proto3.5) and echoes them back on re-serialisation, so a proxy doesn't destroy data it doesn't understand. Adding a field is therefore safe. Removing one means old clients read the type's default (`0`, `""`, `false`) — which is why you must distinguish "absent" from "zero" using `optional` fields or wrapper types when zero is a meaningful value.

**Rule 5 — these type changes are safe:** `int32` ↔ `int64` ↔ `uint32` ↔ `uint64` ↔ `bool` (all varints; beware truncation), `string` ↔ `bytes` (if valid UTF-8), and `optional T` ↔ `repeated T` for length-delimited types. **These are not safe:** `int32` ↔ `string`, `float` ↔ `int32`, changing a field between `repeated` and singular for varint types, or changing an enum's numeric values.

**Rule 6 — enums must have a zero value named `_UNSPECIFIED`**, because 0 is the default an old client sees for an unknown value. And an old client receiving an enum value it doesn't know gets the raw number — handle the default branch explicitly, never `assert unreachable`.

**Rule 7 — version your package, not your methods:** `package booknook.catalog.v1;`. A genuinely breaking change becomes `v2`, and both can be served side by side during migration.

Tooling: **Buf** (`buf breaking`) checks your `.proto` diff against the previous version in CI and fails the build on an incompatible change. Treat it as mandatory — schema compatibility is too important to be enforced by code review.

### 4.6 Is gRPC "RPC done right"?

Partly. What gRPC fixed relative to CORBA/RMI:

- It **does not hide failure**: deadlines, cancellation, and retry-classified status codes are first-class.
- It is **language-neutral and versionable**, rather than tied to one object model.
- It rides **HTTP/2**, so it traverses existing infrastructure rather than needing a bespoke transport.
- Contracts are **checked by a compiler**, not by a wiki page.

What it did not fix: you still have to design interfaces that are *coarse-grained*. gRPC makes each call cheap, which tempts people into chatty designs — 500 cheap calls still cost 500 round trips.

### 4.7 gRPC-Web and browser limitations

**Browsers cannot speak gRPC.** The reason is concrete, not political: the browser `fetch`/XHR APIs give JavaScript no access to **HTTP/2 trailers**, and no control over individual frames. gRPC's entire status mechanism lives in trailers.

**gRPC-Web** is the workaround: a slightly different wire protocol that encodes trailers into the response *body* as a final frame. It requires a **translating proxy** — Envoy's `grpc_web` filter, or a built-in equivalent in modern servers/CDNs.

What you lose with gRPC-Web:

- **Client streaming and bidirectional streaming are not supported.** (Server streaming works, over HTTP/1.1 chunked or HTTP/2.) For bidirectional browser traffic you still reach for WebSocket or WebTransport.
- An **extra proxy hop** to deploy, monitor and keep in sync with your protos.
- **Base64 encoding** in the `grpcwebtext` mode, inflating payloads by 33 % — which erodes protobuf's size advantage, sometimes entirely.
- Larger JS bundles for the generated client and protobuf runtime.

Practical guidance: **use gRPC for service-to-service, and expose REST or GraphQL to browsers.** A very common pattern is **gRPC-Gateway** — annotate your `.proto` with HTTP mappings and generate a reverse-proxy that offers a JSON/REST façade over the same service. One contract, two front doors. (Google's own public APIs are defined in protobuf and exposed as both REST and gRPC this way.)

Also note: **gRPC responses are not HTTP-cacheable.** Everything is a `POST` to a method path, so CDNs and browser caches do nothing for you. If your workload is read-heavy public data, that lost cacheability can outweigh every byte protobuf saves (Lesson 10, Section 13).

## 5. Request / data flow

A unary `GetBook` call from BookNook's order service to its catalogue service, start to finish:

1. **Design time.** An engineer edits `catalog/v1/catalog.proto` and opens a PR. CI runs `buf lint` and `buf breaking --against main`; an incompatible change fails the build before it can reach production.
2. **Build time.** `protoc` (or `buf generate`) emits a Go server interface and a Python client stub from the same file. **One contract, two languages, zero drift.**
3. **Channel creation.** At startup the client creates a **channel** — a managed, long-lived HTTP/2 connection (or pool) to `catalog.internal:50051`. Channels are expensive to build and designed to be reused for the process's lifetime; creating one per request is a classic, costly bug.
4. **Name resolution and load balancing.** The channel resolves the target (DNS, or an xDS/service-discovery plugin) to a set of backend addresses and applies a policy — usually `round_robin` over all of them, because gRPC balances **per call**, not per connection. (Why this matters: see Section 11.)
5. **Call invocation.** Application calls `stub.GetBook(request, timeout=0.25)`. The stub serialises `GetBookRequest` to protobuf bytes.
6. **Interceptors run (client side):** attach an auth token to the metadata, inject the `traceparent` header, start a latency timer, increment an attempt counter.
7. **HTTP/2 stream opened.** `:path = /booknook.catalog.v1.CatalogService/GetBook`, `grpc-timeout = 250m`. The 5-byte length-prefixed message goes out as a `DATA` frame. Because it's HTTP/2, dozens of other in-flight calls share this one connection (Lesson 9).
8. **Server receives.** Server interceptors run: validate the token, start a trace span, record the method label. The dispatcher looks up the method by path and unmarshals the bytes into a `GetBookRequest`.
9. **Deadline enforcement.** The server computes the remaining budget from `grpc-timeout` and attaches it to the request context. Every downstream call (database, cache, another service) inherits it.
10. **Handler executes.** Queries Postgres, builds a `Book` message.
11. **Response.** `HEADERS` (`:status 200`, `content-type: application/grpc+proto`), then a `DATA` frame with the length-prefixed `Book`, then **trailers** `grpc-status: 0`.
12. **Client side.** Stub unmarshals into a `Book` object and returns it. Interceptors record latency and status.
13. **Failure branch.** If the handler returned `UNAVAILABLE`, the generated client's retry policy (declared in a service config) retries with exponential backoff and jitter — but **only** for methods marked retryable, and only up to `maxAttempts`, and the whole retry budget is bounded by the original deadline so retries cannot extend it.
14. **Deadline exceeded branch.** The server's context is cancelled; the in-flight SQL query is cancelled too; the client receives `DEADLINE_EXCEEDED`. **Nobody wasted work on a result that would have been discarded.**

## 6. ASCII diagram

```
THE RPC ILLUSION AND WHAT IT HIDES

  YOUR CODE                                           YOUR CODE
  book = catalog.GetBook(isbn)                        def GetBook(req):
        |                                                  return db.lookup(...)
        v                                                      ^
  +-------------+                                     +--------------+
  | CLIENT STUB |  marshal: Book -> bytes             | SERVER SKEL  | unmarshal
  | (generated) |                                     | (generated)  |
  +-------------+                                     +--------------+
        |                                                      ^
  +-------------+   interceptors: auth, trace, retry, metrics  |
  |   CHANNEL   |------------------------------------------>   |
  +-------------+                                              |
        |                                                      |
  ======v=========== HTTP/2 connection (reused, multiplexed) ===^=========
   stream 1: GetBook      [5B hdr][protobuf]  ... trailers grpc-status:0
   stream 3: ListReviews  [5B hdr][protobuf]  ... (concurrent, Lesson 9)
   stream 5: GetPricing   [5B hdr][protobuf]  ...
  =========================================================================
        ^                     ^                     ^
        |                     |                     |
   WHAT THE ILLUSION HIDES:  latency (1ns -> 1,000,000ns)
                             partial failure (callee may SUCCEED, reply LOST)
                             topology change, auth, serialisation cost

gRPC MESSAGE FRAMING ON THE WIRE
  +--------+------------------+-------------------------+
  | 1 byte |     4 bytes      |      N bytes            |
  | compr. | length (big-end) |   protobuf payload      |
  +--------+------------------+-------------------------+
  (repeat N times for a stream; then HTTP/2 TRAILERS carry grpc-status)

PROTOBUF ENCODING OF  stock = 7  (field 5, varint)
  tag = (field_number << 3) | wire_type = (5 << 3) | 0 = 40 = 0x28
  +------+------+
  | 0x28 | 0x07 |   = 2 bytes total
  +------+------+
  JSON equivalent:  "stock":7,     = 10 bytes      (5x larger)
  Field NAMES never appear on the wire. That is the whole size win --
  and the reason field NUMBERS can never be reused.

DEADLINE PROPAGATION (the operational killer feature)
  t=0ms    Gateway: deadline = t+500ms  -------------------------+
  t=2ms      OrderService  (sees 498ms remaining)                |
  t=5ms        CatalogService (sees 495ms)  --> DB query 480ms   |
  t=485ms      PricingService (sees 15ms) --> refuses, returns   |
                 DEADLINE_EXCEEDED instantly instead of          |
                 burning 300ms on a result nobody will read  <---+
  WITHOUT propagation: each hop restarts its own 500ms clock
                       -> 1500ms of work for a caller who left at 500ms

FOUR CALL MODES
  Unary          C --[1 msg]--> S     C <--[1 msg]-- S
  Server stream  C --[1 msg]--> S     C <--[n msgs]- S   (catalogue export)
  Client stream  C --[n msgs]-> S     C <--[1 msg]-- S   (bulk upload)
  Bidirectional  C <=[n msgs]=> S                        (live tracking)
```

## 7. Detailed example

### BookNook

BookNook has split into services: `gateway` (public REST, Node.js), `catalog` (Go), `pricing` (Go), `inventory` (Java), `orders` (Python), `recommendations` (Python). The browser talks REST to the gateway; the gateway and all services talk gRPC to each other.

```protobuf
syntax = "proto3";
package booknook.catalog.v1;

service CatalogService {
  rpc GetBook      (GetBookRequest)      returns (Book);
  rpc BatchGetBooks(BatchGetBooksRequest) returns (BatchGetBooksResponse);
  rpc ListBooks    (ListBooksRequest)    returns (stream Book);
  rpc BulkUpsert   (stream Book)         returns (UpsertSummary);
}
```

Four design decisions worth defending:

- **`BatchGetBooks` exists precisely because RPC is chatty by default.** The home screen needs 24 books. 24 unary calls at 0.8 ms each would be 19 ms of round trips *inside the datacentre* and 24× the per-call overhead. One batch call is 1.4 ms. **Design coarse-grained methods** — this is the single most important gRPC design habit.
- **`ListBooks` is server-streaming** so the nightly partner export never materialises 1.2 M books in memory on either side. The REST equivalent needed cursor pagination and 12,000 HTTP requests (Lesson 10, Section 9).
- **`BulkUpsert` is client-streaming** for publisher feeds: 200,000 records flow in while the server writes them in batches of 500, returning one summary.
- **Every method is declared in `package ...v1`**, with a `buf breaking` check in CI.

**The incident that taught them Rule 2.** A junior engineer deleted the deprecated `int64 price = 4;` field and, three weeks later, a different engineer added `string currency_code = 4;`. The catalogue service deployed first. For 40 minutes, the Java inventory service — still running the old schema and still sending `price` in field 4 — had its integers reinterpreted as string length prefixes. Some messages decoded as garbage; some threw `InvalidProtocolBufferException`; some *silently* produced empty currency codes. No error pointed at the real cause, because **protobuf cannot detect the mistake — field names aren't on the wire.** The fix was one line they should have written at deletion time:

```protobuf
reserved 4;
reserved "price";
```

and a `buf breaking` gate in CI so the compiler, not a reviewer, enforces it.

**The second incident: deadlines.** `recommendations` began taking 3 seconds under load. Because the gateway set no deadline, requests piled up: every gateway worker was blocked waiting, the connection pool drained, and the *entire site* — including pages that don't show recommendations — went down. A slow non-critical service took out a healthy critical path. Fixes, in order of value: (1) set a 150 ms deadline on the recommendations call, (2) treat `DEADLINE_EXCEEDED` as "render the page without the strip", (3) add a circuit breaker (Phase 4, Topic 14). **A missing deadline turns a slow dependency into a total outage.**

### Contrasting example — a trading or telemetry platform

A market-data platform shows gRPC's streaming side rather than its request/response side.

- A **bidirectional stream** carries subscription changes up and price ticks down over **one** connection: the client sends `Subscribe(["AAPL","MSFT"])`, then `Unsubscribe(["MSFT"])`, while quotes flow continuously the other way.
- **Volume** makes encoding decisive: 400,000 ticks/second. A tick as JSON is ~110 bytes; as protobuf with `sint32` deltas and packed repeated fields, ~18 bytes. That is `400,000 × 92 B = 36.8 MB/s = 294 Mbps` of bandwidth saved — plus the CPU of parsing 400,000 JSON documents per second, which alone would need several cores.
- **No caching is lost**, because every message is unique and real-time. The main REST advantage simply doesn't apply.
- **Backpressure** matters: gRPC inherits HTTP/2 flow control, so a slow consumer naturally slows the producer instead of causing unbounded buffering — something you'd have to build by hand over raw WebSocket.

The contrast with BookNook: BookNook's gRPC win is **typed contracts and composition**; the trading platform's win is **encoding efficiency and streaming**. Know which one you're buying.

## 8. Code or configuration example

This **illustrates protobuf definition, schema-evolution discipline, deadlines, status codes, and interceptors**. Not production-ready: no TLS/mTLS, no real auth, no connection-pool tuning, no graceful shutdown, no tests.

```protobuf
// booknook/catalog/v1/catalog.proto -- ILLUSTRATIVE ONLY.
syntax = "proto3";

// Version lives in the PACKAGE. A breaking change becomes catalog.v2, and both
// can be served side by side while clients migrate at their own pace.
package booknook.catalog.v1;

service CatalogService {
  rpc GetBook       (GetBookRequest)       returns (Book);
  // Coarse-grained batch method. RPC makes each call cheap, which TEMPTS you
  // into 24 calls in a loop. 24 cheap calls still cost 24 round trips.
  rpc BatchGetBooks (BatchGetBooksRequest) returns (BatchGetBooksResponse);
  // Server streaming: the export job never holds 1.2M books in memory.
  rpc ListBooks     (ListBooksRequest)     returns (stream Book);
}

message Book {
  // These numbers were used by fields that no longer exist. Reserving them
  // makes it a COMPILE ERROR to reuse them. Without this, a future engineer
  // assigning `= 4` to a string silently corrupts data for every old client
  // still sending an int64 there -- with NO error, because protobuf never
  // sends field names and so cannot detect the mismatch.
  reserved 4, 11 to 15;
  reserved "price", "legacy_sku";

  string isbn        = 1;   // field number = permanent identity on the wire
  string title       = 2;   // renaming this field is SAFE; renumbering is NOT
  string author      = 3;
  int64  price_paise = 8;   // integer paise, never float -- floats lose money
  int32  stock       = 5;
  bool   in_stock    = 6;
  float  rating      = 7;

  // Added in v1.4. Old servers that don't know this field PRESERVE the bytes
  // and echo them back on re-serialisation, so an intermediate proxy running
  // older code cannot silently destroy data it doesn't understand.
  repeated string genres = 9;

  // Enums MUST have a zero value, because 0 is what an old client sees for a
  // value it doesn't recognise. Naming it _UNSPECIFIED makes that explicit
  // instead of accidentally meaning "HARDCOVER".
  enum Format {
    FORMAT_UNSPECIFIED = 0;
    FORMAT_PAPERBACK   = 1;
    FORMAT_HARDCOVER   = 2;
    FORMAT_EBOOK       = 3;
  }
  Format format = 10;
}

message GetBookRequest  { string isbn = 1; }
message BatchGetBooksRequest {
  // Cap this server-side too. An unbounded list is a free DoS: one caller asks
  // for 1,000,000 ISBNs and your service OOMs.
  repeated string isbns = 1;
}
message BatchGetBooksResponse {
  repeated Book books = 1;
  // Partial success is the NORM for batch APIs. Reporting which inputs failed
  // is far more useful than failing all 24 because 1 was missing.
  repeated string not_found_isbns = 2;
}
message ListBooksRequest { string genre = 1; int32 page_size = 2; }
message UpsertSummary    { int32 created = 1; int32 updated = 2; }
```

```python
# client.py -- ILLUSTRATIVE ONLY, not production-ready.
import grpc, time, uuid
from booknook.catalog.v1 import catalog_pb2, catalog_pb2_grpc

# A CHANNEL is a long-lived, managed HTTP/2 connection (or pool). Create it ONCE
# per process at startup. Creating a channel per request pays a TCP + TLS
# handshake every call -- a 10x latency regression people ship surprisingly often.
channel = grpc.insecure_channel(            # insecure_channel = DEMO ONLY; use mTLS
    "catalog.internal:50051",
    options=[
        # Balance per CALL across backends. Without round_robin, gRPC opens ONE
        # connection and pins every request to ONE backend -- so a newly scaled-up
        # pod receives zero traffic. This is THE classic gRPC scaling bug.
        ("grpc.lb_policy_name", "round_robin"),
        # Recycle connections so the LB can rebalance onto new backends.
        ("grpc.max_connection_age_ms", 300000),
        # Declarative retry policy: ONLY for UNAVAILABLE (transient). Never retry
        # INVALID_ARGUMENT (it will fail identically) or INTERNAL (reproduces a bug).
        ("grpc.service_config", '''{
            "methodConfig": [{
              "name": [{"service": "booknook.catalog.v1.CatalogService"}],
              "retryPolicy": {
                "maxAttempts": 3,
                "initialBackoff": "0.05s",
                "maxBackoff": "0.5s",
                "backoffMultiplier": 2,
                "retryableStatusCodes": ["UNAVAILABLE"]
              }
            }]
        }'''),
    ],
)
stub = catalog_pb2_grpc.CatalogServiceStub(channel)


def get_books(isbns):
    """ONE batch call, not len(isbns) calls. See the proto comment above."""
    req = catalog_pb2.BatchGetBooksRequest(isbns=isbns)
    try:
        # `timeout` becomes an absolute DEADLINE propagated to every downstream
        # hop. ALWAYS set one: a call with no deadline can hang until the TCP
        # connection dies, holding a worker thread the entire time. One missing
        # deadline on a non-critical dependency can take down the whole site.
        resp = stub.BatchGetBooks(req, timeout=0.250,
                                  metadata=(("x-request-id", str(uuid.uuid4())),))
        return resp.books
    except grpc.RpcError as e:
        code = e.code()
        if code == grpc.StatusCode.DEADLINE_EXCEEDED:
            # Degrade, don't fail. A missing "recommended" strip beats a 500 page.
            return []
        if code == grpc.StatusCode.UNAVAILABLE:
            raise                      # already retried by the policy above
        if code == grpc.StatusCode.INVALID_ARGUMENT:
            raise ValueError(e.details())   # our bug; retrying cannot help
        raise
```

```python
# server_interceptor.py -- cross-cutting concerns in ONE place, not per handler.
class MetricsInterceptor(grpc.ServerInterceptor):
    def intercept_service(self, continuation, handler_call_details):
        start = time.perf_counter()
        # Label by METHOD NAME, which is low-cardinality and bounded -- unlike
        # expanded REST paths, which blow up a metrics backend (Lesson 10).
        method = handler_call_details.method
        handler = continuation(handler_call_details)
        # (A real implementation wraps the handler to observe status + duration.)
        LATENCY.labels(method=method).observe(time.perf_counter() - start)
        return handler
```

```bash
# gRPC is binary, so curl is useless. grpcurl is the equivalent tool.
# Server reflection lets it discover the schema at runtime -- enable it in dev,
# think carefully before enabling it in production (it exposes your whole API).
grpcurl -plaintext localhost:50051 list
grpcurl -plaintext -d '{"isbn":"9781492052203"}' \
        localhost:50051 booknook.catalog.v1.CatalogService/GetBook

# Catch breaking schema changes in CI, BEFORE they reach production.
buf lint
buf breaking --against '.git#branch=main'
```

## 9. Numerical example

### Part A — protobuf versus JSON, byte by byte, for one BookNook `Book`

**Assumptions:** a realistic book record; JSON is compact (no whitespace); ASCII strings so 1 char = 1 byte; protobuf field numbers all ≤ 15 (so each tag is 1 byte).

The record:

```json
{"isbn":"9781492052203","title":"Designing Data-Intensive Applications","author":"Martin Kleppmann","price_paise":320000,"stock":7,"in_stock":true,"rating":4.8}
```

**JSON, field by field:**

| Element | Breakdown | Bytes |
|---|---|---|
| `{` and `}` | 1 + 1 | 2 |
| `"isbn":"9781492052203",` | name 6 + colon 1 + value 15 + comma 1 | 23 |
| `"title":"Designing Data-Intensive Applications",` | 7 + 1 + 39 + 1 | 48 |
| `"author":"Martin Kleppmann",` | 8 + 1 + 18 + 1 | 28 |
| `"price_paise":320000,` | 13 + 1 + 6 + 1 | 21 |
| `"stock":7,` | 7 + 1 + 1 + 1 | 10 |
| `"in_stock":true,` | 10 + 1 + 4 + 1 | 16 |
| `"rating":4.8` | 8 + 1 + 3 | 12 |
| **JSON total** | | **160 bytes** |

**Protobuf, field by field** (tag = 1 byte each here):

| Field | Encoding | Bytes |
|---|---|---|
| `isbn` (1, len-delim) | tag 1 + length 1 + 13 chars | 15 |
| `title` (2, len-delim) | tag 1 + length 1 + 37 chars | 39 |
| `author` (3, len-delim) | tag 1 + length 1 + 16 chars | 18 |
| `price_paise` (4, varint) | tag 1 + varint(320000) = 3 bytes, since 2¹⁴=16,384 ≤ 320,000 < 2²¹=2,097,152 | 4 |
| `stock` (5, varint) | tag 1 + varint(7) = 1 | 2 |
| `in_stock` (6, varint/bool) | tag 1 + 1 | 2 |
| `rating` (7, fixed32 float) | tag 1 + 4 | 5 |
| **Protobuf total** | | **85 bytes** |

```
Reduction = (160 - 85) / 160 = 46.9 % smaller
```

**Crucial nuance most people miss: 70 of those 85 protobuf bytes are string content** (13 + 37 + 16 = 66 chars plus tags/lengths). Protobuf cannot compress text — it only removes field names and tightens numbers. **The win depends entirely on your field-name-to-value ratio.**

### Part B — a realistic list response, and the gzip reality check

**Assumptions:** `BatchGetBooks` returns 50 books; gzip compresses repetitive JSON at ~4.5:1 and protobuf (already dense, little redundancy) at ~1.15:1 — both typical, both approximate.

```
JSON:     50 × 160 B = 8,000 B
          + array brackets and 49 commas = 51 B
          = 8,051 B = 7.86 KB

Protobuf: each Book nested in `repeated Book books = 1` costs
          tag 1 + length 1 + 85 = 87 B
          50 × 87 = 4,350 B = 4.25 KB

Raw reduction = (8,051 - 4,350) / 8,051 = 46.0 %

With gzip:
  JSON gzipped     = 8,051 / 4.5  = 1,789 B = 1.75 KB
  Protobuf gzipped = 4,350 / 1.15 = 3,783 B = 3.69 KB

  ---> GZIPPED JSON IS SMALLER THAN GZIPPED PROTOBUF HERE.
```

**This is the honest result, and you should say it in an interview.** Compression eats most of protobuf's size advantage on text-heavy payloads, because what gzip removes (repeated field names) is exactly what protobuf removes. So if size were the only argument, gzipped JSON would often win.

The real, durable gRPC advantages are therefore:

1. **CPU**, not bytes. Assumption: JSON parse ≈ 180 MB/s, protobuf decode ≈ 800 MB/s on one core (typical orders of magnitude).
   ```
   At 20,000 RPS × 8,051 B JSON  = 161 MB/s → 161/180 = 0.89 cores just parsing
   At 20,000 RPS × 4,350 B proto =  87 MB/s →  87/800 = 0.11 cores decoding
   Saving ≈ 0.78 cores per 20k RPS, before counting gzip's own CPU cost
   (gzip at level 6 costs roughly 30-60 MB/s/core to compress -- often MORE CPU
    than the parsing it was meant to offset).
   ```
2. **Typed contracts enforced by a compiler.**
3. **Streaming and deadline propagation.**

### Part C — where protobuf genuinely dominates: numeric payloads

**Assumptions:** a price-history message of 1,000 `(timestamp, price_paise)` pairs; timestamps as Unix seconds (~1,767,000,000, a 5-byte varint); prices 20,000–80,000 paise (3-byte varints); protobuf `packed` repeated fields (the proto3 default for scalars) write the tag **once** and then concatenate values.

```
JSON per pair: {"t":1767225600,"p":32000},
  = 1 + 4 + 10 + 1 + 4 + 5 + 1 + 1  = let's count exactly:
    `{`1 `"t"`3 `:`1 `1767225600`10 `,`1 `"p"`3 `:`1 `32000`5 `}`1 `,`1 = 27 B
  1,000 pairs = 27,000 B + brackets ≈ 27,002 B = 26.4 KB

Protobuf, two packed repeated fields:
  timestamps: tag 1 + length 2 (varint for 5,000) + 1,000 × 5 = 5,003 B
  prices:     tag 1 + length 2 (varint for 3,000) + 1,000 × 3 = 3,003 B
  total = 8,006 B = 7.8 KB

Reduction = (27,002 - 8,006) / 27,002 = 70.4 %

With gzip (JSON ~5:1 here due to heavy digit repetition, protobuf ~1.1:1):
  JSON gzipped     = 27,002 / 5.0 = 5,400 B
  Protobuf gzipped =  8,006 / 1.1 = 7,278 B
```

Even here gzipped JSON is competitive on **size** — but it costs ~4× the decode CPU and loses all type safety. **Conclusion to carry away: choose gRPC for contracts, streaming, deadlines and CPU — not primarily for bytes on the wire.** That is a more sophisticated answer than "protobuf is smaller", and it is the correct one.

### Part D — chatty versus coarse-grained interfaces

**Assumptions:** intra-datacentre RTT 0.4 ms; per-call fixed overhead (framing, interceptors, serialisation) 0.15 ms; server work 0.25 ms per book lookup, or 0.9 ms for a batch of 24 (one query instead of 24).

```
24 unary calls, issued SEQUENTIALLY:
   24 × (0.4 + 0.15 + 0.25) = 24 × 0.80 = 19.2 ms

24 unary calls, issued CONCURRENTLY on one HTTP/2 channel (Lesson 9):
   ≈ 0.4 + 0.15 + 0.25 = 0.80 ms wall clock
   BUT server CPU = 24 × (0.15 + 0.25) = 9.6 ms of work, and 24 DB queries

1 BatchGetBooks call:
   0.4 + 0.15 + 0.9 = 1.45 ms wall clock
   server CPU = 1.05 ms, and 1 DB query

Sequential vs batch : 19.2 / 1.45 = 13.2× faster
Server CPU saved    : (9.6 - 1.05) / 9.6 = 89 % less CPU per home-screen render
DB queries          : 24 → 1
```

Multiplexing fixes *wall-clock* latency; only a coarse-grained method fixes *server cost*. At 40,000 home-screen renders per day that is `40,000 × 23 = 920,000` database queries per day avoided by one method signature.

## 10. Trade-offs

**Advantages**

- **Compiler-enforced contracts.** A breaking change fails CI, not production. This is worth more than every byte you save.
- **Code generation in 11+ languages** from one file — a Go service and a Python service cannot drift.
- **Performance**: typically 3–10× faster decode than JSON, 40–70 % smaller raw payloads, HTTP/2 multiplexing on one long-lived connection.
- **Four streaming modes** with flow control and backpressure built in.
- **Deadlines that propagate**, plus cancellation that actually cancels downstream work.
- **Retry-classified status codes**, so retry policy is declarative rather than a per-caller guess.
- **Interceptors** give uniform auth/tracing/metrics with zero per-handler code.
- **Designed-in schema evolution** (unknown-field preservation, reserved numbers).

**Disadvantages**

- **Not browser-native.** Needs gRPC-Web plus a translating proxy, and loses client/bidirectional streaming there.
- **No HTTP caching.** Everything is a `POST`; CDNs and browser caches do nothing. For read-heavy public data this can outweigh all other gains.
- **Binary = harder debugging.** No `curl`, no "paste the URL in a browser". You need `grpcurl`, reflection, and protobuf-aware logging.
- **Build-system complexity**: `protoc`/`buf`, generated-code checked in or generated at build time, plugin version pinning, and a repo strategy for shared protos.
- **Load balancing is harder.** Long-lived HTTP/2 connections pin to backends unless you use per-call L7 balancing (Lesson 9, Section 15).
- **Tighter coupling via a shared schema repository**, which becomes an organisational dependency and needs governance.
- **Sharp edges**: proto3 default values can't be distinguished from "absent" without `optional`; enum defaults silently absorb unknown values.

**Operational costs**

- Someone must own the proto repo, its lint rules, and its breaking-change gate.
- Observability tooling must understand gRPC status codes, not just HTTP statuses — otherwise everything looks like `200`.
- Mesh/proxy configuration (Envoy, Linkerd) for balancing and gRPC-Web translation.
- Onboarding cost: every new engineer must learn protobuf semantics and the evolution rules.

**When it becomes unnecessary complexity:** a small system with 2–3 services, modest traffic, and one language. The codegen pipeline and proxy infrastructure will cost you more than JSON parsing ever would. Use REST until typed contracts or streaming becomes a real, named pain.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| No deadline set on a call to a slow service | Entire site hangs, not just the slow feature — workers block, pools drain | Thread/connection-pool saturation metrics; latency rises on *unrelated* endpoints | Set deadlines everywhere; degrade on `DEADLINE_EXCEEDED`; add a circuit breaker (Phase 4, Topic 14) |
| Reused field number after deletion | Garbage data, wrong prices, or crashes — with no error pointing at the cause | Protobuf parse errors in logs; impossible field values; correlates with a deploy | `reserved` the numbers and names; roll back; enforce `buf breaking` in CI |
| HTTP/2 connection pins to one backend | New pods take no traffic; one pod is hot while others idle | Per-pod RPS wildly uneven after scale-up | `round_robin` LB policy, `MAX_CONNECTION_AGE` to recycle, or an L7 proxy/mesh |
| Channel created per request | Latency 10× higher than expected; file descriptors climb | Connection-establishment rate ≈ request rate; TLS handshake CPU is high | Create the channel once at startup and share it |
| Retrying a non-idempotent method on `UNAVAILABLE` | Duplicate orders or double charges | Duplicate rows; payment-provider duplicate alerts | Restrict the retry policy to safe methods; use idempotency keys (Lesson 17) |
| Unbounded `repeated` field in a request | Service OOMs and restarts; all in-flight calls die | Memory spikes correlated with one method; `RESOURCE_EXHAUSTED` or OOM-kill events | Cap list sizes in validation and set `max_receive_message_length` (default 4 MB) |
| Proto change deployed to server before clients | Old clients see default values (`0`, `""`) and behave as if data is missing | Sudden rise in "field missing" branches; silent wrong behaviour rather than errors | Deploy additively; never remove a field in the same release that stops writing it |
| Enum value added, old client receives it | Old client hits a `default:` branch or throws | Unknown-enum metrics; crash reports on one client version | Always include `_UNSPECIFIED = 0`; always handle the default branch defensively |
| gRPC errors reported as HTTP 200 by your proxy | Dashboards show 100 % success during an outage | Success rate stays flat while user complaints rise — worst possible signature | Configure the proxy/observability stack to read the `grpc-status` trailer |
| Browser team tries to call gRPC directly | Feature simply can't ship | Fails immediately in development | Use gRPC-Web with a proxy, or gRPC-Gateway to expose REST/JSON |

## 12. Alternatives

| Approach | Encoding | Transport | Typed? | Streaming | Browser | Cacheable | Best for |
|---|---|---|---|---|---|---|---|
| **gRPC** | protobuf (binary) | HTTP/2 | Yes, compiler-enforced | All 4 modes | Via gRPC-Web proxy | No | Internal service-to-service, polyglot, high volume |
| **REST / HTTP-JSON** (Lesson 10) | JSON (text) | HTTP/1.1–3 | No (OpenAPI is docs) | SSE bolt-on | Native | **Yes** | Public APIs, CRUD, browser traffic |
| **GraphQL** (Lesson 12) | JSON | HTTP | Schema, runtime-checked | Subscriptions | Native | Hard | Many clients with differing data needs |
| **Apache Thrift** | Compact binary | TCP/HTTP | Yes | Limited | No | No | Meta-era systems; still solid, smaller ecosystem |
| **Apache Avro** | Binary + schema | Any (often Kafka) | Yes, with a schema registry | N/A | No | No | Data pipelines; superb schema evolution |
| **Cap'n Proto / FlatBuffers** | Zero-copy binary | Any | Yes | Some | No | No | Ultra-low-latency, mmap'd data, games |
| **MessagePack / CBOR** | Binary JSON-like | Any | No | No | Yes (lib) | Partly | Drop-in smaller JSON without a schema |
| **JSON-RPC** | JSON | Any | No | No | Yes | No | Simple RPC, blockchain nodes |
| **SOAP** | XML | HTTP | Yes (WSDL) | No | Painful | No | Legacy enterprise integration |
| **tRPC** | JSON | HTTP | Yes (TS inference) | Limited | Yes | No | TypeScript-only full-stack monorepos |
| **Message queues** (Phase 4, Topic 5) | Any | Broker | Varies | Inherent | No | N/A | Async work, decoupling, buffering |

## 13. When to use it

1. **Internal service-to-service communication at meaningful volume.** Two services you own, exchanging thousands of messages per second, in a datacentre where no CDN would ever help. All of gRPC's advantages apply and its main disadvantage (no caching) costs nothing.
2. **Polyglot organisations.** Go, Java, Python and Node services sharing one `.proto` cannot drift, and nobody writes a client by hand. This is often the *decisive* reason, independent of performance.
3. **Streaming workloads.** Catalogue exports, telemetry, live tracking, log shipping, chat backends. Server streaming replaces 12,000 paginated HTTP calls with one call; bidirectional streaming replaces a hand-rolled WebSocket protocol with a typed one.
4. **Strict latency budgets with multi-hop call chains.** Deadline propagation plus cancellation prevents the "slow dependency takes out the site" failure in Section 7, and stops services burning CPU on abandoned work.
5. **Mobile clients on constrained networks**, where protobuf's smaller payloads and lower parse cost translate into measurable battery and latency savings — and where gRPC (unlike gRPC-Web) works natively on iOS and Android.
6. **Contract-critical integrations** between teams that deploy independently, where `buf breaking` in CI is cheaper than an incident review.

## 14. When *not* to use it

1. **Public, third-party-facing APIs.** Partners expect `curl`, Postman, and a browser. Handing them a `.proto` and a codegen toolchain raises integration cost enormously. Expose REST (Lesson 10) — optionally generated from the same protos via gRPC-Gateway.
2. **Browser-facing traffic.** No native support; gRPC-Web needs a proxy, loses two streaming modes, and base64 inflation can erase the size win entirely.
3. **Read-heavy public data that a CDN could cache.** If `GET /books/{isbn}` can be served from an edge cache at ~0 cost and ~15 ms, no amount of protobuf efficiency competes with *not making the request at all*.
4. **Small systems with 2–3 services and one language.** The proto repo, codegen pipeline, and proxy configuration will cost more engineering time than JSON ever will. Adopt gRPC when you can name the pain it solves.
5. **When debuggability outranks performance.** Low-volume internal admin tools, webhooks, or integrations maintained by non-specialists are better served by something you can read in a log line.
6. **Fire-and-forget or buffered work.** If the caller doesn't need an answer now — sending email, generating a report, updating a search index — a message queue decouples failure domains far better than a synchronous RPC (Lesson 13; Phase 4, Topic 5).

## 15. Common mistakes

**Beginner mistakes**

- **No deadline on any call.** The default is "wait forever". One slow dependency then exhausts every worker and takes down unrelated endpoints.
- **Creating a channel per request**, paying TCP + TLS handshakes on every call.
- **Calling a unary method in a loop.** 24 calls in a `for` loop is 24 round trips; add a batch method.
- **Thinking field numbers are just ordering.** They are permanent wire identity.
- **Using `float`/`double` for money.** `0.1 + 0.2 != 0.3`. Use `int64` paise/cents.
- **Expecting `curl` to work.** It's binary; learn `grpcurl` and enable reflection in non-production environments.
- **Returning `INTERNAL` for everything**, destroying the retry information the status codes exist to carry.
- **Assuming proto3 can tell "absent" from "zero".** It can't, unless you mark the field `optional` or use a wrapper type — a real source of "the user set stock to 0 and we ignored it" bugs.

**Experienced-engineer mistakes**

- **Not recycling HTTP/2 connections**, so autoscaling adds pods that receive zero traffic. Set `MAX_CONNECTION_AGE` (server-side) and use `round_robin` or a service mesh.
- **Reusing a deleted field number** because nobody wrote `reserved`. Silent data corruption with no error.
- **No `buf breaking` gate in CI**, leaving schema compatibility to human code review — which will eventually miss one.
- **Retry policies on non-idempotent methods.** `UNAVAILABLE` on `CreateOrder` plus automatic retry equals duplicate orders (Lesson 17).
- **Not propagating deadlines into the database layer.** The gRPC context is cancelled but the SQL query keeps running, so the connection pool stays exhausted and you get no benefit.
- **Leaking protobuf types into domain logic**, so that every schema change ripples through the whole codebase. Map generated types to domain types at the boundary.
- **A single shared "common.proto"** that every service imports, creating a change-coupling bottleneck across all teams.
- **Enabling server reflection in production** without auth, publishing your full internal API surface to anyone who can reach the port.
- **Forgetting `max_receive_message_length`** (4 MB default) and discovering it only when a legitimate large response starts failing — or forgetting to lower it and letting a caller OOM you.
- **Treating gRPC as a drop-in REST replacement** without accounting for lost HTTP caching — then wondering why origin load went *up* after the migration.

## 16. Interview perspective

- **Define RPC by contrast, in one line.** "REST organises around resources and standard verbs; RPC organises around methods. gRPC is RPC with protobuf contracts over HTTP/2."
- **Say where you'd use each, with a reason.** "REST at the edge for cacheability and partner ergonomics; gRPC internally for typed contracts, streaming, and deadline propagation." Interviewers want the boundary, not a winner.
- **Lead with deadlines.** "Every gRPC call gets a deadline, and the deadline propagates, so a slow leaf service can't exhaust the gateway's workers." This is the most operationally mature thing you can say about gRPC and very few candidates say it.
- **Be precise about schema evolution.** "Field numbers are permanent; `reserved` deleted numbers and names; add fields, never renumber; enforce it with `buf breaking` in CI." Then tell the corruption story from Section 7 — concrete failure beats abstract rules.
- **Be honest about size.** "Protobuf is ~47 % smaller raw, but gzipped JSON is often competitive. The durable wins are CPU, type safety, streaming and deadlines." This signals you've measured rather than repeated marketing.
- **Name the browser constraint and the fix.** "Browsers can't read HTTP/2 trailers, so gRPC-Web needs a proxy and loses client streaming. I'd put REST or GraphQL at the edge."
- **Raise the load-balancing gotcha.** "Long-lived HTTP/2 connections pin clients to backends, so I'd use per-call L7 balancing or max connection age." Strong signal for infrastructure roles.
- **Mention the 8 fallacies by name** when discussing failure handling — it shows you know *why* hiding the network is dangerous.

**Red flags interviewers watch for:** "gRPC is just faster REST"; no mention of deadlines or timeouts; proposing gRPC for a public/browser API without acknowledging gRPC-Web; not knowing field numbers are permanent; retrying non-idempotent calls; claiming protobuf is always smaller without mentioning compression; ignoring that gRPC loses HTTP caching; unable to name a case where REST is the better choice.

## 17. Knowledge check

**Conceptual**

1. State why "making a remote call look like a local call" is considered a design trap. List the 8 Fallacies of Distributed Computing, and for three of them give a concrete bug that the fallacy causes in gRPC code specifically.
2. Explain precisely what happens on the wire when an engineer deletes `int64 price = 4;` and a later engineer adds `string currency_code = 4;`. Why can protobuf not detect this? Give the two mechanisms that prevent it.
3. Explain the difference between a timeout and a propagated deadline. Describe a three-hop call chain where per-hop timeouts produce a result nobody is waiting for, and show how deadline propagation changes the outcome.

**Practical scenario**

4. BookNook's `recommendations` service (Python, 180 ms p50 / 2.4 s p99) is called by the `gateway` (Node) on every home-screen render. Today the gateway makes one gRPC call with no deadline, and retries on any error up to 5 times. Last week a `recommendations` slowdown took the entire site down, including the checkout page. Diagnose the failure chain step by step, propose a complete fix (deadline value with justification, retry policy with which status codes, degradation behaviour, and one Phase 4 pattern), and explain what you would have to change if `recommendations` were replaced by a `CreateOrder` call instead — and why that changes your retry answer.

**Calculation**

5. BookNook's `inventory` service sends stock updates to `catalog`. Each message has: `isbn` (13-char string, field 1), `stock` (int32, value 143, field 2), `warehouse_id` (int32, value 9, field 3), `updated_at_unix` (int64, value 1767225600, field 4), and `reserved_count` (int32, value 12, field 5). All field numbers ≤ 15.
   (a) Compute the exact byte size of the compact JSON encoding, element by element.
   (b) Compute the exact protobuf byte size, field by field, stating the varint length for each integer and showing why.
   (c) At **12,000 messages/second**, how many MB/s does each format use, and what is the annual cross-AZ transfer cost at **$0.01/GB** each way?
   (d) Assuming JSON parses at 180 MB/s/core and protobuf decodes at 800 MB/s/core, how many CPU cores does each format consume at that rate?
   (e) If gzip compresses the JSON 4.5:1 at a cost of 45 MB/s/core to compress, does gzipped JSON beat protobuf on *total* CPU? Show the arithmetic and state every assumption you add.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 12.

---

**Progress checklist**

Phase 1:
- `[x] 1. Client-server architecture`
- `[x] 2. Stateful versus stateless servers`
- `[x] 3. IP addresses, ports, sockets, domain names`
- `[x] 4. DNS resolution and DNS caching`
- `[x] 5. HTTP requests and responses`
- `[x] 6. HTTP methods, headers, cookies, sessions, status codes`
- `[x] 7. HTTPS and TLS`
- `[x] 8. TCP versus UDP`
- `[x] 9. HTTP/1.1 vs HTTP/2 vs HTTP/3`
- `[x] 10. REST APIs`
- `[x] 11. RPC and gRPC`
- `[ ] 12. GraphQL`
- `[ ] 13. Synchronous versus asynchronous communication`
- `[ ] 14. Latency, throughput, bandwidth, concurrency`
- `[ ] 15. Availability, reliability, durability, fault tolerance`
- `[ ] 16. Average latency vs p50/p95/p99, tail latency`
- `[ ] 17. Idempotency, retries, timeouts, exponential backoff`

**NEXT LESSON: Phase 1, Topic 12 — GraphQL**

---

# Lesson 12 — GraphQL

*Phase 1, Topic 12*

## 1. Simple definition

**GraphQL** is a query language for APIs, plus a runtime that executes those queries. It was built at Facebook in 2012 (open-sourced 2015) and is now governed by the GraphQL Foundation.

The one-sentence version:

> Instead of the server deciding what each endpoint returns, the **client sends a query describing exactly the data it wants**, and the server returns exactly that — no more, no less — in a single request.

A query and its response are the same shape:

```graphql
query {
  book(isbn: "9781492052203") {
    title
    pricePaise
    author { name }
  }
}
```

```json
{ "data": { "book": { "title": "Designing Data-Intensive Applications",
                      "pricePaise": 320000,
                      "author": { "name": "Martin Kleppmann" } } } }
```

Terms to define before anything else:

- **Schema**: a strongly-typed description of every type and field your API offers. It is the contract, it is machine-readable, and it is mandatory.
- **Field**: a single piece of data (`title`) or a link to another type (`author`).
- **Resolver**: a server-side function that knows how to produce the value for **one field**. GraphQL execution is nothing more than calling resolvers, field by field, down the tree.
- **Operation**: one of three kinds of request — a **query** (read), a **mutation** (write), or a **subscription** (a live stream of updates).

Critically: **GraphQL is not a database and has nothing to do with graph databases.** The "graph" is your *domain* — books link to authors link to other books. Underneath, resolvers call whatever you already have: SQL, REST services, gRPC services (Lesson 11), caches, files.

## 2. Why it exists

GraphQL exists to solve the two problems Lesson 10 ended on, quantified there and restated here.

**Problem 1 — over-fetching.** `GET /books/{isbn}` returns 12 KB. The mobile "recently viewed" strip needs 180 bytes of it. On a 2012 Facebook mobile app, this was battery, data-plan cost, and latency, all wasted.

**Problem 2 — under-fetching / N+1 round trips.** Rendering one order confirmation screen needed 18 REST calls in a 4-level dependency chain — 1,224 ms sequentially, 272 ms even with HTTP/2 multiplexing, because the chain `order → items → books → authors` is inherently sequential.

The traditional fixes were both bad:

- **Bespoke endpoints**: `GET /mobile/home-screen-v3`. This works — it's the BFF pattern (Section 4.8) — but every UI change needs a backend deploy, and you accumulate dozens of unmaintainable endpoints that nobody dares delete.
- **Sparse fieldsets**: `GET /books/123?fields=title,price`. You've invented a query language in a query string, broken HTTP caching (every field combination is a different cache key), and still can't traverse to the author.

GraphQL's bet: **make the query language first-class, typed, and introspectable, so the client can ask for exactly the tree it needs in one round trip, and the UI team can change what it fetches without a backend deploy.**

The third motivation, often undersold: **introspection**. A GraphQL server can be asked to describe its own schema, which is why tooling (GraphiQL, Apollo Studio, editor autocomplete, typed client generation) is so unusually good. With REST you hope the OpenAPI file is current; with GraphQL the schema *is* the server.

## 3. Real-world analogy

**A restaurant with a fixed menu versus a build-your-own bowl counter.**

REST is the **fixed menu**. Dish 14 ("the thali") comes with rice, dal, two curries, pickle and a sweet. You wanted rice and dal. You pay for and receive all six, and you throw four away — over-fetching. If you also want a drink, that's a separate order from a separate counter — under-fetching.

GraphQL is the **build-your-own counter**. You walk the line and say exactly what goes in your bowl: "rice, dal, that curry, no pickle." One queue, one transaction, exactly what you'll eat.

**Where the analogy breaks down**

| Build-your-own counter | Real GraphQL |
|---|---|
| Whatever you pick, the cost to the kitchen is roughly the same | A query's *cost* varies wildly. `{ books { author { books { author { books } } } } }` is one small request that can trigger millions of database rows. This is why complexity limiting (Section 4.6) exists |
| The counter staff see your whole order at once and can batch it | Naive GraphQL resolvers fire **one database query per field per object** — the N+1 problem (Section 4.4), the defining operational hazard of GraphQL |
| The cashier can reuse yesterday's receipt for an identical order | Every GraphQL request is a `POST` to `/graphql` with a different body. **HTTP and CDN caching simply do not apply** (Section 4.5) |
| Nobody can order 10,000 bowls to shut the counter down | A single hostile 2 KB query can be a denial-of-service weapon |
| You see the price before you commit | The server often doesn't know a query's cost until it's part-way through executing it |

Those four rows are not nitpicks — they are the entire operational burden of running GraphQL, and they're why Sections 4.4–4.6 exist.

## 4. Detailed technical explanation

### 4.1 The schema and the type system

The schema is written in **SDL (Schema Definition Language)**:

```graphql
type Book {
  isbn: ID!                 # "!" means NON-NULL: this field can never be null
  title: String!
  pricePaise: Int!
  rating: Float
  author: Author!           # a link to another type -- this is the "graph"
  reviews(first: Int = 10, after: String): ReviewConnection!
}

type Author {
  id: ID!
  name: String!
  books(first: Int = 10): [Book!]!   # list of non-null Books, list itself non-null
}

type Query {                # the ENTRY POINTS for reads
  book(isbn: ID!): Book
  books(genre: String, first: Int = 20, after: String): BookConnection!
  me: Customer
}

type Mutation {             # the ENTRY POINTS for writes
  createOrder(input: CreateOrderInput!): CreateOrderPayload!
}

type Subscription {         # the ENTRY POINTS for live updates
  orderStatusChanged(orderId: ID!): Order!
}
```

Scalar types are `Int`, `Float`, `String`, `Boolean`, `ID`, plus custom scalars you define (`DateTime`, `EmailAddress`). Composite types are `type` (object), `interface`, `union`, `enum`, and `input` (for arguments).

**Nullability is the most consequential design decision in a GraphQL schema**, and it is routinely got wrong. If a non-null (`!`) field's resolver throws, GraphQL cannot return `null` there — so the error **propagates upward** to the nearest nullable ancestor and nulls *that* instead. Mark a whole response tree non-null and one failing leaf field wipes out the entire response. Practical guidance: **be generous with nullability on fields backed by remote calls**, so a failing recommendation service degrades one field rather than blanking the page.

### 4.2 Queries, mutations, subscriptions

**Queries** are reads, and the spec allows the server to resolve sibling fields **in parallel**.

```graphql
query HomeScreen($isbn: ID!, $reviewCount: Int!) {
  book(isbn: $isbn) {
    title
    author { name }
    reviews(first: $reviewCount) {
      edges { node { rating body } }
    }
  }
  me { cart { itemCount } }        # resolved in parallel with `book`
}
```

Note **variables** (`$isbn`). Never string-interpolate values into a query document: it defeats query caching and persisted queries, and in some server implementations it's an injection risk. Pass variables separately as JSON.

**Mutations** are writes, and top-level mutation fields are resolved **in series, in document order** — a deliberate spec rule, so `addItem` then `checkout` can't race.

```graphql
mutation PlaceOrder($input: CreateOrderInput!) {
  createOrder(input: $input) {
    order { id status totalPaise }      # return the new state
    errors { code message field }       # expected business errors as DATA
  }
}
```

That `errors` field is the **"errors as data"** pattern and it matters. GraphQL's top-level `errors` array is for *unexpected* failures (a resolver threw, a service is down). **Expected business outcomes** — insufficient stock, invalid coupon, address not found — should be typed fields in the payload, so clients handle them with the type system instead of string-matching an error message. This is the single biggest schema-design improvement most teams can make.

**Subscriptions** are long-lived streams, usually over WebSocket (the `graphql-transport-ws` protocol) or SSE. The server pushes a new result each time the event fires. They are genuinely stateful connections, with all of Lesson 2's consequences: you must think about where the connection terminates, how it survives a deploy, and how events reach the right server (usually via Redis pub/sub or a message bus — Phase 4, Topic 5).

### 4.3 How execution actually works

1. **Parse** the query string into an AST. Reject syntax errors.
2. **Validate** the AST against the schema: do these fields exist, are argument types right, are fragments used correctly? This happens *before* any resolver runs — a huge advantage over REST, where you find out at runtime.
3. **Execute**: walk the query tree calling a resolver per field.

A resolver has the signature `(parent, args, context, info)`:

```javascript
Query: {
  book: (parent, { isbn }, ctx) => ctx.db.books.findByIsbn(isbn)
},
Book: {
  // `parent` is the Book object returned above. This resolver turns
  // parent.authorId into a full Author.
  author: (parent, args, ctx) => ctx.db.authors.findById(parent.authorId)
}
```

If a field has no resolver, GraphQL uses the **default resolver**: return `parent[fieldName]`. That's why simple scalar fields need no code.

The execution shape is a tree, and **the tree is the problem**, as the next section shows.

### 4.4 The N+1 resolver problem and DataLoader

This is the defining operational hazard of GraphQL. You must be able to explain it precisely.

The query:

```graphql
query { books(first: 50) { title author { name } } }
```

Naive execution:

```
1 query:    SELECT * FROM books LIMIT 50                     -> 50 books
Then, for EACH of the 50 books, the `author` resolver runs:
50 queries: SELECT * FROM authors WHERE id = ?               -> × 50
TOTAL: 51 queries.  This is "N+1".
```

It's worse than it looks. Add `author { books(first: 3) { title } }` and you get 1 + 50 + 150 = **201 queries** from a 5-line request. The client wrote something innocent; the database got hammered.

**Why does this happen here and not in REST?** Because in REST a developer writes the SQL for an endpoint and *sees* the join. In GraphQL, resolvers are written per field in isolation, and nobody can see the whole access pattern from any single file. **The architecture that gives clients flexibility also hides the cost from the implementer.**

**DataLoader** is the standard fix (a small library, originally from Facebook, now ported everywhere). Two mechanisms:

1. **Batching.** Within a single tick of the event loop, DataLoader collects every `load(key)` call into an array and invokes your **batch function once** with all keys. Your batch function issues `SELECT * FROM authors WHERE id IN (...)`.
2. **Per-request caching.** If the same key is requested twice in one request, the second `load` returns the first's promise. No duplicate work.

```javascript
const authorLoader = new DataLoader(async (ids) => {
  const rows = await db.query('SELECT * FROM authors WHERE id = ANY($1)', [ids]);
  const byId = new Map(rows.map(r => [String(r.id), r]));
  // MUST return results in the SAME ORDER as `ids`, with null for misses.
  return ids.map(id => byId.get(String(id)) ?? null);
});
```

Two rules that bite people:

- **The batch function must return an array of exactly the same length and order as the keys array**, using `null` for misses. Return a filtered list and every book gets the wrong author — silently.
- **DataLoader instances must be created per request**, not per process. A process-wide loader is a cross-user cache that will eventually serve user A's private data to user B. This is a real, reported class of security bug.

**Worked example.** BookNook's "browse databases" page:

```graphql
query { books(genre: "databases", first: 50) {
          title
          author { name }
          reviews(first: 3) { rating }
        } }
```

```
WITHOUT DataLoader:
  1   SELECT books ... LIMIT 50
  50  SELECT author WHERE id = ?            (one per book)
  50  SELECT reviews WHERE book_id = ? LIMIT 3
  = 101 queries

WITH DataLoader:
  1   SELECT books ... LIMIT 50
  1   SELECT * FROM authors WHERE id = ANY($1)        -- 50 ids, deduped
  1   SELECT * FROM reviews WHERE book_id = ANY($1)   -- 50 ids, windowed
  = 3 queries
```

Full latency arithmetic is in Section 9. Spoiler: 258 ms → 15 ms.

A caveat for honesty: batching "top 3 reviews per book" in **one** SQL statement requires a lateral join or a window function (`ROW_NUMBER() OVER (PARTITION BY book_id ORDER BY rating DESC)`). DataLoader hands you the 50 keys; writing an efficient batch query is still your job. **DataLoader solves the round-trip count, not SQL design.**

### 4.5 Why HTTP caching breaks, and what replaces it

REST gets caching free (Lesson 10): `GET /books/123` is a stable URL with a stable response, so browsers, proxies and CDNs cache it with one header.

GraphQL breaks every assumption that relies on:

1. **One URL.** Everything is `/graphql`, so a cache cannot distinguish "the book list" from "my order history" by URL.
2. **`POST`, not `GET`.** `POST` is non-cacheable by definition; intermediaries will not touch it.
3. **Response varies by body.** The cache key would have to be a hash of the query *and* variables *and* the user's identity.
4. **Partial overlap.** Two different queries may share 90 % of their data, but as whole documents they're distinct — so document-level caching has a poor hit rate anyway.

What replaces it, in layers:

- **Automatic Persisted Queries (APQ) over `GET`.** The client sends only a SHA-256 hash of the query; if the server knows it, it executes it; if not, the client resends the full document once to register it. Because the hash is short, the request fits in a `GET` URL — **which makes it CDN-cacheable again**. This is the standard way to get edge caching back for public, non-personalised GraphQL data.
- **Persisted/trusted documents (the stronger form).** At build time, extract every query in your app into a manifest shipped to the server. At runtime the client may send **only** hashes from that manifest; arbitrary queries are rejected. You get: smaller requests (a 2 KB query becomes a 64-byte hash), `GET`-ability, and — most importantly — **complete immunity to malicious queries**, because no attacker-authored query can execute. If you take one production recommendation from this lesson, take this one.
- **Normalised client-side caching.** Apollo Client, Relay and urql store a flat, normalised cache keyed by `__typename` + `id` (e.g. `Book:9781492052203`). A field fetched by one query is reused by another. This is *more* powerful than HTTP caching — it dedupes at the **entity** level, not the response level — but it lives in the client, so it does nothing for server load.
- **Server-side resolver caching.** Cache inside resolvers (Redis, in-process LRU), optionally driven by `@cacheControl(maxAge:)` directives that compute a response-level TTL as the **minimum** across all fields touched. Note the consequence: one `maxAge: 0` field (stock) makes the entire response uncacheable — which is the same "split resources by rate of change" lesson from Lesson 10, in a new costume.
- **CDN caching of persisted `GET` queries** for public, non-personalised data.

**Say this in an interview:** "GraphQL trades free HTTP caching for client-specified queries. You get the caching back with persisted queries over `GET` plus normalised client caches — but it is infrastructure you now own rather than infrastructure you inherit."

### 4.6 Complexity, depth limiting, and malicious queries

Because clients author queries, **a client can author an expensive one**. The canonical attack is a cyclic query:

```graphql
query Evil {
  book(isbn: "x") {
    author { books { author { books { author { books {
      author { books { title } } } } } } } }
  }
}
```

That's under 300 bytes. With 10 books per author and 10 authors per book, depth 8 expands to roughly 10⁸ = **100 million** node resolutions. One request, one attacker, your database on fire. (A related classic is **alias amplification**: `a1: book(...) a2: book(...) ... a1000: book(...)` — one document, 1,000 executions of the same expensive field.)

Defences, in the order you should apply them:

| Defence | What it does | Limitation |
|---|---|---|
| **Persisted/trusted documents** | Only queries from your build manifest may run | Doesn't help a genuinely public API |
| **Depth limiting** | Reject queries nested deeper than N (typically 7–12) | Doesn't stop a shallow, wide query (1,000 aliases) |
| **Breadth/alias limiting** | Cap aliases and total field count per document | Crude; tuned by trial |
| **Query cost analysis** | Assign a cost per field (scalars 0, DB-backed fields 1, lists cost × `first`), compute before execution, reject above a budget | Requires maintaining cost annotations; estimates can be wrong |
| **Mandatory pagination arguments** | Every list field requires `first`, capped (e.g. ≤ 100) | Schema discipline; must be enforced in review or lint |
| **Timeouts + resolver-level rate limits** | Bound the damage of anything that slips through | Reactive, not preventive |
| **Disable introspection in production** | Hides the schema from casual attackers | Security by obscurity; useful but weak alone |
| **Per-user complexity budgets** | Rate-limit by *cost*, not request count | Needs cost analysis working first |

A cost-analysis example — BookNook's budget is 1,000 points:

```
books(first: 50)            = 50 points  (list multiplier)
  title                     =  0         (scalar on an already-loaded object)
  author                    = 50 × 1     (one DB-backed field per book)
  reviews(first: 3)         = 50 × 3 = 150
TOTAL                       = 250 points  -> allowed

books(first: 100) { author { books(first: 100) { author { name } } } }
  = 100 + 100×100 + 100×100 = 20,100 points -> REJECTED before any resolver runs
```

**Rate limiting by request count is nearly meaningless in GraphQL**, because one request can be 10,000× more expensive than another. Rate limit by **cost**.

### 4.7 Federation

One `/graphql` endpoint across a company becomes a bottleneck: every team edits one schema, one service owns everything, deploys couple. **Federation** (Apollo Federation; the open alternative is schema stitching, and there is a similar Netflix/DGS ecosystem) lets each team own a **subgraph** and have a **gateway/router** compose them into one supergraph that clients see as a single schema.

```graphql
# catalog subgraph (Go team)
type Book @key(fields: "isbn") {
  isbn: ID!
  title: String!
  author: Author!
}

# reviews subgraph (Python team) -- EXTENDS a type it does not own
type Book @key(fields: "isbn") {
  isbn: ID! @external
  reviews(first: Int = 10): [Review!]!     # new field on someone else's type
  averageRating: Float
}
```

The router receives one client query, builds a **query plan**, calls each subgraph for its part (in parallel where possible), and resolves cross-subgraph links via the `_entities` lookup on `@key` fields. Clients are unaware any of this happened.

The honest cost: a router is a new critical-path service to run, operate and scale; query plans add a hop and real latency; cross-subgraph N+1 is now distributed and harder to see; and schema composition must be validated in CI (`rover subgraph check`) or one team's change breaks the supergraph. **Federation is an organisational solution to an organisational problem — adopt it when multiple teams need to own parts of one graph, not because it sounds advanced.**

### 4.8 BFF (Backend-For-Frontend)

A **BFF** is a thin server-side layer dedicated to **one** client — one for web, one for iOS, one for the TV app — that composes calls to downstream services and returns exactly the shape that client needs.

GraphQL and BFF are complementary, not alternatives:

- **BFF without GraphQL**: `GET /bff/web/home-screen` returns a hand-built composite. Simple, perfectly cacheable, typed if you want — but each UI change needs a backend deploy.
- **BFF with GraphQL**: the BFF *is* a GraphQL server over gRPC/REST downstreams. The UI team changes its query without a backend deploy; the BFF team owns performance, auth and caching.
- **No BFF, direct GraphQL**: simplest to operate, but one schema must satisfy every client, and per-client optimisation becomes political.

Very common and very sane architecture, combining all three lessons of this block:

```
Browser / mobile  --REST + GraphQL over HTTP/3-->  BFF / Gateway
                                                        |
                                                   gRPC (Lesson 11)
                                                        |
                              catalog | pricing | inventory | orders
```

REST at the edge for cacheable public resources, GraphQL at the edge for composite screens, gRPC internally for typed, fast, streaming service-to-service calls. These three lessons describe three layers of one system, not three competing choices.

### 4.9 When GraphQL is overkill

Be able to say this unprompted:

- **One client, CRUD-shaped data.** An internal admin tool over 12 tables gains nothing and costs you DataLoader, complexity limits, and a lost CDN.
- **Read-heavy public data that a CDN could serve.** `GET /books/{isbn}` at the edge costs ~0 and takes ~15 ms. GraphQL's `POST /graphql` goes to your origin every single time. You can claw this back with persisted `GET` queries — that is infrastructure you must build.
- **Small teams.** The operational surface (cost analysis, query allow-lists, resolver tracing, schema governance, client cache tuning) is real and permanent. A three-person team should ship REST.
- **File uploads and downloads.** GraphQL has no native binary transport; uploads need a multipart spec extension and are routinely regretted. Use plain HTTP endpoints or pre-signed URLs.
- **Simple, stable APIs.** If three endpoints have served you for two years, adding a query language adds risk, not velocity.

## 5. Request / data flow

A BookNook mobile home screen, start to finish:

1. **Build time.** A codegen tool extracts every `.graphql` document from the app, type-checks them against the published schema (failing the build if a field was removed), generates typed client models, and writes a **persisted-query manifest** mapping SHA-256 hashes → documents. The manifest is uploaded to the server.
2. **Client sends a hash, not a query.** `GET /graphql?id=sha256:4f3c...&variables={"first":24}`. Payload: ~120 bytes instead of ~2 KB.
3. **CDN check.** Because it's a `GET` with a stable URL, the CDN can serve this from cache for non-personalised queries. A hit returns in ~15 ms and never touches the origin. (Personalised queries carry `Authorization` and are marked `private`, so they pass through.)
4. **Gateway/router.** Terminates TLS, validates the auth token, rate limits by **cost budget** rather than request count.
5. **Hash lookup.** Server resolves the hash to a document. **An unknown hash is rejected outright** — no attacker-authored query can execute.
6. **Parse and validate.** The document is parsed (usually from a parsed-AST cache) and validated against the schema. Unknown fields, wrong argument types, or fragment misuse fail here, **before any resolver runs and before any database is touched**.
7. **Cost analysis.** Static cost is computed from field annotations and list arguments. Over budget → reject with a typed error. Under budget → proceed.
8. **Context construction.** A per-request context is built: the authenticated user, a trace ID, and **fresh DataLoader instances** (fresh = no cross-user cache leak).
9. **Execution begins at `Query`.** Sibling fields resolve in parallel: `me`, `recommendations`, `cart`.
10. **Depth 2.** `recommendations` returns 24 books. For each, the `author` resolver calls `authorLoader.load(book.authorId)` — 24 calls that DataLoader collapses into **one** `WHERE id = ANY(...)` query.
11. **Depth 3.** `reviews(first: 3)` per book — 24 loads batched into one windowed SQL query using `ROW_NUMBER() OVER (PARTITION BY book_id ...)`.
12. **A downstream failure.** The `personalisedPrice` field's gRPC call to `pricing` exceeds its deadline (Lesson 11). Because the field is **nullable**, it resolves to `null`, an entry is added to the top-level `errors` array, and **the rest of the response is still returned**. The app shows the list price. *Partial success is a first-class GraphQL concept and a genuine advantage over REST's all-or-nothing status code.*
13. **Response.** Always HTTP `200` (for a successfully executed operation), body `{ "data": {...}, "errors": [...] }`. Note how this breaks naive monitoring: your HTTP dashboards show 100 % success during a partial outage. **You must alert on the `errors` array and on per-field resolver error rates**, not on HTTP status.
14. **Client cache write.** Apollo/Relay normalises the response into `Book:9781492052203`, `Author:412`, etc. The next screen that needs that book's title renders instantly from cache with no network call.
15. **Observability.** The server emits per-field resolver timings and error counts, tagged with the **operation name** (`HomeScreen`) — low-cardinality, exactly as Lesson 10 required for route templates.

## 6. ASCII diagram

```
ONE REQUEST, CLIENT-SPECIFIED SHAPE
  REST (Lesson 10)                         GraphQL
  GET /orders/991            68ms          POST /graphql  (one request)
  GET /order-items/{id} x5   68ms          query {
  GET /books/{isbn}     x5   68ms            order(id:991){
  GET /authors/{id}     x5   68ms              items{ book{ title author{name} } }
  ------------------------------            } }
  18 requests / 4 LEVELS = 272ms           1 request = 76ms
  (dependency chain is SEQUENTIAL --
   no transport can remove it)

EXECUTION IS A TREE OF RESOLVER CALLS
  Query
   └── books(first:50)           -> resolver #1 ........ 1 DB query -> 50 rows
        ├── title                -> default resolver (parent.title), FREE
        ├── author               -> resolver #2 ........ runs 50 TIMES
        │     └── name           -> default resolver, FREE
        └── reviews(first:3)     -> resolver #3 ........ runs 50 TIMES
              └── rating         -> default resolver, FREE

  WITHOUT DataLoader:  1 + 50 + 50          = 101 DB queries
  WITH    DataLoader:  1 + 1  + 1           =   3 DB queries

DATALOADER BATCHING, ONE EVENT-LOOP TICK
   author resolver calls:  load(7) load(7) load(19) load(42) ... x50
                                 |
                      +----------v-----------+
                      |  DataLoader queue    |  dedupe -> [7,19,42,...]
                      +----------v-----------+
                                 |  ONE batch fn call at end of tick
                      SELECT * FROM authors WHERE id = ANY([7,19,42,...])
                                 |
          results MUST be returned in the SAME ORDER as the keys,
          null for misses -- otherwise books silently get wrong authors

THE MALICIOUS QUERY (why cost limiting exists)
  book { author { books { author { books { author { books { title }}}}}}}
   1  ->   1   ->  10  ->   10  ->  100 ->  100  -> 1,000 -> ...
  depth 8, ~300 bytes of request  ->  ~10^8 node resolutions
  DEFENCES (apply in this order):
    [1] persisted/trusted documents  -- only build-time queries may run  <-- best
    [2] depth limit (<= 10)
    [3] cost analysis + per-user COST budget (not request-count limit)
    [4] mandatory, capped pagination args on every list field
    [5] timeouts + resolver rate limits

CACHING: WHAT YOU LOSE AND WHAT REPLACES IT
  REST:     GET /books/123  --> CDN HIT, 15ms, origin never touched   (FREE)
  GraphQL:  POST /graphql   --> never cached by ANY intermediary
  Recover with:
     persisted query over GET  -> /graphql?id=sha256:4f3c&vars=... -> CDN-able
     normalised client cache   -> Book:978... reused across screens
     resolver-level cache      -> Redis, @cacheControl(maxAge:)

WHERE EACH LESSON LIVES IN ONE REAL ARCHITECTURE
  browser/mobile --HTTP/3 (L9)--> [ BFF / GraphQL gateway (L12) ]
                 --REST (L10)-->  [ cacheable public resources   ]
                                           |
                                    gRPC (L11), deadlines propagated
                                           |
                     catalog | pricing | inventory | orders | reviews
```

## 7. Detailed example

### BookNook

BookNook's mobile home screen needed, per render: a personalised recommendation list, each book's title/cover/price, each book's author name, each book's average rating, and the user's cart count. In REST that was 1 + 24 + 24 + 24 + 1 = **74 requests**. The iOS team's workaround was a bespoke `GET /mobile/v3/home` endpoint — which worked, until the Android team wanted a slightly different layout and asked for `/mobile/v4/home`, and the TV team asked for `/tv/v1/home`. Three endpoints, three backlogs, one backend team as the bottleneck.

They introduced GraphQL as a **BFF**, resolving against the existing gRPC services from Lesson 11:

```graphql
query HomeScreen($first: Int!) {
  me { cart { itemCount } }
  recommendations(first: $first) {
    isbn
    title
    coverUrl
    pricePaise
    averageRating
    author { name }
    personalisedPricePaise    # nullable ON PURPOSE -- see below
  }
}
```

Decisions worth defending in a review:

- **`personalisedPricePaise` is nullable.** It's backed by a gRPC call to `pricing` with a 120 ms deadline. If pricing is slow or down, the field is `null`, an entry appears in `errors`, and the screen still renders with the list price. Had it been `Float!`, the null would have propagated up and **blanked the entire `recommendations` list** — a total outage caused by a non-critical service. *This one `!` character is the difference between graceful degradation and an incident.*
- **Every list field requires a capped `first` argument.** `recommendations(first: Int = 20)` with a server-side max of 50. An uncapped list is the easiest self-inflicted DoS in GraphQL.
- **Persisted documents only, in production.** The mobile build extracts queries into a manifest; the server rejects any hash it doesn't recognise. Result: arbitrary malicious queries are structurally impossible, request size dropped from ~2.1 KB to ~120 bytes, and the queries became `GET`-able and therefore CDN-cacheable.
- **Reads fan out to gRPC, not SQL.** Resolvers call `catalog`, `pricing` and `reviews` over gRPC with propagated deadlines. GraphQL is a *composition* layer here, not a data layer — a distinction people routinely miss.
- **Mutations keep errors as data.** `createOrder` returns `{ order, errors { code message field } }`, so "insufficient stock" is a typed value the UI switches on, not a string in a generic `errors` array.

**The incident.** Week one in production, p99 home-screen latency was **2.8 seconds** and the catalogue database's connection pool kept saturating. The cause was textbook N+1: the `author` resolver ran once per book (24 gRPC calls), and `averageRating` ran once per book (24 more). One screen render = 49 downstream calls, where the old bespoke endpoint had made 3. **GraphQL did not create new work — it hid the work from the person writing the resolver.** Adding per-request DataLoaders that batched into `BatchGetBooks` and `BatchGetRatings` (the coarse-grained gRPC methods from Lesson 11, which existed for exactly this reason) took it to 3 calls and p99 to **190 ms**.

**The second incident.** Before persisted queries were enforced, a partner's misconfigured crawler issued `books(first: 100) { author { books(first: 100) { author { name } } } }` on a loop. 20,100 cost points per request, roughly 10,000 database rows, several times a second. Cost analysis — rejecting the query **before execution** — plus a per-API-key cost budget ended it. Note that a request-count rate limit would **not** have helped: the crawler's request rate was unremarkable; its *cost* rate was catastrophic.

### Contrasting example — GitHub's public GraphQL API

GitHub shipped a public GraphQL API (v4) alongside its REST API (v3) and has kept both. Instructive differences from BookNook's internal BFF:

- **Persisted queries are impossible.** Clients are third parties writing arbitrary queries, so GitHub must defend with a published **point-based rate limit**: every query is scored before execution (roughly, nodes requested ÷ 100), and each user gets 5,000 points/hour. The cost formula is **documented**, so developers can compute a query's cost themselves — turning a defence mechanism into a feature.
- **Mandatory pagination everywhere**, using the **Relay Connection** spec (`edges { node }`, `pageInfo { hasNextPage, endCursor }`), with a hard cap of 100 per list. This is cursor pagination from Lesson 10, encoded into the schema so it cannot be skipped.
- **Both APIs are kept forever.** REST v3 remains the right tool for "download this release asset" and for simple scripts; GraphQL v4 is the right tool for "give me every open PR in these 40 repos with their reviewers and labels" — which in REST was hundreds of requests.
- **Introspection stays on**, because discoverability is the product for a developer API. BookNook turns it off in production because its clients are its own apps.

**The generalisation:** when you control the clients, **persisted queries** are your defence and your performance win. When you don't, **documented cost-based rate limiting** is the only workable substitute. Those are genuinely different architectures driven by one question: *do you control the client?*

## 8. Code or configuration example

This **illustrates schema design, resolvers, the N+1 problem and its DataLoader fix, cost limiting, and persisted queries**. Not production-ready: no auth, no TLS, no tracing, no tests, trivial in-memory stores, no real batch SQL.

```javascript
// server.js -- ILLUSTRATIVE ONLY, not production-ready.
const { ApolloServer } = require('@apollo/server');
const DataLoader = require('dataloader');

const typeDefs = `#graphql
  type Author {
    id: ID!
    name: String!
    # EVERY list field takes a capped pagination argument. An uncapped list is
    # the easiest self-inflicted DoS in GraphQL: one client asks for 1,000,000.
    books(first: Int = 10): [Book!]!
  }

  type Book {
    isbn: ID!
    title: String!
    pricePaise: Int!
    author: Author!
    # NULLABLE ON PURPOSE. This field is backed by a remote pricing service.
    # If it were "Int!" and the resolver failed, GraphQL could not put null
    # here, so the error would PROPAGATE UP and null the whole parent list --
    # one slow non-critical service blanks the entire screen. One "!" is the
    # difference between graceful degradation and an outage.
    personalisedPricePaise: Int
    reviews(first: Int = 3): [Review!]!
  }

  type Review { id: ID!, rating: Int!, body: String! }

  type Query {
    book(isbn: ID!): Book
    books(genre: String, first: Int = 20): [Book!]!
  }

  # Expected business failures are DATA, not top-level errors. The client
  # switches on a typed code instead of string-matching an error message.
  type CreateOrderPayload { order: Order, errors: [UserError!]! }
  type UserError { code: String!, message: String!, field: String }
  type Order { id: ID!, status: String!, totalPaise: Int! }
  input CreateOrderInput { items: [OrderItemInput!]!, addressId: ID! }
  input OrderItemInput { isbn: ID!, quantity: Int! }
  type Mutation { createOrder(input: CreateOrderInput!): CreateOrderPayload! }
`;

// ---------- DataLoaders: created PER REQUEST, never per process -------------
function buildLoaders(db) {
  return {
    // Without this loader, `author` runs one query PER BOOK: 50 books = 50
    // queries. The resolver author cannot see that cost from their own file --
    // that is exactly why N+1 is GraphQL's signature failure mode.
    authorById: new DataLoader(async (ids) => {
      const rows = await db.query(
        'SELECT * FROM authors WHERE id = ANY($1)', [ids]);   // ONE query
      const byId = new Map(rows.map(r => [String(r.id), r]));
      // CRITICAL: return the SAME LENGTH and SAME ORDER as `ids`, with null
      // for misses. Returning a filtered list silently pairs every book with
      // the WRONG author -- a data-corruption bug with no error anywhere.
      return ids.map(id => byId.get(String(id)) ?? null);
    }),

    // "Top 3 reviews per book" in ONE statement needs a window function.
    // DataLoader removes the ROUND TRIPS; writing efficient SQL is still
    // your job. The library is not a query planner.
    reviewsByBookId: new DataLoader(async (isbns) => {
      const rows = await db.query(`
        SELECT * FROM (
          SELECT *, ROW_NUMBER() OVER (PARTITION BY book_isbn
                                       ORDER BY rating DESC) AS rn
          FROM reviews WHERE book_isbn = ANY($1)
        ) t WHERE t.rn <= 3`, [isbns]);
      const byIsbn = new Map();
      for (const r of rows) {
        if (!byIsbn.has(r.book_isbn)) byIsbn.set(r.book_isbn, []);
        byIsbn.get(r.book_isbn).push(r);
      }
      return isbns.map(i => byIsbn.get(i) ?? []);
    }),
  };
}

const resolvers = {
  Query: {
    book: (_p, { isbn }, ctx) => ctx.db.findBook(isbn),
    // Server-side hard cap. Trust the schema default for ergonomics, enforce
    // the maximum in code -- clients can always send an explicit larger value.
    books: (_p, { genre, first }, ctx) =>
      ctx.db.findBooks(genre, Math.min(first, 50)),
  },

  Book: {
    // `parent` is the Book row already loaded. We only need to turn
    // parent.author_id into an Author -- batched by the loader.
    author: (parent, _a, ctx) => ctx.loaders.authorById.load(parent.author_id),
    reviews: (parent, _a, ctx) => ctx.loaders.reviewsByBookId.load(parent.isbn),

    // A remote call with a DEADLINE (Lesson 11). Returning null degrades ONE
    // field; throwing here would surface in the top-level `errors` array while
    // the rest of the response still renders -- GraphQL's partial-success model.
    personalisedPricePaise: async (parent, _a, ctx) => {
      try {
        return await ctx.pricing.get(parent.isbn, ctx.userId, { deadlineMs: 120 });
      } catch (e) {
        ctx.log.warn({ e, isbn: parent.isbn }, 'pricing unavailable');
        return null;                                  // degrade, don't fail
      }
    },
    // `title` and `pricePaise` need no resolver: the DEFAULT resolver returns
    // parent[fieldName]. Scalars on an already-loaded object are free.
  },

  Mutation: {
    createOrder: async (_p, { input }, ctx) => {
      const stock = await ctx.inventory.check(input.items);
      if (!stock.ok) {
        // A BUSINESS outcome, returned as typed data with HTTP 200 + no
        // top-level error. The UI renders a specific message from `code`.
        return { order: null, errors: [{ code: 'INSUFFICIENT_STOCK',
                 message: `Only ${stock.available} left`, field: 'items' }] };
      }
      return { order: await ctx.orders.create(input, ctx.userId), errors: [] };
    },
  },
};

const server = new ApolloServer({
  typeDefs,
  resolvers,
  // Introspection is the product for a PUBLIC developer API (GitHub keeps it
  // on). For an internal BFF it only helps an attacker map your surface.
  introspection: process.env.NODE_ENV !== 'production',
  plugins: [
    depthLimitPlugin(10),        // blocks the cyclic author->books->author bomb
    costLimitPlugin({ maxCost: 1000 }),  // runs BEFORE any resolver executes
    // Persisted queries: clients send a SHA-256 hash, not a document.
    // - request shrinks ~2 KB -> ~120 B
    // - fits in a GET, so a CDN can cache it
    // - with `trusted: true`, UNKNOWN HASHES ARE REJECTED, which makes
    //   attacker-authored queries structurally impossible. Best single
    //   production control in this entire file.
    persistedQueriesPlugin({ trusted: true, manifest: './queries.json' }),
  ],
  // Fresh loaders per request. A process-wide DataLoader is a cross-user cache
  // that will eventually serve user A's private data to user B.
  context: async ({ req }) => ({
    db, pricing, inventory, orders, log,
    userId: await authenticate(req),
    loaders: buildLoaders(db),
  }),
});
```

```bash
# Persisted query over GET -- short URL, CDN-cacheable, hash-gated.
curl -s 'https://booknook.com/graphql?id=sha256:4f3c9a...&variables={"first":24}'

# Ad-hoc query (development only, since production rejects unknown hashes).
curl -s https://booknook.com/graphql -H 'Content-Type: application/json' \
  -d '{"query":"{ books(genre:\"databases\", first:50){ title author{name} } }"}'

# Count the queries a request actually makes. Run this BEFORE and AFTER adding
# DataLoader: 101 -> 3. If you measure one thing in GraphQL, measure this.
PGOPTIONS='-c log_statement=all' node server.js
```

## 9. Numerical example

### Part A — N+1 versus DataLoader, in milliseconds

**Assumptions**

| Assumption | Value | Why |
|---|---|---|
| Query | `books(first:50) { title author{name} reviews(first:3){rating} }` | Section 4.4 |
| DB round trip (same AZ) | 0.4 ms | Typical in-datacentre RTT |
| Simple indexed lookup, server-side | 2.1 ms | Includes parse, plan, execute |
| Batch `WHERE id = ANY($1)` of 50 keys | 3.8 ms | More rows, but ONE plan and ONE round trip |
| Windowed reviews query, 50 books | 6.0 ms | Window function over ~150 rows |
| GraphQL parse + validate + cost check | 0.9 ms | Cached AST |
| Resolver/JS overhead per field invocation | 0.02 ms | 50 books × 2 fields |

**Without DataLoader (sequential per-object resolution):**

```
books list       : 0.4 + 2.1                        =   2.50 ms
authors (×50)    : 50 × (0.4 + 2.1)                 = 125.00 ms
reviews (×50)    : 50 × (0.4 + 2.1)                 = 125.00 ms
resolver overhead: 100 × 0.02                       =   2.00 ms
parse/validate   :                                  =   0.90 ms
---------------------------------------------------------------
TOTAL                                               = 255.40 ms
DB queries                                          = 101
```

**With DataLoader:**

```
books list       : 0.4 + 2.1                        =   2.50 ms
authors (1 batch): 0.4 + 3.8                        =   4.20 ms
reviews (1 batch): 0.4 + 6.0                        =   6.40 ms
resolver overhead: 100 × 0.02                       =   2.00 ms
parse/validate   :                                  =   0.90 ms
---------------------------------------------------------------
TOTAL                                               =  16.00 ms
DB queries                                          = 3
```

```
Latency reduction = (255.40 - 16.00) / 255.40 = 93.7 % faster (16.0× speedup)
Query reduction   = (101 - 3) / 101           = 97.0 % fewer queries
```

**Why the database cares even more than the clock does.** Assume 40,000 page views/day of this one screen and a pool of 50 connections:

```
Without DataLoader: 40,000 × 101 = 4,040,000 queries/day
With DataLoader   : 40,000 ×   3 =   120,000 queries/day
Avoided                          = 3,920,000 queries/day

Peak-hour load (Lesson 1's 4x multiplier):
  avg/hour  = 40,000 / 24 = 1,667 renders/hour
  peak/hour = 1,667 × 4   = 6,668 renders/hour → 1.85 renders/s
  Without : 1.85 × 101 = 187 queries/s, each holding a connection 2.5 ms
            Little's Law: L = 187 × 0.0025 = 0.47 connections busy  (fine...)
  ...but at a 50x traffic spike: 93.5 renders/s × 101 = 9,444 queries/s
            L = 9,444 × 0.0025 = 23.6 connections -> pool pressure, then
            queueing, then timeouts, then the cascading failure of Section 7.
  With    : 93.5 × 3 = 280 queries/s → L = 280 × 0.0042 = 1.2 connections.
```

**The N+1 problem is not a latency problem; it is a capacity problem that only reveals itself under load.** That's why it survives code review and dies in production.

### Part B — payload: GraphQL versus REST over-fetching

**Assumptions:** 24-book home screen; full REST book representation 9 KB; the fields the screen actually uses total 210 bytes/book; GraphQL adds ~8 % JSON envelope overhead (`data`, nesting, `__typename`); mobile downlink 6 Mbps = 750 KB/s; persisted query request = 120 bytes vs a 2.1 KB inline query.

```
REST (24 full representations + 1 list call):
   down = 24 × 9 KB = 216 KB
   up   = 25 requests × 0.8 KB headers = 20 KB
   transfer down = 216 / 750 = 288 ms

GraphQL, persisted query:
   down = 24 × 210 B × 1.08 = 5,443 B = 5.32 KB
   up   = 120 B + 0.8 KB headers ≈ 0.92 KB
   transfer down = 5.32 / 750 = 7.1 ms

Byte reduction  = (216 - 5.32) / 216 = 97.5 %
Transfer saving = 288 - 7.1 = 280.9 ms per render
```

Monthly egress, at 40,000 DAU × 3 renders/day, $0.09/GB:

```
Renders/month = 40,000 × 3 × 30 = 3,600,000
REST    = 3,600,000 × 216 KB  = 777,600,000 KB = 741.6 GB → $66.74/month
GraphQL = 3,600,000 × 5.32 KB =  19,152,000 KB =  18.3 GB →  $1.65/month
Saving  = $65.09/month
```

**$65/month does not justify adopting GraphQL.** Say so. The justifications that *do* hold at this scale are (a) 281 ms of latency per render on mobile, and (b) three client teams no longer blocked on one backend team. Now scale DAU ×100 (4 M DAU):

```
REST egress = 74,160 GB/month → $6,674/month
GraphQL     =  1,830 GB/month →   $165/month
Saving      = $6,509/month = $78,108/year
```

**At 40,000 DAU it's a developer-velocity decision; at 4 M DAU it's also a cost decision.** Always name the scale at which the answer flips.

### Part C — the cost of a malicious query

**Assumptions:** 10 books per author, 10 authors reachable per book, 2.1 ms per uncached lookup, perfect DataLoader batching *within* each level (generous to the attacker's victim).

```
Depth:   book → author → books → author → books → author → books
Nodes:     1       1       10      10      100     100     1,000

Even WITH per-level batching, one query per LEVEL:
   7 levels × ~4 ms = 28 ms  (survivable)

WITHOUT batching, one query per NODE:
   total nodes = 1+1+10+10+100+100+1,000 = 1,222
   1,222 × 2.1 ms = 2,566 ms = 2.57 s of DB time for ONE 300-byte request

Extend to depth 11 (still a tiny document):
   nodes ≈ 1 + 1 + 10 + 10 + 100 + 100 + 1,000 + 1,000 + 10,000 + 10,000 + 100,000
         = 122,222 nodes
   122,222 × 2.1 ms = 256,666 ms = 4.3 MINUTES of database time, one request

Attacker at a modest 10 requests/second:
   10 × 4.3 min = 43 minutes of DB work demanded per second of wall clock
   -> connection pool exhausted in seconds; total outage.
```

**Cost analysis stops this before a single resolver runs:**

```
Cost of the depth-11 query under the Section 4.6 formula ≈ 122,000 points
Budget                                                   =   1,000 points
-> rejected at validation time, CPU cost ≈ 0.9 ms (parse + cost walk)
```

And note the control that *doesn't* work: a rate limit of "100 requests per minute per user" happily permits 10 requests/second of these. **Rate limit by cost, not by count.**

### Part D — when does GraphQL stop paying for itself?

**Assumptions:** GraphQL infrastructure costs roughly 0.5 engineer-years/year to run well (schema governance, cost limits, resolver tracing, client cache tuning, persisted-query pipeline) ≈ $75,000/year fully loaded; a REST BFF endpoint costs ~2 engineer-days per UI change.

```
With 3 client teams × 18 UI data-shape changes/year = 54 changes/year
REST BFF cost  = 54 × 2 days = 108 engineer-days ≈ 0.49 engineer-years ≈ $73,500
GraphQL cost   = 0.5 engineer-years                                    ≈ $75,000
  -> roughly break-even at 3 client teams and ~54 changes/year.

With 1 client team × 6 changes/year:
REST BFF cost  = 12 engineer-days ≈ $8,200
GraphQL cost   = $75,000
  -> GraphQL costs ~9x more than it saves. DON'T.

With 6 client teams × 30 changes/year:
REST BFF cost  = 360 engineer-days ≈ 1.64 engineer-years ≈ $246,000
GraphQL cost   = $75,000  (sublinear: the schema is shared)
  -> GraphQL saves ~$171,000/year. DO.
```

These figures are illustrative, but the *structure* of the argument is the point: **GraphQL's cost is roughly fixed; its benefit scales with the number of distinct clients and the rate of UI change.** That is the honest decision rule, and it's far better than "GraphQL is modern".

## 10. Trade-offs

**Advantages**

- **No over-fetching**: clients request exact fields — 97.5 % fewer bytes in the Section 9 example.
- **No under-fetching**: one round trip replaces a multi-level request chain that no transport can flatten.
- **Strongly typed, introspectable schema**: validation happens before execution; editor autocomplete, codegen and typed clients come free.
- **Client teams ship independently** of backend deploys — usually the real reason companies adopt it.
- **Partial success is first-class**: a failing non-critical field returns `null` plus an entry in `errors` while the rest of the page renders.
- **Schema evolution without versioning**: add fields freely; deprecate with `@deprecated(reason:)` and use field-level usage metrics to know exactly when it's safe to remove — something REST can only approximate.
- **One endpoint to secure, instrument and route.**
- **Excellent client caching** via normalised entity stores, which dedupe across screens better than HTTP caching ever could.

**Disadvantages**

- **HTTP/CDN caching is gone** unless you build persisted queries over `GET`.
- **N+1 resolvers** are the default behaviour, not an edge case. DataLoader is effectively mandatory.
- **Unpredictable query cost** means you must build cost analysis, depth limits and cost-based rate limiting before exposing it widely.
- **Always HTTP 200**, which breaks naive uptime monitoring and alerting until you instrument the `errors` array.
- **File uploads are awkward** and need a non-standard multipart extension.
- **Observability is harder**: you need per-field tracing, not per-endpoint metrics, to find what's slow.
- **Schema governance is a permanent job** — naming, nullability, pagination conventions, deprecation policy.
- **Larger client bundles** for Apollo/Relay and the normalised cache.
- **Authorisation is per-field, not per-endpoint**, which is more correct but more work and easier to get subtly wrong.

**Operational costs**

- Build and maintain the persisted-query pipeline and manifest deployment.
- Maintain cost annotations as the schema grows.
- Run per-field tracing and alert on resolver error rates and slow fields.
- Review every schema change for nullability and pagination discipline (lint with `graphql-eslint` or `graphql-schema-linter`).
- If federated: run and scale a router, and gate composition in CI.

**When it becomes unnecessary complexity:** one client, stable CRUD data, a small team, or read-heavy public data a CDN could serve. All of Section 4.9.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| N+1 resolvers in production | Page is slow under load; fine in staging with 10 rows | DB query count per request; p99 rises with list size; slow-query log floods | Add per-request DataLoaders; batch into coarse-grained downstream calls (Lesson 11) |
| Non-null field's resolver fails | **Entire section or page goes blank**, not just one value | `errors` array shows null-propagation; `data` is null where it shouldn't be | Make remote-backed fields nullable; return `null` + an error entry and degrade |
| Malicious or accidental deep query | Site-wide slowdown, then connection-pool exhaustion | One operation name dominates DB time; cost metrics spike | Depth limit, cost analysis, cost-based rate limits, persisted queries |
| Process-wide (not per-request) DataLoader | User A sees user B's private data | Hard to detect — often found in a security review or by a user report | Create loaders inside the per-request context factory; add a lint rule |
| Always-200 responses | Dashboards show 100 % success during a partial outage | Only visible if you instrument `errors`; otherwise you learn from support tickets | Alert on `errors` array rate and per-field resolver error rate, not HTTP status |
| Schema field removed while a client still uses it | Old mobile app shows blank fields or crashes | Field-level usage metrics; client error reports by app version | `@deprecated` first; remove only after usage hits zero for all supported versions |
| DataLoader batch function returns wrong order/length | **Silently wrong data** — books paired with the wrong authors | Almost invisible; caught by contract tests or a sharp-eyed user | Always map keys → results positionally, `null` for misses; unit-test the batch fn |
| Subscription server redeployed | Live updates stop; clients may not notice they're stale | Connection-count cliff at deploy time | Client auto-reconnect with backoff + a re-sync query on reconnect (Lesson 2) |
| Federation subgraph down | A slice of every page fails | Router reports subgraph error rates; the field is null across many queries | Nullable fields + router-level timeouts; degrade that section only |
| Resolver without a deadline on a downstream call | One slow service makes every query slow | Per-field timing shows one field dominating | Propagate deadlines into resolvers (Lesson 11); return null on timeout |
| Introspection enabled publicly | No user impact; an attacker maps your entire API | Introspection query volume from unexpected sources | Disable in production for internal APIs; keep it on only for developer-facing APIs |

## 12. Alternatives

| Approach | Who decides the response shape | Caching | Typed | Round trips for a composite screen | Best for |
|---|---|---|---|---|---|
| **GraphQL** | Client | Hard (persisted queries + client cache) | Yes, runtime-validated | 1 | Many clients, nested data, fast UI iteration |
| **REST** (Lesson 10) | Server | **Free via HTTP/CDN** | No (OpenAPI is docs) | Many | Public APIs, CRUD, cacheable resources |
| **gRPC** (Lesson 11) | Server | None | Yes, compiler-enforced | 1 per method | Internal service-to-service, streaming |
| **REST BFF** | Server, per client | Free | Optional | 1 | Few clients, stable screens, simplest operations |
| **OData** | Client (standard query syntax) | Partly (it's `GET`) | Yes | 1 | Enterprise data services, Microsoft stack |
| **JSON:API** | Server, with `include`/`fields` | Mostly free | Partly | 1–2 | REST plus bounded shaping, keeping cacheability |
| **Falcor** | Client (virtual JSON graph) | Client-side | No | 1 | Netflix-era alternative; largely historical |
| **tRPC** | Server | None | Yes (TS inference) | 1 per procedure | TypeScript monorepos |
| **Hasura / PostGraphile** | Client | Hard | Yes, generated from DB | 1 | Instant GraphQL over Postgres; **beware exposing your schema as your API** |
| **Server-driven UI** | Server sends layout + data | Varies | Varies | 1 | Shipping UI changes without app-store releases |

Worth noting: **JSON:API's `include` and `fields[]`** gives you a bounded slice of GraphQL's benefit while keeping `GET` and HTTP caching. If your pain is "two more round trips", that is often the cheaper answer.

## 13. When to use it

1. **Multiple heterogeneous clients with genuinely different data needs.** Web, iOS, Android, TV, watch, partner portal — each rendering different subsets of the same graph. One schema serves all of them; the alternative is N bespoke BFF endpoints and N backlogs. This is the strongest reason and it is organisational, not technical.
2. **Deeply nested, relationship-heavy domains.** Social graphs, catalogues with authors/series/publishers, project trackers (issues → PRs → reviewers → teams). The 4-level chain that cost 272 ms in REST becomes one 76 ms request.
3. **Rapid UI iteration where backend deploys are the bottleneck.** If "add the author's nationality to the card" currently takes a two-week backend cycle, GraphQL removes that queue entirely — the field already exists in the schema.
4. **Mobile clients on constrained networks.** 97.5 % fewer bytes and one round trip instead of 25 is directly visible as latency and battery.
5. **Aggregating many backend services behind one façade.** A GraphQL BFF over gRPC services (Lesson 11) gives clients one typed entry point while services stay independently owned and deployed.
6. **When you control the clients**, so persisted/trusted documents are available — which removes the malicious-query problem entirely and restores `GET`-based CDN caching.

## 14. When *not* to use it

1. **One client, CRUD data, small team.** Section 9 Part D: GraphQL's cost is roughly fixed (~0.5 engineer-years/year) while the REST alternative costs ~2 days per change. With one team and six changes a year, GraphQL costs about nine times what it saves.
2. **Read-heavy public data that a CDN can serve.** `GET /books/{isbn}` cached at the edge costs ~0 and takes ~15 ms. `POST /graphql` reaches your origin every time. You can recover this with persisted `GET` queries — but that's infrastructure you must build and operate, so don't pretend it's free.
3. **Internal service-to-service calls.** Two services you own, 50,000 RPS, fixed message shapes: you want compiler-enforced contracts, tiny payloads and deadline propagation. That's gRPC (Lesson 11). GraphQL's flexibility is dead weight when the shape never varies.
4. **Binary/file-heavy workloads.** Uploads and downloads need the multipart extension or pre-signed URLs anyway. Use plain HTTP.
5. **Public APIs for untrusted third parties — unless you commit to the defences.** Without persisted queries you must build and document cost-based rate limiting, as GitHub did. If you can't fund that, REST's fixed-cost endpoints are inherently safer.
6. **Teams without the operational maturity to run it.** If nobody will own schema governance, per-field tracing, cost limits and the persisted-query pipeline, GraphQL degrades into a slow, unmonitorable, unprotected `POST` endpoint — strictly worse than the REST API it replaced.

## 15. Common mistakes

**Beginner mistakes**

- **No DataLoader.** Shipping N+1 by default, then concluding "GraphQL is slow". GraphQL isn't slow; unbatched resolvers are.
- **Marking everything non-null (`!`).** One failing leaf field then nulls the entire response tree. Be deliberate: non-null for database columns you control, nullable for anything backed by a remote call.
- **Uncapped list fields.** `books: [Book!]!` with no `first` argument is an open invitation to request the whole table.
- **Mirroring database tables as GraphQL types.** Your API then cannot survive a schema refactor, and you leak internal structure permanently (same mistake as Lesson 10, new syntax).
- **Interpolating values into query strings** instead of using variables — defeating query caching and persisted queries.
- **Treating business failures as top-level errors.** "Insufficient stock" should be typed data the UI switches on, not a string the client regex-matches.
- **Leaving introspection on** for an internal API in production.
- **Alerting on HTTP status.** Everything is 200; your uptime graph will look perfect during an outage.

**Experienced-engineer mistakes**

- **Process-wide DataLoader instances** — a cross-request cache that eventually leaks one user's data to another. A genuine security incident waiting to happen.
- **Batch functions that return filtered or reordered results.** Silently wrong data with no error. Unit-test every batch function for order, length and misses.
- **Rate limiting by request count** instead of query cost, which provides essentially no protection (Section 9 Part C).
- **Adopting federation before there are multiple teams to federate.** You've bought a router, a composition pipeline and a latency hop to solve a problem you don't have.
- **No field-level usage metrics**, so deprecated fields can never be safely removed and the schema only ever grows.
- **Forgetting deadlines inside resolvers**, so GraphQL's parallelism turns one slow dependency into universally slow queries.
- **Over-fetching in resolvers.** `SELECT *` on a table with a 40 KB blob column when the query asked for `title`. The client fetched 210 bytes; your database read 40 KB. Use the `info` argument or a projection layer when it matters.
- **Exposing a database-generated schema (Hasura/PostGraphile) directly to the public internet**, publishing your entire data model and every access pattern, with authorisation as an afterthought.
- **Ignoring the `@cacheControl` minimum rule**: one volatile field makes the whole response uncacheable. Split volatile fields out, exactly as in Lesson 10.
- **No persisted queries when you control the clients** — leaving the biggest available win (security, size, CDN cacheability) on the table.

## 16. Interview perspective

- **Open with the problem, not the technology.** "GraphQL exists to solve over-fetching and under-fetching. In our REST design the order screen needed 18 calls across 4 dependency levels — 272 ms even with HTTP/2, because the chain is sequential. One GraphQL query makes it 76 ms."
- **Bring up N+1 before the interviewer does.** "The default execution model runs one resolver per field per object, so `books(first:50){author{name}}` is 51 queries. I'd add per-request DataLoaders to batch it to 3 — and they must be per-request, because a process-wide loader leaks data across users." Mentioning the security angle unprompted is a strong signal.
- **Volunteer the caching trade-off.** "We lose free HTTP/CDN caching because everything is a `POST` to one URL. I'd get it back with persisted queries over `GET` plus a normalised client cache."
- **Name the security model concretely.** "Request-count rate limiting is almost meaningless here — one query can be 10,000× more expensive than another. I'd use cost analysis with a per-user cost budget, depth limits, mandatory capped pagination, and persisted queries if we control the clients."
- **Use the nullability insight.** "I'd make remote-backed fields nullable so a slow pricing service degrades one field instead of nulling the whole list." Almost nobody says this, and it's the difference between a working page and an outage.
- **Know when to say no.** "With one client team and stable screens, I'd ship a REST BFF. GraphQL's cost is roughly fixed; its benefit scales with the number of clients and the rate of UI change."
- **Place all four lessons in one architecture.** "HTTP/3 at the edge, REST for cacheable public resources, GraphQL as the BFF for composite screens, gRPC between internal services with propagated deadlines." That sentence demonstrates you understand them as layers, not rivals.
- **Mention the monitoring trap.** "Everything returns 200, so I'd alert on the `errors` array and per-field resolver error rates, not HTTP status codes."

**Red flags interviewers watch for:** proposing GraphQL with no stated reason; not knowing what N+1 is or how DataLoader fixes it; claiming GraphQL "replaces REST"; no answer for malicious queries; no answer for caching; marking every field non-null; rate limiting by request count; putting GraphQL between internal services where gRPC fits better; no awareness that the always-200 convention breaks monitoring.

## 17. Knowledge check

**Conceptual**

1. Explain the N+1 resolver problem: why it arises from GraphQL's execution model specifically, why it is structurally harder to notice than the equivalent problem in REST, and exactly what DataLoader's two mechanisms do. Then state two rules about DataLoader that, if broken, cause silent data corruption or a data-leak between users — and explain the mechanism of each.
2. GraphQL gives up free HTTP and CDN caching. Explain precisely which four properties of REST make CDN caching work and which GraphQL convention breaks each one. Then describe the three layers you'd build to recover caching, and say which of them reduces **server** load versus **client** latency.
3. A schema marks `personalisedPrice: Float!` on `Book`, and `recommendations: [Book!]!` on `Query`. The pricing service times out for one book. Describe exactly what the client receives and why, name the GraphQL rule responsible, and give the one-character schema change that converts a page-wide outage into a single degraded field.

**Practical scenario**

4. BookNook is exposing a **public** GraphQL API to partner developers who will write their own queries — so persisted queries are impossible. Design the protection: list every defence you'd deploy, the order you'd deploy them in, the specific limits you'd set (depth, cost budget, page-size cap, per-key rate), and how you'd calculate a query's cost. Then explain how you'd introduce cost limits to **existing** partners without breaking their working integrations overnight, what you'd measure first, and what you'd do about the one partner whose legitimate query exceeds your proposed budget.

**Calculation**

5. BookNook's "author page" query is `author(id:$id) { name books(first: 40) { title reviews(first: 5) { rating reviewer { name } } } }`. Assume: DB round trip 0.45 ms; simple indexed lookup 2.0 ms server-side; a batched `ANY($1)` query 3.5 ms for ≤ 50 keys and 7.0 ms for ≤ 250 keys; the windowed reviews query 6.5 ms; parse/validate/cost 0.9 ms; resolver overhead 0.02 ms per field invocation.
   (a) How many resolver invocations and how many database queries occur **without** DataLoader? Show the tree level by level.
   (b) Total latency without DataLoader.
   (c) How many database queries **with** DataLoader at every level, and what is the total latency?
   (d) Under the Section 4.6 cost model (scalars 0, DB-backed field 1, list fields cost × `first`), what is this query's cost, and does it pass a 1,000-point budget?
   (e) At 12,000 author-page views/day with a 4× peak-hour multiplier, compute peak queries/second for both designs, and use Little's Law (`L = λ × W`, with W = the per-query DB service time) to say how many database connections each design occupies at peak. State every assumption you add.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 13.

---

**Progress checklist**

Phase 1:
- `[x] 1. Client-server architecture`
- `[x] 2. Stateful versus stateless servers`
- `[x] 3. IP addresses, ports, sockets, domain names`
- `[x] 4. DNS resolution and DNS caching`
- `[x] 5. HTTP requests and responses`
- `[x] 6. HTTP methods, headers, cookies, sessions, status codes`
- `[x] 7. HTTPS and TLS`
- `[x] 8. TCP versus UDP`
- `[x] 9. HTTP/1.1 vs HTTP/2 vs HTTP/3`
- `[x] 10. REST APIs`
- `[x] 11. RPC and gRPC`
- `[x] 12. GraphQL`
- `[ ] 13. Synchronous versus asynchronous communication`
- `[ ] 14. Latency, throughput, bandwidth, concurrency`
- `[ ] 15. Availability, reliability, durability, fault tolerance`
- `[ ] 16. Average latency vs p50/p95/p99, tail latency`
- `[ ] 17. Idempotency, retries, timeouts, exponential backoff`

**NEXT LESSON: Phase 1, Topic 13 — Synchronous versus asynchronous communication**
