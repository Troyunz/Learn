# Phase 1 — Internet and System-Design Foundations

Before you can design a system, you must know exactly what happens when one computer asks another computer for something. Phase 1 builds that mental model from the ground up: who talks to whom, over what, in what format, how fast, and how it fails.

**Running example for the whole course: BookNook**, an online bookstore. Users browse books, search, read reviews, add to cart, check out, and track orders. We will grow BookNook from one server to a global system over five phases.

**Phase 1 topics**

| # | Topic | Lesson |
|---|---|---|
| 1 | Client-server architecture | Lesson 1 |
| 2 | Stateful versus stateless servers | Lesson 2 |
| 3 | IP addresses, ports, sockets, domain names | Lesson 3 |
| 4 | DNS resolution and DNS caching | Lesson 4 |
| 5 | HTTP requests and responses | Lesson 5 |
| 6 | HTTP methods, headers, cookies, sessions, status codes | Lesson 6 |
| 7 | HTTPS and TLS | Lesson 7 |
| 8 | TCP versus UDP | Lesson 8 |
| 9 | HTTP/1.1 vs HTTP/2 vs HTTP/3 | Lesson 9 |
| 10 | REST APIs | Lesson 10 |
| 11 | RPC and gRPC | Lesson 11 |
| 12 | GraphQL | Lesson 12 |
| 13 | Synchronous versus asynchronous communication | Lesson 13 |
| 14 | Latency, throughput, bandwidth, concurrency | Lesson 14 |
| 15 | Availability, reliability, durability, fault tolerance | Lesson 15 |
| 16 | Average latency vs p50/p95/p99, tail latency | Lesson 16 |
| 17 | Idempotency, retries, timeouts, exponential backoff | Lesson 17 |

---

# Lesson 1 — Client-Server Architecture

*Phase 1, Topic 1*

## 1. Simple definition

**Client-server architecture** is a way of splitting a software system into two kinds of programs:

- A **client** is a program that *asks* for something. Your browser, a mobile app, a smart TV, another backend service.
- A **server** is a long-running program that *waits* for requests, does work, and sends back a response.

The defining rule: **the client initiates, the server responds**. The server never speaks first. It sits in a loop, listening.

Two terms to define now, because they get confused constantly:

- **Host / machine**: the physical or virtual computer.
- **Server**: the *program* (the process) that listens for requests. One machine can run many servers; one logical server can run on many machines.

So "server" in this course means "the role a process plays in a conversation", not "a metal box in a data centre".

## 2. Why it exists

Imagine BookNook with no server: every customer's laptop holds the entire book catalogue and all orders. The problems are immediate:

1. **Shared state is impossible.** If I buy the last copy of a book, your laptop has no way to know stock went from 1 to 0. Two customers buy the same physical copy.
2. **Trust is impossible.** The price of a book would live on the customer's machine. Anyone could edit it to ₹1.
3. **Updates are impossible.** Adding a new book means pushing a new catalogue file to every customer.
4. **Scale of data is impossible.** A 50 million book catalogue cannot sit on a phone.

A server solves all four by being **a single authoritative place where shared, trusted, long-lived state lives**, reachable over a network by many clients at once.

Without client-server you get one of two worlds:
- **Standalone apps** (a calculator) — fine, but no sharing between users.
- **Peer-to-peer** (BitTorrent) — sharing without a central authority, but then nobody is trusted and consistency is extremely hard.

Client-server exists because most business systems need **shared state plus a trust boundary**.

## 3. Real-world analogy

A **restaurant**.

- You (client) read a menu and place an order. You never walk into the kitchen.
- The waiter + kitchen (server) receive many orders, work on them, and return dishes.
- The kitchen owns the ingredients (the data). You cannot reach in and change what's in the fridge.
- The menu is the **API**: the agreed list of things you're allowed to ask for, in a format the kitchen understands.

**Where the analogy breaks down** — and this matters:

| Restaurant | Real client-server |
|---|---|
| The waiter remembers your table between courses | HTTP servers remember *nothing* between requests by default (Lesson 2) |
| One kitchen serves ~50 people | One server handles thousands of concurrent requests, interleaved mid-request |
| You physically sit there while cooking happens | The network can drop, delay, or duplicate your order, and you may never learn which |
| The kitchen is always "up" while the restaurant is open | Servers crash mid-order; the client often cannot tell "crashed before cooking" from "crashed after cooking" (this is the core problem of Lesson 17 and all of Phase 4) |

That last row is the single most important difference. In a restaurant, failure is visible. On a network, **failure is ambiguous**.

## 4. Detailed technical explanation

### What a server actually is, mechanically

A server program does roughly this, forever:

1. **Bind**: ask the operating system for a *port* (a number, e.g. 443) on a *network interface*. "Deliver me anything addressed to this machine on port 443."
2. **Listen**: tell the OS to start queueing incoming connection attempts.
3. **Accept**: pull one established connection out of that queue. You now have a two-way byte pipe to one client.
4. **Read** bytes from the pipe until a complete request has arrived.
5. **Parse** those bytes into a structured request (method, path, headers, body).
6. **Process**: run business logic — validate, query the database, compute.
7. **Serialise** a response (status code, headers, body) back into bytes.
8. **Write** those bytes to the pipe.
9. **Keep the connection open** for more requests, or close it.
10. Go to 3.

(Ports and sockets are Lesson 3; the request/response format is Lesson 5. For now, just hold the shape of the loop.)

### Handling many clients at once

Step 6 can take 50 ms waiting on a database. If the server did one request at a time, it could serve 20 requests per second — useless. Three standard strategies:

| Model | How it works | Used by | Weakness |
|---|---|---|---|
| **Thread/process per request** | OS thread per connection; blocked threads sleep | Java (classic Tomcat), PHP-FPM, Ruby Puma | ~1 MB stack per thread; 10k threads = 10 GB RAM and heavy context switching |
| **Event loop (async I/O)** | One thread, a queue of callbacks; when a request blocks on I/O the thread moves to another request | Node.js, Nginx, Python asyncio, Go's runtime (hybrid) | One slow CPU-bound function blocks *everything*; harder to write |
| **Lightweight threads** | Runtime-scheduled green threads, cheap (~2–8 KB) | Go goroutines, Java virtual threads, Erlang | Runtime complexity, still shares the CPU |

Key insight that drives most of system design: **most web requests are not CPU-bound, they are I/O-bound.** The server spends 90 %+ of a request's wall-clock time *waiting* on a database, a cache, or another service. Concurrency models exist to make that waiting cheap. We quantify this in Lesson 14.

### The trust boundary

Everything on the client side is **attacker-controlled**. The user can open developer tools, modify JavaScript, replay requests with `curl`, or forge headers. Therefore:

- **Validation on the client is a UX feature.** Validation on the server is the security control. You need both, and they are not the same thing.
- Prices, stock, discounts, permissions, and totals must be computed **server-side**.
- The client may *tell* you who it thinks it is; the server must *verify* it (Lesson 6, sessions and tokens).

### Tiers

"Client-server" describes a relationship, not a count of machines. Systems are usually described in **tiers**:

- **2-tier**: client ↔ database directly. Used by old desktop apps. Dangerous — database credentials live on the client.
- **3-tier**: client ↔ application server ↔ database. The overwhelmingly common web shape, and where BookNook starts.
- **N-tier**: client ↔ gateway ↔ many services ↔ many data stores. Phase 4 territory.

Crucially, **the roles compose**. BookNook's application server is a *server* to the browser, and simultaneously a *client* of PostgreSQL, of Redis, and of a payment provider. Nearly every backend process you ever write is both.

## 5. Request / data flow

A customer opens a BookNook product page. End to end:

1. User types `booknook.com/books/1492052205` and presses Enter.
2. Browser resolves `booknook.com` to an IP address, e.g. `203.0.113.10` (Lesson 4).
3. Browser opens a TCP connection to `203.0.113.10:443` (Lessons 3 and 8).
4. Browser and server perform a TLS handshake; the channel is now encrypted (Lesson 7).
5. Browser sends `GET /books/1492052205` with headers (Lessons 5–6).
6. Server's `accept()` returns the connection; a worker thread/goroutine picks it up.
7. Server parses the request, extracts the book ID `1492052205`.
8. Server authenticates/identifies the user from a cookie, if present.
9. Server **acts as a client** and asks the cache: `GET book:1492052205`. Miss.
10. Server **acts as a client** and asks PostgreSQL: `SELECT ... WHERE isbn = $1`. 12 ms.
11. Server writes the result to the cache with a time limit.
12. Server renders JSON (or HTML) and computes headers, including `Content-Length`.
13. Server writes the bytes to the socket; the OS chops them into packets.
14. Browser receives bytes, reassembles, parses, renders pixels.
15. Connection stays open for the follow-up requests (CSS, images, `/api/reviews`).

Note steps 9–11: **inside one inbound request, the server made two outbound requests.** This nesting is the normal structure of backend work, and it is why one slow dependency makes an entire page slow.

## 6. ASCII diagram

The role relationship:

```
   CLIENTS (initiate)                 SERVER (listens)             DEPENDENCIES
 ┌──────────────────┐
 │ Browser          │──┐
 ├──────────────────┤  │   request    ┌───────────────────┐  query  ┌────────────┐
 │ Mobile app       │──┼─────────────▶│                   │────────▶│ PostgreSQL │
 ├──────────────────┤  │              │  BookNook app     │◀────────│            │
 │ Partner backend  │──┤◀─────────────│  server :443      │  rows   └────────────┘
 ├──────────────────┤  │   response   │                   │  get/set┌────────────┐
 │ curl / scripts   │──┘              │  (server to left, │────────▶│ Redis      │
 └──────────────────┘                 │   client to right)│◀────────│ cache      │
                                      └───────────────────┘         └────────────┘
        ▲                                        │                  ┌────────────┐
        │                                        └─────────────────▶│ Payments   │
   untrusted zone          │ TRUST BOUNDARY │                       │ (3rd party)│
                                                                    └────────────┘
```

BookNook's starting (3-tier) deployment:

```
Browser ──HTTPS──▶ [ Application server ] ──SQL──▶ [ Database ]
                     business logic              books, users,
                     rendering                   orders, stock
```

## 7. Detailed example — BookNook v0

BookNook v0 is deliberately the simplest thing that works: **one virtual machine**, running the app process and PostgreSQL side by side.

**Responsibilities**

| Lives on client (browser) | Lives on server |
|---|---|
| Rendering, scroll, animation | Catalogue data, stock counts |
| Form UX, instant "required field" hints | Authoritative validation |
| Showing the cart | Pricing, discount, tax, total calculation |
| Remembering a session cookie | Deciding what that cookie is allowed to do |
| Nothing security-relevant | Payments, order creation, inventory decrement |

**A concrete trap.** Suppose the browser sends the checkout request like this:

```json
{ "isbn": "1492052205", "quantity": 1, "unit_price": 3200, "total": 3200 }
```

This is a bug, not a design. A hostile client sends `"unit_price": 1`. The correct contract sends only *intent*:

```json
{ "isbn": "1492052205", "quantity": 1 }
```

…and the server looks up the price itself. Rule of thumb: **clients send intent, servers compute consequences.**

**Second example for contrast — a multiplayer game server.** Same architecture, different constraints: the client *must* predict movement locally (otherwise it feels laggy), so it renders optimistically and the server later corrects it ("server reconciliation"). The trust rule still holds — the server decides who actually got shot — but the latency budget (under ~50 ms) forces the client to do speculative work. BookNook has a 200–500 ms budget and needs no such trickery.

## 8. Code example

A minimal server, with the accept loop made visible. Python, intentionally low level — this is *not* production code, it exists to show the loop from Section 4.

```python
import socket

# AF_INET = IPv4 addressing. SOCK_STREAM = TCP (an ordered, reliable byte stream).
listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

# Without this, the port stays unusable for ~60s after restart (TIME_WAIT); dev-only convenience.
listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

# BIND: claim port 8080 on all interfaces. "" means "any local IP on this machine".
listener.bind(("", 8080))

# LISTEN: start queueing connections. 128 = backlog, how many completed-but-not-yet-accepted
# connections the OS will hold. Overflowing this queue is a real production failure mode.
listener.listen(128)
print("listening on :8080")

while True:
    # ACCEPT: blocks until a client connects. Returns a NEW socket for that one client.
    # The listener socket itself never carries request data.
    conn, addr = listener.accept()

    # READ: pull up to 65535 bytes. Note: TCP is a stream, not messages -- one recv()
    # is NOT guaranteed to contain a whole HTTP request. Real servers loop until the
    # request is complete. This is the #1 bug in hand-written servers.
    raw = conn.recv(65535).decode("utf-8", errors="replace")

    # PARSE: first line looks like "GET /books/1492052205 HTTP/1.1"
    request_line = raw.split("\r\n")[0]
    try:
        method, path, _version = request_line.split(" ")
    except ValueError:
        method, path = "GET", "/"

    # PROCESS: the business logic. Server-side, therefore trustworthy.
    body = f'{{"method": "{method}", "path": "{path}", "server_decides": true}}'

    # SERIALISE: status line + headers + blank line + body.
    # Content-Length tells the client where this response ends so the connection can be reused.
    response = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body.encode())}\r\n"
        "Connection: close\r\n"
        "\r\n"
        f"{body}"
    )

    conn.sendall(response.encode())   # WRITE
    conn.close()                      # done with this client
```

Test it:

```bash
python server.py &
curl -i http://localhost:8080/books/1492052205
```

**What makes this toy a toy:** it handles exactly one client at a time (no concurrency from Section 4), assumes a request fits in one `recv`, has no timeouts, no TLS, no limits on body size, and no error handling. Every one of those gaps is a real outage in production. In practice you use a framework (FastAPI, Express, Spring) on top of a battle-tested server (Uvicorn, Nginx), and you *never* write the loop yourself.

The same endpoint as you would actually write it:

```python
from fastapi import FastAPI, HTTPException

app = FastAPI()

@app.get("/books/{isbn}")
def get_book(isbn: str):
    book = db.fetch_one("SELECT isbn, title, price_paise, stock FROM books WHERE isbn = $1", isbn)
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")  # see Lesson 6
    return book          # price comes from the DB -- never from the client
```

## 9. Numerical example

Let's size BookNook v0. **Every number below is an assumption, stated explicitly** — this is exactly how you should talk in an interview.

**Assumptions**
- 100,000 registered users.
- 10 % are active on a given day → 10,000 daily active users (DAU).
- Each active user makes 30 requests/day (page views + API calls + images).
- Traffic is not uniform: peak hour carries ~4× the average hourly rate (evening shopping).
- Read:write ratio ≈ 100:1 (people browse far more than they buy).
- Average request costs 20 ms of server time (mostly waiting on the DB).

**Average requests per second**

```
Requests/day = 10,000 DAU × 30 req = 300,000 req/day
Seconds/day  = 86,400
Average RPS  = 300,000 / 86,400 ≈ 3.5 req/s
```

**Peak requests per second**

```
Average per hour = 300,000 / 24 = 12,500 req/hour
Peak hour        = 12,500 × 4   = 50,000 req/hour
Peak RPS         = 50,000 / 3,600 ≈ 13.9 req/s   → round up to ~14 RPS
```

**How many concurrent requests is that?** Little's Law — `L = λ × W`, where `L` = requests in flight, `λ` = arrival rate, `W` = average time in system:

```
L = 14 req/s × 0.020 s = 0.28 concurrent requests
```

Under a third of a request in flight at peak. **One modest server is wildly sufficient.** This is the most important lesson in Phase 1: *most systems do not need to be distributed.* A 4-core VM handling 14 RPS is not a scaling problem, it is a Tuesday.

**When does v0 break?** Suppose one worker process can hold 100 concurrent requests:

```
Max RPS per server = L / W = 100 / 0.020 = 5,000 req/s
```

So the app tier breaks somewhere above ~5,000 RPS — about **350× current peak**. You would grow DAU from 10,000 to ~3.5 million before the architecture, not the database, becomes the limit.

**Bandwidth**

```
Average response (JSON page data)  ≈ 40 KB
Peak egress = 14 req/s × 40 KB     ≈ 560 KB/s ≈ 4.5 Mbps
```

Trivial. But add book cover images at 200 KB each, 8 per page:

```
14 req/s × 8 images × 200 KB ≈ 22 MB/s ≈ 180 Mbps
```

**40× more bandwidth for images than for the entire application.** That single calculation is the whole justification for CDNs (Phase 2, Topic 14) — and you reached it with arithmetic, not opinion.

**Storage**

```
50,000 books × 2 KB metadata      = 100 MB
100,000 users × 1 KB              = 100 MB
500 orders/day × 1 KB × 365 days  = 180 MB/year
Total year one                    < 500 MB
```

Fits in RAM. There is no data problem yet.

## 10. Trade-offs

**Advantages**
- Single source of truth; shared state actually works.
- Security controls live where the attacker isn't.
- Deploy a fix once; every client gets it instantly.
- Clients stay thin — a phone can use a system backed by petabytes.
- Clear boundary enables independent scaling of client and server.

**Disadvantages**
- The server is a **single point of failure** until you replicate it (Phase 2).
- Every interaction pays network latency — 1 ms in-region, 150 ms+ cross-continent.
- Offline operation requires explicit extra design (local queue + sync).
- Server cost scales with users; peer-to-peer pushes that cost to users.
- A **central bottleneck**: everyone's traffic funnels through infrastructure you must pay for and operate.

**Operational costs**
- Someone is on call. The server must be monitored, patched, backed up, and capacity-planned.
- You own uptime. If the server is down, the product is down for *everyone* simultaneously — correlated failure.

**When it becomes unnecessary complexity**
- A single-user tool with no shared data (an offline note-taking app, a CLI formatter). Adding a server there buys you nothing and costs you an outage surface.

## 11. Failure scenarios

| Failure | What the user sees | How it's detected | Recovery |
|---|---|---|---|
| Server process crashes | Connection refused; instant error page | Health check fails within seconds (Phase 2, Topic 7) | Process supervisor restarts it; load balancer removes it from rotation |
| Server machine loses power | Connection timeout (slow — no one sends a rejection) | Health check timeout; missing heartbeat | Failover to a replica; DNS or LB redirects |
| Server is up but overloaded | Spinner, then timeout; p99 latency explodes before errors appear | Latency percentiles and queue depth rise *before* error rate (Lesson 16) | Shed load, autoscale, add capacity |
| Database down, app up | HTTP 500s, or ideally a degraded page | App-level dependency health check; error-rate alert | Circuit breaker + cached/degraded response (Phase 4, Topic 14) |
| Network partition between client and server | Request hangs, then times out | Client-side timeout | Retry with backoff — **only if the operation is idempotent** (Lesson 17) |
| Client sends malicious input | Nothing unusual, if you're doing it right | WAF / validation rejection logs | Server-side validation blocks it; the client was never trusted |

The ambiguous case again: if the connection drops *after* the server created an order but *before* the response arrived, the client genuinely cannot tell whether the order exists. Retrying blindly creates a duplicate order. This is why Lesson 17 exists, and why it is the most under-appreciated topic in Phase 1.

## 12. Alternatives

| Model | How it differs | Good at | Bad at | Example |
|---|---|---|---|---|
| **Client-server** | Central authority | Shared state, trust, consistency | Central cost, SPOF | BookNook, every SaaS |
| **Peer-to-peer** | Peers are symmetric, no authority | Bandwidth scaling, censorship resistance, zero infra cost | Trust, consistency, discovery | BitTorrent, blockchains |
| **Serverless (FaaS)** | Still client-server; the *server lifecycle* is the provider's problem | Spiky/low traffic, ops offloading | Cold starts, vendor lock-in, long-lived connections | AWS Lambda |
| **Edge computing** | Server code runs in ~300 cities near users | Latency, personalisation at the edge | Limited runtime, hard to hold strong state | Cloudflare Workers |
| **Local-first / offline-first** | Client holds a replica; syncs with CRDTs | Offline use, instant UI | Merge conflicts, complex sync logic | Figma, Linear, Git |
| **Standalone** | No network at all | Simplicity, privacy | No sharing | A desktop calculator |

Note that serverless and edge are not *alternatives* to client-server so much as *different ways of operating the server*. The client/server roles are unchanged.

## 13. When to use it

1. **Shared, authoritative state across users.** Inventory, bookings, bank balances, multiplayer state. BookNook's stock count has exactly one correct value and it cannot live on 10,000 laptops.
2. **A trust boundary is required.** Payments, authorisation, pricing, grading, anything where the user benefits from lying.
3. **Centralised or heavy computation.** Search ranking over 50 M books, ML inference, report generation — work too big or too proprietary for a client.
4. **Clients are heterogeneous.** Web + iOS + Android + partner integrations all need the same logic; put it in one place behind an API rather than reimplementing it four times.
5. **Data must outlive the device.** Losing your phone must not lose your order history.

## 14. When *not* to use it

1. **Single-user, local-only tools.** A Markdown editor, an image converter, a CLI. A server adds latency, cost, privacy risk, and an outage surface for zero benefit.
2. **Offline-first requirements.** Field data collection with no connectivity needs a local-first model with sync; a pure client-server design is unusable there.
3. **Massive, cheap content distribution with no trust needs.** Distributing a large public dataset is cheaper over P2P/BitTorrent than paying egress on central servers.
4. **Ultra-low-latency peer interaction.** Voice/video calls route media peer-to-peer (WebRTC) and use the server only for *signalling* — relaying everything through a central server would add avoidable round trips.

## 15. Common mistakes

**Beginner mistakes**
- Trusting client-supplied prices, roles, totals, or user IDs. *(The single most common security bug in web apps.)*
- Putting API keys or DB credentials in client code — "it's minified" is not encryption.
- Assuming the network is reliable, fast, free, and secure. (These are 4 of the classic *8 Fallacies of Distributed Computing*; the other 4 are: topology doesn't change, there's one administrator, transport cost is zero, the network is homogeneous.)
- Confusing "server" (process) with "machine", which then makes load balancing confusing.
- No timeout on outbound calls, so one slow dependency hangs every worker.

**Experienced-engineer mistakes**
- Splitting into microservices before there's an organisational or scaling reason (Phase 4, Topic 23) — buying distributed-systems problems to solve a code-organisation problem.
- Chatty APIs: one page triggering 40 round trips (the N+1 problem at the network layer) — the reason GraphQL and BFFs exist (Lesson 12).
- Forgetting that the app server is itself a *client*, and therefore needs timeouts, retries, and circuit breakers on everything it calls.
- Designing for 1 M users when peak is 14 RPS — building the Phase 4 architecture for a Phase 1 problem.
- Treating a crashed request as "failed" when it may have succeeded — the ambiguity problem.

## 16. Interview perspective

You will rarely be asked "what is client-server?" directly. It shows up as the *frame* of your answer. How to use it:

- **Open by naming the clients.** "Who are the clients — web, mobile, partner APIs, internal services? That changes the API shape and the auth model." This instantly signals seniority.
- **Draw the 3-tier box diagram first**, always. Client → app server → database. Then justify every box you add after that with a requirement or a number.
- **Say the trust boundary out loud.** "Pricing is computed server-side; the client only sends ISBN and quantity." Interviewers notice.
- **Resist premature distribution.** If you do the estimate and get 14 RPS, *say* "one server handles this; I'll add components only when a number forces me to." Candidates who sketch Kafka for 14 RPS are down-levelled, not up-levelled.
- **Mention the app server's dual role.** "The app server is a client of the DB and of Stripe, so it needs timeouts and a circuit breaker on both." That's a Phase 4 idea delivered in Phase 1 language.

**Red flags interviewers watch for:** no mention of validation; client-computed totals; jumping to microservices unprompted; being unable to explain what happens between typing a URL and seeing pixels.

## 17. Knowledge check

Answer these before reading on. Write your answers down — even one line each.

**Conceptual**

1. A colleague says "our mobile app talks straight to the database over the internet, which removes a hop and makes it faster." Give two specific, concrete reasons this is a bad idea, and name one narrow case where a variant of it is acceptable.
2. The BookNook app server calls PostgreSQL, Redis, and Stripe. In that sentence, when is the app server a server and when is it a client? Why does the distinction change how you write the code?
3. A request takes 20 ms of which 18 ms is waiting on the database. Explain why adding CPU cores to the app server barely helps, and name the two things that *would* help.

**Practical scenario**

4. BookNook's checkout currently accepts this body from the browser:
   `{"isbn": "1492052205", "quantity": 2, "unit_price": 3200, "discount_pct": 0, "total": 6400}`
   Rewrite the request contract, state what the server must now do, and describe one failure that your new design *still* doesn't solve.

**Calculation**

5. BookNook runs a one-day sale. DAU jumps from 10,000 to 250,000, requests per user rise from 30 to 45, and the peak-hour multiplier worsens from 4× to 6×. Average server time per request stays 20 ms.
   (a) What is the peak RPS? (b) Using Little's Law, how many concurrent requests are in flight at peak? (c) If one server can hold 100 concurrent requests, how many servers do you need at peak, and what's the first non-app-tier component you'd expect to break? Show every step.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 2.

---

**Progress checklist**

Phase 1: `[x] 1. Client-server architecture` → next `[ ] 2. Stateful versus stateless servers`
Phases 2–5: not started.

**NEXT LESSON: Phase 1, Topic 2 — Stateful versus stateless servers**

---

# Lesson 2 — Stateful versus Stateless Servers

*Phase 1, Topic 2*

## 1. Simple definition

First, define **state**. State is *any information a system remembers between one moment and the next*. If a program's behaviour today depends on something that happened earlier, that "something" is state.

- A **stateful server** keeps information about a particular client *inside the server process* (in its RAM) and relies on that information to handle the client's next request. Request #2 only works correctly if it lands on the *same* server that handled request #1.
- A **stateless server** keeps nothing about a particular client between requests. Every request carries, or points to, everything the server needs. Any server can handle any request.

The precise test, and the one to use in interviews:

> **If I kill this server process right now and send the next request to a different machine, does the user notice?**
> If yes → stateful. If no → stateless.

### A critical distinction: which state are we talking about?

"Stateless" does **not** mean "has no data". BookNook obviously remembers your orders — that's the whole point. There are three different kinds of state and only one of them is being discussed:

| Kind of state | Example | Where it lives | Does it make the *server* stateful? |
|---|---|---|---|
| **Connection state** | TCP sequence numbers, TLS session keys, the open socket itself | Kernel + process, per connection | No — it's scoped to one connection, not to the application logic |
| **Application / session state** | "User 42 is logged in", shopping cart contents, a multi-step wizard's progress, a WebSocket subscription list | **This is the one that matters** | **Yes, if kept in process memory** |
| **Persistent / business state** | Orders, users, books, stock levels | Database | No — it's shared and external, every server sees the same copy |

So the slogan is more precisely: **stateless servers keep no *per-client session state* in local process memory.** They are absolutely allowed to read and write shared databases.

## 2. Why it exists

Statelessness exists to make servers **interchangeable**, and interchangeability is the single precondition for almost everything in Phase 2.

Picture BookNook v0 keeping logged-in sessions in a Python dictionary:

```python
sessions = {}   # session_id -> {"user_id": 42, "cart": [...]}
```

This works perfectly on one server. Now watch five separate things break:

1. **You cannot add a second server.** A load balancer sends the user's next request to server B. Server B's dictionary is empty. The user is logged out mid-checkout. You have not scaled; you have built a coin-flip.
2. **You cannot deploy.** Restarting the process to ship a bug fix wipes the dictionary and logs out every active user. Your release process becomes a visible outage, so you deploy at 2 a.m., so you deploy rarely, so each deploy is riskier. (This is how teams end up shipping monthly.)
3. **You cannot survive a crash.** One `OutOfMemory` and every cart on that box is gone.
4. **You cannot autoscale down.** Removing a server destroys whatever sessions it held, so you must keep paying for peak capacity forever (Phase 2, Topic 22 — Autoscaling).
5. **Memory grows without bound.** 50,000 active sessions × 20 KB of cart data = 1 GB of RAM that exists purely to hold things the database should own.

Statelessness converts all five problems into one problem — "where do we put the session?" — and that one problem has well-understood solutions.

**What would happen without the concept?** You'd be forced into *sticky sessions* (Section 4), and your system's availability would be capped by the availability of individual machines rather than the availability of the fleet.

## 3. Real-world analogy

**A hospital emergency department versus a family doctor.**

- The **family doctor** is stateful. She remembers your history in her head. She is fast and the consultation is efficient — you don't re-explain your allergies. But if she's on holiday, nobody else can treat you well.
- The **ED** is stateless. Any doctor on shift can treat you, because everything needed is *written on the chart you carry* or *in the shared hospital records system*. Slightly more overhead per visit (someone must read the chart), but the department works 24/7 regardless of who's rostered.

Note what actually happened: the ED did not *abolish* memory. It **moved memory out of individual heads into a shared, durable record**. That is exactly what a session store does.

**Where the analogy breaks down:**

| Hospital | Real servers |
|---|---|
| Reading a chart takes 2 minutes out of a 20-minute visit (10 % overhead) | Fetching a session takes ~1 ms out of a 20 ms request (5 % overhead) — but it also adds a **new dependency that can fail** |
| A chart cannot be forged by the patient | A client-held token **can** be forged unless cryptographically signed — this is the entire security problem of JWTs |
| Only one doctor treats you at a time | Two of a user's browser tabs hit two servers *simultaneously*, creating write conflicts on the same session |
| The shared records system is assumed to be up | The session store becomes a **new single point of failure** — if Redis dies, every user is logged out at once |

That last row is the honest cost of statelessness, and beginners rarely mention it.

## 4. Detailed technical explanation

### How a stateful server works

```
Request 1 ─▶ Server A : creates sessions["abc"] = {user:42, cart:[]}
                         sends Set-Cookie: sid=abc
Request 2 ─▶ Server A : looks up sessions["abc"]  ✓ works
Request 2 ─▶ Server B : looks up sessions["abc"]  ✗ KeyError → logged out
```

The data lives in the heap of one OS process. It is fast (nanoseconds), free (no network call), and **non-replicated, non-durable, and non-shared**.

### Making a server stateless: the three strategies

There are exactly three ways to deal with session state, and every real system picks one or a hybrid.

#### Strategy 1 — Sticky sessions (session affinity)

Keep state in server memory, but make the load balancer always route a given client to the same server. The LB does this by hashing the client IP, or by injecting its own cookie (e.g. `AWSALB=...`) that names the chosen backend.

```
Client X ──▶ LB ──(always)──▶ Server A   [holds X's session]
Client Y ──▶ LB ──(always)──▶ Server B   [holds Y's session]
```

This is a **workaround, not a solution**. What it actually costs:

| Problem | Consequence |
|---|---|
| Server A dies | Every user pinned to A loses their session. Partial, confusing outage. |
| Deploy restarts A | Same as above, but you caused it, every release. |
| Uneven load | A long-lived heavy user is stuck on A forever; the LB cannot rebalance. "Least connections" balancing (Phase 2, Topic 6) stops working. |
| Autoscaling | New servers get only *new* users, so they stay cold while old servers stay hot. Scaling out doesn't relieve the hotspot. |
| Hashing by client IP | Thousands of users behind one corporate NAT (Lesson 3) all hash to the same server. |

When it's legitimately fine: long-lived connections that *inherently* bind to one process — WebSockets, gRPC streams, video transcoding sessions. There, affinity isn't a hack; it's physics.

#### Strategy 2 — Externalise the state (server-side sessions)

Move the dictionary out of the process and into a shared store that every server can reach.

```
Client ──▶ any server ──▶ Redis/Memcached/DynamoDB : GET session:abc
```

The client holds only an **opaque session ID** — a long random string that means nothing by itself. All real data stays server-side.

Design points that matter:
- **The ID must be cryptographically random**, ≥128 bits. A sequential or guessable ID means session hijacking.
- **The store needs a TTL** (time-to-live), so abandoned sessions expire instead of accumulating forever.
- **The store must be fast** — you're adding a network hop to *every* request. In-region Redis is ~0.5–1 ms (typical); that's acceptable. A cross-region call at 80 ms is not.
- **The store is now critical infrastructure**: it needs replication and failover of its own (Phase 2, Topic 15 — Database replication).

#### Strategy 3 — Put the state in the client (tokens / JWTs)

Give the client a **signed** blob containing the facts the server needs, and have the client send it back each time.

A **JWT** (JSON Web Token) is three base64url segments joined by dots: `header.payload.signature`.

```
header    {"alg":"HS256","typ":"JWT"}
payload   {"sub":"42","role":"customer","exp":1735689600}
signature HMAC-SHA256(base64(header) + "." + base64(payload), server_secret)
```

The server doesn't look anything up. It recomputes the signature with its secret key; if it matches, the payload wasn't tampered with, so `sub: 42` can be trusted. **No database call at all.**

Two things beginners get wrong, consistently:

1. **A JWT is signed, not encrypted.** The payload is base64, which is *encoding*, not encryption. Anyone holding the token can read it. Never put anything secret in a JWT.
2. **You cannot revoke a JWT.** It is valid until `exp`. "Log out everywhere", "ban this user", and "this admin was just demoted" do not take effect until expiry — unless you add a revocation list, which puts the database call right back and loses the main benefit.

#### Comparison

| | Sticky sessions | Server-side session store | JWT / signed token |
|---|---|---|---|
| Extra latency per request | 0 | ~1 ms (one cache round trip) | ~0.05 ms (a signature check) |
| Survives server crash | **No** | Yes | Yes |
| Survives store outage | n/a | **No — everyone logged out** | Yes |
| Instant revocation | Yes (delete from memory) | Yes (delete key) | **No** (until expiry, or add a blocklist) |
| Request size overhead | ~40 bytes (cookie) | ~40 bytes (cookie) | ~400–1,000 bytes **on every request** |
| Can store big data (cart) | Yes | Yes | No — it rides on every request, and most servers cap headers at 8 KB |
| Horizontal scaling | Poor | Good | Excellent |
| Where secrets can leak | Nowhere new | Nowhere new | Payload is readable by anyone holding it |
| Typical fit | WebSockets, legacy apps | Web apps needing logout, carts, roles that change | Stateless microservice-to-microservice auth, short-lived access tokens |

**The mainstream production answer in 2026** is a hybrid: a short-lived JWT access token (5–15 minutes) for cheap verification, plus a long-lived **refresh token** that *is* checked against a database when a new access token is minted. Revocation then takes effect within one access-token lifetime — a bounded, explicitly chosen window. This is the design you should name in an interview, along with the window you chose and why.

### "Stateless" doesn't mean the state disappeared

Be honest about this: statelessness is **state relocation**, not state elimination. You move it from many unreliable places (process memories) to one deliberately-engineered place (a replicated store), or onto the client (with cryptography paying for trust). The total amount of state is the same. What changes is that it now lives somewhere you can replicate, back up, monitor, and reason about.

### HTTP is stateless by design

HTTP itself has no memory. Every request is independent — the protocol provides nothing to link request #2 to request #1. Cookies (Lesson 6) were invented in 1994 precisely to bolt a notion of session on top of a deliberately stateless protocol. That original design decision is a large part of why the web scaled at all.

## 5. Request / data flow

**Flow A — stateful (what breaks):**

1. User logs in. Request hits Server A.
2. A validates the password, generates `sid=9f2c...`, stores `sessions["9f2c"] = {user_id: 42, cart: []}` in local RAM.
3. A responds `Set-Cookie: sid=9f2c`.
4. User adds a book. Load balancer picks **Server B** (round robin).
5. B looks up `sessions["9f2c"]` → not found.
6. B responds `401 Unauthorized`. Browser redirects to the login page.
7. User, mid-checkout, is dumped at a login screen. They leave. Revenue lost.

**Flow B — stateless with an external store (what works):**

1. User logs in. Request hits Server A.
2. A validates the password and generates `sid=9f2c...` (128 random bits).
3. A writes to Redis: `SET session:9f2c '{"user_id":42}' EX 1800` — expires in 30 minutes.
4. A responds `Set-Cookie: sid=9f2c; Secure; HttpOnly; SameSite=Lax`.
5. User adds a book. LB picks **Server B**.
6. B reads `GET session:9f2c` from Redis → `{"user_id":42}` in ~0.8 ms.
7. B writes the cart line to PostgreSQL (`cart_items` table) — carts are business data, not session data.
8. B returns `200 OK`. The user never knows two different machines were involved.
9. Deploy happens. Both A and B restart. **Nothing is lost** — nothing of value was in their memory.

Note step 7 carefully. A shopping cart that must survive "I'll finish this on my phone tonight" is **not session state at all** — it belongs in the database, keyed by user ID. Putting carts in Redis-with-a-TTL is a very common and very annoying bug.

## 6. ASCII diagram

```
STATEFUL (sticky) — fragile
┌────────┐                      ┌──────────────────────┐
│ Client │──sid=9f2c──▶ LB ────▶│ Server A             │
└────────┘             (pinned) │  sessions{9f2c:...}  │ ◀── state lives HERE
                                └──────────────────────┘
                                ┌──────────────────────┐
                                │ Server B  sessions{} │  cannot help
                                └──────────────────────┘
        ✗ A restarts/crashes ⇒ every pinned user logged out


STATELESS + EXTERNAL STORE — the common production shape
┌────────┐          ┌────┐   any server   ┌──────────┐
│ Client │─sid=9f2c▶│ LB │──┬────────────▶│ Server A │──┐
└────────┘          └────┘  ├────────────▶│ Server B │──┼──▶ ┌─────────────┐
                            └────────────▶│ Server C │──┘    │   Redis     │
                                          └──────────┘       │ session:9f2c│
   servers are interchangeable, disposable, restartable       └─────────────┘
                                                                     │
        ✗ new single point of failure ──────────────────────────────┘
          (so replicate it: Phase 2, Topic 15)


STATELESS + TOKEN — no lookup at all
┌────────┐  Authorization: Bearer eyJhbGciOi...  ┌──────────┐
│ Client │────────────────────────────▶ LB ─────▶│ Any      │ verify_signature(secret)
└────────┘  (token carries user_id, role, exp)   │ server   │ → trust payload, no I/O
   ▲                                             └──────────┘
   └── holds the state itself; server holds nothing
       ✗ cannot be revoked before exp
```

## 7. Detailed example — BookNook v1

BookNook outgrows one box. Before adding a second server, we must classify every piece of state. This classification exercise is the actual engineering work; the code is trivial afterwards.

| Data | Current home | Correct home | Reason |
|---|---|---|---|
| "Is this user logged in?" | App RAM | **Redis** (or a signed token) | Needed by every server; must survive restarts; must be revocable on logout |
| Shopping cart | App RAM | **PostgreSQL**, keyed by `user_id` | Must survive 3 days and a device change. It is business data. |
| Guest (not-logged-in) cart | App RAM | **Redis with a 7-day TTL** | No user ID to key on; genuinely disposable; merge into the DB cart on login |
| Uploaded book-cover image, mid-upload | Local disk `/tmp` | **Object storage (S3)** | A retry may land on another server and never find the partial file (Phase 3, Topic 23) |
| Book catalogue | PostgreSQL | PostgreSQL | Already shared and correct |
| Rate-limit counter ("5 login attempts") | App RAM | **Redis** | Per-server counters let an attacker get 5 attempts × N servers (Phase 5, Design 3) |
| Rendered homepage HTML | App RAM | App RAM is **fine** | It's a *cache*, identical on every server, derived from the DB. Losing it costs latency, not correctness. |

**That last row is the key nuance.** A cache in process memory does not make a server stateful, because the state is **derivable** — any server can recompute it, and losing it harms performance, not correctness. The test is not "is there data in RAM?" but "**does a specific client depend on a specific server's RAM?**"

**The subtle bug this exposes.** BookNook's guest cart lives in Redis under the session ID. A guest adds three books, then logs in. If login mints a *new* session ID (which it must — see "session fixation" below) and you forget to migrate the cart, the user watches their cart empty at the exact moment they identify themselves. Correct flow: read the old session's cart → merge into the DB cart for `user_id` → delete the old session → issue a new ID.

**Session fixation**, since we raised it: if an attacker can set a victim's session ID *before* login (via a crafted link), and the server keeps that same ID after authenticating, the attacker now holds a logged-in session. Fix: **always issue a brand-new session ID on privilege change** (login, logout, role elevation).

**Second example for contrast — a multiplayer game server.** Here statelessness is impossible and attempting it is wrong. The world simulation (every player's position, velocity, health) is updated 30–60 times per second; a 1 ms Redis round trip per tick per player would destroy the tick budget. So game servers are deliberately, aggressively **stateful**: one process owns one match, players are hard-pinned to it, and the state is periodically snapshotted so a crash loses ≤5 seconds rather than everything. The scaling model changes completely — instead of "any server handles any request", it becomes "a matchmaking service assigns a match to a server and tells clients where to connect". Same trade-off, opposite answer, because the *latency budget* and the *mutation rate* are different. That is what "it depends" actually depends on: **how often the state changes, and how much latency a lookup can afford.**

## 8. Code example

Illustrative only — not production code. Error handling, connection pooling, and key rotation are omitted deliberately.

### The stateful version (the anti-pattern)

```python
from fastapi import FastAPI, Response, Cookie
import secrets

app = FastAPI()

# THE PROBLEM: a plain dict in this process's heap.
# Invisible to every other server. Erased by every restart.
sessions: dict[str, dict] = {}

@app.post("/login")
def login(email: str, password: str, response: Response):
    user = verify_password(email, password)        # returns None on failure
    if user is None:
        return {"error": "invalid credentials"}

    sid = secrets.token_urlsafe(32)                # 256 bits of randomness
    sessions[sid] = {"user_id": user.id}           # ← stored LOCALLY. This is the bug.
    response.set_cookie("sid", sid, httponly=True)
    return {"ok": True}

@app.get("/me")
def me(sid: str | None = Cookie(default=None)):
    session = sessions.get(sid)                    # KeyError on any other server
    if session is None:
        return {"error": "not logged in"}
    return {"user_id": session["user_id"]}
```

### The stateless version (external store)

```python
from fastapi import FastAPI, Response, Cookie, HTTPException
import secrets, json, redis

app = FastAPI()

# Shared by EVERY app server. The session no longer belongs to a process.
r = redis.Redis(host="booknook-sessions.internal", port=6379)

SESSION_TTL_SECONDS = 1800       # 30 min of inactivity ends the session

@app.post("/login")
def login(email: str, password: str, response: Response):
    user = verify_password(email, password)
    if user is None:
        # Deliberately vague: revealing "no such email" leaks which accounts exist.
        raise HTTPException(status_code=401, detail="invalid credentials")

    sid = secrets.token_urlsafe(32)
    r.setex(                                  # SET with EXpiry, one atomic command
        f"session:{sid}",
        SESSION_TTL_SECONDS,                  # auto-cleanup; without this, Redis fills up
        json.dumps({"user_id": user.id}),     # keep it SMALL -- fetched on every request
    )
    response.set_cookie(
        "sid", sid,
        httponly=True,   # JavaScript cannot read it -> blunts XSS cookie theft
        secure=True,     # only ever sent over HTTPS (Lesson 7)
        samesite="lax",  # not sent on cross-site POSTs -> blunts CSRF (Lesson 6)
        max_age=SESSION_TTL_SECONDS,
    )
    return {"ok": True}

@app.get("/me")
def me(sid: str | None = Cookie(default=None)):
    if sid is None:
        raise HTTPException(status_code=401, detail="not logged in")

    raw = r.get(f"session:{sid}")             # ~0.5-1 ms in-region (typical)
    if raw is None:                           # expired, logged out, or forged
        raise HTTPException(status_code=401, detail="not logged in")

    # Sliding expiry: active users shouldn't be logged out mid-session.
    r.expire(f"session:{sid}", SESSION_TTL_SECONDS)
    return {"user_id": json.loads(raw)["user_id"]}

@app.post("/logout")
def logout(sid: str | None = Cookie(default=None)):
    if sid:
        r.delete(f"session:{sid}")            # INSTANT revocation -- the JWT model cannot do this
    return {"ok": True}
```

### The token version

```python
import jwt, time    # PyJWT

SECRET = load_secret("JWT_SIGNING_KEY")       # from a secrets manager, NEVER in source
ACCESS_TTL = 900                              # 15 min -- this IS your revocation delay

def issue_access_token(user) -> str:
    payload = {
        "sub": str(user.id),                  # subject: who this token is about
        "role": user.role,                    # avoids a DB lookup for authorisation
        "iat": int(time.time()),              # issued-at
        "exp": int(time.time()) + ACCESS_TTL, # expiry -- the ONLY thing limiting damage
        "jti": secrets.token_hex(16),         # unique ID, so it can be blocklisted if needed
    }
    # HS256 = symmetric: every verifying service needs the same secret.
    # For many services, prefer RS256/EdDSA: sign with a private key,
    # verify with a public one, so a compromised service cannot MINT tokens.
    return jwt.encode(payload, SECRET, algorithm="HS256")

def verify_access_token(token: str) -> dict:
    # MUST pin the algorithm. Omitting `algorithms=` historically allowed the
    # "alg: none" attack, where an attacker submits an unsigned token.
    return jwt.decode(token, SECRET, algorithms=["HS256"])   # raises if expired/tampered
```

### Load balancer configuration — making stickiness explicit

```nginx
# Illustrative nginx config. Sticky sessions: a WORKAROUND for stateful servers.
upstream booknook_app {
    ip_hash;                    # route by hashing client IP -> same client, same server
    server 10.0.1.11:8000;
    server 10.0.1.12:8000;
    server 10.0.1.13:8000;
}
# Problems baked in above:
#  - 10.0.1.12 dies  -> its users lose their sessions
#  - a corporate NAT -> 5,000 employees hash to ONE server (Lesson 3)
#  - adding a 4th server rehashes many clients -> mass logout
#    (consistent hashing reduces this: Phase 2, Topic 21)

# The stateless alternative needs no affinity at all:
upstream booknook_app_stateless {
    least_conn;                 # free to send each request to the least busy server
    server 10.0.1.11:8000;
    server 10.0.1.12:8000;
    server 10.0.1.13:8000;
}
```

## 9. Numerical example

### Part A — How much RAM does in-process session state cost?

**Assumptions (stated explicitly):**
- 10,000 daily active users, 30-minute sessions, evenly spread over a 12-hour active window.
- Session payload: user ID, role, CSRF token, preferences ≈ 2 KB; guest cart ≈ 20 KB.
- 20 % of concurrent sessions are guests with carts.

```
Concurrent sessions = 10,000 users × (30 min / 720 min window) = 417 concurrent sessions
Memory (logged-in, 80%) = 417 × 0.8 × 2 KB   =   667 KB
Memory (guest carts, 20%) = 417 × 0.2 × 20 KB = 1,668 KB
Total                                          ≈ 2.3 MB
```

**2.3 MB.** Memory is *not* the reason to go stateless at this scale — availability is. Be honest about that in an interview; claiming "we'd run out of RAM" at 10k users is wrong and an interviewer will catch it.

Now the sale-day scale from Lesson 1:

```
250,000 DAU × (30/720) = 10,417 concurrent sessions
Logged-in: 10,417 × 0.8 × 2 KB  =  16.7 MB
Guest carts: 10,417 × 0.2 × 20 KB = 41.7 MB
Total ≈ 58 MB per server, replicated on EVERY server if you broadcast sessions
```

Still small. The real cost appears with heavier sessions — cached search results, rendered pages, uploaded files. At 500 KB/session: `10,417 × 500 KB ≈ 5.2 GB`. *Now* it's a memory problem.

### Part B — The latency cost of externalising

**Assumptions:** 20 ms average request (Lesson 1); Redis GET in-region = 0.8 ms; Redis `EXPIRE` = 0.8 ms; peak 14 RPS normally, 125 RPS on sale day (from Lesson 1's sale calculation).

```
Added latency per request = 0.8 ms (GET) + 0.8 ms (EXPIRE) = 1.6 ms
New average request time  = 20 + 1.6 = 21.6 ms
Relative slowdown         = 1.6 / 20 = 8%
```

An 8 % latency increase to gain restartability, autoscaling, and crash survival. That is an outstanding trade, and you should say the number out loud rather than hand-waving "a small overhead".

**Redis load:**
```
Peak sale-day RPS = 125
Redis ops/sec     = 125 × 2 (GET + EXPIRE) = 250 ops/sec
```
A single Redis instance handles ~100,000 ops/sec (typical, simple commands). We are at **0.25 %** of one instance. Redis is not the bottleneck; it is the *availability risk* — which is why you replicate it, not shard it.

### Part C — The bandwidth cost of JWTs

**Assumptions:** session cookie = 40 bytes; JWT = 600 bytes (typical with roles and claims); 300,000 requests/day normally, 11.25 M on sale day (250,000 × 45 from Lesson 1).

```
Per-request overhead = 600 - 40 = 560 extra bytes, sent on EVERY request

Normal day : 300,000    × 560 B =   168 MB/day ingress
Sale day   : 11,250,000 × 560 B = 6,300 MB/day = 6.3 GB/day
```

**6.3 GB of extra upload traffic per day**, just to carry identity. On mobile networks this is also the user's data allowance and battery. It is why access tokens should be *lean* — put `sub`, `role`, `exp` in them, never a cart, never a profile.

### Part D — The availability maths that justifies the whole lesson

**Assumptions:** one app server has 99 % availability (≈7.2 hours of downtime per month).

```
STATEFUL, 3 sticky servers:
  A given user is pinned to exactly ONE server.
  That user's availability = 99%   → ~7.2 h/month of downtime, per user.
  Adding servers does NOT help the pinned user at all.

STATELESS, 3 interchangeable servers behind an LB:
  The service is down only if ALL THREE are down simultaneously.
  P(all down) = (1 - 0.99)^3 = 0.01^3 = 0.000001
  Availability = 1 - 0.000001 = 99.9999%  → ~2.6 seconds/month

BUT add the session store (99.9% available) in SERIES:
  Availability = 0.999999 × 0.999 = 0.998999 ≈ 99.9%  → ~43 min/month
  The store is now the dominant term. Replicate it (Phase 2, Topic 15)
  to 99.99% and you get 0.999999 × 0.9999 ≈ 99.99% → ~4.3 min/month.
```

Read that last block twice. Statelessness took the fleet from 99 % to 99.9999 %, and then the shared store dragged it back to 99.9 %. **The thing you added to enable statelessness became the limiting factor.** This is the series-availability rule, developed properly in Lesson 15 — the chain is only as available as its weakest link, and every component you add multiplies in.

## 10. Trade-offs

**Advantages of stateless servers**
- Any server handles any request → true horizontal scaling (Phase 2, Topic 2).
- Servers become **disposable**: deploy, restart, autoscale, and terminate without user impact. This is what makes continuous deployment and spot instances viable.
- Crash blast radius shrinks from "everyone on that box" to "the handful of in-flight requests".
- Simpler debugging: no "it only breaks on server 3" mysteries caused by divergent memory.
- Enables blue-green and rolling deploys, multi-region, and serverless.

**Disadvantages**
- A network round trip on every request (typically 0.5–2 ms in-region).
- A new shared dependency that must be replicated, monitored, capacity-planned, and backed up.
- Correlated failure: when the session store goes down, **every user is logged out at once** — a worse experience than 1/3 of users losing sessions.
- Token approaches add per-request bandwidth and give up instant revocation.
- Re-fetching state each request can be wasteful for chatty workflows (10 requests on one page = 10 session lookups, unless you cache locally for a few seconds).

**Operational cost**
- Running Redis/Memcached with replication and failover: another system to patch, monitor, and page someone about.
- Secret management and **key rotation** for tokens — rotating a signing key without logging everyone out requires supporting two valid keys during the overlap.
- Session-store capacity planning and eviction policy tuning (Phase 2, Topic 13).

**When statelessness is unnecessary complexity**
- A single-server internal tool with 20 users where a restart logging everyone out is genuinely fine. Adding Redis there buys nothing and adds a thing that can break at 3 a.m.
- Workloads that are inherently session-bound: WebSocket chat servers, game servers, long-running video transcodes, interactive database sessions. Fighting physics with architecture is a losing game; pin the connection and design the failure handling instead.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| Stateful server crashes | Abruptly logged out mid-checkout; cart empty | Error-rate spike; 401s rise on one instance; health check fails | Users re-login manually. **No automatic recovery** — the data is gone. |
| Rolling deploy of stateful servers | A third of users logged out per batch, every release | Correlated 401 spike aligned with deploy timestamps | None. The fix is architectural (externalise state). |
| Session store (Redis) down | **All** users logged out simultaneously; login also fails | Connection errors from every app server; store health check red | Failover to a replica (seconds). Degrade gracefully: keep *browsing* working, only block cart/checkout. |
| Session store full / evicting | Random users logged out unpredictably; "it logs me out sometimes" | `evicted_keys` metric rising; memory at maxmemory | Raise memory, shorten TTL, shrink payloads. Use `volatile-lru` so only expiring keys are evicted (Phase 2, Topic 13). |
| Redis failover loses recent writes | Users who logged in during the last second must log in again | Replication-lag metric; small 401 blip | Acceptable for sessions. **Not** acceptable for orders — which is why orders are in PostgreSQL, not Redis. |
| JWT signing key leaked | Nothing visible — attacker silently forges any identity | Anomaly detection only; often undetected for a long time | Rotate the key immediately; this invalidates **all** tokens and logs out every user. A genuine incident. |
| JWT used after user is banned | Banned user keeps full access for up to `exp` | Audit logs show post-ban activity | Shorten TTL; add a revocation blocklist checked on sensitive actions only. |
| Clock skew between servers | Sporadic "token not yet valid" / premature expiry | 401s correlated with one host; NTP drift alert | Run NTP; allow a small leeway (±30 s) in validation. |
| Sticky session + server removed from LB | That subset of users logged out while everyone else is fine | Hard to spot — error rate barely moves in aggregate | Per-instance error-rate dashboards. The real fix is to stop being sticky. |

## 12. Alternatives

| Approach | State lives in | Scales horizontally | Revocable | Latency cost | Best for |
|---|---|---|---|---|---|
| **In-process memory** | App server RAM | No | Yes | None | Single server, dev, prototypes |
| **Sticky sessions** | App server RAM + LB affinity | Poorly | Yes | None | WebSockets, legacy apps you can't rewrite |
| **Session replication** (broadcast sessions to all servers) | Every server's RAM | Up to ~5–10 nodes | Yes | None per request; heavy gossip traffic | Small Java/.NET clusters; falls apart as N grows (O(N²) chatter) |
| **External session store** | Redis / Memcached / DynamoDB | Yes | Yes | ~1 ms | **Most web apps** |
| **Signed token (JWT)** | The client | Yes | No (until `exp`) | ~0 | Service-to-service auth, mobile APIs, short-lived access |
| **Hybrid: short JWT + refresh token** | Client + DB | Yes | Within one TTL window | ~0 on most calls | **Modern default for APIs** |
| **Client-side encrypted cookie** | The client, encrypted | Yes | No | ~0 | Small sessions; avoids a store, but 4 KB cookie limit |
| **Deliberately stateful + snapshots** | App server RAM, periodically persisted | Via sharding/matchmaking | n/a | None | Games, real-time collaboration, stream processing |

## 13. When to use statelessness

1. **Anything behind a load balancer.** The moment there are two app servers, statelessness stops being a preference and becomes a requirement. BookNook crosses this line in Phase 2.
2. **Anything that autoscales or runs on preemptible/spot capacity.** The platform will kill your instances with ~2 minutes' notice. If sessions die with them, you cannot use the cheap capacity.
3. **Public HTTP APIs consumed by mobile apps and partners.** There's no browser cookie jar to rely on and clients reconnect constantly; bearer tokens fit naturally.
4. **Microservice-to-microservice calls.** Service B must authorise a request without a session lookup for every hop; a signed token propagates identity cheaply (Phase 4, Topic 23).
5. **Multi-region deployments.** A user routed to Singapore today and Mumbai tomorrow cannot depend on a specific machine's memory (Phase 2, Topic 22).

## 14. When *not* to use statelessness

1. **Long-lived connections with high-frequency state updates.** WebSocket chat rooms, multiplayer games, collaborative editors. Round-tripping to Redis 60 times a second per user is absurd; accept affinity and design snapshot/restore instead.
2. **A single-server internal tool.** 20 users, restarts are announced, a re-login is a non-event. Adding a session store adds a failure mode and an on-call burden for zero user benefit.
3. **Workflows that are inherently a server-held resource.** An open database transaction, a streaming file upload, an in-progress video encode. The state *is* the work; it cannot be relocated.
4. **Extremely latency-sensitive paths where the lookup dominates.** If your request budget is 2 ms, a 1 ms session fetch is 50 % of it. Use a signed token, or cache the session locally for a few seconds and accept bounded staleness.

## 15. Common mistakes

**Beginner mistakes**
- Keeping sessions in a module-level dict and only discovering the problem after adding a second server.
- Believing "stateless" means "no database". It means no *per-client session state in process memory*.
- Storing a shopping cart in a session store with a TTL, then being surprised when carts vanish overnight. Carts are business data.
- Putting sensitive data in a JWT payload, thinking base64 hides it.
- Forgetting to issue a new session ID on login (**session fixation**).
- No TTL on session keys, so the store grows until it starts evicting at random.
- Writing files to local disk (`/tmp`, `./uploads`) — invisible to other servers and erased on every deploy. Very common with image uploads.
- Omitting `algorithms=[...]` when verifying JWTs, enabling the `alg: none` forgery attack.

**Experienced-engineer mistakes**
- Treating the session store as a cache rather than a dependency — no replication, no failover, no alerting, until the day it takes the whole site down.
- Using long-lived (24-hour) JWTs for everything, then discovering during a security incident that there is no way to log anyone out.
- Sticky sessions "temporarily", which become permanent and silently block autoscaling for years.
- Putting so much in the token that headers exceed the server's 8 KB limit, producing mystifying `431`/`400` errors that only affect users with many roles.
- No plan for signing-key rotation, so a suspected leak means logging out every user at once.
- Reading the session on every one of the 12 API calls a single page makes, turning 1 ms into 12 ms of page latency.
- Ignoring the concurrency problem: two tabs mutating the same session object, last-write-wins, silently losing data.
- Assuming "stateless app servers" implies the *system* is stateless, and then forgetting to make the store itself highly available (Part D of Section 9).

## 16. Interview perspective

This topic appears in almost every system-design interview, usually implicitly, the moment you draw a second app server.

**What to say, in order:**

1. **Say it unprompted when you add the load balancer.** "I'm adding a second app server, which means the app tier must be stateless — session state moves to Redis." Saying this *before* being asked is a strong signal.
2. **Classify the state out loud.** "Sessions go to Redis with a 30-minute TTL. Carts go to Postgres because they must survive days and devices. Rendered-page caches can stay in process memory because they're derivable."
3. **Name the trade-off with a number.** "That adds about 1 ms to a 20 ms request — 8 % — in exchange for being able to deploy without logging anyone out."
4. **Volunteer the new SPOF.** "Redis is now in the critical path, so I'd run it with a replica and automatic failover; otherwise my 99.9999 % app tier is capped at Redis's 99.9 %." Interviewers love this because most candidates never notice.
5. **Handle the JWT question properly.** The question "sessions or JWTs?" is a trap if answered absolutely. The strong answer: "Short-lived signed access tokens for cheap verification, plus a refresh token checked against the database. That bounds revocation delay to 15 minutes, which I'd confirm is acceptable for this product. For a banking app I'd shorten it or check a blocklist on sensitive operations."
6. **Know when to be stateful.** If asked to design chat or a game, saying "WebSocket connections are inherently sticky, so I'd route by room ID to a specific server and snapshot state every few seconds" shows you apply principles rather than recite them.

**Red flags interviewers watch for:**
- Drawing multiple app servers without ever mentioning session state.
- "We'll use sticky sessions" as a first resort rather than a last one.
- "JWTs are better than sessions" with no mention of revocation.
- Claiming a system is stateless while writing uploads to local disk.
- Not noticing that the session store is a single point of failure.
- Being unable to answer "what happens to logged-in users when you deploy?"

## 17. Knowledge check

Write down your answers before moving on — even one line each.

**Conceptual**

1. BookNook caches rendered homepage HTML in each app server's memory, and also keeps logged-in user sessions in each app server's memory. Both are "data in RAM". Explain precisely why one of these makes the server stateful and the other does not, and give the general test you used.

2. A teammate argues: "JWTs make our system stateless, so we don't need Redis at all — that's strictly better." Give two specific capabilities the team loses, and describe a concrete BookNook scenario where each loss causes a real business or security problem.

3. Your app tier is stateless with three servers at 99 % availability each, behind a session store at 99.9 %. A colleague proposes adding a fourth app server to improve availability. Explain quantitatively why this barely helps, and state what you would do instead.

**Practical scenario**

4. BookNook lets users upload a profile photo. The current code saves it to `./uploads/<user_id>.jpg` on the app server, then returns a URL like `/uploads/42.jpg`. The team has just moved from one server to three behind a round-robin load balancer. Describe exactly what the user now experiences (be specific about the sequence of requests), explain the root cause in the vocabulary of this lesson, and propose a fix. Then name one *additional* problem your fix introduces that you would need to handle.

**Calculation**

5. BookNook has 400,000 DAU. Sessions last 45 minutes, usage is spread over a 15-hour active window, and each session payload is 3 KB. Users make 40 requests per session.
   (a) How many concurrent sessions are there, and how much memory would they consume in a session store?
   (b) If each request does one session `GET` and one `EXPIRE` against Redis, and DAU traffic peaks at 5× the average hourly rate, what is the peak Redis operations-per-second? Is one Redis instance (~100,000 ops/sec) sufficient, and by what margin?
   (c) The team instead proposes a 700-byte JWT, eliminating the Redis calls. Compared with a 40-byte session cookie, how much extra inbound bandwidth per day does this cost across all users? Show every step and state your assumptions.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 3.

---

**Progress checklist**

Phase 1:
`[x] 1. Client-server architecture`
`[x] 2. Stateful versus stateless servers`
`[ ] 3. IP addresses, ports, sockets, and domain names` ← next
`[ ] 4. DNS resolution and DNS caching`
`[ ] 5–17` not started
Phases 2–5: not started.

**NEXT LESSON: Phase 1, Topic 3 — IP addresses, ports, sockets, and domain names**

---

# Lesson 3 — IP Addresses, Ports, Sockets, and Domain Names

*Phase 1, Topic 3*

## 1. Simple definition

Four terms that together answer the question *"how does a packet find the right program on the right machine?"*

- An **IP address** identifies a **network interface on a machine**. It answers *which computer*. Example: `203.0.113.10`.
- A **port** is a 16-bit number (0–65535) identifying a **program on that machine**. It answers *which process*. Example: `443`.
- A **socket** is the programming-level handle representing **one endpoint of one connection**. It's the object your code reads and writes.
- A **domain name** is a human-readable label (`booknook.com`) that is **translated into an IP address** before any connection happens. The network itself has never heard of domain names.

The chain, in one line:

```
booknook.com  ──DNS──▶  203.0.113.10  ──:443──▶  the app server process  ──socket──▶ your code
   (name)                 (machine)               (program)                (one conversation)
```

Two terms used constantly from here on:
- **Packet**: one chunk of data with addressing information on the front, travelling across the network independently.
- **Interface**: a machine's connection to a network (Wi-Fi, Ethernet, a virtual NIC in a cloud VM). A machine can have several, each with its own IP.

## 2. Why it exists

Work through what you'd need if these didn't exist.

**Why IP addresses?** Machines need globally meaningful names that **routers can make decisions about without a lookup table of the entire internet**. IP addresses are hierarchical: `203.0.113.10` belongs to the block `203.0.113.0/24`, which belongs to a larger block owned by an ISP. A router doesn't need to know about 5 billion devices — it needs to know "anything starting with 203.0.113 goes out that cable". Hierarchy is what makes routing tractable. (MAC addresses, by contrast, are flat and only work within one local network.)

**Why ports?** One machine runs many programs that all want network traffic: a web server, a database, SSH, a metrics agent. Packets arrive at the machine addressed only to its IP. Without ports, the operating system would have no idea which program to hand them to. The port is the **demultiplexing key**: "IP gets you to the building, port gets you to the apartment".

**Why sockets?** A server has thousands of simultaneous conversations. The kernel needs a way to keep them apart and your code needs a handle for each. The socket is that handle — and critically, it's the abstraction that lets you use ordinary read/write calls instead of thinking about packets.

**Why domain names?** Three reasons, and the third is the one beginners miss:
1. Humans cannot remember `203.0.113.10`, let alone `2001:db8:85a3::8a2e:370:7334`.
2. One name can map to many IPs (load balancing, geographic routing).
3. **Indirection enables change.** You can replace every server, move cloud providers, or fail over to another region, and `booknook.com` still works. If customers had bookmarked an IP, you could never move. This is the same "add a layer of indirection" principle that underpins load balancers and service discovery (Phase 4, Topic 24).

## 3. Real-world analogy

**Postal addressing in a large office building.**

| Postal concept | Network concept |
|---|---|
| Street address of the building | IP address |
| Suite / apartment number | Port |
| "Legal & Compliance, 4th floor" on the directory | Domain name → resolved to a suite number |
| An ongoing correspondence between two specific people | A socket / connection |
| The city's sorting hierarchy (country → city → postcode → street) | Hierarchical IP routing |

A letter addressed to the building with no suite number gets dumped in the lobby. That's a packet to an IP with no listening port — the machine replies "nobody here wants this" (a TCP RST, which is why you see *connection refused* instantly).

**Where the analogy breaks down:**

| Post office | Networking |
|---|---|
| Your street address is yours; it identifies you | Your public IP is probably **shared with thousands of strangers** via NAT (Section 4). A café's 200 customers all appear as one address. |
| Suite 443 is a fixed physical place | Port 443 is only a *convention*. Any program can listen on any free port; the number means nothing to the network. |
| Addresses rarely change | Your laptop's IP changes every time you change networks, and many home IPs change weekly |
| One letter, one delivery | A single connection is thousands of packets that may arrive out of order, duplicated, or not at all (Lesson 8) |
| The building knows its own address | A NATed machine genuinely does **not know** its own public IP; it must ask an external service |

## 4. Detailed technical explanation

### IPv4

An **IPv4** address is 32 bits, written as four decimal numbers 0–255 ("dotted quad"): `203.0.113.10`.

```
203  .  0  .  113  .  10
11001011 00000000 01110001 00001010   ← the same 32 bits
```

Total space: 2³² ≈ **4.3 billion** addresses. That seemed infinite in 1981 and ran out in 2011. There are more phones than that today.

A **subnet mask** or **CIDR prefix** splits the address into a network part and a host part. `203.0.113.0/24` means "the first 24 bits identify the network; the last 8 identify the host", giving 256 addresses (254 usable — the first is the network address, the last is broadcast).

| CIDR | Addresses | Typical use |
|---|---|---|
| `/32` | 1 | A single host (firewall rules) |
| `/24` | 256 | A small subnet |
| `/16` | 65,536 | A cloud VPC subnet |
| `/8` | 16,777,216 | A huge legacy allocation |

### Private vs public addresses

Certain ranges are reserved as **private** — routable inside your own network, never on the public internet. Every router on the internet is configured to drop them.

| Range | CIDR | Size | Where you see it |
|---|---|---|---|
| `10.0.0.0 – 10.255.255.255` | `10.0.0.0/8` | 16.7 M | Cloud VPCs, large corporate networks |
| `172.16.0.0 – 172.31.255.255` | `172.16.0.0/12` | 1 M | Docker's default bridge network |
| `192.168.0.0 – 192.168.255.255` | `192.168.0.0/16` | 65 K | Home routers |
| `127.0.0.0 – 127.255.255.255` | `127.0.0.0/8` | — | **Loopback** — never leaves the machine |
| `169.254.0.0/16` | link-local | — | "I failed to get an address from DHCP" (and, in AWS, the metadata service at `169.254.169.254`) |

This is why BookNook's database sits at `10.0.2.15` and is **unreachable from the internet by construction**, not merely by firewall rule. Network design is a security control.

### NAT — Network Address Translation

Private addresses can't be routed publicly, so how does your laptop at `192.168.1.5` reach Google? The router performs **NAT**: it rewrites the source address of outgoing packets to its own public IP, and remembers the mapping so replies can be rewritten back.

```
Laptop                    Home router                       Server
192.168.1.5:51234  ──▶   rewrite source to              ──▶ 142.250.x.x:443
                          203.0.113.7:51234
                          and record in NAT table:
                          (192.168.1.5:51234) ↔ (203.0.113.7:51234)

                     ◀──  reply to 203.0.113.7:51234     ◀──
                          look up table, rewrite dest
Laptop ◀── 192.168.1.5:51234
```

The **port number is what makes NAT work.** The router distinguishes your laptop's connection from your phone's by the port it assigned. NAT is really "Network Address *and Port* Translation" (NAPT).

Consequences that matter for system design:
- **Inbound connections are impossible by default.** Nothing on the internet can initiate a connection to `192.168.1.5`; there's no entry in the NAT table yet. This is why peer-to-peer apps need STUN/TURN servers and why "port forwarding" exists.
- **Many users share one public IP.** IP-based rate limiting will throttle an entire office. IP-hash load balancing (Lesson 2) will pile them all onto one server. **Never treat a public IP as a user identifier.**
- **NAT tables expire.** An idle connection gets evicted after typically 2–5 minutes, and the client then sees a silently dead connection. This is precisely why TCP keepalives and WebSocket ping frames exist.

### IPv6

128 bits, written as eight groups of four hex digits: `2001:0db8:85a3:0000:0000:8a2e:0370:7334`, shortened by dropping leading zeros and collapsing one run of zero-groups to `::` → `2001:db8:85a3::8a2e:370:7334`.

2¹²⁸ ≈ **3.4 × 10³⁸** addresses — roughly 10²⁸ per person alive. Enough that every device can have a globally unique public address, making NAT unnecessary.

| | IPv4 | IPv6 |
|---|---|---|
| Size | 32 bits | 128 bits |
| Notation | `203.0.113.10` | `2001:db8::1` |
| Address count | 4.3 × 10⁹ | 3.4 × 10³⁸ |
| NAT | Near-universal | Normally unnecessary |
| Header | Variable length, has a checksum | Fixed 40 bytes, no checksum (faster routers) |
| Address assignment | DHCP | SLAAC (self-configuring) or DHCPv6 |
| Adoption (approx., 2026) | Universal | ~45 % of Google's traffic |

**Dual-stack** means running both. Clients then use **Happy Eyeballs** (RFC 8305): start the IPv6 connection, and if it hasn't completed in ~250 ms, race an IPv4 attempt and use whichever wins. This is why a broken IPv6 path usually produces "the site is slow" rather than "the site is down" — a genuinely nasty class of bug, because it's invisible in aggregate metrics.

In URLs, IPv6 literals need brackets to avoid colliding with the port separator: `http://[2001:db8::1]:8080/`.

### Ports

16 bits → 65,536 values. Three ranges, assigned by IANA:

| Range | Name | Meaning |
|---|---|---|
| 0–1023 | **Well-known** | Standard services. On Unix, binding these requires root or a capability — a deliberate security measure so unprivileged users can't impersonate the SSH daemon. |
| 1024–49151 | **Registered** | Applications register these by convention (PostgreSQL 5432, Redis 6379) |
| 49152–65535 | **Ephemeral / dynamic** | Assigned automatically to *client* sockets for the life of one connection |

Ports you must know:

| Port | Service | Port | Service |
|---|---|---|---|
| 22 | SSH | 3306 | MySQL |
| 53 | DNS | 5432 | PostgreSQL |
| 80 | HTTP | 6379 | Redis |
| 443 | HTTPS / QUIC | 9092 | Kafka |
| 25/587 | SMTP | 27017 | MongoDB |

**Nothing enforces these.** You can run a web server on port 9999. The convention exists so clients know where to look by default: a browser given `https://booknook.com` with no port assumes 443, which is why you don't type it.

**Client ports are ephemeral.** When your browser connects out, the kernel picks an unused high port for the source. You never choose it and it's discarded when the connection closes.

### Sockets and the 4-tuple

A TCP connection is uniquely identified by **four values**:

```
(source IP, source port, destination IP, destination port)
```

This is the **4-tuple**, and understanding it resolves the most common beginner confusion: *"if everyone connects to port 443, how does the server keep them apart?"*

```
Conn 1:  (198.51.100.7, 51234) ──▶ (203.0.113.10, 443)
Conn 2:  (198.51.100.7, 51235) ──▶ (203.0.113.10, 443)   ← same client, different src port
Conn 3:  (192.0.2.44,   51234) ──▶ (203.0.113.10, 443)   ← same src port, different client IP
```

All three are distinct 4-tuples, so all three are distinct connections, all to port 443. The server's **listening socket** on port 443 does nothing but produce new sockets; each **connected socket** owns one 4-tuple. (In Lesson 1's code, `listener` was the first kind and `conn` the second.)

### Bind addresses: `127.0.0.1` vs `0.0.0.0` vs `localhost`

This trips up nearly every beginner deploying their first app.

| Value | Meaning when a server binds to it |
|---|---|
| `127.0.0.1` | Loopback only. **Reachable only from this same machine.** Traffic never touches a network card. |
| `0.0.0.0` | All interfaces. Reachable from anywhere that can route to any of this machine's IPs. |
| `10.0.1.11` (a specific IP) | Only via that one interface — e.g. the private network but not the public one |
| `localhost` | A *name*, normally resolving to `127.0.0.1` (and `::1` on IPv6). Resolution happens via the hosts file. |
| `::1` | IPv6 loopback. A server bound to `127.0.0.1` will **not** accept a connection to `::1` — a classic "works in curl, fails in the browser" bug. |

The practical rule: your app binds `127.0.0.1` when a reverse proxy on the same box fronts it (Phase 2, Topic 8), and `0.0.0.0` when it must be reachable from other machines (e.g. inside a container). Binding `0.0.0.0` on a machine with a public IP exposes your service to the internet — fine for a web server behind a firewall, catastrophic for a database. Unauthenticated databases bound to `0.0.0.0` are one of the most common sources of mass data breaches.

### Ephemeral port exhaustion — a real production failure

Each *outbound* connection from a machine consumes one ephemeral port **per destination 4-tuple**. Linux defaults to roughly `32768–60999` ≈ **28,232 ports**.

Now recall that a closed TCP connection lingers in **`TIME_WAIT`** for 2 × MSL (typically 60 s on Linux) — the kernel holds the port so that delayed duplicate packets from the old connection can't be mistaken for a new one (Lesson 8).

So: a BookNook app server that opens a **new** connection to the database for every request, at 500 requests/second:

```
Ports consumed per second            = 500
Seconds each port is held (TIME_WAIT) = 60
Ports in use in steady state          = 500 × 60 = 30,000
Ports available                       = 28,232
```

**You run out.** New connections fail with `EADDRNOTAVAIL` / "cannot assign requested address". The symptom is bizarre — the service works fine for 60 seconds after a restart, then collapses, and the database looks healthy throughout.

Fixes, in order of preference:
1. **Connection pooling** — reuse a handful of long-lived connections. This is the real fix and you should reach for it first.
2. **HTTP keep-alive** for outbound HTTP calls (Lesson 5).
3. Widen the range: `net.ipv4.ip_local_port_range = 10000 65535` (~55 k ports).
4. `net.ipv4.tcp_tw_reuse = 1` — lets the kernel reuse `TIME_WAIT` sockets for new outbound connections.

Note the limit is per *destination*: 28 k connections to the DB **and** 28 k to Redis are fine, because the destination IP/port differ, making the 4-tuples distinct.

### Domain names

A domain name is read **right to left**, most general first:

```
          api  .  booknook  .  com   .
           │        │          │     └── root (the trailing dot, usually implied)
           │        │          └──────── TLD (top-level domain)
           │        └─────────────────── second-level domain (what you register)
           └──────────────────────────── subdomain (yours to create freely)
```

A **fully qualified domain name (FQDN)** ends with that root dot: `api.booknook.com.`

Key properties:
- A name has no inherent IP; the mapping is data in DNS (Lesson 4) and changes whenever you like.
- One name → many IPs (round-robin, geo-routing).
- Many names → one IP (virtual hosting: the `Host` header in Lesson 5 tells the server which site is wanted).
- **Subdomains are free and instant.** `api.`, `cdn.`, `admin.`, `staging.` cost nothing and are a core architectural tool.

### The hosts file

Before consulting DNS, the OS checks a local text file:
- Linux/macOS: `/etc/hosts`
- Windows: `C:\Windows\System32\drivers\etc\hosts`

```
127.0.0.1    localhost
127.0.0.1    booknook.local
10.0.1.11    db.booknook.internal
```

It's an override, useful for local development and for testing a new server before changing public DNS. It's also a classic debugging trap: a stale entry makes a machine behave differently from every other machine, and nobody thinks to look.

## 5. Request / data flow

A BookNook customer in Mumbai opens `https://booknook.com/books/1492052205`. From keypress to bytes:

1. Browser splits the URL: scheme `https` (→ default port **443**), host `booknook.com`, path `/books/...`.
2. OS checks `/etc/hosts`. No entry.
3. OS checks its DNS cache. Miss.
4. Stub resolver queries the configured recursive resolver over **UDP port 53** (Lesson 4).
5. Resolver returns `A 203.0.113.10` (and possibly `AAAA 2001:db8::10`).
6. Dual-stack client starts an IPv6 attempt; Happy Eyeballs will fall back to IPv4 after ~250 ms if needed.
7. Kernel picks an unused **ephemeral source port**, say 51234, and allocates a socket.
8. The 4-tuple is now fixed: `(198.51.100.7, 51234) → (203.0.113.10, 443)`.
9. If the client is behind NAT, the home router rewrites the source to `(203.0.113.7, 51234)` and records the mapping.
10. TCP three-way handshake to port 443 (Lesson 8). If no process is listening there, the server sends RST → instant "connection refused".
11. The server's kernel matches the arriving packets against its listening socket on 443, completes the handshake, and places the new connection on the **accept queue**.
12. The application calls `accept()`, receiving a connected socket bound to that 4-tuple.
13. TLS handshake (Lesson 7); **SNI** carries `booknook.com` so the right certificate is chosen.
14. Browser sends the HTTP request, including `Host: booknook.com`, so one IP can serve many sites.
15. Response flows back; NAT rewrites the destination on the way in; the kernel routes it to the socket whose 4-tuple matches; the browser reads it.
16. Connection is kept alive for CSS, images, and API calls — avoiding repeats of steps 7–13.
17. On close, the client's port 51234 enters `TIME_WAIT` for ~60 s.

## 6. ASCII diagram

```
NAME → MACHINE → PROCESS → CONVERSATION

  booknook.com
       │ (1) DNS lookup, Lesson 4
       ▼
  203.0.113.10                        ← identifies the MACHINE
       │ (2) connect to port 443
       ▼
┌──────────────────────────────────────────────────────────────┐
│ Machine 203.0.113.10                                         │
│                                                              │
│  :22   sshd            :443  BookNook app   :5432  postgres  │  ← PORT picks the PROCESS
│         │                     │                      │       │
│         │              ┌──────┴───────┐              │       │
│         │              │ listening    │  accept()    │       │
│         │              │ socket :443  ├──────┐       │       │
│         │              └──────────────┘      │       │       │
│         │                                    ▼       │       │
│         │        connected sockets (one per 4-tuple):│       │
│         │        ┌───────────────────────────────┐   │       │
│         │        │ (198.51.100.7:51234 → .10:443) │   │       │
│         │        │ (198.51.100.7:51235 → .10:443) │   │       │
│         │        │ (192.0.2.44  :51234 → .10:443) │   │       │
│         │        └───────────────────────────────┘   │       │
└──────────────────────────────────────────────────────┼───────┘
                                                       │
  bound to 0.0.0.0 → reachable externally   bound to 127.0.0.1 → local only


NAT: many private machines behind one public address

  192.168.1.5:51234 ┐
  192.168.1.6:49200 ┼─▶ ROUTER ─▶ 203.0.113.7:{51234, 49200, 60111} ─▶ internet
  192.168.1.9:60111 ┘   NAT table         ▲
                                          └── the SERVER sees ONE IP for all three
                                              (so never rate-limit by IP alone)
```

## 7. Detailed example — BookNook's network layout

BookNook moves into a cloud **VPC** (Virtual Private Cloud — a private, isolated network you control) at `10.0.0.0/16`, split into subnets:

| Subnet | CIDR | Contents | Public IP? | Reachable from internet? |
|---|---|---|---|---|
| Public | `10.0.1.0/24` | Load balancer | Yes — `203.0.113.10` | Yes, ports 80/443 only |
| Private app | `10.0.2.0/24` | App servers `10.0.2.11–13`, port 8000 | No | No — LB only |
| Private data | `10.0.3.0/24` | PostgreSQL `10.0.3.20:5432`, Redis `10.0.3.21:6379` | No | No — app subnet only |

The security posture is **structural**: the database has no public IP at all. Even a misconfigured firewall rule cannot expose it to the internet, because no route exists. Compare this with running PostgreSQL on a public VM bound to `0.0.0.0:5432` — then one bad rule is a data breach.

**DNS layout:**

| Name | Type | Points to | Purpose |
|---|---|---|---|
| `booknook.com` | A | `203.0.113.10` | The LB |
| `www.booknook.com` | CNAME | `booknook.com` | Alias |
| `api.booknook.com` | A | `203.0.113.10` | Same LB, routed by `Host` header |
| `cdn.booknook.com` | CNAME | `d1x2.cloudfront.net` | Images via CDN (Phase 2, Topic 14) |
| `db.booknook.internal` | A | `10.0.3.20` | **Private DNS** — resolves only inside the VPC |

That last row is important: app servers connect to `db.booknook.internal`, not to `10.0.3.20`. When the database fails over to a standby at `10.0.3.25`, you update one DNS record instead of redeploying every app server. The indirection from Section 2, used deliberately.

**A real incident this layout prevents.** A developer debugging locally adds `127.0.0.1 db.booknook.internal` to their hosts file and runs a local Postgres. Months later, a migration script run from their laptop silently targets the local database instead of staging — and reports success. Hosts-file overrides are invisible to everyone else; this is why they should never be used for anything that isn't throwaway.

**Second example for contrast — a video game client.** BookNook uses one TCP connection on a well-known port (443) and doesn't care about its own address. A peer-to-peer game needs the opposite: two players both behind NAT must connect *directly* for latency reasons, but neither can accept inbound connections. The solution is **STUN** (the client asks an external server "what public IP and port do I appear as?") plus **hole punching** (both peers send outbound packets simultaneously, each creating a NAT mapping the other can then use), falling back to **TURN** (relay everything through a server) when symmetric NAT defeats it. Roughly 10–20 % of connections (typical) fall back to relaying. This is the same addressing machinery, pushed to a case where NAT is the central obstacle rather than an invisible detail.

## 8. Code and configuration examples

Illustrative, not production-ready.

### Seeing the 4-tuple from both ends

```python
import socket

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

# "" is equivalent to 0.0.0.0 -- ALL interfaces. Use "127.0.0.1" to stay local-only.
server.bind(("", 8000))
server.listen(128)

# getsockname() on the LISTENING socket: the (IP, port) we claimed.
print("listening on", server.getsockname())        # ('0.0.0.0', 8000)

conn, peer = server.accept()
# Now the full 4-tuple is visible:
print("local  end:", conn.getsockname())           # ('10.0.2.11', 8000)   dest IP:port
print("remote end:", conn.getpeername())           # ('198.51.100.7', 51234) src IP:port
# NOTE: for a NATed client, getpeername() returns the NAT's PUBLIC address,
# not the client's real 192.168.x.x. Behind a load balancer it returns the LB's
# address -- which is why X-Forwarded-For exists (Lesson 5).
```

```python
client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
# We never choose a source port; the kernel assigns an ephemeral one on connect().
client.connect(("203.0.113.10", 443))
print("my ephemeral port:", client.getsockname())  # ('192.168.1.5', 51234)
```

### Name resolution, without connecting

```python
import socket

# Simple form: first address only, IPv4-biased.
print(socket.gethostbyname("booknook.com"))       # '203.0.113.10'

# The correct modern form: returns ALL addresses, both families, in the order
# the OS recommends you try them (this ordering is what Happy Eyeballs consumes).
for family, socktype, proto, canonname, sockaddr in socket.getaddrinfo(
        "booknook.com", 443, proto=socket.IPPROTO_TCP):
    print(family.name, sockaddr)
# AF_INET6 ('2001:db8::10', 443, 0, 0)
# AF_INET  ('203.0.113.10', 443)
```

### Inspecting ports and connections

```bash
# Which processes are listening, and on which address? (Linux)
#   -t TCP  -u UDP  -l listening  -n numeric (don't resolve names)  -p show process
$ sudo ss -tulnp
Netid State  Local Address:Port   Process
tcp   LISTEN 0.0.0.0:443          nginx        # all interfaces -> public
tcp   LISTEN 127.0.0.1:8000       python       # loopback only  -> safe behind nginx
tcp   LISTEN 10.0.3.20:5432       postgres     # private IP only -> not internet-reachable

# Count connections by state -- how you DIAGNOSE port exhaustion:
$ ss -tan | awk '{print $1}' | sort | uniq -c
  28101 TIME-WAIT      # ← near the ephemeral-port limit: you are about to fail
    412 ESTAB

# The configured ephemeral range:
$ cat /proc/sys/net/ipv4/ip_local_port_range
32768   60999           # => 28,232 ports
```

```powershell
# Windows equivalents
netstat -ano | Select-String "LISTENING"
Get-NetTCPConnection -State Listen | Select-Object LocalAddress,LocalPort,OwningProcess
```

### Connection pooling — the real fix for port exhaustion

```python
# BAD: a new TCP connection (and a new ephemeral port) per request.
def get_book_bad(isbn):
    conn = psycopg2.connect("host=db.booknook.internal dbname=booknook")
    ...
    conn.close()          # port now stuck in TIME_WAIT for ~60s

# GOOD: a small pool of long-lived connections, reused forever.
from psycopg2 import pool
db_pool = pool.ThreadedConnectionPool(
    minconn=5,
    maxconn=20,     # 20 ports total, regardless of request rate.
                    # Size this from Little's Law (Lesson 14), not by guessing.
    host="db.booknook.internal", dbname="booknook",
)

def get_book(isbn):
    conn = db_pool.getconn()
    try:
        ...
    finally:
        db_pool.putconn(conn)   # returned to the pool, NOT closed
```

```python
# Same principle for outbound HTTP. requests.get() per call = a new connection each time.
import requests
session = requests.Session()        # keeps connections alive and reuses them
adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=50)
session.mount("https://", adapter)
session.get("https://api.payments.example/charge")   # reuses a pooled connection
```

### Listening on a privileged port without running as root

```bash
# Ports < 1024 normally require root. Running your app as root is a bad idea.
# Grant just this one capability instead:
sudo setcap 'cap_net_bind_service=+ep' /usr/local/bin/booknook

# Or (more common): let nginx own 443 and proxy to an unprivileged app on 8000.
```

## 9. Numerical example

### Part A — Does BookNook need IPv6?

**Assumptions:** 40 % of mobile traffic in India is IPv6-only or IPv6-preferred (approximate, 2026); 60 % of BookNook's traffic is mobile; IPv6-only clients reach IPv4 services via carrier NAT64, adding roughly 15–30 ms.

```
Mobile traffic              = 60% of all traffic
IPv6-preferring mobile      = 60% × 40% = 24% of all traffic
Extra latency for them      ≈ 20 ms (midpoint) per connection setup
Weighted average penalty    = 0.24 × 20 ms = 4.8 ms on every request, averaged
```

Add an `AAAA` record and ~24 % of users skip a translation layer. The cost is one DNS record and making sure the LB has an IPv6 listener. Cheap, so do it — but notice the *justification is a number*, not "IPv6 is modern".

### Part B — Connection capacity of one server

**Assumptions:** Linux; each established TCP socket costs ~4 KB of kernel buffers minimum (typical, tunable); the server has 16 GB RAM, 12 GB usable for networking + app.

```
Theoretical 4-tuple limit for ONE listening port:
  source IPs × source ports = 2^32 × 2^16 ≈ 2.8 × 10^14
  → the "a server can only have 65,535 connections" claim is FALSE.
    65,535 limits OUTBOUND connections per destination, not inbound ones.

Practical limits:
  File descriptors:  ulimit -n must be raised (default 1024 → set 1,000,000)
  Memory:  12 GB / 4 KB per socket = 3,000,000 sockets (buffers only, no app data)
  Realistic with app state at ~40 KB/conn: 12 GB / 40 KB = 300,000 connections
```

So a single modern machine can hold **hundreds of thousands** of concurrent connections. Memory and application state are the constraint, not ports. (This matters directly for the chat system in Phase 5, Design 8.)

### Part C — Ephemeral port exhaustion, worked precisely

**Assumptions:** 3 app servers; sale-day peak 125 RPS total (Lesson 1) ≈ 42 RPS per server; each request makes 1 DB call + 2 Redis calls; `TIME_WAIT` = 60 s; ephemeral range = 28,232 ports.

**Without pooling:**
```
Outbound connections per second per server = 42 × 3 = 126
Ports held                                  = 126 × 60 = 7,560
Utilisation                                 = 7,560 / 28,232 = 27%
```
Survives today. Now 10× traffic (the "test against 10×" step you'll do in every Phase 5 design):
```
1,260 conn/s × 60 s = 75,600 ports needed vs 28,232 available = 268% → FAILURE
```
The service breaks at roughly **3.7× current peak**, with a symptom that looks nothing like "too much traffic".

**With pooling (20 DB + 20 Redis connections per server):**
```
Ports held = 40, constant, at ANY request rate.
Utilisation = 40 / 28,232 = 0.14%
```
Headroom improves by a factor of ~1,900. This is why connection pooling is non-negotiable, and why it belongs in your default architecture rather than in a later optimisation phase.

### Part D — Private address space planning

**Assumptions:** VPC `10.0.0.0/16`; three availability zones; each AZ needs a public and a private subnet; plan for 10× growth.

```
/16 total       = 65,536 addresses
Split into /20s = 2^(20-16) = 16 subnets of 2^(32-20) = 4,096 addresses each
Needed now      = 3 AZs × 2 subnet types = 6 subnets  → 6/16 = 37.5% allocated
Usable per /20  = 4,096 - 5 (AWS reserves 5 per subnet) = 4,091 instances

Can one private subnet hold 10× BookNook's app tier?
  Current: 3 app servers. 10× = 30. 30 / 4,091 = 0.7%. Yes, comfortably.
```

The failure mode to avoid: choosing `/24` subnets (251 usable), then discovering during a traffic spike that **autoscaling cannot launch instances because the subnet is full**. Address space is free — allocate generously up front, because re-addressing a live VPC is extremely painful.

## 10. Trade-offs

**Public IPs on servers**
- *For:* direct reachability, simple debugging, no NAT gateway cost.
- *Against:* every public IP is an attack surface that must be firewalled correctly, forever.
- *Operational cost:* cloud providers charge per idle static IP (~$3–4/month, typical) and for NAT gateway data processing.

**IPv6**
- *For:* no NAT, end-to-end addressing, better mobile performance, future-proof.
- *Against:* dual-stack doubles firewall rules, monitoring dashboards, and test matrices; partial IPv6 breakage causes slow, hard-to-diagnose failures.
- *Unnecessary complexity when:* a purely internal service inside one VPC — nobody benefits.

**Hostnames instead of IPs in configuration**
- *For:* failover and migration without redeploying; a single place to change.
- *Against:* adds a DNS dependency to every connection; stale caches cause confusing behaviour (Lesson 4).
- *Unnecessary complexity when:* a single fixed appliance that will never move.

**Non-standard ports (e.g. SSH on 2222)**
- *For:* eliminates most automated scanning noise in logs.
- *Against:* **not security** — a real attacker scans all ports in minutes. It also breaks defaults and confuses newcomers.
- *Unnecessary complexity when:* you have key-based auth and a firewall, which is the actual control.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| Nothing listening on the port | **Instant** "connection refused" | TCP RST; health check fails in <1 s | Supervisor restarts the process; LB removes the instance |
| Firewall silently drops packets | **Hang**, then timeout after 20–75 s | Timeout, not refusal — the slowness *is* the clue | Fix the security-group rule; distinguishing "refused" from "timed out" is the whole diagnosis |
| Bound to `127.0.0.1` instead of `0.0.0.0` | Works on the server, unreachable from anywhere else | `ss -tulnp` shows `127.0.0.1:8000`; health checks from the LB fail | Change the bind address and restart |
| Ephemeral port exhaustion | Service works ~60 s after restart, then fails; "cannot assign requested address" | `ss -tan \| grep -c TIME-WAIT` near the limit | Add connection pooling; widen the range; enable `tcp_tw_reuse` |
| NAT table entry expires on an idle connection | A long-lived WebSocket dies silently; the app thinks it's connected | No traffic for 2–5 min, then writes fail | TCP keepalive or application-level pings every 30–60 s |
| IPv6 `AAAA` record exists but the path is broken | Site feels slow (~250 ms extra) for IPv6 users only | Latency percentiles split by address family | Happy Eyeballs masks it; fix the route or remove the `AAAA` record |
| Stale `/etc/hosts` entry | One machine behaves differently from all others | Nothing automated catches it — pure human debugging | Remove the entry; ban hosts-file overrides outside local dev |
| Two processes compete for one port | The second fails to start: "address already in use" | Startup crash loop | Kill the stale process; use `SO_REUSEADDR` only where appropriate |
| Rate limiting by public IP | An entire office or ISP is throttled by one abuser | Support tickets clustered by organisation | Rate-limit by user ID or API key; treat IP as a weak signal only |
| Database bound to `0.0.0.0` with a weak password | Nothing visible — data is quietly exfiltrated | External scanners; breach notification; often discovered months later | Bind to a private IP, enforce security groups, rotate credentials |

## 12. Alternatives

| Problem | Approach | Pros | Cons |
|---|---|---|---|
| Address scarcity | NAT (IPv4) | Works today, universal | Breaks inbound connections, shared IPs, stateful middlebox |
| | IPv6 | Abundant, end-to-end | Incomplete adoption, dual-stack overhead |
| | CGNAT (carrier NAT) | Lets ISPs serve many users on few IPs | Thousands of users per IP; IP-based blocking becomes useless |
| Finding a service | Hardcoded IP | Zero lookup latency | Cannot move anything, ever |
| | DNS name | Human-readable, flexible, universal | Cache staleness, TTL management |
| | Service discovery (Consul, Kubernetes Services) | Fast, health-aware, instant updates | More infrastructure (Phase 4, Topic 24) |
| | Service mesh (Envoy sidecar) | Discovery + mTLS + retries + telemetry | Significant operational complexity |
| Reaching a peer behind NAT | Port forwarding | Simple | Manual, impossible at scale |
| | STUN + hole punching | Direct peer path, low latency | Fails with symmetric NAT (~10–20 %) |
| | TURN relay | Always works | You pay for all the bandwidth; adds latency |
| Identifying a client | Public IP | Free, always available | Shared by NAT; changes; spoofable; a privacy issue |
| | Session ID / user ID | Accurate | Requires auth (Lesson 2) |

## 13. When to use these concepts deliberately

1. **Designing a VPC / network topology.** Deciding which tiers get public IPs is a security decision made in address space, not in firewall rules. BookNook's database is unreachable *by construction*.
2. **Diagnosing connectivity.** "Refused vs timed out" instantly separates "wrong port / process down" from "firewall or routing". This single distinction resolves a large fraction of production incidents.
3. **Capacity planning for connection-heavy systems.** Chat, streaming, IoT, and long-polling systems are limited by sockets and memory, not CPU — you must be able to compute the limits (Section 9, Part B).
4. **Choosing a rate-limiting key.** Understanding NAT tells you why per-IP limits punish offices and why per-user or per-API-key limits are correct (Phase 5, Design 3).
5. **Planning migrations.** Private DNS names as a layer of indirection let you move a database without touching application config.

## 14. When *not* to worry about these details

1. **Fully managed platforms.** On Heroku, Vercel, or Cloud Run, you bind to `$PORT` and the platform handles addressing, TLS, and load balancing. Hand-managing subnets there is wasted effort.
2. **Early-stage prototypes.** A single VM with a public IP and a firewall is fine for the first users. Designing a three-AZ VPC for a product with 50 users is premature.
3. **Inside a single container network.** Kubernetes gives every pod an IP and resolves services by name. You should understand the model, but you rarely assign addresses by hand.
4. **Application-level feature work.** Writing a checkout flow requires no knowledge of ephemeral ports — until the day it fails at 2 a.m., which is why the knowledge is worth having even when unused.

## 15. Common mistakes

**Beginner mistakes**
- Binding to `127.0.0.1` in a container, then wondering why nothing can reach it (containers need `0.0.0.0`).
- Believing a server can only handle 65,535 connections. The limit is per-destination for *outbound*; inbound is bounded by memory and file descriptors.
- Hardcoding IP addresses in config files, making every migration a code change.
- Assuming an IP identifies a person — it's shared by NAT and changes constantly.
- Thinking private IPs are "secure". They're unroutable from outside, which is useful, but anything already inside the network can reach them. Authenticate anyway.
- Confusing `localhost` (a name, resolved via the hosts file) with `127.0.0.1` (an address), then getting bitten by the `::1` vs `127.0.0.1` mismatch.
- Running the app as root just to bind port 80.
- Using `ping` to test whether a *service* is up. Ping tests ICMP reachability of a host; it says nothing about whether a process is listening on a port. Use `nc -zv host port` or an HTTP health check.

**Experienced-engineer mistakes**
- No connection pooling on an outbound dependency, producing port exhaustion that only appears under sustained load — and never in staging.
- Allocating `/24` subnets in a VPC, then being unable to autoscale during a traffic spike because the subnet has no free addresses.
- Logging `getpeername()` as the client IP when behind a load balancer, so every request appears to come from the LB. You need `X-Forwarded-For` — and you must only trust it from your own proxy, or clients can forge it.
- Adding an `AAAA` record without end-to-end IPv6 testing, silently adding ~250 ms (Happy Eyeballs fallback) for a slice of users.
- Relying on long-lived idle TCP connections across NAT or a cloud load balancer without keepalives; idle timeouts (typically 350 s on AWS NLB) kill them silently.
- Treating non-standard ports as a security control instead of defence-in-depth noise reduction.
- Forgetting that security groups and NACLs are stateful vs stateless respectively, producing rules that work outbound but silently drop the return traffic.

## 16. Interview perspective

Addressing rarely gets a dedicated question, but it's assumed knowledge, and demonstrating it cheaply signals depth.

**How to use it:**
- **When you draw the architecture, say which tiers are public.** "The LB is in a public subnet; app servers and databases are in private subnets with no route to the internet, egress via a NAT gateway." Two sentences, large credibility payoff.
- **When you draw a connection to a database, say "connection pool".** Then, if pressed, explain port exhaustion with the 60-second `TIME_WAIT` arithmetic.
- **For chat/streaming designs (Phase 5, Design 8), size by connections.** "Each WebSocket costs ~40 KB of kernel plus app state, so a 16 GB machine holds roughly 300,000 — I'd plan for 100,000 per node with headroom." That calculation is the difference between a hand-wave and a design.
- **When discussing rate limiting, pre-empt the NAT problem.** "Per-IP limits would throttle a whole office behind one NAT, so I'd key on user ID, with IP as a secondary signal for unauthenticated endpoints."
- **Use DNS as the migration tool.** "App servers connect to `db.booknook.internal`; failover updates one record rather than redeploying the fleet."

**Red flags interviewers watch for:**
- Putting a database in a public subnet.
- "A server can only handle 65k connections."
- No connection pooling anywhere in a high-throughput design.
- Using client IP as a user identity.
- Being unable to explain the difference between *connection refused* and *connection timed out* — it's the single most diagnostic question in networking.

## 17. Knowledge check

Write your answers down before continuing.

**Conceptual**

1. A BookNook engineer claims: "our load balancer listens on port 443, so we can have at most 65,535 simultaneous users." Explain precisely why this is wrong, state what the 65,535 limit actually constrains, and name the two resources that really bound inbound connection count.

2. You deploy a new service. From the server itself, `curl http://localhost:8000/health` returns 200. From another machine in the same subnet, the same request hangs for 30 seconds and then times out. From a third machine it returns "connection refused" immediately. Explain what each of the three outcomes tells you, and give the most likely root cause for each of the two failures.

3. BookNook rate-limits logins to 5 attempts per IP per minute. Support reports that an entire corporate customer — about 800 employees — is locked out, while a credential-stuffing attack from a botnet is going completely undetected. Explain both halves of this outcome using the concepts in this lesson, and state what the rate-limit key should be instead.

**Practical scenario**

4. BookNook's app servers each open a fresh PostgreSQL connection per request and close it afterwards. Everything is fine in staging (5 RPS). In production, every app server serves traffic normally for about a minute after each deploy, then begins throwing "cannot assign requested address", recovers briefly, and fails again. The database's own metrics show low CPU and plenty of spare connections throughout.
   Diagnose the problem precisely, explain *why the 60-second pattern appears*, state the primary fix and two secondary mitigations, and explain why this never reproduced in staging.

**Calculation**

5. BookNook adds a live "readers online now" feature using WebSockets. Assumptions: 250,000 concurrent connected users at peak; each connection costs 6 KB of kernel socket buffers and 34 KB of application state; each app server has 32 GB RAM, of which 24 GB is usable for connections; each connection also sends a 60-byte keepalive ping every 30 seconds.
   (a) How many connections fit on one server, and how many servers are needed at peak?
   (b) What is the aggregate keepalive traffic in packets per second and bytes per second across the whole system?
   (c) Each app server also holds a pool of 25 connections to Redis. If a server is restarted, how long (given a 60-second `TIME_WAIT`) before those ports are fully reclaimed, and why is this not a problem here whereas it *was* a problem in question 4? Show every step and state your assumptions.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 4.

---

**Progress checklist**

Phase 1:
`[x] 1. Client-server architecture`
`[x] 2. Stateful versus stateless servers`
`[x] 3. IP addresses, ports, sockets, and domain names`
`[ ] 4. DNS resolution and DNS caching` ← next
`[ ] 5–17` not started
Phases 2–5: not started.

**NEXT LESSON: Phase 1, Topic 4 — DNS resolution and DNS caching**

---

# Lesson 4 — DNS Resolution and DNS Caching

*Phase 1, Topic 4*

## 1. Simple definition

**DNS** (the Domain Name System) is a globally distributed database whose job is to answer questions like *"what IP address is `booknook.com`?"*

Two framings, both useful:
- **The phone-book framing:** you know a name, DNS gives you the number.
- **The accurate framing:** DNS is a hierarchical, cached, eventually-consistent **key-value store** optimised overwhelmingly for reads, operated by thousands of independent organisations, with no single machine holding the whole dataset.

The second framing is the one that makes DNS's behaviour make sense. Every strange thing about DNS — stale records, "propagation", the 60-second migration dance — follows from "distributed, cached, eventually consistent".

Terms defined before use:
- **Resolver**: software that *asks* DNS questions.
- **Nameserver**: software that *answers* them.
- **Record**: one entry in the database, e.g. `booknook.com → A → 203.0.113.10`.
- **Zone**: the set of records one organisation is responsible for (`booknook.com` and everything under it).
- **Authoritative**: a nameserver that holds the real, original records for a zone — not a cached copy.

## 2. Why it exists

Before DNS (until 1983), the entire internet's name-to-address mapping lived in a **single text file**, `HOSTS.TXT`, maintained at Stanford. Administrators downloaded it by FTP, periodically. With a few hundred machines this worked. It then failed in four ways simultaneously:

1. **It didn't scale in size.** A file listing every host on the internet is now ~billions of entries.
2. **It didn't scale in update rate.** Every change required a central edit and a global re-download. Updates took days.
3. **It didn't scale organisationally.** One team became a bottleneck for every name change on Earth, and had to arbitrate naming disputes.
4. **It had no delegation.** Stanford had to know about every machine at MIT.

DNS solves all four with **hierarchy and delegation**: the root servers don't know about `booknook.com`; they only know who runs `.com`. The `.com` servers don't know BookNook's IP; they only know which nameservers BookNook designated. Each organisation controls its own zone. No single entity holds or must approve the whole dataset.

And it solves the resulting latency problem with **aggressive caching at every layer**, which is where all the operational subtlety comes from.

**Without DNS**, you'd have what Lesson 3 described: hardcoded IPs everywhere, no ability to move servers, no load balancing by name, no failover, no CDNs.

## 3. Real-world analogy

**Finding someone's desk in a multinational corporation.**

You want Priya in Legal at the Mumbai office of a company. You don't call a global directory that lists all 200,000 employees. Instead:
1. You ask the **global switchboard**: "which country?" → "India office, call this number."
2. You ask the **India office**: "which department?" → "Legal, ask their reception."
3. You ask **Legal reception**: "Priya?" → "Desk 4-12, extension 8891."

Three hops, each answering only what it owns, each delegating down. And once you know the extension, **you write it on a sticky note** and skip all three hops next time — until the note goes stale and you call a disconnected number.

| Mapping | DNS equivalent |
|---|---|
| Global switchboard | Root nameservers |
| Country office | TLD nameservers (`.com`, `.in`) |
| Legal reception | Authoritative nameservers for `booknook.com` |
| Your sticky note | DNS cache |
| "This note is good for a week" | TTL |
| Your assistant who makes the calls for you | Recursive resolver |

**Where the analogy breaks down:**

| Corporate directory | DNS |
|---|---|
| Reception knows if Priya moved yesterday | Your cached answer can be **hours stale** and there is **no way to invalidate it** — you can only wait for the TTL |
| One receptionist per office | 13 root *identities* served by 1,500+ physical servers worldwide via **anycast** (Section 4) |
| The answer is the same for everyone | DNS can give **different answers to different askers** based on geography, load, or health |
| Calling costs you a minute | A full uncached resolution costs 20–120 ms (typical), and caching makes 90 %+ of lookups cost ~0 ms |
| The directory is trusted | DNS answers are **unauthenticated by default** — an attacker on the path can forge them (hence DNSSEC and DoH) |

## 4. Detailed technical explanation

### The actors

| Actor | Role | Who runs it |
|---|---|---|
| **Stub resolver** | The tiny client in your OS/browser. Asks one question, accepts one answer. Does no legwork. | Your operating system |
| **Recursive resolver** | Does the actual multi-step lookup on your behalf and caches the results. The workhorse. | Your ISP, or `8.8.8.8` (Google), `1.1.1.1` (Cloudflare), `9.9.9.9` (Quad9) |
| **Root nameservers** | Know only which servers run each TLD | 12 organisations, 13 identities (`a.root-servers.net` … `m`) |
| **TLD nameservers** | Know which nameservers are authoritative for each domain under them | Registry operators (Verisign for `.com`) |
| **Authoritative nameservers** | Hold the real records for a zone | You, or your DNS provider (Route 53, Cloudflare, NS1) |

The crucial asymmetry: the stub resolver asks a **recursive** query ("give me the final answer"); the recursive resolver makes **iterative** queries ("tell me who to ask next").

### Full resolution, step by step

Cold cache everywhere, looking up `www.booknook.com`:

```
Step 0. Browser cache?           miss
        OS cache?                miss
        /etc/hosts?              no entry
Step 1. Stub → Recursive resolver (8.8.8.8:53/UDP):
          "A record for www.booknook.com?"  [recursive query]

Step 2. Resolver → a ROOT server:
          "A record for www.booknook.com?"
        Root: "I don't know, but .com is served by a.gtld-servers.net (192.5.6.30)"
          [this is a REFERRAL, not an answer]

Step 3. Resolver → .com TLD server:
          "A record for www.booknook.com?"
        TLD: "I don't know, but booknook.com is served by
              ns1.booknook.com (203.0.113.53) and ns2.booknook.com"
          [another referral; the IP alongside the name is a GLUE record]

Step 4. Resolver → ns1.booknook.com (AUTHORITATIVE):
          "A record for www.booknook.com?"
        ns1: "www.booknook.com. 300 IN A 203.0.113.10"   ← AUTHORITATIVE ANSWER

Step 5. Resolver caches it for 300 seconds (the TTL) and returns it to the stub.
Step 6. The OS and browser cache it too.
Step 7. Browser opens a TCP connection to 203.0.113.10:443 (Lesson 3).
```

**Why is step 2 possible at all?** Every resolver ships with a hardcoded list of the 13 root server addresses (the "root hints" file). That's the one piece of bootstrap knowledge; everything else is discovered.

**Glue records** deserve a note, because they resolve a chicken-and-egg problem. If `booknook.com`'s nameserver is *named* `ns1.booknook.com`, you cannot resolve that name without first asking `ns1.booknook.com`. So the `.com` servers store the **IP** of `ns1.booknook.com` alongside the delegation. That IP is "glue".

### Record types

| Type | Meaning | Example |
|---|---|---|
| **A** | Name → IPv4 address | `booknook.com. 300 IN A 203.0.113.10` |
| **AAAA** | Name → IPv6 address | `booknook.com. 300 IN AAAA 2001:db8::10` |
| **CNAME** | Name → another *name* (an alias) | `www.booknook.com. 300 IN CNAME booknook.com.` |
| **NS** | Which nameservers are authoritative for a zone | `booknook.com. 172800 IN NS ns1.booknook.com.` |
| **MX** | Where to deliver email, with a priority number | `booknook.com. 3600 IN MX 10 mail.booknook.com.` |
| **TXT** | Arbitrary text. Used for domain-ownership proof, SPF, DKIM, DMARC | `booknook.com. 3600 IN TXT "v=spf1 include:_spf.google.com ~all"` |
| **SOA** | "Start of Authority" — zone metadata: primary NS, admin email, serial, refresh/retry/expire, **minimum TTL (used for negative caching)** | one per zone, mandatory |
| **PTR** | IP → name (reverse DNS). Used by mail servers to check sender legitimacy | `10.113.0.203.in-addr.arpa. IN PTR booknook.com.` |
| **SRV** | Service location: protocol, port, weight, priority | `_sip._tcp.example.com. IN SRV 10 60 5060 sipserver.example.com.` |
| **CAA** | Which certificate authorities may issue certs for this domain | `booknook.com. IN CAA 0 issue "letsencrypt.org"` |

**The CNAME rules that bite everyone:**
1. **A CNAME cannot coexist with any other record at the same name.** So you cannot have `booknook.com CNAME ...` *and* `booknook.com MX ...`.
2. **Therefore the zone apex (`booknook.com` with no subdomain) cannot be a CNAME**, because the apex *must* have `SOA` and `NS` records. This is why you can CNAME `www.booknook.com` to a CDN but not `booknook.com` itself.
3. Providers work around rule 2 with proprietary types — Route 53 `ALIAS`, Cloudflare "CNAME flattening" — which resolve the target server-side and return an `A` record. They look like CNAMEs but aren't.
4. **CNAME chains cost extra round trips.** `www → cdn-alias → cloudfront.net → A record` is three resolutions. Keep chains to one hop.

### TTL and the caching layers

Every record carries a **TTL** (time to live) in seconds: "you may cache this answer for this long." When it expires, the cache discards it and asks again.

A single lookup may be answered by any of these, checked in order:

| Layer | Typical lifetime | Notes |
|---|---|---|
| Browser DNS cache | 60 s (Chrome, approximate) | Often **ignores the record's TTL** and applies its own |
| OS resolver cache | Honours TTL | `systemd-resolved`, `dnsmasq`, Windows DNS Client |
| Application/runtime cache | **Java historically cached forever** (`networkaddress.cache.ttl=-1` with a security manager) | A notorious source of failover bugs |
| Local router | Honours TTL, sometimes badly | Consumer routers are frequently buggy here |
| ISP / public recursive resolver | Honours TTL, but **many clamp a minimum** (e.g. ignore TTLs below 30 s) | This is why very low TTLs don't always work |
| Authoritative server | n/a — the source of truth | |

**Cache-hit rates are extremely high.** Across the internet, 80–95 % of DNS queries (typical) are answered from a cache without ever reaching an authoritative server. DNS is viable *only* because of this.

### Negative caching

If a name doesn't exist, the authoritative server returns **NXDOMAIN**. That negative answer is **also cached**, for a duration taken from the zone's `SOA` minimum TTL field (commonly 300–3600 s).

The practical consequence catches people out constantly: if you look up a name *before* you create it, the NXDOMAIN gets cached, and the record appears not to work for the next hour even though you created it correctly. **Never test a DNS name before you've created it** — or set a low SOA minimum first.

### TTL: the central trade-off

| Low TTL (30–60 s) | High TTL (3600–86400 s) |
|---|---|
| Fast failover and migration | Slow failover — stale answers for hours |
| More queries to your authoritative servers → higher cost | Far fewer queries → cheap, resilient |
| Slightly higher average client latency (more cache misses) | Better client latency |
| Bigger blast radius if your DNS provider goes down (caches drain fast) | Cached answers keep working through a DNS outage |

**The standard operational pattern — "the TTL dance":**

```
T-48h : lower the TTL from 3600s to 60s.
        You must do this at least one OLD TTL in advance, because caches
        populated before the change still hold the old 3600s value.
T-0   : change the A record to the new IP.
T+2min: ~all caches have expired; traffic moves.
T+24h : raise the TTL back to 3600s.
```

Forgetting the T-48h step is why migrations drag on for hours.

### "DNS propagation" is a myth

You will hear "DNS changes take 24–48 hours to propagate." **Nothing propagates.** No one pushes anything anywhere. Your authoritative record changes *instantly*. What you're waiting for is **caches around the world expiring on their own schedules**, which is bounded by the TTL that was in effect *when each cache stored its copy*.

Why 24–48 hours became folklore:
- Default TTLs used to be 86,400 s (24 h).
- Some resolvers and routers ignore TTLs and cache for fixed long periods.
- Registrar-level **NS** changes genuinely are slow, because TLD servers hold NS records with ~48-hour TTLs.

So: changing an **A record** with a 60 s TTL is effectively immediate. Changing your **nameservers** really can take two days. These are completely different operations and conflating them causes a lot of confusion.

### DNS as a load balancer

Return multiple A records and clients will spread across them:

```
booknook.com. 60 IN A 203.0.113.10
booknook.com. 60 IN A 203.0.113.11
booknook.com. 60 IN A 203.0.113.12
```

Most resolvers rotate the order, so clients roughly round-robin.

**Why this is a weak load balancer** — know these four points, they're a common interview question:

| Weakness | Explanation |
|---|---|
| **No health awareness** (by default) | If `.11` dies, DNS keeps handing it out. ~1/3 of users get errors until you remove the record *and* caches expire. Managed providers add health checks, but then failover is still TTL-bounded. |
| **No load awareness** | DNS cannot know that `.10` has 500 connections and `.12` has 5. A real LB can (Phase 2, Topic 6). |
| **Caching defeats distribution** | One ISP resolver caches one answer and hands it to 50,000 subscribers. Distribution is per-*resolver*, not per-user. |
| **Clients misbehave** | Browsers pin an IP for the session; some libraries use only the first record, forever. |

So DNS round-robin is a coarse **first tier** of distribution (and genuinely useful for spreading across *regions* or *load balancers*), not a substitute for a real load balancer.

### Anycast

**Anycast** means announcing the *same IP address* from many physical locations, and letting internet routing (BGP) deliver each client to the topologically nearest one.

```
          "1.1.1.1"                "1.1.1.1"               "1.1.1.1"
          Mumbai POP               Frankfurt POP           São Paulo POP
               ▲                        ▲                       ▲
        Indian users             European users          Brazilian users
         (~5 ms)                   (~8 ms)                 (~6 ms)
```

This is how 13 root server *identities* become 1,500+ actual machines, and how public resolvers achieve single-digit-millisecond response times globally. It also absorbs DDoS traffic by splitting it across sites. Anycast underpins CDNs too (Phase 2, Topic 14).

### GeoDNS

**GeoDNS** returns *different records* depending on where the query came from:

```
Query from India   → booknook.com → 13.234.x.x  (Mumbai region)
Query from Germany → booknook.com → 3.120.x.x   (Frankfurt region)
```

Useful for latency and data residency. Two caveats:
1. The provider sees the **resolver's** IP, not the user's. A Mumbai user on a resolver in Singapore gets Singapore's answer. The **EDNS Client Subnet** extension passes a truncated client subnet to fix this — at a measurable privacy cost.
2. It interacts badly with caching: a shared corporate resolver caches one region's answer for everyone.

Anycast generally beats GeoDNS for latency (routing decides per-packet, no caching involved), but GeoDNS is necessary when the *endpoints must differ* — e.g. EU user data must reach EU servers.

### Security, briefly

- **DNS is unauthenticated by default.** An attacker on the path, or one who poisons a resolver's cache, can send users to the wrong IP. TLS (Lesson 7) limits the damage — the attacker can't present a valid certificate — but can still enable denial of service and phishing.
- **DNSSEC** cryptographically signs records, creating a chain of trust from the root. Correct, but complex, and a mis-rotated key takes your domain **completely offline**. Adoption remains partial.
- **DoH / DoT** (DNS over HTTPS / over TLS) encrypt the query between stub and recursive resolver. They stop your ISP from reading and tampering with your lookups; they do nothing about the resolver itself.
- **CAA records** limit which certificate authorities may issue certificates for your domain — cheap and worth setting.

### Transport

DNS traditionally runs over **UDP port 53** — one small packet out, one back, no handshake, minimal latency (Lesson 8). If a response exceeds 512 bytes (or the EDNS0-negotiated size, commonly 1232–4096 bytes), the server sets a truncation flag and the client **retries over TCP port 53**. Firewalls that allow UDP/53 but block TCP/53 cause baffling intermittent failures for large responses — a classic misconfiguration.

## 5. Request / data flow

A BookNook user in Mumbai, cold caches, loading `https://www.booknook.com`:

1. Browser checks its own cache (~60 s entries). Miss.
2. OS stub resolver checks `/etc/hosts`. No entry.
3. OS checks its cache. Miss.
4. Stub sends a UDP query to the configured resolver (`1.1.1.1:53`), flag RD ("recursion desired") set. Typical RTT to an anycast resolver: **2–10 ms**.
5. Resolver checks its cache. Miss (assume fully cold).
6. Resolver → root server (anycast, ~5 ms): referral to `.com` nameservers.
7. Resolver → `.com` TLD server (~20 ms): referral to `ns1.booknook.com` + glue IP.
8. Resolver → `ns1.booknook.com` (~30 ms): `www.booknook.com. 300 IN CNAME booknook.com.`
9. The answer is a CNAME, so the resolver must resolve **that**: it asks again for `booknook.com` → `A 203.0.113.10` (~30 ms, often returned in the same response if the server is authoritative for both).
10. Resolver caches: the CNAME for 300 s, the A record for 300 s, the NS referrals for 172,800 s.
11. Resolver returns the full chain to the stub.
12. OS caches it; browser caches it.
13. Browser connects to `203.0.113.10:443` (Lesson 3, step 7 onward).
14. **The user's next 1,000 requests over the next 5 minutes do steps 1 only.** Zero network cost.
15. At T+300 s the entry expires. The next lookup goes to the resolver — but the resolver still has the **NS** records cached for 48 hours, so it skips the root and TLD and asks `ns1.booknook.com` directly. That's ~30 ms, not ~85 ms.

Step 15 is the subtlety worth remembering: **warm-cache resolution is much cheaper than cold**, because the upper hierarchy has very long TTLs and is essentially always cached.

## 6. ASCII diagram

```
FULL RECURSIVE RESOLUTION (cold cache)

 Browser          Stub          Recursive Resolver         The DNS hierarchy
   │               │             (1.1.1.1, caching)
   │ www.booknook.com?           │
   ├──────────────▶│             │
   │               ├────────────▶│   ① "www.booknook.com A?"
   │               │  recursive  ├─────────────────────────▶ ┌───────────────┐
   │               │   query     │                           │ ROOT  (.)     │
   │               │             │◀───── "ask .com at ──────┤ 13 identities │
   │               │             │        192.5.6.30"        │ 1500+ servers │
   │               │             │                           │ via ANYCAST   │
   │               │             │   ② same question         └───────────────┘
   │               │             ├─────────────────────────▶ ┌───────────────┐
   │               │             │                           │ .com TLD      │
   │               │             │◀── "ask ns1.booknook.com ─┤ (Verisign)    │
   │               │             │     at 203.0.113.53"      └───────────────┘
   │               │             │     (+ GLUE record)
   │               │             │   ③ same question         ┌───────────────┐
   │               │             ├─────────────────────────▶ │ AUTHORITATIVE │
   │               │             │                           │ ns1.booknook  │
   │               │             │◀── "A 203.0.113.10        │ (yours)       │
   │               │             │     TTL 300"              └───────────────┘
   │               │◀────────────┤  cache for 300s
   │◀──────────────┤  cache       │
   │  203.0.113.10 │
   ▼
 connect() to 203.0.113.10:443        Typical cold total: 60-120 ms
                                      Typical warm total: 0 ms (local cache)


CACHE LAYERS — a query stops at the FIRST hit

 browser(60s) → OS(TTL) → router(TTL) → ISP resolver(TTL) → authoritative
     ~0ms        ~0ms       ~1ms          ~5-20ms            ~30-80ms
     └─────── 80-95% of lookups stop somewhere in here ──────┘

 ⚠ You cannot invalidate ANY of these. You can only wait out the TTL.


THE TTL DANCE (migrating 203.0.113.10 → 198.51.100.20)

 TTL=3600 ───────┐
                 │ T-48h: set TTL=60   (must be ≥1 old TTL before the change,
                 ▼                      or caches still hold the 3600s value)
 TTL=60 ─────────┐
                 │ T-0: change the A record
                 ▼
 T+60s: caches expire ──▶ traffic moves to 198.51.100.20
                 │
                 │ T+24h: raise TTL back to 3600
                 ▼
```

## 7. Detailed example — BookNook's zone

BookNook's complete DNS configuration, with the reasoning for every TTL:

| Name | Type | Value | TTL | Why this TTL |
|---|---|---|---|---|
| `booknook.com` | A | `203.0.113.10` | 300 | Points at the LB. 5 min balances failover speed against query volume. |
| `booknook.com` | AAAA | `2001:db8::10` | 300 | IPv6 path (Lesson 3, Part A) |
| `booknook.com` | NS | `ns1/ns2.booknook.com` | 172800 | Changes almost never; long TTL = resilience |
| `booknook.com` | SOA | `ns1... admin... 2026100801 ...` | 3600 | Minimum field = 300 → **negative caching** is 5 min |
| `booknook.com` | MX | `10 aspmx.l.google.com` | 3600 | Email routing is stable |
| `booknook.com` | TXT | `v=spf1 include:_spf.google.com ~all` | 3600 | Anti-spoofing for email |
| `booknook.com` | CAA | `0 issue "letsencrypt.org"` | 3600 | Only Let's Encrypt may issue our certs |
| `www.booknook.com` | CNAME | `booknook.com.` | 300 | Alias; one hop only |
| `api.booknook.com` | A | `203.0.113.10` | 300 | Same LB; routed by `Host` header (Lesson 5) |
| `cdn.booknook.com` | CNAME | `d1x2y3.cloudfront.net.` | 300 | CDN controls its own endpoints (Phase 2, Topic 14) |
| `status.booknook.com` | CNAME | `statuspage.io.` | 3600 | **Deliberately on third-party infrastructure** — the status page must survive our outage |
| `db.booknook.internal` | A | `10.0.3.20` | 60 | Private zone; short TTL for fast DB failover |

**Four design decisions worth calling out:**

1. **`status.booknook.com` is hosted elsewhere.** If BookNook's own infrastructure is down, a status page served from it is also down — exactly when customers need it. This is a dependency-isolation decision expressed in DNS.

2. **`db.booknook.internal` has a 60-second TTL.** When PostgreSQL fails over to the standby at `10.0.3.25`, we update one record and app servers reconnect within a minute. But this only works if the application **re-resolves**. A JVM caching DNS forever (Section 4) will keep hammering the dead IP indefinitely — a genuinely famous production trap.

3. **Two nameservers minimum**, ideally with two *different providers*. In October 2016 the Dyn DDoS attack took down Twitter, Spotify, GitHub and Reddit — not because their servers failed, but because their single DNS provider did. Secondary DNS with a different vendor is cheap insurance; the registrar NS records simply list four nameservers across two providers.

4. **The apex is an A record, not a CNAME**, because of the CNAME-coexistence rule (Section 4). To put the apex behind a CDN, BookNook would need Route 53 `ALIAS` or Cloudflare CNAME flattening.

**A real incident this zone would have prevented.** A BookNook engineer migrates the LB to a new IP on a Friday afternoon. The record's TTL has been 3600 s for months. They change the A record at 16:00 and declare success because `dig @ns1.booknook.com` shows the new value. Customers keep hitting the old IP until 17:00, and some consumer routers hold it until Saturday. The record was right the whole time — the TTL dance was skipped.

**Second example for contrast — a CDN's DNS.** BookNook's zone is a handful of near-static records. A CDN's authoritative DNS is a *live traffic-engineering control plane*: TTLs of 20–30 s, answers computed per query from real-time health, load, and the client's network, serving millions of queries per second from anycast POPs. Same protocol, radically different use: BookNook uses DNS as a **name directory**; a CDN uses it as a **routing engine**. Recognising which one you're building determines whether a 30-second TTL is reckless or essential.

## 8. Code and configuration examples

Illustrative, not production-ready.

### Reading `dig` output properly

```bash
$ dig www.booknook.com

; <<>> DiG 9.18.1 <<>> www.booknook.com
;; global options: +cmd
;; Got answer:
;; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 42317
;;                             ↑ NOERROR = found. NXDOMAIN = name doesn't exist.
;;                               SERVFAIL = the resolver broke or DNSSEC failed.
;; flags: qr rd ra; QUERY: 1, ANSWER: 2, AUTHORITY: 0, ADDITIONAL: 1
;;        ↑qr=response  rd=recursion desired  ra=recursion available
;;        NOTE: no "aa" flag -> this came from a CACHE, not an authoritative server

;; QUESTION SECTION:
;www.booknook.com.              IN      A

;; ANSWER SECTION:
www.booknook.com.       287     IN      CNAME   booknook.com.
booknook.com.           287     IN      A       203.0.113.10
;                        ↑ 287, not 300: this answer has been cached for 13 seconds.
;                          A COUNTDOWN. Run it again and it will be lower.
;                          This is the single most useful field in dig output.

;; Query time: 3 msec          ← 3 ms = cache hit. 60-120 ms = full recursion.
;; SERVER: 1.1.1.1#53(1.1.1.1) ← which resolver answered
```

```bash
# Ask the AUTHORITATIVE server directly, bypassing every cache.
# Use this to verify a change is live, BEFORE blaming "propagation".
$ dig @ns1.booknook.com booknook.com +norecurse
;; flags: qr aa;                        ← "aa" = Authoritative Answer. The truth.
booknook.com.    300    IN   A   198.51.100.20      ← new IP is live at the source

# Meanwhile, what the world still sees:
$ dig @8.8.8.8 booknook.com +short
203.0.113.10                             ← stale. Not broken -- just cached.

# Watch the full delegation chain, hop by hop (root → TLD → authoritative):
$ dig +trace booknook.com

# Which nameservers are authoritative?
$ dig booknook.com NS +short
ns1.booknook.com.
ns2.booknook.com.

# Check negative caching behaviour (SOA minimum = last field):
$ dig booknook.com SOA +short
ns1.booknook.com. admin.booknook.com. 2026100801 7200 3600 1209600 300
#                                      serial    refresh retry  expire  minimum(=NEG TTL)
```

```powershell
# Windows equivalents
Resolve-DnsName www.booknook.com
Resolve-DnsName booknook.com -Server 8.8.8.8 -Type A
Get-DnsClientCache | Where-Object { $_.Entry -like "*booknook*" }   # inspect OS cache
Clear-DnsClientCache                                                # flush OS cache
```

### A zone file

```dns
; BookNook zone file. Illustrates the record set; real zones are usually
; managed through a provider's API or Terraform rather than edited by hand.
$ORIGIN booknook.com.
$TTL 3600                       ; default TTL for records that don't specify one

; SOA: zone metadata. The LAST field (300) is the NEGATIVE caching TTL --
; how long NXDOMAIN answers for this zone may be cached.
@   IN  SOA ns1.booknook.com. admin.booknook.com. (
            2026100801  ; serial -- MUST increase on every change, or secondaries
                        ; will not pick up the update (date-based format: YYYYMMDDnn)
            7200        ; refresh -- how often a secondary checks the primary
            3600        ; retry   -- wait before retrying a failed refresh
            1209600     ; expire  -- secondary stops answering after 14d of no contact
            300 )       ; minimum -- NEGATIVE cache TTL

; Delegation: who is authoritative. Long TTL; these change almost never.
@       172800  IN  NS      ns1.booknook.com.
@       172800  IN  NS      ns2.booknook.com.

; The apex MUST be A/AAAA, never CNAME (CNAME cannot coexist with SOA/NS/MX).
@       300     IN  A       203.0.113.10
@       300     IN  AAAA    2001:db8::10

; www is an alias. One hop only -- chains cost extra resolutions.
www     300     IN  CNAME   booknook.com.
api     300     IN  A       203.0.113.10
cdn     300     IN  CNAME   d1x2y3.cloudfront.net.

; Status page on INDEPENDENT infrastructure, so it survives our own outage.
status  3600    IN  CNAME   statuspage.io.

; Email
@       3600    IN  MX  10  aspmx.l.google.com.
@       3600    IN  TXT     "v=spf1 include:_spf.google.com ~all"

; Only Let's Encrypt may issue certificates for this domain.
@       3600    IN  CAA 0 issue "letsencrypt.org"
```

### The application-level caching trap

```python
# PROBLEM: many HTTP clients and DB drivers resolve a hostname ONCE at startup
# and reuse the IP forever. DNS failover then does nothing -- the app keeps
# connecting to the dead IP until it is restarted.

# Demonstration of the bug:
import socket
DB_IP = socket.gethostbyname("db.booknook.internal")   # resolved ONCE, at import
def connect():
    return psycopg2.connect(host=DB_IP)                # ignores the 60s TTL forever

# Better: pass the NAME, and let the driver re-resolve on each new connection.
def connect():
    return psycopg2.connect(host="db.booknook.internal")

# Java specifically (a classic outage cause):
#   -Dnetworkaddress.cache.ttl=60        respect short TTLs
#   -Dnetworkaddress.cache.negative.ttl=5
#   Historically the default was -1 (cache FOREVER) when a SecurityManager was set.
```

```python
# A deliberate, bounded DNS cache with failover awareness.
# Illustrative only -- in production use the resolver your HTTP client provides.
import time, socket

class DNSCache:
    def __init__(self, ttl_seconds=60):
        self._cache = {}          # host -> (ip, expires_at)
        self._ttl = ttl_seconds   # we clamp to OUR ttl, ignoring the record's

    def resolve(self, host: str) -> str:
        now = time.monotonic()
        entry = self._cache.get(host)
        if entry and entry[1] > now:
            return entry[0]                       # cache hit, ~0 ms
        ip = socket.gethostbyname(host)           # miss: 1-80 ms depending on caches
        self._cache[host] = (ip, now + self._ttl)
        return ip

    def invalidate(self, host: str) -> None:
        # Call this when a connection to the cached IP FAILS, so the next attempt
        # re-resolves immediately instead of waiting out the TTL. This is what
        # turns a 60-second outage into a 1-second one.
        self._cache.pop(host, None)
```

### Health-checked DNS failover (Route 53 style)

```json
{
  "Comment": "Primary/secondary failover. Note the TTL is the FLOOR on recovery time.",
  "Changes": [{
    "Action": "UPSERT",
    "ResourceRecordSet": {
      "Name": "api.booknook.com",
      "Type": "A",
      "SetIdentifier": "primary-mumbai",
      "Failover": "PRIMARY",
      "TTL": 60,
      "HealthCheckId": "abc123",
      "ResourceRecords": [{ "Value": "203.0.113.10" }]
    }
  }]
}
```
```
Real recovery time = health-check detection (~30 s: 3 failed checks × 10 s)
                   + DNS TTL expiry        (up to 60 s)
                   + client re-resolution  (0-60 s, client-dependent)
                   ≈ 30-150 seconds, and NOT guaranteed, because some clients
                     cache longer than the TTL.
Compare: a load balancer removes an unhealthy backend in ~5-15 seconds, for
all clients, with no caching involved (Phase 2, Topic 7). DNS failover is a
REGION-level tool; LB health checks are the INSTANCE-level tool.
```

## 9. Numerical example

### Part A — What does DNS actually cost a user?

**Assumptions (stated explicitly):** Mumbai user; anycast resolver RTT 5 ms; root RTT 5 ms; `.com` TLD RTT 20 ms; authoritative RTT 30 ms; resolver cache-hit rate 85 % (typical).

```
COLD (nothing cached anywhere):
  stub → resolver           5 ms
  resolver → root           5 ms
  resolver → .com          20 ms
  resolver → authoritative 30 ms
  resolver → stub           5 ms
  Total                  ≈ 65 ms

WARM resolver (NS records cached, A record expired) — the common miss case:
  stub → resolver           5 ms
  resolver → authoritative 30 ms   (root and TLD skipped: NS TTL is 48h)
  resolver → stub           5 ms
  Total                  ≈ 40 ms

FULL HIT (A record cached at the resolver):
  stub → resolver → stub  ≈ 10 ms

LOCAL HIT (OS or browser cache):
  ≈ 0 ms

Expected latency with an 85% resolver hit rate:
  0.85 × 10 ms + 0.15 × 40 ms = 8.5 + 6.0 = 14.5 ms average
```

**Interpretation:** DNS adds ~15 ms on average, but up to 65 ms on a cold first visit — paid *before a single byte of the page is requested*, and before TCP and TLS add their own round trips (Lessons 8–9). For a page with a 500 ms budget, 65 ms is 13 % spent on an empty screen. This is why DNS prefetching (`<link rel="dns-prefetch">`) and preconnect exist.

### Part B — Query load on BookNook's authoritative servers

**Assumptions:** 10,000 DAU (Lesson 1); each user's device performs one uncached lookup per 5-minute TTL window while active; average active session 20 min → 4 lookups/user/day reaching a resolver; 50 distinct recursive resolvers serve BookNook's user base (ISPs + public resolvers); each resolver caches, so only **one** query per resolver per TTL window reaches us.

```
Naive (no caching at all):
  10,000 users × 30 requests/day = 300,000 lookups/day = 3.5 queries/sec

WITH resolver caching (the reality):
  Queries reaching authoritative = resolvers × (seconds per day / TTL)
                                 = 50 × (86,400 / 300)
                                 = 50 × 288
                                 = 14,400 queries/day
                                 = 0.17 queries/sec
  Reduction: 300,000 → 14,400 = 95% fewer queries
```

**Now compute the cost of a short TTL.** Drop the TTL from 300 s to 30 s:

```
  50 resolvers × (86,400 / 30) = 50 × 2,880 = 144,000 queries/day = 1.7 q/s
  10× the query volume, for 10× faster failover.
```

At Route 53 pricing (~$0.40 per million queries, approximate): 144,000/day ≈ 4.3 M/month ≈ **$1.73/month**. The TTL decision here is *not* about money — it's about failover speed versus resilience during a DNS provider outage. Say that explicitly rather than hand-waving about "cost".

At scale the maths flips. For 50 M DAU and 50,000 distinct resolvers:
```
  TTL 300s: 50,000 × 288   = 14.4 M/day = 432 M/month ≈ $173/month
  TTL 30s:  50,000 × 2,880 = 144 M/day  = 4.3 B/month ≈ $1,730/month
```
Now it's a real budget line, and the trade-off is genuine.

### Part C — Real failover time with DNS

**Assumptions:** A-record TTL 60 s; health check every 10 s, 3 consecutive failures required; 15 % of clients ignore TTL and cache for 300 s (typical, driven by browsers and buggy routers).

```
Detection:            3 × 10 s          = 30 s
DNS TTL expiry:       uniform 0-60 s    → mean 30 s, worst case 60 s
Client re-resolution: immediate for most

Mean recovery for well-behaved clients  = 30 + 30 = 60 s
Worst case for well-behaved clients     = 30 + 60 = 90 s
Worst case for the misbehaving 15%      = 30 + 300 = 330 s (5.5 minutes)

So: 85% of users recover within ~90 seconds.
    15% of users are broken for up to 5.5 minutes.
```

**Availability impact of one such failover event per month:**
```
Downtime = 0.85 × 60 s + 0.15 × 330 s = 51 + 49.5 = 100.5 user-weighted seconds
Monthly seconds = 30 × 86,400 = 2,592,000
Availability    = 1 - (100.5 / 2,592,000) = 99.9961%
```
That's inside a 99.99 % SLO — but only just, and only for *one* event per month. **Conclusion: DNS failover is adequate for region-level disaster recovery, and inadequate for instance-level failure.** For instance failures you need a load balancer (Phase 2, Topics 4 and 7), which removes a backend in 5–15 seconds for *all* clients with no cache involved.

### Part D — The negative-caching trap, quantified

**Assumptions:** SOA minimum (negative TTL) = 3600 s; an engineer queries `new-api.booknook.com` at 14:00 to "check if it's ready", then creates the record at 14:05.

```
14:00  Query → NXDOMAIN → cached by the resolver until 15:00
14:05  Record created. dig @ns1 shows it correctly (authoritative, uncached).
14:06  Application still gets NXDOMAIN. Engineer concludes DNS is "broken".
15:00  Negative cache expires. It starts working, with no action taken.

Wasted time: 55 minutes of debugging a non-problem.
Prevention:  set SOA minimum to 300s, and never query a name before creating it.
```

## 10. Trade-offs

**Using DNS names everywhere instead of IPs**
- *For:* migration and failover without redeploying; one place to change; enables CDNs and multi-region.
- *Against:* adds a dependency with its own outage modes; stale caches produce confusing, machine-specific behaviour; adds 0–65 ms to cold connections.
- *Operational cost:* zone management, TTL discipline, monitoring that records resolve correctly from multiple vantage points.

**Low TTLs**
- *For:* fast failover and migration.
- *Against:* more queries (cost, and load on your provider); **less resilience during a DNS outage**, because caches drain quickly and clients have nothing to fall back on; marginally higher average client latency.
- *Becomes unnecessary complexity when:* your IPs never change — e.g. an anycast address in front of everything. Then a long TTL is strictly better, and you handle failover at the routing layer instead.

**DNSSEC**
- *For:* cryptographic protection against cache poisoning and forged answers.
- *Against:* real operational risk — key rollover mistakes take the entire domain offline, not just degrade it; more moving parts; limited validation coverage means partial benefit.
- *Becomes unnecessary complexity when:* you serve everything over HTTPS with HSTS, which already prevents the attacker from impersonating you. DNSSEC then mainly protects against denial of service and some email attacks.

**Multiple DNS providers**
- *For:* survives a provider-wide outage (the 2016 Dyn lesson).
- *Against:* you must keep two zones in sync; some provider-specific features (ALIAS, weighted routing) don't translate; roughly doubles cost.
- *Becomes unnecessary complexity when:* your availability target is 99.9 % and your provider reliably delivers better than that.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| Authoritative nameservers down | Users with cached answers are **fine**; everyone else sees "server not found" — a slow, creeping outage as caches expire | External DNS monitoring from multiple regions; a drop in new sessions with flat existing traffic | Secondary provider answers; otherwise restore the provider. Long TTLs genuinely save you here. |
| DNS provider DDoSed (Dyn, 2016) | Site unreachable for a growing fraction of users even though servers are perfectly healthy | Your own servers look healthy — misleading; synthetic external checks fail | Secondary DNS provider on different infrastructure |
| Record deleted by accident | NXDOMAIN; "server not found"; then **negative caching extends the outage past the fix** | Alert on NXDOMAIN rate; config diff | Recreate the record, then wait out the negative TTL (SOA minimum) |
| Domain registration expires | Entire domain, email included, stops resolving | Registrar emails — frequently sent to an address *at the expired domain* | Renew. Enable auto-renew and registrar lock. A genuinely catastrophic, entirely preventable failure. |
| TTL too high during a migration | Traffic splits across old and new servers for hours; "it works for me" reports | Old server still receiving traffic long after cutover | Wait it out. Keep the old server alive during the overlap — **never decommission at cutover**. |
| CNAME chain too long / loop | Slow resolution or SERVFAIL | `dig +trace` shows the chain; resolver logs | Flatten the chain to one hop |
| Missing glue record | Intermittent resolution failures, resolver-dependent | `dig +trace` stalls at the delegation | Add glue at the registrar |
| TCP/53 blocked by firewall | Large responses (many records, DNSSEC) fail while small ones succeed — baffling intermittency | Truncated (TC flag) responses with no TCP retry | Allow TCP/53 |
| App caches DNS forever (Java) | After DB failover, one service keeps hitting the dead IP while others recover | Connection errors isolated to one service; restart "fixes" it | Set `networkaddress.cache.ttl`; invalidate on connection failure |
| Negative cache after creating a record | "DNS is broken" for up to the SOA minimum, despite a correct record | `dig @authoritative` works; `dig @8.8.8.8` returns NXDOMAIN | Wait out the negative TTL; lower SOA minimum pre-emptively |
| DNS cache poisoning | Users silently sent to an attacker's server | TLS certificate errors (if HTTPS); otherwise often undetected | DNSSEC, DoH, and HSTS limit the damage |

## 12. Alternatives

| Need | Approach | Pros | Cons |
|---|---|---|---|
| Name → address | **DNS** | Universal, cached, hierarchical, free to query | Eventually consistent, cache-stale, unauthenticated by default |
| | `/etc/hosts` | Instant, no dependency | Per-machine, unmanageable, invisible to others |
| | Service discovery (Consul, etcd, Kubernetes) | Health-aware, sub-second updates, rich metadata | Only works inside your cluster; more infrastructure (Phase 4, Topic 24) |
| | Service mesh (Envoy/Istio) | Discovery + load balancing + mTLS + retries | Heavy operational complexity |
| | Hardcoded IPs | Zero lookup latency, zero dependencies | Cannot ever move; this is how you get a 3-day migration |
| Traffic distribution | DNS round-robin | Free, no extra hop, works across regions | No health or load awareness; caching defeats it |
| | **Anycast** | Per-packet routing, instant failover, DDoS absorption | Requires BGP and multiple POPs; hard to do yourself |
| | GeoDNS | Region-specific endpoints; data residency | Sees the resolver's location, not the user's; cache interference |
| | Load balancer | Health-aware, fast, fine-grained (Phase 2, Topic 4) | An extra hop; a component to run |
| Privacy/integrity | DoH / DoT | Encrypts stub↔resolver; blocks ISP tampering | Doesn't protect against the resolver; complicates enterprise filtering |
| | DNSSEC | Authenticates records end to end | Complex; key errors cause total outages |

## 13. When to use these concepts deliberately

1. **Planning a migration or failover.** The TTL dance (Section 4) is the difference between a 2-minute cutover and a 24-hour tail of mysterious reports. Any time you change an IP, TTL planning comes first.
2. **Building multi-region architecture.** GeoDNS or anycast is how a user in Mumbai reaches the Mumbai region (Phase 2, Topic 22). DNS is the *only* practical global steering mechanism that works before a connection exists.
3. **Fronting assets with a CDN.** `cdn.booknook.com → CNAME → provider` is how you hand off traffic without changing a single URL in your HTML (Phase 2, Topic 14).
4. **Isolating critical dependencies.** Putting the status page on a separate provider, and using a secondary DNS provider, are availability decisions made entirely in DNS.
5. **Debugging "the site is down for some people."** `dig` from multiple vantage points, with the TTL countdown visible, resolves this class of incident faster than anything else.

## 14. When *not* to rely on DNS

1. **Instance-level failover.** DNS recovery is 60–330 s and not guaranteed (Section 9, Part C). Use a load balancer, which reacts in 5–15 s for every client. DNS is a region-level tool.
2. **Fine-grained load balancing.** DNS cannot see connection counts, CPU, or queue depth. Use a real LB with least-connections or similar (Phase 2, Topic 6).
3. **Service-to-service discovery inside a cluster.** Pods come and go in seconds; DNS TTLs and client caching are too coarse. Use the platform's service discovery.
4. **As a security boundary.** DNS answers are unauthenticated. Never decide "this host is trusted" based on a name resolving a particular way; use mTLS or signed tokens (Lesson 7, Phase 4, Topic 24).

## 15. Common mistakes

**Beginner mistakes**
- Believing in "DNS propagation" and waiting 48 hours when the answer was live immediately and the problem was a local cache.
- Checking only `dig @8.8.8.8` and concluding a change failed — without checking the authoritative server to see whether the change is actually live at the source.
- Trying to CNAME the zone apex, then being confused when email breaks (CNAME cannot coexist with MX).
- Querying a name before creating it and then fighting a cached NXDOMAIN for an hour.
- Setting a 24-hour TTL on records that front production servers, guaranteeing a painful migration later.
- Forgetting that the browser has its own cache that ignores your TTL, so "it still shows the old site" even after everything else expired.
- Testing DNS with `ping` and concluding the service is fine (it tests the host, not the port — Lesson 3).

**Experienced-engineer mistakes**
- Lowering the TTL *at the same time* as the migration, instead of one old-TTL period beforehand — so caches still hold the old long TTL and the change takes hours anyway.
- Decommissioning the old servers at cutover rather than after the TTL window plus a safety margin.
- Running a single DNS provider for a service with a 99.99 % target (the Dyn lesson).
- Hosting the status page on the infrastructure it reports on.
- Ignoring client-side DNS caching in application runtimes, so DNS failover silently does nothing for one service.
- Relying on DNS round-robin as the load balancer and being surprised that one dead server causes a persistent 1/3 error rate.
- Letting domain registration lapse — still, in 2026, a recurring cause of total outages at real companies.
- Setting a very low TTL "for safety", which *reduces* resilience: when your DNS provider has an outage, short-TTL caches drain in a minute and everyone breaks, whereas long-TTL caches would have ridden it out.
- Forgetting CAA records, leaving any CA able to issue certificates for the domain.

## 16. Interview perspective

DNS appears in two guises: as the opening step of "what happens when you type a URL" (an extremely common warm-up question), and as a *tool* inside multi-region and CDN designs.

**How to use it well:**

1. **In "what happens when you type a URL", be precise about the hierarchy and the caches.** Name the layers in order — browser, OS, hosts file, recursive resolver, root, TLD, authoritative — and mention that 80–95 % of lookups never leave a cache. That level of specificity is exactly what the question is testing.
2. **Reach for DNS at the *global* layer of a multi-region design, and name its limits.** "GeoDNS routes users to the nearest region; within a region, a load balancer handles instance-level health, because DNS failover takes 60–300 seconds and some clients ignore TTLs." This shows you know which tool operates at which timescale.
3. **Quantify the TTL trade-off instead of asserting it.** "I'd use 60 s on the apex: failover in about a minute, at roughly 10× the query volume, which is a few dollars a month. For a provider outage, a longer TTL would serve me better, so I'd accept that risk only with secondary DNS."
4. **Volunteer the DNS single point of failure.** "Two providers for DNS — a single provider is a single point of failure for the whole domain regardless of how redundant the servers are." Very few candidates say this.
5. **If asked how you'd migrate a database or a region, lead with the TTL dance.** Lower TTL 48 hours ahead; change; verify authoritative; watch old-server traffic drain; decommission only after the window.

**Red flags interviewers watch for:**
- Saying "DNS propagates" without being able to explain what's actually happening.
- Using DNS round-robin as the primary load balancer in a design that needs health awareness.
- Not knowing that DNS answers are cached, or thinking you can purge a global DNS cache.
- Treating DNS failover as instantaneous in an availability calculation.
- Not recognising the DNS provider as a dependency in the availability chain (Lesson 15).

## 17. Knowledge check

Write your answers down before continuing.

**Conceptual**

1. You change `booknook.com`'s A record from `203.0.113.10` to `198.51.100.20`. `dig @ns1.booknook.com booknook.com` immediately returns the new IP with the `aa` flag set, but `dig @8.8.8.8 booknook.com` still returns the old IP, and a colleague in another country reports the old site for the next three hours. Explain exactly what is happening at each layer, why the phrase "DNS propagation" is a misleading description of it, and state the one field in `dig` output you would look at to predict when it will resolve itself.

2. A teammate proposes removing the load balancer entirely and instead publishing three A records with a 30-second TTL, arguing it's "free load balancing with fast failover." Give three specific technical reasons this is worse than a load balancer, and name one situation where this approach is nonetheless the right choice.

3. Explain why a **long** TTL *increases* your resilience to a DNS provider outage but *decreases* your ability to fail over quickly, and describe a concrete configuration that gets you a reasonable amount of both.

**Practical scenario**

4. BookNook's checkout service connects to `payments-gw.booknook.internal`, a private DNS name with a 30-second TTL. The payments gateway VM fails and is replaced by a standby at a new IP; the DNS record is updated automatically within 10 seconds. The Python-based order service recovers within a minute. The Java-based fraud service continues to throw connection errors for six hours until someone restarts it, and the on-call engineer's first three hours are spent investigating the network and the DNS provider, both of which are healthy.
   Diagnose the root cause precisely, explain why the two services behaved differently, state the fix for the Java service, and describe one *additional* defensive change you would make in the application code so that this class of problem self-heals regardless of language or runtime settings.

**Calculation**

5. BookNook expands to 2,000,000 DAU across three regions and uses GeoDNS with health checks.
   Assumptions: 20,000 distinct recursive resolvers serve its user base; the apex A record TTL is 120 s; DNS provider pricing is $0.40 per million queries; health checks run every 10 s and require 3 consecutive failures; 12 % of clients ignore TTLs and cache for 600 s.
   (a) How many queries per day reach the authoritative nameservers, and what is the monthly DNS bill?
   (b) If the TTL were lowered to 20 s, what would the new query volume and monthly bill be, and by what factor does each change?
   (c) With the original 120 s TTL, compute the mean and worst-case failover time for well-behaved clients and for the 12 % that ignore TTLs, then compute the user-weighted downtime in seconds for a single regional failure. Express that as an availability percentage over a 30-day month, and state whether it fits inside a 99.99 % SLO. Show every step and state any additional assumptions you make.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 5.

---

**Progress checklist**

Phase 1:
`[x] 1. Client-server architecture`
`[x] 2. Stateful versus stateless servers`
`[x] 3. IP addresses, ports, sockets, and domain names`
`[x] 4. DNS resolution and DNS caching`
`[ ] 5. HTTP requests and responses` ← next
`[ ] 6–17` not started
Phases 2–5: not started.

**NEXT LESSON: Phase 1, Topic 5 — HTTP requests and responses**
