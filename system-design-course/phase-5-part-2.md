# Lesson 96 — Design a Notification Service

*Phase 5, Topic 4*

## 1. Simple definition

**The problem.** One service that takes a notification request ("tell user 8842 that order 5512 has shipped") and delivers it over the right channel — email, SMS, push, in-app — respecting the user's preferences, their timezone, their provider's limits, and the law, while giving the sender a delivery status and never sending the same message twice.

**Why it is a platform, not a feature.** Every product team needs notifications, and every team that builds its own gets the same four things wrong: it sends marketing pushes at 03:00 local time, it retries a failed email into a duplicate, it takes down checkout when the email provider has an outage, and it cannot answer "did the user get the OTP?" So the notification service exists to centralise **one capability with a compliance surface**, exactly like payments.

Terms defined before use:

- **Notification request**: the inbound event (`notification_id`, recipient, template, data, channel, priority).
- **Template**: a versioned message with placeholders and per-locale variants.
- **Channel adapter**: the code that talks to one provider for one channel (SES, Twilio, FCM/APNs).
- **Fan-out**: turning one logical event into N per-recipient notifications.
- **Digest / collapse**: merging many similar notifications into one message ("3 people replied to your review").
- **Quiet hours**: a per-user window (in their local timezone) in which non-urgent channels are suppressed or deferred.
- **Preference**: per-user, per-channel, per-category consent — including "unsubscribed", which is legally binding.
- **Delivery state machine**: queued → sent → delivered → opened / bounced / failed, updated by provider webhooks.
- **Priority class**: critical (OTP, security), transactional (order), engagement (review reply), marketing (promotion) — and they must not share a queue.

The one sentence to carry out of this lesson: **a notification service is a fan-out pipeline whose correctness is measured in three places — the user never gets a duplicate, never gets marketing at 03:00, and never misses an OTP because a marketing campaign filled the queue.**

## 2. Why it exists

**Because the failure modes are all user-visible and all legal.** A duplicated "your order has shipped" is embarrassing; a duplicated password-reset token is a security event; an unwanted marketing email after an unsubscribe is a regulatory event (GDPR/PECR fines, and in the US, CAN-SPAM). Centralising the retry, dedupe, consent and quiet-hours logic means it is built and audited **once**.

**Because the load is spiky and multi-tenant.** BookNook's numbers: 12 M emails/day (139/s), 40 M pushes/day (463/s), 800 k SMS/day — and a price-drop campaign can multiply push volume by **40×** for ten minutes. The sales team will never warn you. Priority separation is what keeps an OTP at 2 s p99 while 18,000 pushes/s queue behind it.

**Because providers fail, and they fail independently.** Measured over 18 months: the email provider had 3 incidents totalling 4 h 40 m of 5xx; the SMS provider had 1 incident of 2 h 15 m (and a second provider is cheap insurance for OTPs); APNs is reliable but FCM has regional blips. A single-provider design turns a provider outage into "no password resets", which is a full outage in a different costume.

**Because the channel decides the cost model.** Email ≈ £0.00008, push ≈ £0, SMS ≈ £0.045, and in-app is free but only works when the user opens the app. One wrong fan-out policy — sending an SMS where a push would do — costs **560×** more per message. The service exists to make that choice explicit and auditable.

**And because "did it arrive?" is a product requirement.** Order-shipped emails, OTP delivery, and dunning messages all need a delivery state, which means provider webhooks, idempotent state transitions, and a reconciliation job for the webhooks that never arrive.

## 3. Real-world analogy

**A company mailroom.**

Every department drops letters into the mailroom's slot with an internal routing slip. The mailroom does not write the letters; it decides **how they travel** and proves they left.

- **Sorting by class is priority separation.** Registered post (an OTP), first class (an order confirmation), and bulk mail (a promotion) travel in different bags. If a marketing run of 200,000 letters is stuffed into the same bag as the registered post, the registered letters are late — which is why the mailroom never mixes them. This is the single most important property of a notification service.
- **The mailroom knows the addressee's preferences.** There is a list on the wall: "do not send bulk mail to this address", "no deliveries before 09:00". Bulk mail to a household that has opted out is not a mistake, it is a **breach**.
- **Two carriers, one routing rule.** The mailroom has a contract with a primary carrier and a standing arrangement with a second. When the primary's depot floods, the post still moves. That is provider failover, and the reason it works is that the mailroom was designed with the abstraction in the first place.
- **Registered post comes back with a signature.** That is the delivery webhook: the mailroom's log says *delivered at 14:12, signed for*. Without it, the mailroom can only say "we posted it", which is not the same claim — and the difference is exactly `sent` versus `delivered` in the state machine (§4.6).
- **And every letter carries one routing slip, not two.** If a department sends the same letter twice (because it lost the receipt), the mailroom's duplicate detector catches the identical slip. This is the idempotency key, and it is why a retried send does not become a second letter to the customer.

The analogy's one instructive failure: **a mailroom that batches.** Posting one letter per day per household (a digest) instead of forty separate ones is cheaper, less annoying, and is what a good mailroom does with bulk mail — but it is the wrong policy for registered post. The service must know which policy applies to which class, which is a **template-level** decision, not a global one.

## 4. Detailed technical explanation

### 4.1 Requirements

**Functional.**

| # | Requirement |
|---|---|
| F1 | Accept a notification request with a stable `notification_id` and deliver over email, SMS, push or in-app |
| F2 | Template rendering with versioning, per-locale variants and typed variables |
| F3 | User preferences: per-channel, per-category consent, quiet hours, digest preference |
| F4 | Priority classes that cannot block each other |
| F5 | Retry with backoff, provider failover, DLQ for permanently failed sends |
| F6 | Delivery status per message, with webhook-driven state transitions |
| F7 | Dedupe and collapse (no duplicate sends; digests for high-frequency events) |
| F8 | Scheduling (send at a specific time, or at a local-time window) |
| F9 | Suppression lists: bounces, complaints, unsubscribes, blocklisted addresses |

**Non-functional.**

| # | Requirement | Number |
|---|---|---|
| N1 | OTP end-to-end latency | p99 < 3 s (SMS), < 6 s (email) |
| N2 | Transactional latency | p99 < 30 s |
| N3 | Marketing throughput | 18,000/s sustained for 10 min |
| N4 | Exactly-once **effect** | Zero duplicate sends for a retried `notification_id` |
| N5 | Compliance | Global unsubscribe honoured within **10 seconds** of the request |
| N6 | Availability | 99.95 % accept-rate; delivery degrades gracefully with providers |
| N7 | Cost control | Per-channel, per-category budget with automatic throttle |

### 4.2 Capacity estimation

```
  VOLUME (daily)
      email        12,000,000  →  139/s average, peak × 8 = 1,111/s
      push         40,000,000  →  463/s average, peak × 40 = 18,519/s
      SMS             800,000  →  9.3/s average, peak × 6 = 56/s
      in-app       30,000,000  →  347/s (written to a feed, not "sent")
      ─────────────────────────────────────────────────────────────────
      total        82,800,000 notifications/day = 958/s average

  COMPOSITION
      62 % engagement (review replies, likes, follows)  ← digestible
      31 % transactional (order, shipping, dunning)     ← never digestible
       6 % marketing (price drop, campaign)             ← throttled by budget
       1 % critical (OTP, security, password reset)     ← must never queue

  BANDWIDTH / STORAGE
      request payload ~1.2 KB; state row ~600 B
      82.8 M × 600 B = 50 GB/day of state → 18 TB/year (partitioned, 90-day
          hot retention, then aggregated — L57)
      rendered bodies are NOT stored whole: template id + version + data hash
      is enough to re-render for audit (1.2 KB × 82.8 M = 99 GB/day otherwise)

  PROVIDER QUOTAS (the constraint that actually binds)
      email provider: 3,000/s sustained, 50,000/s burst (negotiated)
      SMS provider:   200/s, and per-country long-code limits (US 1/s/number)
      FCM:            ~600,000/min per project = 10,000/s
      ⇒ push peak 18,519/s EXCEEDS the FCM project limit by 1.85×
        ⇒ either two projects (or per-tenant projects) or a throttle with
          a 4-minute drain (§9 step 2). This is the kind of number that
          changes the design, and it is invisible without the capacity step.

  COST PER MESSAGE (the reason channel choice is a product decision)
      email £0.00008 · push £0.0000002 · SMS £0.045 · in-app £0
      ⇒ sending one notification as SMS instead of push is 225,000× more
        expensive; as email instead of push, 400×.
```

### 4.3 API

```
POST /v1/notifications
     Idempotency-Key: 2f9c1a7e-…                 # REQUIRED, caller-supplied
     {
       "recipient": { "user_id": 8842 },
       "template": "order.shipped", "template_version": 7,
       "category": "transactional",              # critical|transactional|
                                                 # engagement|marketing
       "channels": ["email", "push", "in_app"],  # preferences may narrow this
       "data": { "order_id": 5512, "carrier": "Royal Mail", "eta": "2026-10-31" },
       "locale": "en-GB",
       "send_at": null,                          # or an ISO timestamp
       "collapse_key": "order:5512"              # optional: replaces a queued
     }                                           # notification with this key
  →  202 { "notification_id": "ntf_01J8…", "accepted_channels": ["email","push"],
           "suppressed": [{"channel":"in_app","reason":"preference"}] }
     errors: 409 duplicate (same Idempotency-Key, different payload)
             422 unknown template / invalid variables
             429 rate limited (per tenant, L95)

GET  /v1/notifications/{id}
  →  200 { "status": "delivered", "attempts": [
             {"channel":"push","at":"…","state":"delivered","provider":"fcm"},
             {"channel":"email","at":"…","state":"opened","provider":"ses"} ] }

POST /v1/notifications/{id}/cancel      # only while queued
POST /v1/preferences                    # per-user, per-category consent
POST /v1/suppressions                   # bounce/complaint ingestion (internal)
POST /webhooks/{provider}               # delivery receipts, signed, idempotent
```

### 4.4 Data model

```sql
-- The request, deduped by the caller's idempotency key (L74)
CREATE TABLE notifications (
  id                UUID PRIMARY KEY,             -- ntf_… (UUIDv7: sortable)
  idempotency_key   TEXT NOT NULL,
  tenant_id         BIGINT NOT NULL,
  user_id           BIGINT NOT NULL,
  template_id       TEXT NOT NULL,
  template_version  INTEGER NOT NULL,             -- rendered content is
  data_hash         BYTEA NOT NULL,               -- reproducible, not stored
  category          SMALLINT NOT NULL,
  priority          SMALLINT NOT NULL,            -- 0 critical … 3 marketing
  scheduled_for     TIMESTAMPTZ NOT NULL,
  state             SMALLINT NOT NULL,            -- queued|sending|done|failed
  collapse_key      TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, idempotency_key)             -- ← the dedupe
);
CREATE INDEX ntf_due ON notifications (priority, scheduled_for)
  WHERE state = 0;                                -- the scheduler's index

-- One row per (notification, channel): the unit of delivery and of retry
CREATE TABLE deliveries (
  notification_id   UUID NOT NULL REFERENCES notifications(id),
  channel           SMALLINT NOT NULL,
  state             SMALLINT NOT NULL,            -- queued|sent|delivered|
                                                  -- opened|bounced|failed|
  attempt           SMALLINT NOT NULL DEFAULT 0,
  provider          TEXT,
  provider_msg_id   TEXT,                         -- for webhook correlation
  next_attempt_at   TIMESTAMPTZ,
  last_error        TEXT,
  PRIMARY KEY (notification_id, channel)
);
CREATE UNIQUE INDEX del_provider ON deliveries (provider, provider_msg_id)
  WHERE provider_msg_id IS NOT NULL;              -- webhooks are idempotent

-- Consent: the legally binding one. Written by the unsubscribe link, read on
-- every send, cached for 10 s (N5) and invalidated by pub/sub.
CREATE TABLE suppressions (
  scope      TEXT NOT NULL,     -- 'user' | 'address' | 'domain' | 'tenant'
  value      TEXT NOT NULL,
  category   SMALLINT,          -- NULL = all categories
  reason     TEXT NOT NULL,     -- unsubscribe|hard_bounce|complaint|manual
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (scope, value, category)
);
```

### 4.5 The pipeline

```
  ACCEPT → RENDER → ROUTE → QUEUE → SEND → TRACK
   │        │        │       │       │      │
   │        │        │       │       │      └─ provider webhooks → idempotent
   │        │        │       │       │         state transition → reconcile
   │        │        │       │       └─ adapter per channel, with retry,
   │        │        │       │          failover, and circuit breaker (L77)
   │        │        │       └─ FOUR priority queues; never shared, because
   │        │        │          a marketing flood must not delay an OTP
   │        │        └─ preference check, quiet hours, suppression, budget,
   │        │           channel narrowing (email+push requested, user allows
   │        │           only email ⇒ email)
   │        └─ template version pinned at accept time (so a template edit
   │           does not change a message already in flight), typed variables,
   │           per-locale rendering, physical-address footer for email
   └─ validate, dedupe on idempotency key, persist, return 202
```

**Why priority queues are separate, with numbers.** A shared FIFO with 18,519/s of marketing arrivals and a critical message at the tail means the OTP waits for the queue to drain: at 18,519/s and a 10-minute campaign, that is a **3–10 minute OTP delay** — a login outage. Four queues with four consumer pools, and a **dedicated SMS pool for `critical`** that never scales down below 3 pods, gives the measured p99 in §9 step 1.

### 4.6 Delivery tracking: the state machine and its lies

```
   QUEUED ──► SENT ──► DELIVERED ──► OPENED
      │        │
      │        ├──► BOUNCED (hard: permanent; soft: retry with backoff)
      │        └──► FAILED (provider 5xx, exhausted retries → DLQ)
      └──► CANCELLED

  THE LIES, in order of how often they cause a support ticket:
    · SENT ≠ DELIVERED. An SMTP 250 means "accepted", not "in the inbox".
      BookNook measured 2.1 % of accepted emails landing in spam or being
      rejected silently downstream — invisible without a feedback loop.
    · DELIVERED ≠ READ. Push "delivered" means the device accepted it; a push
      to a device with notifications disabled is delivered and never seen.
      Measured: 31 % of delivered pushes had notifications disabled.
    · A missing webhook is not a failure. Webhooks are at-least-once and
      sometimes never (L73). A reconciler polls the provider for sends stuck
      in SENT for > 30 min and either resolves them or marks them UNKNOWN.
    · OPENED is a tracking-pixel artefact. Apple Mail Privacy Protection
      pre-fetches pixels, so ~26 % of "opens" in BookNook's data were
      machine opens. Never make a product decision on open rate alone.
  ⇒ the state machine is a PRODUCT CONTRACT. "Delivered" must mean what the
    support team thinks it means, or every escalation starts with an argument
    about definitions.
```

### 4.7 Provider failover and the abstraction that makes it possible

```
  CHANNEL ADAPTER INTERFACE (identical for every provider):
      send(msg) -> provider_msg_id
      classify_error(e) -> RETRYABLE | PERMANENT | PROVIDER_DOWN
      parse_webhook(req) -> (provider_msg_id, state, timestamp)

  FAILOVER POLICY, per channel:
      email   primary SES → secondary (a second region/adapter) after
              3 consecutive 5xx or a 20 % error rate over 60 s
              ⇒ non-urgent sends QUEUE during an outage (email can wait)
      sms     primary → secondary after 2 consecutive failures, and
              CRITICAL sends go to both providers?? NO — that double-charges
              and double-delivers. Instead: primary with a 1.5 s timeout,
              then secondary, and a dedupe on the provider webhook so a
              double-send is collapsed if it happens (§9 step 4)
      push    FCM → APNs are different platforms, not alternatives. Failover
              here means *project* failover for FCM quota (§4.2), not vendor
              failover. State that explicitly; it is a common interviewer trap.

  ⚠ THE SUBTLE BUG: failover without an idempotency key at the PROVIDER
    level duplicates messages. Where the provider supports it (SES message
    tags, Twilio's idempotency, FCM's collapse keys), pass your
    (notification_id, channel) so the second attempt is a no-op.
```

### 4.8 Quiet hours, digests and the timezone arithmetic

```
  QUIET HOURS: no marketing or engagement messages between 21:00 and 08:00
  in the RECIPIENT'S local timezone. Critical and transactional bypass.

  implementation: store the user's IANA timezone (not an offset), compute the
  next permitted instant with a real tz library, and schedule for it.
      · an OFFSET (-05:00) is wrong for any country with DST: the user's
        quiet hours shift by an hour twice a year, and a scheduled marketing
        send lands at 20:00 or 22:00 instead (L66 §4.4: "next 09:00 local"
        is a timezone operation, not arithmetic).
      · users with an unknown timezone (4.2 % of BookNook's base) get a
        conservative UTC window, and are excluded from time-sensitive
        campaigns rather than guessed at.

  DIGESTS: when a user has > K pending engagement notifications of the same
  type within a window, replace them with one summary.
      BookNook: 40 M pushes/day, of which 24.8 M are engagement
        2.4 M messages/day were "someone replied to your review" for users
        with 3+ replies in a day
      ⇒ collapsing to one digest per user per day: 2.4 M → 180 k messages
        (−92 %), saving £0 (push is free) but cutting push opt-outs by 41 %
        in a 6-week A/B test, which was the actual goal.
  ⇒ the digest exists for the USER, not the cost. Say so in a design review;
    it is the difference between a feature and a cost-cutting exercise.
```

## 5. Request / data flow

```
 CRITICAL PATH — an OTP, end to end, with every budget consumption.
 ─────────────────────────────────────────────────────────────────────────
 t+0     auth service calls POST /v1/notifications (critical, sms+email)
 t+2 ms  accept: idempotency check (Redis + unique index), persist row
 t+4 ms  render: template v12, locale en-GB, 0.4 ms
 t+6 ms  route: preference (cached 10 s) + suppression check (Bloom + exact)
         + channel selection: sms allowed? yes. email allowed? yes
 t+8 ms  enqueue to CRITICAL-SMS (its own topic, its own consumer pool)
 t+9 ms  return 202 to the auth service            [USER SEES "code sent" at
                                                    t+9 ms]
 t+120 ms SMS pool picks up (dedicated, warm, 3 pods minimum)
 t+140 ms provider POST /messages (1.5 s timeout)
 t+310 ms provider returns 202 + message_id
 t+312 ms state → SENT, webhook subscription recorded
 t+1.9 s webhook: "delivered"                      [p99 SLO 3 s: MET]

 BULK PATH — an 18,000/s marketing push run
 ─────────────────────────────────────────────────────────────────────────
 · accepted into topic MARKETING with a 60 req/s per-tenant admission limit
   (L95) so the run cannot starve anything else
 · consumers throttle to the FCM project quota (10,000/s), so the run drains
   in 18,000/10,000 ≈ 1.8 s of work per second of arrival — i.e. it takes
   ~2.2× longer than the arrival window and is shaped, not dropped
 · budget check before send: £0.0000002 × 660,000 = £0.13 for the run
 · a campaign that requested SMS would be BLOCKED at route time unless the
   tenant's SMS budget has headroom (L95 + a spend meter)
 · every consumer writes a delivery row, then a batch state update every 2 s
   (never one UPDATE per message — that is 18,000 writes/s for no reason)

 WEBHOOK PATH — delivery receipts
 ─────────────────────────────────────────────────────────────────────────
 provider → API gateway (signature verified) → /webhooks/{provider}
   → parse to (provider_msg_id, state, ts)
   → UPSERT deliveries SET state = GREATEST(state, new)  WHERE
     (provider, provider_msg_id) = …                  ← idempotent
   → if state ∈ {bounced_hard, complained}: write suppression, then
     invalidate the preference cache for that address (10 s N5 budget)
   → publish an event for analytics; never block the webhook on it
```

## 6. ASCII diagram

```
   producers: auth · orders · reviews · marketing · support · cron
                        │
                        ▼
            ┌───────────────────────────┐
            │ ACCEPT                    │ idempotency key · validate
            │ (returns 202 in ~9 ms)    │ persist · template pin
            └───────────┬───────────────┘
                        ▼
            ┌───────────────────────────┐
            │ ROUTE                     │
            │  · preferences (10 s cache)│
            │  · suppressions (Bloom+kg) │
            │  · quiet hours (IANA tz)   │
            │  · budget per category     │
            │  · channel narrowing       │
            └───────────┬───────────────┘
                        ▼
   ┌──────────────┬─────────────┬────────────────┬──────────────────┐
   │ CRITICAL     │ TRANSACTION │ ENGAGEMENT     │ MARKETING        │
   │ OTP/security │ order/ship  │ replies/likes  │ campaigns        │
   │ 3 warm pods  │ 6 pods      │ digest-or-send │ shaped to quota  │
   │ never scale↓ │             │              │ + spend meter    │
   └──────┬───────┴──────┬──────┴────────┬───────┴────────┬─────────┘
          ▼              ▼               ▼                ▼
   ┌──────────────────────────────────────────────────────────────┐
   │ CHANNEL ADAPTERS  (email·SMS·push·in_app)                     │
   │  · circuit breaker per provider (L77)                         │
   │  · retry: 3 attempts, exponential + jitter (L65)              │
   │  · failover: email→queue, sms→secondary, push→project quota   │
   │  · provider idempotency key = (notification_id, channel)      │
   └───────────┬──────────────────────────────────────────────────┘
               ▼
   ┌───────────────────────┐        ┌──────────────────────────────┐
   │ PROVIDERS             │        │ WEBHOOKS (signed, idempotent)│
   │ SES · Twilio · FCM    │───────►│ sent→delivered→opened        │
   │ APNs · in-app store   │        │ bounce/complaint→suppression │
   └───────────────────────┘        └──────────────┬───────────────┘
                                                   ▼
                                     ┌──────────────────────────────┐
                                     │ RECONCILER (every 15 min)    │
                                     │ stuck in SENT > 30 min →     │
                                     │ query provider → resolve or  │
                                     │ mark UNKNOWN                 │
                                     └──────────────────────────────┘

  WHY FOUR QUEUES: at 18,519 marketing msgs/s arriving and one OTP behind
  them in a single queue, the OTP waits 3–10 minutes. Separated consumers
  give the OTP a p99 of 1.9 s. This is the whole design in one line.
```

## 7. Detailed example

**BookNook's notification platform, migrated from five bespoke senders.**

```
  before: 5 services each calling SES/Twilio directly
    · 3 different retry policies; one retried a 400 fourteen times
    · marketing emails sent at 03:00 local for 41 % of the base (UTC windows)
    · an SES outage on 2025-03-02 stopped order confirmations for 4 h 20 m,
      while password resets continued (different service, different luck)
    · unsubscribe honoured in 4 h (nightly batch) — a PECR complaint and a
      £9,000 fine
    · nobody could answer "did user 8842 get their OTP?" without grepping logs
  after (12 weeks of work):
    · one accept API, four priorities, four channels, one state machine
    · p99 OTP 1.9 s · p99 transactional 14 s · marketing shaped to quota
    · unsubscribe honoured in 2.4 s p99 (SLO 10 s) — no complaints since
    · provider failover tested monthly; the 2026-01-19 SES incident queued
      61,000 emails for 3 h and delivered 99.98 % of them afterwards
```

**The three incidents that shaped the design:**

| Incident | What happened | Design change |
|---|---|---|
| **Duplicate shipping emails** | The order service retried on a 500 from the notification API; the API had *accepted* the request. 8,400 customers got 2–3 identical emails. | Mandatory `Idempotency-Key` with a unique index; the retry returns the original `notification_id` (§4.4) |
| **03:00 marketing** | Quiet hours computed from a stored UTC offset, so DST moved the window. 210,000 sends landed between 01:00 and 04:00 local. | Store IANA timezone; compute with a tz library; a test that asserts quiet hours for every timezone on both DST boundaries (§4.8) |
| **OTP starvation** | A price-drop campaign put 14,000/s of pushes in the shared queue; login OTPs waited 6 min 40 s; support saw "I never got the code" for 40 minutes. | Four separate queues and consumer pools; `critical` never shares a topic with `marketing` (§4.5) |

**The provider-failover drill, which is where the confidence comes from:**

```
  quarterly: point the email adapter at a blackhole endpoint for 10 minutes
  expected behaviour and the measured result:
    · circuit breaker opens after 3 consecutive 5xx (< 2 s)  ✅
    · 41,000 emails queue rather than fail                 ✅
    · no duplicate delivery on recovery (provider idempotency) ✅
    · a synthetic canary email reaches a seed inbox in 4 s ✅
  a real incident (2026-01-19): SES us-east-1 degraded for 3 h
    · queue depth peaked at 61,000; drained in 22 min after recovery
    · 12 emails were lost to a 24 h provider-side drop and never observed —
      which is why the reconciler marks UNKNOWN rather than pretending
```

## 8. Code or configuration example

```python
# ── Accept: idempotency, template pinning, and a fast 202 ────────────────
@app.post("/v1/notifications", status_code=202)
async def accept(req: NotificationRequest, idem: str = Header(...)):
    # 1. dedupe on the CALLER's key (L74) — a retry must not send twice
    existing = await db.fetchval(
        "SELECT id FROM notifications WHERE tenant_id=$1 AND idempotency_key=$2",
        req.tenant_id, idem)
    if existing:
        if await payload_hash(existing) != req.payload_hash:
            raise HTTPException(409, "idempotency key reused with new payload")
        return {"notification_id": existing, "deduplicated": True}

    # 2. pin the template version NOW, so an edit cannot change a message
    #    that is already in flight
    tpl = await templates.get_current(req.template)          # 0.3 ms, cached
    if tpl.missing_vars(req.data):
        raise HTTPException(422, {"missing": tpl.missing_vars(req.data)})

    # 3. persist + enqueue in ONE transaction (outbox, L85) so a crash
    #    cannot lose an accepted notification
    async with db.transaction():
        nid = new_uuid7()
        await db.execute("""INSERT INTO notifications
            (id, idempotency_key, tenant_id, user_id, template_id,
             template_version, data_hash, category, priority, scheduled_for,
             state, collapse_key)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,COALESCE($10, now()),0,$11)""",
            nid, idem, req.tenant_id, req.recipient.user_id, tpl.id,
            tpl.version, sha256(req.data), req.category,
            PRIORITY[req.category], req.send_at, req.collapse_key)
        await db.execute(
            "INSERT INTO outbox (topic, key, payload) VALUES "
            "('notify.accepted', $1, $2)", str(nid), json.dumps({"id": nid}))
    return {"notification_id": nid}
```

```python
# ── Route: every suppression reason, in one place, in a fixed order ──────
async def route(n: Notification) -> list[Channel]:
    prefs   = await preferences.get(n.user_id)        # 10 s TTL, pub/sub bust
    allowed = []
    for ch in n.requested_channels:
        if not prefs.allows(ch, n.category):
            metrics.inc("notify.suppressed", {"why": "preference", "ch": ch})
            continue
        if await suppressions.blocks(n.user_id, ch, n.category):
            metrics.inc("notify.suppressed", {"why": "suppression", "ch": ch})
            continue
        if n.priority >= PRIORITY_ENGAGEMENT and in_quiet_hours(n.user_id):
            n.scheduled_for = next_permitted_instant(n.user_id)   # IANA tz
            metrics.inc("notify.deferred", {"why": "quiet_hours"})
            continue
        if not await budget.allows(n.category, ch):
            metrics.inc("notify.suppressed", {"why": "budget", "ch": ch})
            continue
        allowed.append(ch)
    if not allowed:
        await mark_suppressed(n, reason="all_channels_blocked")   # NOT failed
    return allowed

def in_quiet_hours(user_id) -> bool:
    tz = preferences.timezone(user_id)                 # IANA, e.g. Europe/London
    if tz is None:
        return is_quiet_utc(now())                     # conservative fallback
    local = datetime.now(ZoneInfo(tz))                 # NOT a stored offset:
    return local.hour >= 21 or local.hour < 8          # DST-safe (§4.8)
```

```python
# ── Send: retry, failover, provider idempotency, and error classification ─
async def send(delivery: Delivery) -> None:
    adapter = adapters.primary(delivery.channel)
    try:
        msg_id = await asyncio.wait_for(
            adapter.send(delivery, idempotency_key=delivery.key),  # §4.7
            timeout=adapter.timeout)                     # sms 1.5 s, email 5 s
    except (TimeoutError, ProviderDown):
        adapter.record_failure()                         # breaker (L77)
        alt = adapters.secondary(delivery.channel)
        if alt and delivery.channel is Channel.SMS and delivery.is_critical:
            msg_id = await alt.send(delivery, idempotency_key=delivery.key)
        else:
            raise Retryable()                            # queue and retry
    except ProviderPermanent as e:                       # 4xx: no point retrying
        await to_dlq(delivery, reason=str(e))            # L75
        return
    await db.execute("""UPDATE deliveries SET state=1, provider=$2,
                        provider_msg_id=$3, attempt=attempt+1
                        WHERE notification_id=$1 AND channel=$4""",
                     delivery.nid, adapter.name, msg_id, delivery.channel)
```

```python
# ── Webhooks: signed, idempotent, monotonic, and never blocking ──────────
@app.post("/webhooks/{provider}")
async def webhook(provider: str, request: Request):
    body = await request.body()
    if not adapters[provider].verify_signature(body, request.headers):
        raise HTTPException(401)
    ev = adapters[provider].parse_webhook(body)          # (msg_id, state, ts)

    # GREATEST = monotonic: a late "sent" cannot overwrite "delivered"
    await db.execute("""UPDATE deliveries SET state = GREATEST(state, $1)
                        WHERE provider = $2 AND provider_msg_id = $3""",
                     ev.state, provider, ev.msg_id)

    if ev.state in (BOUNCED_HARD, COMPLAINED):
        await suppressions.add(ev.address, reason=ev.reason)
        await cache.invalidate(f"pref:{ev.address}")     # N5: 10 s budget
    await bus.publish("notify.state", ev)                # async, non-blocking
    return {"ok": True}                                  # ALWAYS 200: a
    # provider that gets a 5xx will retry the webhook for hours
```

```yaml
# ── The alerts that make the SLOs real ──────────────────────────────────
alerts:
  - alert: OTPDeliveryLatencyBreach
    expr: histogram_quantile(0.99, rate(notify_e2e_seconds_bucket{category="critical"}[5m])) > 3
    severity: page                    # a login outage in progress

  - alert: MarketingQueueBlockingCritical
    expr: notify_queue_depth{priority="critical"} > 100
    severity: page                    # the separation has failed somehow

  - alert: UnsubscribeLag
    expr: time() - notify_unsubscribe_last_applied_timestamp > 10
    severity: page                    # a compliance breach with a clock on it

  - alert: ProviderErrorRate
    expr: rate(notify_provider_errors_total[5m]) / rate(notify_sends_total[5m]) > 0.05
    severity: page                    # failover should already have fired

  - alert: DuplicateSendDetected
    expr: increase(notify_duplicate_detected_total[1h]) > 0
    severity: ticket                  # a dedupe bug; find it before users do
```

## 9. Numerical example

BookNook: 82.8 M notifications/day, 958/s average, four priorities, four channels.

**Step 1 — priority separation, quantified.**

```
  without separation (one FIFO, one consumer pool at 20,000/s):
      marketing arrival 18,519/s for 600 s = 11.1 M messages
      queue drain at 20,000/s ⇒ backlog grows only if arrival > capacity;
      it does not, BUT latency = queue depth / throughput:
        · the queue is 11.1 M deep by the end of the campaign
        · a critical message arriving at t=300 s sits behind ~5.5 M messages
        · its wait = 5,500,000 / 20,000 = 275 s = 4 min 35 s  ✗ (SLO 3 s)
  with separation (4 pools):
      critical pool: 3 pods, 800 msg/s capacity, arrival 1 % of 958/s ≈ 10/s
      p99 latency measured 1.9 s  ✓
  ⇒ the separation is worth 275 s → 1.9 s on the one metric that is a login
    outage. It costs 3 always-warm pods (≈£210/month).
```

**Step 2 — provider quota: the constraint the capacity step found.**

```
  push peak 18,519/s vs FCM project limit 10,000/s (600,000/min)
  ⇒ deficit factor 1.85×
  options:
    (a) two FCM projects (shard by user_id % 2) ⇒ 20,000/s, headroom 8 %
        cost: one more service account, 2× quota, no extra money
    (b) shape to 10,000/s ⇒ the campaign drains in 1.85× its arrival window
        (a 10-minute run takes 18.5 min) — acceptable for marketing
    (c) buy a quota increase (Google's process, 2–3 weeks)
  ⇒ BookNook chose (a)+(b): two projects for headroom, shaping as the
    policy, so a third project is never needed and the campaign still
    completes inside its window. WITHOUT this step in the design, the
    campaign silently 429s and 46 % of pushes are dropped.
```

**Step 3 — cost, and why channel selection is a product decision.**

```
  monthly volume × unit cost:
      email   360 M × £0.00008    = £28,800
      push    1.2 B × £0.0000002  = £240
      SMS      24 M × £0.045      = £1,080,000   ← 96 % of the entire bill
      in-app  900 M × 0           = £0
      ─────────────────────────────────────────────────
      total                       ≈ £1,109,040/month

  ⇒ SMS is 0.9 % of messages and 96 % of the cost. Every design decision
    about SMS is a decision about the budget:
      · OTP by SMS: 24 M/month, of which 61 % could be push-TOTP or email
        ⇒ moving half of those to push/email saves £329,000/month
      · failover SMS on every failed push would multiply the bill by ~8×
        ⇒ NEVER fail over push→SMS automatically. Fail push→email→nothing.
  ⇒ this single table is the most valuable artefact in the design, and it
    comes from the capacity step, not from the architecture diagram.
```

**Step 4 — dedupe: the arithmetic that makes idempotency cheap.**

```
  idempotency keys: 82.8 M/day, retained 7 days = 580 M keys
      at 60 B (UUID + tenant) = 35 GB in a unique index, or 12 GB as a
      Redis set with a TTL ⇒ use the DATABASE unique index (it must be
      durable; a Redis-only key loses dedupe across a flush) and let the
      index serve the check. 0.2 ms per lookup.
  observed duplicate rate without the key: 0.11 % (retries from 5 producers)
      = 91,000 duplicate sends/day, of which 8,400 reached customers in the
      §7 incident ⇒ the key removed ~£40/month of push cost and an unbounded
      amount of support load.
  ⇒ the dedupe is not expensive; it is a unique index you already need.
```

**Step 5 — quiet hours and digests, in numbers.**

```
  quiet-hours suppression: 31 % of engagement sends arrive in a user's
      21:00–08:00 window (the platform's traffic is global, so this is the
      expected fraction, not a bug) ⇒ 7.7 M messages/day deferred to 08:00
      local. Deferral, not discard: the 08:00 burst is 7.7 M pushes over
      ~6 hours of timezone spread = 356/s extra, which is inside capacity.
  digest: 2.4 M "reply to your review" messages/day → 180 k digests/day
      (−92.5 %), push opt-outs down 41 % over 6 weeks A/B, engagement with
      the digest 2.3× higher per message than with the individual ones.
  ⇒ BOTH are product wins with a cost side-effect; neither is primarily a
    cost measure, and presenting them as one is how you lose the argument
    with the product team.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **Central notification platform** | One retry/dedupe/consent implementation | A new service on the critical path | More than ~2 senders exist (always) |
| **Four priority queues** | Critical never queues behind bulk | More topics, more consumer pools, idle capacity | Any system where an OTP matters |
| **Caller-supplied idempotency key** | No duplicates on retry | API friction; key lifecycle | Always |
| **Store rendered bodies** | Exact audit trail | 99 GB/day | Regulated content only; otherwise re-render |
| **Digest by default** | Fewer, better messages; fewer opt-outs | Delayed information | Engagement categories |
| **Per-message send (no digest)** | Immediate | Notification fatigue, opt-outs | Transactional and critical |
| **SMS for OTP** | Universal reach, no app | **96 % of the cost** | Users without the app; step up to push-TOTP |
| **Push→SMS failover** | Higher delivery | 8× the bill | Never automatic |
| **Provider failover** | Survives provider outages | Double-send risk without idempotency; test burden | All channels, tested monthly |
| **10-second unsubscribe propagation** | Compliance | Cache invalidation on the send path | Always |
| **Sync delivery status** | Accurate dashboards | Webhook volume on the request path | Batch status updates every 2 s |

## 11. Failure scenarios

**11.1 — Duplicate sends from a retry.** The producer retried an accepted request; 8,400 customers got 2–3 emails (§7). **Fix:** mandatory idempotency key with a unique index, returning the original id.

**11.2 — OTP starvation behind marketing.** A campaign filled the shared queue; OTPs waited 6 min 40 s (§7). **Fix:** separate priority queues and consumer pools; an alert on `critical` queue depth > 100.

**11.3 — Provider outage.** SES degraded for 3 h; without failover, order confirmations stop (§7). **Fix:** circuit breaker per provider (L77), queue for email, secondary for SMS critical only, and a monthly drill.

**11.4 — Unsubscribe honoured late.** A nightly batch meant consent changes took 4 h; a £9,000 fine (§7). **Fix:** write the suppression synchronously, invalidate the preference cache, and alert on `unsubscribe_last_applied` age > 10 s.

**11.5 — DST moves quiet hours.** A stored UTC offset meant 210,000 sends landed at 01:00–04:00 local (§7). **Fix:** IANA timezones and DST-boundary tests for every zone.

**11.6 — Webhook storm.** A provider retried 1.4 M webhooks after a 20-minute outage of ours; each triggered a full row update and a cache invalidation. **Fix:** idempotent `GREATEST` updates, batched writes, and a queue in front of the webhook handler; the handler must always return 200.

**11.7 — Template edit changes an in-flight message.** A v12 → v13 edit during a campaign produced two different messages for the same event. **Fix:** pin the version at accept (§4.5).

**11.8 — The campaign that silently dropped half its pushes.** FCM project quota 10,000/s against an 18,519/s peak; 46 % were 429'd and the retries compounded it. **Fix:** two projects, shaping to quota, and a per-tenant admission limit (L95).

**11.9 — Hard bounces ignored.** 91,000 emails/day went to dead addresses, damaging sender reputation until the domain was throttled by the provider. **Fix:** ingest bounce webhooks into `suppressions` synchronously, and alarm on the bounce rate > 5 %.

## 12. Alternatives

| Instead of | Consider | When |
|---|---|---|
| Building a platform | A managed notification vendor (SendGrid-style, OneSignal) | You have no compliance-critical needs and few channels |
| A platform | A library inside each service | One service, one channel, one template |
| Four queues | Two (critical vs bulk) | Your scale is small and the OTP path is short |
| Digests | Aggressive per-user rate limiting | The problem is volume, not relevance |
| SMS OTP | Push-TOTP or email OTP | The user has the app installed |
| Provider failover | A single provider with a large quota | The provider is genuinely multi-region and you have an SLA |
| Own state machine | Provider webhooks only | You never need to answer "did they get it?" |

## 13. When to use it

- **A central platform** as soon as two teams send anything, for dedupe, consent and auditability alone.
- **Priority separation** in any system where one class of message is an outage when delayed.
- **Provider abstraction with failover** where a provider outage would stop authentication or payment messages.
- **Digests and quiet hours** for any engagement-driven channel, before the opt-out rate forces it.
- **A delivery state machine** wherever "did it arrive?" is a support question.

## 14. When *not* to use it

- **For a single transactional email** — a library call is fine.
- **A platform before there is a second sender** — the abstraction costs more than it saves at one.
- **Synchronous in-app notifications through the pipeline** — in-app is a write to a feed, not a send.
- **Automatic push→SMS failover** — the cost multiplication is unbounded.
- **Digests for OTPs or security alerts** — never; the whole point is immediacy.
- **Storing rendered bodies "just in case"** unless regulation requires the exact artefact.

## 15. Common mistakes

1. **No caller idempotency key** (§11.1).
2. **One queue for all priorities** (§11.2).
3. **Quiet hours from a stored offset** rather than an IANA timezone (§11.5).
4. **Unsubscribe propagated by batch** (§11.4).
5. **Template not pinned at accept** (§11.7).
6. **Push→SMS failover** and an eight-figure SMS bill (§9 step 3).
7. **No per-tenant admission control**, so one campaign starves everything.
8. **Webhook handlers that return 5xx**, causing provider retry storms (§11.6).
9. **Bounces not feeding suppressions**, burning sender reputation (§11.9).
10. **Assuming `SENT` means delivered**, then arguing with customers about definitions (§4.6).
11. **No reconciler** for webhooks that never arrive.
12. **Rendering on the send path** instead of at accept, so a template bug is discovered 40 minutes into a campaign.
13. **No spend meter**, so an SMS campaign discovered at the end-of-month invoice.
14. **Ignoring provider quotas in the capacity step** (§9 step 2).

## 16. Interview perspective

**What is actually being tested.** Whether you treat notifications as a **fan-out pipeline with a compliance and cost surface**, not as "call SES". Strong candidates: separate priorities, put an idempotency key on the accept API, compute the cost table per channel, decide the fail mode and provider strategy per channel, and know that `SENT ≠ DELIVERED`. Weak candidates list the channels and stop.

**Answers that land well:**

- *"Walk me through a notification."* — "Accept with an idempotency key and pin the template version, so a retry sends nothing new and a template edit cannot change a message in flight. Route against preferences, suppressions, quiet hours in the user's IANA timezone, and the category budget. Enqueue to one of four priority queues — an OTP must never sit behind a marketing run: we measured 275 seconds of wait in a shared queue versus 1.9 seconds separated. Then a channel adapter with a circuit breaker, provider-level idempotency and per-channel failover, and a delivery state machine fed by signed, idempotent webhooks."
- *"How do you make sure only one is sent?"* — "Exactly-once effect, not exactly-once delivery: the caller supplies an idempotency key, there is a unique index on it, and a retry returns the original notification ID. That fixed a real incident where 8,400 customers got three identical shipping emails."
- *"How do you handle an email provider outage?"* — "Per channel, on purpose. Email queues: a three-hour backlog is recoverable and nobody needs it in two seconds. Critical SMS fails over to a second provider with a 1.5-second timeout and a provider-level idempotency key so it cannot double-send. Push does not fail over to SMS — that would multiply an already 96 %-of-budget line item by eight. And we drill it quarterly, because a failover path nobody has executed is not a failover path."
- *"What does it cost?"* — "SMS is 0.9 % of messages and 96 % of the bill — about £1.08 M a month against £29 k for email and £240 for push. Which means every SMS decision is a budget decision: moving half the SMS OTPs to push-TOTP saves £329 k a month."

**Common interview tasks:** design accept/route/send/track; handle duplicates; separate priorities; choose provider and failover strategy; design the delivery state machine; handle quiet hours and digests; cost the channels; design unsubscribe propagation.

**Red flags interviewers watch for:**

- "Just call the provider" with no retry, dedupe or queue.
- One queue for everything.
- No idempotency anywhere.
- Unsubscribe as a nightly job.
- Quiet hours with a fixed UTC window for a global user base.
- Automatic SMS failover for push.
- `SENT` treated as delivered.
- No cost arithmetic, and no awareness that SMS dominates the bill.
- No reconciler for missing webhooks.

## 17. Knowledge check

**Conceptual**

1. Explain why priority separation is the defining property of a notification service, and compute the OTP wait in a shared FIFO with 18,519/s of marketing arrivals. Then state what the separation costs and why the cost is irrelevant.
2. Explain the difference between `SENT`, `DELIVERED` and `OPENED`, and give a measured reason for treating each with suspicion. Then explain what a reconciler adds and why it must be able to say `UNKNOWN`.
3. Explain the failover policy per channel (email, SMS, push) and why they differ. Then explain the mechanism that prevents a failover from double-sending, and what happens on channels that lack it.

**Practical scenario**

4. Design BookNook's notification platform: 82.8 M messages/day across four channels and four priorities, 8 M users in 40 countries, a £1.1 M/month budget dominated by SMS, a 10-second unsubscribe SLO and a 3-second OTP SLO. Give the requirements and capacity tables (including the provider quotas), the API with idempotency, the data model with dedupe and suppressions, the four-queue design with consumer pools, the routing order of checks, the quiet-hours implementation, the digest policy, the delivery state machine, the failover policy per channel, the alerts, and the cost table. Then state the two changes that would halve the monthly bill without breaking the SLOs.

**Calculation**

5. 40 M pushes/day with a peak factor of 40, and an FCM project quota of 600,000/minute. (a) Compute the peak sends/s. (b) Compute the quota in sends/s and the deficit factor. (c) Compute the drain time for a 10-minute campaign under shaping. (d) State two design responses and the headroom each provides.

6. Daily volume: 12 M email at £0.00008, 40 M push at £0.0000002, 800 k SMS at £0.045, 30 M in-app at £0. (a) Compute the monthly cost per channel and the total. (b) Compute SMS's share of messages and of cost. (c) If 61 % of SMS are OTPs and half of those move to push-TOTP, compute the monthly saving. (d) Compute the cost of automatically failing over every failed push (2 % of 40 M) to SMS, and state the conclusion.

**Progress checklist**

- Phase 1 — Foundations (L1–17): **complete** ✅
- Phase 2 — Scaling (L18–39): **complete** ✅
- Phase 3 — Data and Storage (L40–63): **complete** ✅
- Phase 4 — Distributed Systems (L64–92): **complete** ✅
- Phase 5 — Complete system designs (L93–106):
  - [x] 1. Design Pastebin (L93)
  - [x] 2. Design a URL shortener (L94)
  - [x] 3. Design a rate limiter (L95)
  - [x] **4. Design a notification service (L96) ← you are here**
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

**NEXT LESSON: Phase 5, Topic 5 — Design search autocomplete**

---

---

# Lesson 97 — Design Search Autocomplete

*Phase 5, Topic 5*

## 1. Simple definition

**The problem.** As a user types into a search box, return the **top 10 suggestions** for the characters so far, ranked so that the thing they probably mean is first, within a latency budget so tight that it must feel instantaneous.

**Why that word — "instantaneous" — is the whole design.** A keystroke arrives every 100–200 ms. If a suggestion request takes 300 ms, suggestions arrive *behind* the typing, the list flickers, and users stop reading it. The practical budget is **p99 < 100 ms end to end**, of which the network is 30–60 ms, leaving **20–30 ms of server time** for the lookup, the ranking, and the serialisation. That budget rules out almost every database, and dictates a design in which the answer is **precomputed and held in memory**.

Terms defined before use:

- **Prefix**: the characters typed so far (`har`).
- **Suggestion / completion**: a candidate phrase returned for that prefix (`harry potter and the philosopher's stone`).
- **Top-K**: the K highest-scoring suggestions for a prefix (K = 10).
- **Prefix hash table**: a map from prefix → precomputed top-K list. The workhorse.
- **Trie**: a tree keyed by character, sharing prefixes. Elegant, memory-hungry.
- **Score**: the ranking value — usually a function of historical query frequency with a recency decay.
- **Aggregation pipeline**: the offline job that turns query logs into counts and rebuilds the index.
- **Fast lane**: a separate, quickly-updated slice of the index for trending terms.
- **Keystroke multiplier**: requests per completed query (each character generates a request). At ~4.2, this multiplies your QPS.
- **Rerank**: a cheap second pass over the top ~50 candidates using personal or contextual signals.

The one sentence to carry out of this lesson: **autocomplete is not a search problem, it is a precomputation problem — you do the expensive work offline, key the results by prefix, and serve 10 precomputed strings from memory in under 20 milliseconds.**

## 2. Why it exists

**Because the latency budget forbids the obvious approach.** "Query the search engine with the prefix and take the top 10" costs 40–120 ms per request (L47) — over budget before the network. It also multiplies: 200 M searches/day × 4.2 keystrokes = **840 M** requests/day, so the "obvious" approach means running the full search engine at **9,700 rps minimum and 38,000 at peak**, for results that are 90 % identical to what they were a second ago. Precomputation converts that into a memory lookup.

**Because the ranking signal comes from the logs, not from the documents.** The best completion for `har` is not "the most important document starting with har" — it is "what people who typed `har` actually searched for, weighted by recency". That is a **feedback loop**: logs → counts → ranking → serving, rebuilt on a schedule. It also means the index quality is an analytics problem, and the pipeline matters as much as the serving path.

**Because freshness and cost pull in opposite directions.** A new bestseller released this morning should complete for its title within minutes, but rebuilding a 6M-prefix index takes 20–40 minutes. The resolution is a **two-tier index**: a large, slowly rebuilt base and a small, rapidly rebuilt fast lane merged at query time (§4.6).

**Because the index is bigger than it looks.** 2.4 M distinct suggestion terms × ~14 prefixes each = **33.6 M prefix entries**, each holding 10 suggestions — roughly 5 GB uncompressed. That is not "small", and the memory arithmetic determines sharding, pruning and replication (§9 step 2).

**And because autocomplete is a visible abuse and quality surface.** Autocomplete suggestions are published on your homepage; an injected term becomes a defamatory or inappropriate suggestion (a real class of incident, with legal consequences), and SEO spammers try deliberately to inject phrases. Filtering is a **product-safety** requirement, not a nicety.

## 3. Real-world analogy

**A dictionary's thumb index.**

A physical dictionary has semicircular tabs cut into the page edges — one per letter — so you can find the "H" section without opening the book. Now imagine a *better* dictionary: on each tab is printed **the ten most-looked-up words in that section**. You do not need to open the book at all for the common case; the answer is on the outside of the pages, where a glance finds it.

That is autocomplete exactly: the tab is the prefix, and the ten words printed on it are the precomputed top-K.

The analogy carries the design's four decisions precisely:

**The work is done by the printer, not the reader.** Someone had to decide which ten words go on the "H" tab, and that requires knowing what people look up — which is the aggregation pipeline. The reader's experience is instantaneous because the expensive part happened in advance. **This is the whole lesson**: move work from the keystroke to the batch job.

**The tabs are cut where people actually look.** A dictionary does not spend a page-edge tab on "Xy" because nobody looks there. That is **prefix pruning**: prefixes with negligible query volume are not worth the memory (§9 step 2). Pruning 80 % of the prefixes costs almost nothing in coverage and saves four-fifths of the memory.

**A thumb index cannot be re-cut cheaply.** You cannot add a new tab to a printed dictionary in five minutes — you reprint. So how do you handle a word that becomes famous overnight? Put a **sticky note on the cover**: a small, cheap, frequently replaced supplement for the handful of terms that are suddenly popular. That is the fast lane, and recognising *why* you need it is the analytical point.

**And the printer filters what gets printed.** A dictionary does not print a slur on a tab. Somebody has an editorial policy, applied by the printer, reviewed after publication. Autocomplete without that step ships your worst-case backlog to the homepage.

The one place the analogy is dangerous: **the printed tabs cannot be personalised, but yours can.** A rerank step over the top 50 candidates — by language, by the user's history, by the section of the site they are in — is cheap and is what makes the feature feel intelligent. Just keep it *after* the precomputation, never inside it, or you multiply the batch job by the number of users.

## 4. Detailed technical explanation

### 4.1 Requirements

**Functional.**

| # | Requirement |
|---|---|
| F1 | Return the top 10 suggestions for a prefix of ≥ 2 characters |
| F2 | Suggestions are real searchable entities (titles, authors, series) **and** historical queries |
| F3 | Ranked by popularity with a recency decay; deterministic for a given index snapshot |
| F4 | Multi-language, with per-language indexes |
| F5 | Basic typo tolerance (`harry poter` → `harry potter`) for prefixes ≥ 5 characters |
| F6 | Personal rerank using the user's recent activity (optional, never a hard filter) |
| F7 | Trending terms appear within **5 minutes** of becoming popular |
| F8 | Adult and unsafe terms filtered before publication |
| F9 | Only suggest terms that return results (no dead ends) |

**Non-functional.**

| # | Requirement | Number |
|---|---|---|
| N1 | Latency | p99 < 100 ms end to end; **server-side p99 < 25 ms** |
| N2 | Availability | 99.99 % — an autocomplete outage should not stop search |
| N3 | Scale | 200 M searches/day, 4.2 keystroke multiplier ⇒ 840 M requests/day |
| N4 | Index freshness | Base: hourly. Fast lane: 5 minutes |
| N5 | Memory | ≤ 16 GB per serving node before replication |
| N6 | Degradation | Empty suggestions on timeout (never block the search) |

### 4.2 Capacity estimation

```
  QUERIES     200,000,000 searches/day
  KEYSTROKES  × 4.2 = 840,000,000 autocomplete requests/day
              (measured: users type 4.2 characters per submitted query on
               average, and every character ≥ 2 triggers a request; the
               multiplier is the number that surprises people)
              = 9,722/s average
              peak factor × 4 = 38,889/s

  RESPONSE    10 suggestions × ~40 B = 400 B + 200 B overhead = 600 B
              ⇒ egress 38,889 × 600 B = 23 MB/s at peak

  INDEX
      suggestion terms:
          8,000,000 titles + 1,200,000 authors + series + 900,000 popular
          queries, normalised and deduplicated → 2,400,000 distinct terms
      prefixes per term: average term length 12 chars ⇒ 11 prefixes (≥ 2)
          ⇒ 2,400,000 × 14 (allowing for longer titles) = 33,600,000 prefixes
      per prefix: 10 suggestion ids (uint32) + 10 scores (uint16) + 24 B
          header = 84 B, allocated at 120 B with fragmentation
      ⇒ 33,600,000 × 120 B = 4.03 GB of posting lists
      + prefix keys: 33.6 M × ~30 B = 1.0 GB
      ⇒ TOTAL ≈ 5.0 GB uncompressed, ~3.1 GB after pruning (§9 step 2)

  SERVING
      3.1 GB fits in one 16 GB node with headroom, but availability needs
      replication: 8 shards × 2 replicas = 16 nodes of 2 GB slices
      (sharded by prefix hash, so a shard holds ~420 MB — cache-friendly)
      ⇒ 16 nodes × £70/month ≈ £1,120/month. Cheap, because there is no
        database in the serving path and no per-request I/O.
```

### 4.3 API

```
GET /v1/suggest?q=har&lang=en-GB&limit=10&context=book
     (edge-cached 60 s per (q, lang, context) — a huge win: 71 % of
      keystrokes are repeats of a small set of prefixes)

  →  200 {
       "prefix": "har",
       "suggestions": [
         { "text": "harry potter and the philosopher's stone", "type": "title",
           "id": "bk_9981", "score": 98213 },
         { "text": "harry potter",          "type": "series", "id": "sr_12" },
         { "text": "haruki murakami",       "type": "author", "id": "au_441" }
       ],
       "complete": true                 # false if the prefix was truncated
     }
     Headers: Cache-Control: public, max-age=60
     Timeout behaviour: the CLIENT abandons at 150 ms and renders nothing —
     autocomplete must never delay or block the search it is assisting.

GET /v1/suggest?q=harry+poter&lang=en-GB&fuzzy=1     # typo path (§4.7)

POST /internal/index/publish { snapshot, version, langs }   # pipeline → servers
```

### 4.4 The index: prefix hash table versus trie

```
  A. PREFIX HASH TABLE (chosen)
     key: prefix string → value: a packed top-K list (10 × (id, score))
     lookup: one hash, one memory read of < 1 KB ⇒ 40–150 µs
     + O(1) lookup, cache-friendly, trivially shardable by hash(prefix)
     + a top-K list is exactly what the API returns; no traversal needed
     − memory: every prefix stored as a full string (33.6 M keys)
     − no prefix enumeration (you cannot ask "all prefixes of har"), which
       you do not need at query time

  B. TRIE
     each node: char → child pointer, plus a top-K list at the node
     + shares prefixes structurally; no per-prefix string storage
     + supports range enumeration and efficient offline pruning
     − pointer chasing: a 3-character prefix costs 3 indirections, and in a
       large trie those are cache misses (~100 ns each) — a 12-character
       prefix costs 12
     − measured in BookNook's prototype: trie p99 6.8 ms vs hash p99 0.9 ms
       at 38,000 rps, because of cache locality, not algorithmic complexity
     − harder to shard (a subtree is a shard, and hot subtrees are hot)

  C. SUFFIX ARRAY / FM-INDEX
     + compact, supports substring and fuzzy search naturally
     − O(log n) binary search with cache misses; 15–40 ms for a lookup at
       this index size — outside the budget
     ⇒ right choice for FULL search (L47), wrong for autocomplete

  ⇒ the hybrid used by most production systems: build a TRIE offline
    (because it makes aggregation, pruning and enumeration easy), then
    FLATTEN it into a prefix hash table for serving. Best of both: the
    pipeline reasons about a tree; the server does one hash lookup.
```

### 4.5 Scoring and ranking

```
  BASE SCORE (computed offline, stored as uint16 after quantisation):
      score = Σ over the last 30 days of
                (query_count_in_day × decay^(days_ago))
              + document_boost          # a published title outranks a query
              + click_through_boost     # suggestions that get clicked
              − report_penalty          # user-reported suggestions

      decay = 0.94 per day (30-day half-life ≈ 11 days) ⇒ a term that was
      popular 3 weeks ago scores 0.94^21 = 27 % of its peak, so yesterday
      matters and last month is history.

  QUANTISATION: scores are stored as log2(score) × 1000 in a uint16, so a
  top-K list is 10 × 6 B = 60 B rather than 10 × 12 B. The comparison order
  is preserved for any ratio below ~2^13, which is all ranking needs.

  RERANK (online, over the top 50 candidates):
      final = base_score × (1 + personal_boost) × (1 + context_boost)
      personal_boost: has the user read/searched this author or series
                      recently? (+0.35 max)
      context_boost:  are we in the "children's" section? (+0.5 for
                      children's titles)
      ⇒ a multiply, not a filter: a personal signal can reorder, never
        exclude, so an empty-history user still gets good results.
  ⚠ RERANK MUST NOT BE INDIVIDUALISED PRE-COMPUTATION. A per-user top-K is
    impossible at 8 M users; a multiply over 50 candidates costs 0.8 µs.
```

### 4.6 The aggregation pipeline: from logs to a servable index

```
  EVERY HOUR (base index):
    1. COLLECT     query logs from the search service (Kafka, L70)
                   + entity catalogue (titles/authors) + click events
    2. NORMALISE   lowercase, unicode NFKC, strip punctuation, collapse
                   whitespace, language-detect ⇒ one normalised surface form
    3. AGGREGATE   count by (normalised_query, day), last 30 days
                   at 200 M queries/day: 30 days = 6 B events
                   ⇒ stream-aggregate in Spark/Flink into 2.4 M terms with
                     30 daily counts each (L57)
    4. FILTER      adult/unsafe terms (a maintained blocklist + a classifier)
                   spam/SEO-injected terms (see §4.9)
                   terms with < 100 counts in 30 days (long tail)
                   terms with < 1 result (no dead ends — needs a result
                   count per term, obtained by a cheap existence probe)
    5. EXPAND      each surviving term → all its prefixes (≥ 2 chars)
    6. MERGE       for each prefix, merge candidate top-K lists (a heap of
                   size 10 per prefix) ⇒ top 10 by score
    7. PRUNE       drop prefixes whose best candidate scores below a floor
                   (they would return junk) and prefixes with < 10 candidates
                   and no volume (§9 step 2)
    8. SERIALISE   a compact binary snapshot: sorted prefix keys + packed
                   posting lists + a suggestion-id → text/payload table
    9. PUBLISH     upload to object storage; servers load it (mmap), verify a
                   checksum, swap atomically, keep the old snapshot for a
                   one-command rollback
  EVERY 5 MINUTES (fast lane):
    - aggregate only the last 60 minutes of queries with a high count
    - take the top 2,000 trending terms, expand to prefixes (~20,000)
    - publish a tiny snapshot (2 MB) that servers merge OVER the base at
      query time: fast-lane result first, then base results, dedupe by id
    ⇒ cost: 1 % of the base job's runtime, and it is what makes a new
      release appear in autocomplete within minutes.
```

### 4.7 Typo tolerance and the "no dead ends" rule

```
  TYPO TOLERANCE, done cheaply:
      only for prefixes ≥ 5 characters, and only over CANDIDATES:
        1. exact prefix lookup (fast path, 99.1 % of requests)
        2. on < 3 results, delete-one-character variants of the prefix,
           looking each up (a transposition-aware variant list: ~3 × len
           keys ⇒ 15 hash lookups at 150 µs each = 2.2 ms)
        3. merge, rerank, dedupe
      ✗ NOT full edit-distance search over 2.4 M terms — that is 40–120 ms
        and belongs to the full search engine (L47)
      measured: 0.9 % of requests take the fuzzy path; p99 for those 9.4 ms

  NO DEAD ENDS:
      a suggestion that returns zero results is worse than no suggestion —
      the user clicks and sees "no results", which is a broken promise.
      ⇒ every term carries a result_count (an existence probe during
        aggregation, not a live search); terms with 0 are excluded at
        step 4 of the pipeline.
      ⚠ and this must be rechecked as the catalogue changes: a delisted book
        makes its suggestions dead. The hourly pipeline re-probes, which is
        the reason the base index cannot be "build once".
```

### 4.8 Where it is served, and what must not be on the path

```
  MUST NOT appear on the request path:
      · any database (SQL or document) — 1–10 ms is 5–50 % of the budget
      · any network hop beyond one: edge → one shard, no fan-out, no scatter
      · any per-user computation beyond a 50-candidate multiply
      · logging synchronously (batch it; a keystroke log is 840 M events/day)

  THE THREE TIERS:
      1. EDGE CACHE (CDN) — 71 % of keystrokes repeat a small prefix set;
         a 60 s TTL is safe because the index itself is only hourly
      2. SERVING NODES — 8 shards × 2 replicas, index in memory (mmap'd
         snapshot), one hash lookup + rerank + serialise ≈ 1.2 ms
      3. FALLBACK — if a shard is unavailable, serve from the local
         process's copy of the previous snapshot, or return an empty list
         and let the search box show history only. NEVER block: the search
         must work while autocomplete is broken.

  LATENCY BUDGET (server side, p99):
      parse + validate            0.1 ms
      edge/route                  — (network 30–60 ms outside this budget)
      shard lookup                0.9 ms
      fast-lane lookup + merge    0.2 ms
      rerank (50 candidates)      0.8 ms
      serialise 10 × 40 B         0.1 ms
      ─────────────────────────────────
      total                       2.1 ms   ⇒ 10× headroom against N1
```

### 4.9 Quality, abuse and personalisation limits

```
  ADULT / UNSAFE TERMS — three layers, because one is never enough:
      1. a maintained blocklist (exact + substring), applied at pipeline
         step 4, versioned and reviewed weekly
      2. a classifier flag with a human review queue (terms above a score)
      3. an ONLINE last-resort filter at the serving node, so a bad term
         published by mistake can be suppressed in < 60 s rather than
         waiting for the next hourly rebuild
      ⇒ this is the layer that has caused real-world incidents at large
        platforms; treat it as a product-safety requirement with an owner.

  SEO / INJECTION ABUSE: an attacker searches for a term 500,000 times
  from a botnet so it enters the top-K and gets served on your homepage.
      · pipeline step 4 counts DISTINCT SESSIONS with bot detection applied,
        not raw requests
      · a term whose count is > 90 % from new sessions with no clicks is
        quarantined rather than promoted
      · rate limiting at the search API (L95) bounds the raw injection rate

  PERSONALISATION LIMITS: allowed signals are (a) the user's own recent
  searches, (b) their library/reading history, (c) site context. NOT
  allowed: cross-user inference, or anything that would surface another
  user's private query history. Say this out loud in the design review; the
  privacy consequences of a "helpful" autocomplete are real.
```

## 5. Request / data flow

```
 USER TYPES "h" → "ha" → "har" → "harr" → "harry"  (5 requests in 1.1 s)
 ─────────────────────────────────────────────────────────────────────────
 t=0.00   "h"     → 2 chars not reached ⇒ CLIENT sends nothing
 t=0.18   "ha"    → edge cache: HIT (this prefix is in the top 200 by volume)
                    served in 9 ms, 10 suggestions
 t=0.39   "har"   → edge cache: HIT, 8 ms
 t=0.61   "harr"  → edge cache MISS → origin
                    · shard = hash("harr") % 8, one node, no fan-out
                    · base snapshot lookup: 10 candidates in 0.4 ms
                    · fast lane: 0 hits
                    · rerank with the user's reading history: +0.35 for
                      "haruki murakami", 0.6 ms
                    · serialise 410 B
                    server time 1.5 ms; total 52 ms (network dominates)
 t=0.83   "harry" → edge MISS → origin, 1.4 ms
                    · now the fast lane has "harry potter: the new release"
                      (published 3 minutes ago) ⇒ merged first
 t=1.10   the client ABANDONS any response arriving after 150 ms of its
          keystroke, so a stalled request never renders a stale list

 PIPELINE, hourly (off the request path entirely):
   6 B log events (30 days) → normalise → aggregate (2.4 M terms) →
   filter (adult, spam, dead ends) → expand (33.6 M prefixes) → merge
   top-10 (a 10-element heap per prefix) → prune (→ 6.2 M prefixes) →
   serialise 3.1 GB snapshot → publish → servers mmap and hot-swap
   total job runtime measured: 22 minutes on 40 cores.
```

## 6. ASCII diagram

```
                     ┌──────────────────────────────────────┐
   keystroke ───────►│ CLIENT (debounce 60–100 ms, abandon   │
                     │ at 150 ms, render nothing on timeout) │
                     └───────────────┬──────────────────────┘
                                     ▼
                     ┌──────────────────────────────────────┐
                     │ EDGE CACHE  (key: q+lang+context)     │
                     │ 60 s TTL · 71 % hit ratio             │
                     └───────────────┬──────────────────────┘
                                     │ 29 % miss
                     ┌───────────────▼──────────────────────┐
                     │ SERVING NODES  8 shards × 2 replicas  │
                     │  ┌─────────────────────────────────┐ │
                     │  │ BASE SNAPSHOT (mmap, 3.1 GB shard│ │
                     │  │ prefix → packed top-10 list)     │ │
                     │  ├─────────────────────────────────┤ │
                     │  │ FAST LANE (2 MB, 5-min refresh)  │ │
                     │  │ trending prefixes, merged over   │ │
                     │  └─────────────────────────────────┘ │
                     │  · rerank 50 candidates (0.8 µs)     │
                     │  · online safety filter (last resort)│
                     └──────────────────────────────────────┘

   OFFLINE PIPELINE (this is where all the work happens):

   query logs ──► normalise ──► aggregate 30 d ──► filter ──► expand ──►
   (6 B events)                  (2.4 M terms)   adult/spam    all prefixes
                                                 /dead-ends    (33.6 M)
        │                                                          │
        │                                                    merge top-10
        │                                                          ▼
        │                                                      prune → 6.2 M
        │                                                          ▼
        │                                                   serialise 3.1 GB
        │                                                          ▼
        │                                                  publish (versioned,
        │                                                   checksummed) ──►
        └──► FAST LANE every 5 min: last 60 min, top 2,000 terms ──► 2 MB

   THE SHAPE: the request path is ONE hash lookup; the batch path is a
   22-minute Spark job. Everything expensive is on the right-hand side.
```

## 7. Detailed example

**BookNook's catalogue autocomplete.**

```
  scale:
    200 M searches/day (books, authors, series, ISBN lookups)
    4.2 keystroke multiplier ⇒ 840 M suggest requests/day
    catalogue: 8 M titles, 1.2 M authors, 41 k series → 2.4 M normalised
    suggestion terms after filtering
  40 languages, 9 of them with enough volume for their own index
  context: the same box serves the shop, the "my library" tab and the
  children's section, which is why context_boost exists
```

**The measurements that shaped it:**

| Measurement | Value | Consequence |
|---|---|---|
| Edge hit ratio at a 60 s TTL | 71 % | Origin sees 11,278/s at peak, not 38,889 |
| Server p99 (hash + rerank) | 2.1 ms | 12× headroom; a trie prototype was 6.8 ms |
| Prefixes surviving the pruning floor | 6.2 M of 33.6 M (18 %) | Memory 5.0 GB → 3.1 GB; coverage loss 0.4 % of requests |
| Requests taking the fuzzy path | 0.9 % | p99 for those 9.4 ms — affordable |
| Base rebuild runtime | 22 min (40 cores) | Hourly is comfortable; 15-minutely would not be |
| Fast-lane terms | 2,000 terms → 20,000 prefixes | 2 MB snapshot, 40 s to publish |
| Suggestions returning 0 results **before** the dead-end filter | 6.1 % | Fixed to 0.02 % by the existence probe |

**The release-day story (the fast lane's justification):**

```
  2026-02-11, a sequel to a popular series released at 09:00.
    · by 09:04 the title had 44,000 searches and was the 3rd most typed
      prefix-completion, but the base index would not rebuild until 10:00
    · before the fast lane existed, autocomplete offered the FIRST book for
      six hours, and 11 % of shoppers who typed the sequel's prefix selected
      book 1 and abandoned at the product page (measured via a
      session-following funnel)
    · with the fast lane: the term entered the trending set at 09:03:40,
      published at 09:04:20, and appeared first for its prefix from 09:04:35
    · measured effect on release day: 4,100 fewer abandonment sessions
  ⇒ the fast lane is ONE extra small pipeline and it fixed a top-5 business
    problem. That is what "freshness" buys, and it is why you design for it
    rather than discovering it.
```

**The incident: a defamatory suggestion on the homepage.**

```
  2025-09-08: an organised campaign searched a fabricated phrase
  180,000 times in 3 hours; it entered the top-K for a two-letter prefix and
  was served on the shop homepage. Two hours elapsed before a human noticed.

  fixes:
    1. distinct-SESSION counting with bot detection (the 180,000 searches
       came from 900 sessions of 200 searches each ⇒ the count collapsed to
       900, well below the floor)
    2. a quarantine rule: terms with > 90 % new sessions and a click-through
       rate under 2 % are held for review rather than promoted
    3. an ONLINE suppression list of 40 terms, evaluable in 60 s, so a
       removal does not wait for the hourly rebuild
    4. a 24/7 on-call rotation for "suggestion safety" reports, with a
       15-minute removal SLA
  ⇒ after the fix, the same campaign produced 0 published suggestions.
```

## 8. Code or configuration example

```python
# ── Offline: build the prefix → top-K map (the pipeline's last step) ─────
def build_index(terms: dict[str, Term]) -> PrefixIndex:
    """terms: normalised term → (score, payload). 33.6 M prefixes at scale."""
    postings: dict[str, list[tuple[int, int]]] = defaultdict(list)

    for tid, t in terms.items():
        if t.score < SCORE_FLOOR or t.result_count == 0 or t.blocked:
            continue                              # §4.6 step 4
        for n in range(2, len(t.text) + 1):       # all prefixes, ≥ 2 chars
            prefix = t.text[:n]
            lst = postings[prefix]
            # bounded insert: keep only the best K, so memory is O(K), not
            # O(candidates). At 10 candidates per prefix this is a small
            # sorted insert, far cheaper than building then sorting.
            if len(lst) < K:
                lst.append((t.quantised_score, tid))
                lst.sort(reverse=True)
            elif t.quantised_score > lst[-1][0]:
                lst[-1] = (t.quantised_score, tid)
                lst.sort(reverse=True)

    # prune: a prefix whose best candidate is weak returns junk
    pruned = {p: v for p, v in postings.items()
              if v and v[0][0] >= PREFIX_SCORE_FLOOR and len(v) >= 3}
    return PrefixIndex(pruned)
```

```python
# ── Online: one hash, a fast-lane merge, a rerank. No I/O. ──────────────
class Suggester:
    def __init__(self, snapshot: PrefixIndex, fast: PrefixIndex, safety: Safety):
        self.base, self.fast, self.safety = snapshot, fast, safety

    def suggest(self, q: str, lang: str, ctx: str, user: UserCtx,
                limit: int = 10) -> list[Suggestion]:
        q = normalise(q, lang)                       # same as the pipeline
        if len(q) < 2:
            return []

        base = self.base.get(q, ())                  # ONE dict lookup
        fl   = self.fast.get(q, ())                  # tiny, 5-min fresh
        cand = merge_top(fl, base, key=lambda x: x[0], n=50)   # 50 candidates

        if len(cand) < 3 and len(q) >= 5:             # typo path, 0.9 %
            cand = merge_top(cand, self._fuzzy(q), key=lambda x: x[0], n=50)

        out = []
        for score, tid in cand:
            s = self.payload[tid]                     # suggestion text/type/id
            if self.safety.suppressed(s.text):        # ≤ 40 terms, hydrated
                continue                              # every 60 s
            final = score * (1 + user.personal_boost(s)) * (1 + ctx_boost(s, ctx))
            out.append((final, s))
        out.sort(reverse=True)
        return [s for _, s in out[:limit]]

    def _fuzzy(self, q: str):
        # delete-one-character variants only — 15 hash lookups, 2.2 ms total
        return [hit for v in deletion_variants(q) for hit in self.base.get(v, ())]
```

```python
# ── Client: debounce, abandon, and never let autocomplete block search ──
const suggest = debounce(async (q, ctrl) => {
  const t0 = performance.now();
  const r = await fetch(`/v1/suggest?q=${encodeURIComponent(q)}&lang=${lang}`,
                        { signal: AbortSignal.timeout(150) });
  if (performance.now() - t0 > 150) return;        // do not render a stale list
  render(await r.json());
}, 80);
input.addEventListener('input', e => {
  abortInFlight();                                  // cancel the previous one
  suggest(e.target.value, new AbortController());
});
// and on Enter: submit the search IMMEDIATELY — the suggestion is a
// convenience, never a dependency (N6, §4.8 tier 3)
```

```yaml
# ── Serving node: snapshot lifecycle and safety rails ───────────────────
snapshot:
  source: "s3://booknook-suggest/{lang}/current/"
  load: mmap            # no copy, no GC pressure, instant swap
  verify: sha256        # a corrupt snapshot must fail to load, not serve junk
  atomic_swap: true     # requests in flight keep the old pointer
  keep_previous: 2      # one-command rollback
  reload_check_seconds: 30
fast_lane:
  refresh_seconds: 300
  max_terms: 2000
safety:
  online_suppression_refresh_seconds: 60
  max_suppressions: 40
budget:
  server_deadline_ms: 25     # a hard deadline; return partial or empty

alerts:
  - alert: SuggestLatencyBreach
    expr: histogram_quantile(0.99, rate(suggest_seconds_bucket[5m])) > 0.025
    severity: page                  # the whole feature is the latency
  - alert: SuggestEmptyRateHigh
    expr: rate(suggest_empty_total[10m]) / rate(suggest_total[10m]) > 0.08
    severity: ticket                # an index or sharding problem
  - alert: FastLaneStale
    expr: time() - suggest_fastlane_published_timestamp > 900
    severity: page                  # release days go wrong here
  - alert: DeadEndSuggestions
    expr: rate(suggest_click_no_results_total[1h]) > 0
    severity: ticket                # the existence probe is leaking
```

## 9. Numerical example

BookNook: 200 M searches/day, multiplier 4.2, 2.4 M terms.

**Step 1 — the keystroke multiplier, which is the number people miss.**

```
  completed searches:  200,000,000/day
  average characters typed per search: 4.2 measured (and every character
      from the 2nd onwards issues a request)
  ⇒ requests: 200 M × 4.2 = 840,000,000/day = 9,722/s
  peak factor: 4 (evening reading, plus a release-day spike) = 38,889/s
  ⇒ "200 million searches a day" sounds like a modest service. Multiplied by
    the keystroke factor it is 38,889 rps at peak — the same order as a much
    bigger product. ALWAYS compute the multiplier before sizing anything.
```

**Step 2 — pruning: 40 % of the memory for 0.4 % of the requests.**

```
  unpruned: 33.6 M prefixes → 5.0 GB
  query distribution over prefixes (measured):
      top 100,000 prefixes       (0.3 %)  → 63 % of requests
      next 2,000,000             (6 %)    → 30 % of requests
      remaining 31.6 M           (94 %)   → 7 %  of requests
  pruning thresholds and the resulting index:
      drop prefixes with < 3 candidates or a top score below the floor
      drop prefixes with < 100 monthly queries
      ⇒ 6.2 M prefixes survive (18 %), 3.1 GB
      ⇒ requests with no suggestions: 0.4 % (they would mostly have returned
        one weak, unclicked candidate anyway)
  ⇒ 1.9 GB saved for 0.4 % coverage. Do the same arithmetic at 10× and it
    is 19 GB saved, which is the difference between 3 shards and 24.
```

**Step 3 — the latency budget, and why the trie lost.**

```
  measured at 38,000 rps, 8 shards:
      prefix hash:  p50 0.4 ms · p99 0.9 ms
      trie:         p50 2.1 ms · p99 6.8 ms     ← 7.5× worse at p99
  reason: the trie's advantage is memory (shared prefixes); its cost is
  pointer-chasing with a cache miss per character. At 12 characters that is
  12 potential cache misses (~100 ns each, but with dependency chains the
  real cost is higher). The hash table is ONE contiguous read of < 1 KB.
  ⇒ at 1,000 suggestions/s the trie would be better; at 38,000/s the hash
    wins decisively, because the bottleneck is memory locality and not
    algorithmic complexity. State the workload when you state the choice.
```

**Step 4 — freshness: what the fast lane costs and earns.**

```
  base rebuild: 22 min, 40 cores, hourly ⇒ £180/month of compute
  fast lane: 40 s, 4 cores, every 5 min ⇒ £95/month
  measured release-day effect: 4,100 fewer abandonments
  at a 1.8 % purchase rate and £11.40 contribution per order:
      4,100 × 0.018 × £11.40 = £841 per release event; ~120 releases per
      year ⇒ £101,000/year
  ⇒ the fast lane costs £1,140/year and returns ~£100,000, because
    "freshness" on a release day is not a performance metric, it is revenue.
    This is the arithmetic that justifies a second pipeline.
```

**Step 5 — the total cost, and where it sits.**

```
  serving   16 nodes (8 shards × 2) × £70          = £1,120/mo
  edge      egress = £0 marginal (served from the CDN contract that already
            serves the storefront; the autocomplete payload is 0.6 KB)
  pipeline  base £180 + fast lane £95              = £275/mo
  storage   snapshots, 9 languages, 3 versions     = £40/mo
  ─────────────────────────────────────────────────────────
  total ≈ £1,435/month for a feature on every page.
  ⇒ compare: the same requests served by the search engine at 40 ms each
    would need ~150 nodes ≈ £10,500/month — and would still miss the
    latency budget. PRECOMPUTATION IS 7× CHEAPER AND 20× FASTER. That is
    the whole argument, in one line.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **Precomputed prefix hash table** | 0.9 ms p99, no I/O, cheap | Offline job; hourly staleness; memory | **The default for typeahead** |
| **Trie in memory** | Memory-efficient, supports enumeration | Pointer chasing; 6.8 ms p99 at scale | Small index, low QPS |
| **Live search-engine query** | Always fresh, no pipeline | 40–120 ms, 150 nodes, still over budget | Never for typeahead |
| **Top-10 per prefix** | Exactly the response shape | Cannot re-rank beyond 10 | K fixed by the UI |
| **Top-50, rerank to 10** | Personal and context signals | +0.8 ms, 5× the memory | When personalisation matters |
| **Hourly base + 5-min fast lane** | Freshness where it matters | Two pipelines | Release-driven catalogues |
| **Hourly only** | Simpler | 1–60 min stale on new terms | Slow-moving catalogues |
| **Prefix pruning at a volume floor** | 40 % of the memory, 0.4 % loss | Coverage of the long tail | Always |
| **Query-log ranking** | Matches what users actually want | Feedback loop; manipulable | Always, with anti-injection |
| **Document-only ranking** | Unmanipulable | Worse suggestions for ambiguous prefixes | Legal/regulatory corpora |
| **Fuzzy path on delete-one only** | 2.2 ms, 0.9 % of requests | Misses some typos | Typeahead specifically |
| **Edge caching at 60 s** | 71 % offload | Up to 60 s stale | Always (index is hourly anyway) |

## 11. Failure scenarios

**11.1 — The pipeline fails and no new index is published.** Suggestions quietly become stale. **Fix:** the serving nodes report the age of the snapshot they are serving; alarm at 2× the expected interval (§8). Staleness is invisible without this.

**11.2 — A bad snapshot is published** (a ranking bug, or an offensive term that passed the filter). **Fix:** checksum verification, an atomic swap, `keep_previous: 2` for a one-command rollback, and a canary publish to one language/one shard first.

**11.3 — Hot shard.** `hash(prefix) % 8` is uneven for very short prefixes (`a`, `b`, `c` are all in the top 100 by volume). **Fix:** shard by prefix *length* first, then by hash — all 1–2 character prefixes get their own dedicated shard(s) because they are 100 % predictable hot keys.

**11.4 — Injection attack** (§4.9). 180,000 bot searches pushed a fabricated phrase onto the homepage for two hours. **Fix:** distinct-session counting, quarantine rules, an online suppression list with a 60 s refresh, and a 15-minute removal SLA.

**11.5 — Dead-end suggestions.** 6.1 % of suggestions returned zero results because the term existed in the logs but the book had been delisted. **Fix:** an existence probe during aggregation, re-run hourly (§4.7).

**11.6 — Autocomplete blocks search.** A suggest endpoint slowed to 2 s and the search box awaited it, so search itself appeared broken. **Fix:** the client abandons at 150 ms and never awaits; the server has a hard 25 ms deadline returning empty (§4.8).

**11.7 — Memory blow-up from a longer term limit.** A decision to allow 60-character series titles increased the average prefix count per term from 11 to 26 and the index from 3.1 GB to 7.4 GB, exceeding the node budget. **Fix:** truncate suggestion text to 40 characters, and make the prefix-count-per-term a monitored pipeline output.

**11.8 — The multilingual index that was never pruned.** Nine language indexes, but pruning used a global volume floor, which emptied the three smallest languages entirely (their terms never reach a global threshold). **Fix:** volume floors are **per language**, derived from that language's traffic.

## 12. Alternatives

| Instead of | Consider | When |
|---|---|---|
| A custom index | A managed search service's suggest API | Your catalogue is small or your team is small |
| A custom index | A managed "search-as-you-type" index (an inverted index with completion fields) | You already run a search engine with that feature |
| Own pipeline | Recommending from the search engine's query logs feature | The engine already aggregates popularity |
| Personalised per-user top-K | Rerank over global top-50 | Always — per-user precomputation is infeasible |
| Fuzzy search in autocomplete | Redirect the last keystroke to full search | Typo tolerance is a search-engine concern |
| Building for all 40 languages | Top 9 by volume, fall back to English | Long-tail languages have no data anyway |

## 13. When to use it

- **Any search box** with more than a handful of possible results.
- **Precomputation** whenever the p99 target is under ~50 ms, which for typeahead it always is.
- **A fast lane** for release- or news-driven content.
- **Pruning at a per-language volume floor**, always.
- **An online safety suppression list** for anything user-visible, because the pipeline cannot react in 60 seconds.

## 14. When *not* to use it

- **For structured form fields** (a country picker) — there is a list; filter it client-side.
- **When the catalogue is under ~10,000 items** — ship the whole list to the client and filter locally. Cheaper in every way.
- **When freshness must be sub-second** — you want a live search, and you must budget the latency.
- **When suggestions carry legal significance** — a curated list with an approval process, not a query-log feedback loop.
- **When you cannot staff the safety review** — do not ship a public suggestion surface without it.

## 15. Common mistakes

1. **Forgetting the keystroke multiplier** and sizing for "queries/day" (§9 step 1).
2. **Querying the search engine per keystroke**, then wondering why p99 is 120 ms.
3. **A trie for serving** at high QPS, where cache locality decides (§9 step 3).
4. **No pruning**, so the index costs 3× what it needs to.
5. **A single volume floor across languages** (§11.8).
6. **Dead-end suggestions** with no existence probe (§11.5).
7. **Per-user precomputation** instead of a rerank (§4.5).
8. **No fast lane** in a release-driven catalogue (§9 step 4).
9. **No snapshot age alarm**, so staleness is silent (§11.1).
10. **No rollback path** for a bad snapshot (§11.2).
11. **Hash-only sharding**, so short prefixes create a hot shard (§11.3).
12. **Client awaiting the suggest response** before allowing search (§11.6).
13. **Safety filtering only in the pipeline**, with no online suppression.
14. **Logging keystrokes synchronously** on a 38,000 rps path.
15. **Unbounded suggestion length**, blowing the memory budget (§11.7).

## 16. Interview perspective

**What is actually being tested.** Whether you recognise autocomplete as **precomputation**, whether you compute the **keystroke multiplier**, and whether you can hold a 25 ms server budget while justifying every component inside it. Strong candidates: prefix hash table built from a trie, top-K precomputed, pruned, fast lane, rerank not precompute, no database on the path. Weak candidates: "query Elasticsearch with a prefix filter".

**Answers that land well:**

- *"How do you get under 100 ms?"* — "By not computing anything at request time. The pipeline aggregates 30 days of query logs into 2.4 million terms, expands them to 33.6 million prefixes, keeps the top ten per prefix, prunes to 6.2 million, and publishes a 3 GB snapshot that the serving nodes mmap. A request is one hash lookup plus a rerank: 2.1 milliseconds at p99, with the network dominating the budget."
- *"Why not a trie?"* — "A trie is what I'd build the index with offline, because it makes prefix expansion and pruning easy. I would not serve from it: at 38,000 requests a second we measured 6.8 ms p99 for the trie against 0.9 for the flat hash table, because every character is a pointer and a likely cache miss. The algorithmic complexity is the same; the memory locality is not."
- *"What about a new bestseller?"* — "The hourly rebuild is up to an hour stale, so there is a second pipeline every five minutes that takes the last hour's queries, finds the top 2,000 trending terms — about 20,000 prefixes, a 2 MB snapshot — and merges over the base at query time. It costs £1,140 a year and on release days it saved 4,100 abandonment sessions, which is about £100,000."
- *"How much traffic?"* — "200 million searches a day, times a keystroke multiplier of 4.2, so 840 million suggest requests a day and 38,889 a second at peak. A 71 % edge hit ratio on a 60-second TTL brings the origin down to 11,000 a second, and the whole thing is 16 nodes."
- *"What can go wrong?"* — "Three things. A stale or bad snapshot — so we verify checksums, swap atomically, keep two previous versions, and alarm on snapshot age. Injection — someone searched a fabricated phrase 180,000 times to get it onto the homepage, which we fixed by counting distinct sessions and quarantining terms with high new-session ratios and low clicks. And dead ends — a suggestion that returns no results, which we prevent with an existence probe in the pipeline."

**Common interview tasks:** size the index and the QPS; choose a data structure with justification; design the offline pipeline; explain freshness; design the serving and sharding; handle personalisation; handle safety and abuse; cost it.

**Red flags interviewers watch for:**

- No keystroke multiplier.
- A live database or search-engine query on the keystroke path.
- A trie served at high QPS with no cache-locality discussion.
- Personalisation by per-user precomputation.
- No pruning, no memory arithmetic.
- No freshness story for new content.
- No safety filter, or one that operates only in a batch job.
- No dead-end prevention.
- No numbers at all.

## 17. Knowledge check

**Conceptual**

1. Explain why autocomplete is a precomputation problem rather than a search problem, using the latency budget and the cost comparison between the two approaches. Then explain what the offline pipeline produces and why the response shape is exactly what is precomputed.
2. Compare a prefix hash table, a trie and a suffix array for this workload on latency, memory, shardability and pipeline convenience, and explain why production systems often use one offline and another online.
3. Explain the need for a fast lane, what it costs, and how it is merged at query time. Then explain why personalisation must be a rerank and never a per-user index.

**Practical scenario**

4. Design BookNook's autocomplete: 200 M searches/day, 4.2 keystroke multiplier, 8 M titles and 1.2 M authors in 9 indexed languages, a 100 ms end-to-end target, hourly base freshness with 5-minute trending, personal reranking from reading history, adult/spam filtering, and no dead ends. Give the requirements and capacity tables, the index arithmetic (terms → prefixes → memory, with pruning), the data structures with justification, the API with cache headers, the pipeline stages with runtimes, the scoring formula, the serving topology and sharding (including the short-prefix hazard), the safety layers, the alerts, and the cost. State what changes at 10× traffic and at 10× catalogue size.

**Calculation**

5. 200 M searches/day, a keystroke multiplier of 4.2, and a peak factor of 4. (a) Compute requests per day and per second at peak. (b) Given a 71 % edge hit ratio, compute the origin request rate. (c) With 16 serving nodes, compute the per-node rate and state whether 2.1 ms per request leaves adequate CPU headroom (assume 4 cores and ~0.4 ms of CPU per request). (d) Compute the monthly node cost at £70/node.

6. An index has 2.4 M terms with an average of 14 prefixes each, each top-10 list costing 120 B and each prefix key 30 B. (a) Compute the total prefix count and index size. (b) Apply a pruning rule that keeps 18 % of prefixes and compute the new size. (c) If the average term length doubled, compute the new prefix count and size, and state the mitigation. (d) Compute the memory per shard for 8 shards with 2 replicas and state whether a 2 GB slice is comfortable.

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
  - [x] **5. Design search autocomplete (L97) ← you are here**
  - [ ] 6. Design file storage (L98)
  - [ ] 7. Design a web crawler (L99)
  - [ ] 8. Design real-time chat (L100)
  - [ ] 9. Design a news feed (L101)
  - [ ] 10. Design video streaming (L102)
  - [ ] 11. Design ride sharing (L103)
  - [ ] 12. Design e-commerce and inventory (L104)
  - [ ] 13. Design payment processing (L105)
  - [ ] 14. Design a metrics and logging platform (L106)

**NEXT LESSON: Phase 5, Topic 6 — Design file storage**

---

---

# Lesson 98 — Design File Storage

*Phase 5, Topic 6*

## 1. Simple definition

**The problem.** Store users' files durably and serve them back on any device, keeping folders in sync within seconds, keeping 30 days of versions, supporting sharing, and doing it at a cost that scales with bytes rather than with files.

**Why "cost scales with bytes" is the design.** Bytes are the only resource here that grows without bound: 8 PB of raw file data dominates every other cost line by orders of magnitude. So the central technique is **block-level deduplication** — split each file into 4 MB blocks, identify each block by a hash of its content, store each distinct block **once**, and represent a file as a small **manifest** listing block hashes. Two users storing the same EPUB share one copy. Two *versions* of the same 1 GB file differ in a handful of blocks, so a version costs 4 MB rather than 1 GB.

Terms defined before use:

- **Block / chunk**: a fixed or content-defined slice of a file (4 MB here). The unit of storage, dedupe, upload and download.
- **Block hash**: `SHA-256(block)`. The identity of a block, and the dedupe key.
- **Manifest**: the ordered list of block hashes plus a file's metadata. Small (a few KB), versioned, and stored in a database.
- **Content-defined chunking (CDC)**: choosing chunk boundaries from the *content* (a rolling hash) instead of fixed offsets, so an insertion at the start of a file does not change every subsequent block.
- **Dedupe**: storing one physical copy per distinct block, with a reference count.
- **Refcount / GC**: how many manifests reference a block; blocks at zero are reclaimed.
- **Sync cursor**: a per-device position in the change stream, so a device can ask for "everything since X".
- **Conflict copy**: the file produced when two devices edit the same version without seeing each other's change.
- **Presigned URL**: a time-limited URL letting a client upload or download **directly** to object storage, bypassing your servers.
- **Convergent encryption**: encrypting a block with a key derived from its content, so identical blocks produce identical ciphertext (and can still be deduped).

The one sentence to carry out of this lesson: **a file is a manifest, not a blob — dedupe the blocks, keep the metadata in a transactional store, and move the bytes directly between the client and object storage so your servers never touch the data they cannot make cheaper.**

## 2. Why it exists

**Because the storage bill is the product.** 8 PB at £0.021/GB-month is **£176,000/month** before any engineering. Dedupe at 35 % removes £62,000/month of that, permanently, and versioning via block reuse means the second version of a file costs ~1 % of the first. Everything else in the design — the upload protocol, the sync protocol, the download path — exists to make those numbers achievable without hurting the user.

**Because sync is a distributed-systems problem wearing a file manager's clothes.** Two devices, an offline laptop, a phone on a train, and a server; each has a partial view. You need: an ordering of changes (L66), a way to detect that two devices changed the same file concurrently (L91's conflict problem), a delta protocol that costs O(changed bytes) rather than O(file size), and a rule for what happens when the network dies mid-upload. BookNook's first sync implementation silently lost 41 user files by appending changes out of order — the classic lesson from L66 and L72.

**Because your servers must not carry the bytes.** An upload through your application servers means the file passes through their memory and their network: 84 MB/s of uploads and 340 MB/s of downloads is not "some traffic", it is a permanent bandwidth tax plus a scaling problem you did not need. **Presigned URLs** let the client talk to object storage directly, with authorisation decided once by your metadata service. This is the same insight as L93's blob-first rule, taken to its conclusion.

**Because dedupe is cheap to get wrong and legally expensive.** Cross-user dedupe creates a **covert channel**: if a user can probe whether a block already exists, they can learn whether *someone else* has a specific file ("does anyone have this payroll PDF?"). This is a real, published attack against deduplicating storage, and it forces a decision about keying, encryption and what "dedupe" is allowed to span (§4.8).

**And because "sync" fails in the one case users notice most: two devices, one file.** There is no correct automatic merge for a binary file. The design must produce a **conflict copy** — never a silent overwrite, and never a merge that corrupts. Getting this wrong is how a file-sync product loses trust permanently.

## 3. Real-world analogy

**A warehouse of sealed crates, and a shelf of packing lists.**

The warehouse holds **crates**, and each crate is identified by a **fingerprint of its contents** — not by a label someone typed. If two customers ship identical crates, the warehouse stores **one** crate and records that both customers' packing lists refer to it.

What the customer actually "owns" is a **packing list**: an ordered set of crate numbers, plus a name and a date. The list is tiny; the crates are huge. Opening a file is walking down the list and fetching each crate.

The analogy delivers the design's five properties precisely:

**Nothing in the warehouse is ever modified.** A crate's contents are addressed by their fingerprint; if you changed the contents, it would be a different crate. This immutability is what makes dedupe, versioning, caching and integrity all cheap at once — the same property L93 exploited. **A new version of a document is a new packing list that references the old crates plus one or two new ones.**

**A small edit must produce a small change.** If the customer inserts one page at the front of a 300-page binder, a naive warehouse that splits binders at fixed points ("every 50 pages") would find that *every* crate after the insertion has different contents — 300 pages re-shipped for a one-page edit. A clever warehouse splits on **content patterns** (a mark at the top of a page), so the pieces stay aligned and only one or two crates change. That is content-defined chunking, and the difference is 25× in upload volume (§9 step 3).

**The packing list is kept in a ledger, not in the warehouse.** The list must be transactional — you cannot half-update a list and call the file intact. So the lists live in a database with real transactions, while the crates live in cheap bulk storage. Mixing them is the mistake that made L93's first version bloat its backups.

**And two customers filing the same crate creates a disclosure.** If the warehouse will happily tell you "that crate already exists", you can ask it about crates you do not own. A warehouse that answers that question is a warehouse that leaks whether your competitor stores a particular document. **The fix is either not to share crates across customers, or to make the fingerprint computable only by someone who already has the contents** (§4.8). This subtlety is the difference between a storage optimisation and a privacy incident.

## 4. Detailed technical explanation

### 4.1 Requirements

**Functional.**

| # | Requirement |
|---|---|
| F1 | Upload, download and delete files and folders |
| F2 | Sync across a user's devices within 60 s p99 |
| F3 | Keep 30 days of version history with restore |
| F4 | Share a file or folder with another user, or via a link (read or read-write) |
| F5 | Resume an interrupted upload without re-sending completed blocks |
| F6 | Detect and preserve concurrent edits as conflict copies |
| F7 | Search by filename and metadata (not full-text of contents — out of scope) |
| F8 | Enforce per-user storage quota |

**Non-functional.**

| # | Requirement | Number |
|---|---|---|
| N1 | Durability | 11 nines (object storage) + a manifest store with PITR |
| N2 | Availability | 99.99 % for metadata; 99.9 % for downloads |
| N3 | Upload throughput | 84 MB/s aggregate at peak |
| N4 | Download throughput | 340 MB/s aggregate at peak |
| N5 | Metadata latency | p99 < 50 ms for `list` and `delta` |
| N6 | Dedupe ratio | ≥ 30 % (measured, not assumed) |
| N7 | Storage cost | ≤ £0.025/GB-month effective, including versions |
| N8 | Integrity | Every byte verified end to end; a corrupt block is never served |

### 4.2 Capacity estimation

```
  USERS        6,000,000 accounts, 1.5 devices each = 9,000,000 devices
  FILES        400,000,000 files total; 68 files uploaded per user per year
  RAW DATA     8.0 PB stored (1.33 GB per user average)

  BLOCKS (4 MB fixed)        8.0 PB / 4 MB = 2.0 × 10^9 block instances
  DISTINCT BLOCKS after dedupe (measured 35 % savings):
                             1.3 × 10^9 distinct blocks = 5.2 PB stored
      ⇒ storage bill: 5.2 PB = 5,452,800 GB × £0.021 = £114,509/month
         (vs £176,160 without dedupe: dedupe saves £61,651/month)

  METADATA
      blocks table      1.3 B rows × 60 B = 78 GB
      file_blocks       400 M files × avg 2.4 blocks = 960 M rows × 40 B
                        = 38 GB
      files + versions  400 M × 1.6 versions × 700 B = 448 GB
      ⇒ ~570 GB in a partitioned relational store (L54, L63) — comfortably
        one primary plus replicas; the metadata is NOT the scaling problem,
        the BYTES are.

  TRAFFIC
      uploads   400 M files/year ÷ 365 ÷ 86,400 = 12.7/s · avg 6.6 MB
                ⇒ 84 MB/s average, 3× at peak = 250 MB/s
      downloads 4.7 downloads per file per month = 62 M/month
                ⇒ 24/s average, 12.3 MB average ⇒ 297 MB/s, 340 at peak
      sync events 9 M devices × 20 changes/day = 180 M/day = 2,083/s
      ⇒ the METADATA traffic is 2,083/s of small requests; the BYTES are
        340 MB/s. Both must bypass the application servers: presigned URLs
        for bytes, a delta API for metadata.

  QUOTA       1.33 GB average × a 3× headroom design = 4 GB free tier
```

### 4.3 API

```
POST /v1/files/upload/init
     { "path": "/Library/books/dune.epub", "size": 8_912_345,
       "content_hash": "sha256:…",         # whole-file hash for a fast path
       "base_version": 12,                 # the version this edit is based on
       "block_size": 4_194_304 }
  →  202 { "upload_id": "up_…",
           "missing_blocks": ["9f2c…", "a1b3…"],   # ← dedupe hint: only send
           "block_urls": { "9f2c…": "<presigned PUT, 1 h>", … },   # what is new
           "expires_at": "…" }

PUT  <presigned URL>                       # client → object storage DIRECTLY
     (one PUT per missing block, parallel up to 4, retried independently)

POST /v1/files/upload/{upload_id}/complete
     { "blocks": ["9f2c…", "a1b3…", …], "mtime": "…" }   # the manifest
  →  200 { "file_id": "f_…", "version": 13,
           "conflict": false }             # or a conflict copy's id

GET  /v1/files/{file_id}?version=13        # or by path
  →  200 { "manifest": ["9f2c…", …],
           "download_urls": ["<presigned GET>", …], "size": 8_912_345 }

GET  /v1/delta?cursor=9f31ab&limit=500
  →  200 { "changes": [ { "file_id", "version", "op": "upsert"|"delete",
                          "path", "manifest_hash" } ],
           "next_cursor": "…", "has_more": false }

GET  /v1/notifications?cursor=…            # long-poll / websocket: "there are
  →  "cursor-changed"                      #  changes"; the client then calls
                                           # /delta (never push the payload)
POST /v1/files/{id}/share    { "user_id": 8842, "mode": "read" }
POST /v1/shares/link         { "file_id": "f_…", "mode": "read", "ttl_days": 7 }
```

### 4.4 Data model

```sql
CREATE TABLE files (
  id            UUID PRIMARY KEY,           -- stable across renames and versions
  owner_id      BIGINT NOT NULL,
  path          TEXT NOT NULL,              -- current path (a materialised view
                                            -- of the folder structure)
  current_version INTEGER NOT NULL,
  size_bytes    BIGINT NOT NULL,
  content_hash  BYTEA NOT NULL,             -- whole-file sha256 (fast path)
  mtime         TIMESTAMPTZ NOT NULL,       -- client clock, stored as data
  deleted_at    TIMESTAMPTZ,
  UNIQUE (owner_id, path) WHERE deleted_at IS NULL
);

CREATE TABLE file_versions (                -- the VERSIONED MANIFESTS
  file_id     UUID NOT NULL REFERENCES files(id),
  version     INTEGER NOT NULL,
  manifest    BYTEA NOT NULL,               -- packed array of block hashes
  size_bytes  BIGINT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by  BIGINT,                       -- device/user that wrote it
  PRIMARY KEY (file_id, version)
);

CREATE TABLE blocks (                       -- one row per DISTINCT block
  hash        BYTEA PRIMARY KEY,            -- sha256, 32 B
  size_bytes  INTEGER NOT NULL,
  refcount    INTEGER NOT NULL,             -- manifests referencing it
  stored_at   TIMESTAMPTZ NOT NULL,
  verified_at TIMESTAMPTZ                   -- last integrity scrub
);

-- The user → blocks mapping is derivable from file_versions.manifest, but a
-- materialised table makes GC and quota accounting tractable.
CREATE TABLE block_refs (
  owner_id  BIGINT NOT NULL,
  hash      BYTEA NOT NULL,
  PRIMARY KEY (owner_id, hash)
);

CREATE TABLE shares (
  resource_type SMALLINT NOT NULL,          -- file | folder
  resource_id   UUID NOT NULL,
  grantee_id    BIGINT,                     -- NULL for link shares
  mode          SMALLINT NOT NULL,          -- read | write
  token_hash    BYTEA,                      -- for link shares
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at    TIMESTAMPTZ
);

-- Per-device sync position: the cursor is (server_seq), a global monotonic
-- counter per user — NOT a timestamp (L66 §4.2: clocks cannot order events).
CREATE TABLE device_cursors (
  device_id  UUID PRIMARY KEY,
  owner_id   BIGINT NOT NULL,
  cursor     BIGINT NOT NULL,
  last_seen  TIMESTAMPTZ NOT NULL
);
```

### 4.5 Write path: chunking, dedupe, and the commit as a transaction

```
  1. CHUNK  (client side)
     · content-defined chunking (CDC) using a gear/Rabin rolling hash with a
       target 4 MB and a 1–8 MB range, OR fixed 4 MB for immutable content
     · CDC costs ~5 % CPU and produces variable sizes, but survives insertions
       and prepends (§9 step 3). For a file-sync product, CDC is the right
       default; for "upload a video once", fixed is simpler.
  2. HASH each block (SHA-256). The client now has (sizes, hashes).
  3. INIT  → server returns the MISSING blocks (dedupe hint) and presigned
     URLs for them. If the user has all blocks already, the upload is
     metadata-only — measured 31 % of uploads, and for library documents the
     figure is 58 %.
  4. PUT each missing block directly to object storage, in parallel.
     Block key: s3://bnb-blocks/<hh>/<hh>/<sha256> — content-addressed, so a
     PUT is idempotent and a re-PUT is harmless.
  5. COMPLETE → the server verifies that the claimed blocks EXIST (a HEAD,
     not a download), inserts the blocks rows with refcounts, writes the
     new file_version, and updates files.current_version.
  6. ATOMICITY → step 5 is ONE local transaction. If it fails, the old
     version remains current and the blocks are unreferenced garbage, which
     the GC reclaims (L93 §4.8's ordering rule, same reasoning).
  ⇒ THE MANIFEST COMMIT IS THE TRANSACTION. Bytes can be written before it;
    they mean nothing until the manifest references them.
```

**Why the server must not stream the bytes**: at 250 MB/s peak upload and 340 MB/s download, routing data through application pods costs (a) bandwidth you pay for twice (ingress + egress from the pods), (b) pods sized for throughput rather than for metadata, and (c) an outage coupling between "the metadata service is being deployed" and "uploads are failing". Presigned URLs cost one extra round trip at init.

### 4.6 The sync protocol

```
  CLIENT STATE: the manifest it last saw per file, and a cursor.

  1. NOTIFY   the client holds a long-poll (or websocket) to /notifications.
              The server sends only "cursor advanced" — never the payload.
  2. DELTA    GET /v1/delta?cursor=N → changes since N, with manifest HASHES
              (not manifests). The client compares each manifest hash with its
              own copy: unchanged ⇒ nothing to do (the common case, and it is
              ONE hash comparison).
  3. FETCH    for changed files, GET the manifest, diff the block lists, and
              download only MISSING BLOCKS (the same dedupe logic as upload).
              A 1 GB file with 3 changed blocks transfers 12 MB.
  4. APPLY    write blocks, then swap the local manifest atomically. The
              local cursor advances ONLY after the file is complete on disk.
  5. OFFLINE  queue local changes with (base_version, mtime, device_id); on
              reconnect, upload as a normal edit with base_version = the
              version the edit was based on.

  CONFLICT DETECTION — the only correct test:
      the server receives a commit whose base_version != current_version
      ⇒ someone else wrote first. The server does NOT overwrite. It stores the
        incoming manifest as a NEW FILE at a conflict path:
          "/Library/books/dune (conflicted copy 2026-10-30, laptop).epub"
      and returns conflict: true.
      ⇒ WHY A COPY AND NOT A MERGE: there is no correct automatic merge for
        binary content, and even for text, a silent merge corrupts. A conflict
        copy is honest, recoverable and never loses a byte. Measured user
        behaviour: 94 % resolve a conflict copy within a day; 100 % of them
        would have been furious about a silent overwrite.
  ⇒ AND: compare CONTENT HASHES, never mtimes. Two devices with clocks 4
    minutes apart will otherwise re-upload each other's changes forever
    (L66 §4.1). The mtime is stored for display; the cursor orders events.
```

### 4.7 Read path and sharing

```
  DOWNLOAD
    client → metadata service: is this user allowed to read file f, version v?
             (an ACL check, cached; see the authorisation note below)
           → presigned GET URLs per block, 15-minute TTL
    client → object storage directly (parallel block GETs, 4–8 concurrent)
           → verify each block's SHA-256 against the manifest ← MANDATORY
    ⇒ the metadata service handles 24/s of downloads and 0 bytes of the data.
      Egress is paid once, at object storage / CDN.

  RANGE REQUESTS: a PDF viewer asks for pages 4–7, not the whole file. With a
    manifest you can map a byte range to block ranges and issue 1–2 presigned
    range GETs. Fixed-size blocks make this trivial; CDC makes it a binary
    search over block sizes. That is a real argument for fixed blocks on
    large media, and the reason both modes exist.

  FOLDER SHARING AND THE AUTHORISATION COST: a folder share to 40,000 files
    must not mean 40,000 ACL rows checked per list request. BookNook stores
    shares on the ANCESTOR folder and resolves inheritance at read time with
    a recursive CTE, cached per (user, folder) for 60 s. The measured effect:
    list p99 41 ms → 8 ms, and a folder share is one row rather than 40,000.
```

### 4.8 Dedupe's two traps: privacy and reference counting

**Trap 1 — the covert channel.** Cross-user dedupe answers the question "does anyone already store this exact block?", and that answer leaks. The published **confirmation-of-a-file** attack:

```
  attacker wants to know whether a target has "department-salaries-2026.pdf":
    · compute its blocks, ask the upload endpoint which blocks are missing
    · if all blocks are "present", the file exists somewhere in the system
  ⇒ the "missing_blocks" response (§4.3) is the leak, and it is not
    hypothetical; it is the standard critique of deduplicating storage.
```

Three mitigations, in increasing strength:

| Mitigation | Effect | Cost |
|---|---|---|
| **Keyed hash**: `HMAC(server_secret, block)` instead of SHA-256 | An attacker cannot compute the block id without the secret | Breaks client-side block identity; the client must ask the server to key each hash (2,083/s — fine) |
| **Convergent encryption**: `AES(block, key = HMAC(content))` | Identical plaintext → identical ciphertext, so dedupe still works, and the server cannot read the blocks | **Does not fix the channel** unless the key derivation includes a secret; and it enables the same attack |
| **Per-user dedupe only** | No cross-tenant leak at all | Loses the largest savings; BookNook measured 58 % → 31 % dedupe |

BookNook's decision: **HMAC-keyed block ids with a server secret, plus cross-user dedupe within a tenant (a user's own files and the shared publisher catalogue)**. Measured effect: dedupe 35 % → 31 % (a £7,000/month give-up) and the covert channel closed, because the block id is not computable from the file. That is a **correctness/privacy trade, quantified** — which is what a design review wants to see.

**Trap 2 — refcounts and GC.** A block is deleted only when nothing references it, and "nothing" must be computed on a schedule you trust:

```
  · refcount on commit: increment for each new (owner, block) pair; decrement
    when a version is deleted (undo retention window, deleted-file trash
    30 days, then purge)
  · a periodic MARK-AND-SWEEP (weekly) recomputes the referenced set from
    file_versions + trash and deletes blocks not referenced for > 7 days
    (the grace window protects in-flight uploads whose commit has not landed)
  · measured drift: refcounts had accumulated a 0.4 % over-count over 5
    months (from aborted uploads) ⇒ 21 TB of unreclaimable blocks. The sweep
    is not optional.
```

## 5. Request / data flow

```
 UPLOAD a 1 GB file where only 3 blocks changed since version 12
 ─────────────────────────────────────────────────────────────────────────
 1. client chunks (CDC, 4 MB target) → 251 blocks, hashes them    [2.1 s]
 2. POST /upload/init {size, content_hash, base_version: 12}
    server: 248 of 251 blocks already exist ⇒ missing_blocks: 3
            returns 3 presigned PUTs + upload_id                   [28 ms]
 3. client PUTs 3 × 4 MB = 12 MB to object storage (parallel)     [1.4 s]
 4. POST /upload/{id}/complete {blocks, mtime}
    server: HEADs the 3 blocks (existence, not content)             [9 ms]
            ONE transaction: insert 3 blocks (refcount 1),
              insert file_versions (file, 13, manifest), update files
            publish a change event → the user's other devices        [14 ms]
 5. total: 12 MB transferred instead of 1 GB — 83× less. At 250 MB/s of
    aggregate upload capacity, the same fleet supports 83× the edits.

 OTHER DEVICES (2 devices, one on mobile data)
 ─────────────────────────────────────────────────────────────────────────
 t+0.3 s  long-poll returns "cursor advanced"
 t+0.4 s  GET /delta?cursor=8871 → 1 change, with manifest_hash
 t+0.5 s  manifest differs from the local copy → GET manifest (240 B)
 t+0.6 s  diff: 3 new block hashes → 3 presigned GETs (12 MB)
 t+2.1 s  blocks verified against their SHA-256, manifest swapped atomically,
          cursor advanced to 8872
 ⇒ the phone transferred 12 MB, not 1 GB, and did it on the metadata path
   only — 6 small requests and one direct object-storage fetch.
```

## 6. ASCII diagram

```
        ┌────────────┐   ┌────────────┐   ┌────────────┐
        │ laptop     │   │ phone      │   │ web        │
        │ (has half  │   │ (has some  │   │ (stateless)│
        │  the blocks)│  │  blocks)   │   │            │
        └─────┬──────┘   └─────┬──────┘   └─────┬──────┘
              │ blocks direct  │ blocks direct  │
              │ (presigned)    │ (presigned)    │  metadata only
              ▼                ▼                ▼
   ┌───────────────────────────────────┐   ┌──────────────────────────┐
   │ OBJECT STORAGE (content-addressed)│   │ METADATA SERVICE         │
   │ s3://bnb-blocks/hh/hh/<hmac(block)>│  │ · files, versions, paths │
   │ 5.2 PB deduped · 1.3 B blocks     │   │ · manifests (block lists)│
   │ · immutable · 11 nines            │   │ · ACLs, shares, quotas   │
   │ · no query language, no listing   │   │ · cursors, delta API     │
   │   on the hot path                 │   │ · Postgres, partitioned  │
   └───────────────────────────────────┘   └───────────┬──────────────┘
                                                       │
                                     ┌─────────────────▼────────────────┐
                                     │ CACHE (manifest + ACL, 60 s)     │
                                     └──────────────────────────────────┘

   A FILE IS A MANIFEST:
       dune.epub v13  →  [7f2c…, a1b3…, 9e04…, … 251 hashes]
                          │      │      │
                          │      │      └─ shared with v12 (unchanged)
                          │      └──────── new block (12 MB of edit)
                          └─────────────── shared with 4,100 other users

   SYNC, DRAWN AS A CURSOR (not a timestamp):
       server change log (per user, monotonic):
         seq 8870 ─ seq 8871 ─ seq 8872 ─ seq 8873 ─►
                    ▲                    ▲
         device A ──┘   (was here)       │
         device B ────────────────────────┘
       ⇒ delta = "everything after my cursor". Never "everything after my
         clock" — clocks disagree (L66) and mtimes are client-controlled.

   DEDUPE ACROSS VERSIONS (why versioning is nearly free):
       v12: [A][B][C][D]        1 GB
       v13: [A][B][X][D]        1 GB, but only [X] (250 MB) is new storage
       ⇒ 30 days of versions costs ~1.05× the file, not 30×
```

## 7. Detailed example

**BookNook Library**: readers upload EPUBs and PDFs they own, plus publisher assets (cover images, marketing kits), and the reader apps sync a reading position file per book.

```
  scale, measured after 14 months:
    6.0 M accounts, 1.5 devices each
    8.0 PB raw, 5.2 PB stored after dedupe (35 % → later 31 % after the
        privacy fix, §4.8)
    400 M files; 68 uploads/user/year
    average file 6.6 MB (EPUB 2 MB, PDF 18 MB, cover images 0.4 MB)
    top 1,000 publisher assets are referenced by 41 % of all files
    ⇒ 31 % of uploads are metadata-only (every block already stored);
      for library EPUBs specifically, 58 %.
```

**The measurements that decided the design:**

| Measurement | Value | Consequence |
|---|---|---|
| Dedupe ratio (fixed 4 MB) | 28 % | Below the 30 % target — triggered CDC |
| Dedupe ratio (CDC, 4 MB target) | 35 % | Met the target: £61.6k/month saved |
| Upload volume for a 1 % edit, fixed chunks | 1 GB (100 %) | The prepend problem (§9 step 3) |
| Upload volume for a 1 % edit, CDC | 12 MB (1.2 %) | 83× less |
| Metadata-only uploads | 31 % overall, 58 % for EPUBs | Presigned + dedupe hint was worth it |
| Server bandwidth if bytes were proxied | 340 MB/s at peak | Would need ~30 extra pods and double egress |
| Sync p99 (cursor + delta) | 2.4 s to notify, 6.1 s to have the file | Inside the 60 s SLO by 10× |
| Conflict rate | 0.31 % of edits | 21,000 conflicts/month — worth designing properly |

**The incident that produced the conflict-copy rule:**

```
  2025-06: two devices edited the same 40-page PDF annotation folder.
  v1 logic: last writer wins (an UPDATE on the manifest).
  result: device A's 3 hours of annotations were silently discarded; the user
  discovered it a week later, escalated to the CEO, and churned with a public
  review. Two more users hit the same bug within a month.
  fix: (a) base_version in every commit, (b) mismatch ⇒ conflict copy,
       (c) never a silent overwrite, (d) an "activity" view showing versions
       so a user CAN find the lost work if it does happen.
  ⇒ the cost is a slightly cluttered folder (0.31 % of edits) versus data loss.
    That trade is not close.
```

**The privacy fix, and what it cost:**

```
  security review finding: the upload/init response leaked existence of
  arbitrary blocks, i.e. "does anyone here have this exact file?" — the
  confirmation-of-a-file attack (§4.8).
  fix: block ids became HMAC(server_secret, sha256(block)); clients ask the
  server to key each hash at init (2,083/s, trivial).
  measured consequences:
    · dedupe 35 % → 31 %: storage 5.2 PB → 5.5 PB → +£6,700/month
    · one extra round trip per upload: +14 ms
    · cross-user dedupe for the shared publisher catalogue retained by
      allowing unkeyed ids WITHIN that tenant only (a deliberate, documented
      exception, because those assets are public anyway)
  ⇒ £6,700/month and 14 ms, for closing a real disclosure. Cheap, and the
    number is what makes the decision defensible to finance.
```

## 8. Code or configuration example

```python
# ── Content-defined chunking: boundaries from content, not offsets ───────
GEAR = [random_uint64(seed=i) for i in range(256)]     # fixed table, both ends
MASK = (1 << 22) - 1         # target 4 MB ⇒ ~22 bits of hash: 1/4M chance per
MIN_B, MAX_B, TARGET = 1 << 20, 1 << 23, 1 << 22       # byte; bounds 1–8 MB

def chunk(data: bytes):
    """Yields (offset, length). Inserting bytes early does NOT shift later
    boundaries, so an edit re-sends ~1 block instead of all of them."""
    start, i, h = 0, 0, 0
    n = len(data)
    while i < n:
        h = ((h << 1) + GEAR[data[i]]) & 0xFFFFFFFFFFFFFFFF
        i += 1
        size = i - start
        if (size >= MIN_B and (h & MASK) == 0) or size >= MAX_B:
            yield start, size
            start, h = i, 0
    if start < n:
        yield start, n - start

def block_id(block: bytes, secret: bytes) -> bytes:
    # HMAC, not a plain hash: the id is not computable from the file, so the
    # upload endpoint leaks nothing (§4.8)
    return hmac.new(secret, hashlib.sha256(block).digest(), "sha256").digest()
```

```python
# ── init + complete: the dedupe hint and the single-transaction commit ───
@app.post("/v1/files/upload/init")
async def init(req: InitUpload, user: User):
    await quota.check(user, req.size)                    # L95-style guard
    ids = [block_id(h) for h in req.block_hashes]        # client-sent hashes
    existing = set(await db.fetchvals(
        "SELECT hash FROM blocks WHERE hash = ANY($1)", ids))
    missing = [h for h in req.block_hashes if block_id(h) not in existing]
    upload_id = new_id()
    await redis.setex(f"up:{upload_id}", 3600,
                      json.dumps({"user": user.id, "path": req.path,
                                  "base_version": req.base_version,
                                  "hashes": req.block_hashes}))
    return {
        "upload_id": upload_id,
        "missing_blocks": [b64(h) for h in missing],     # ← the dedupe hint
        "block_urls": {b64(h): presign_put(h) for h in missing},
        "expires_at": 3600,
    }

@app.post("/v1/files/upload/{upload_id}/complete")
async def complete(upload_id: str, body: CompleteUpload, user: User):
    st = json.loads(await redis.get(f"up:{upload_id}"))
    # 1. verify the blocks EXIST — a HEAD, never a download
    for h in body.block_hashes:
        if not await storage.head(block_key(block_id(h))):
            raise HTTPException(400, {"missing": b64(h)})

    # 2. ONE transaction: the manifest commit IS the transaction (§4.5)
    async with db.transaction():
        cur = await db.fetchval(
            "SELECT current_version FROM files WHERE id=$1 FOR UPDATE",
            body.file_id)
        if body.base_version != cur:                      # conflict → copy
            return await create_conflict_copy(body, user, cur)

        await db.executemany(
            """INSERT INTO blocks (hash, size_bytes, refcount, stored_at)
               VALUES ($1,$2,1,now()) ON CONFLICT (hash) DO UPDATE
               SET refcount = blocks.refcount + 1""",
            [(block_id(h), size) for h, size in zip(body.block_hashes,
                                                    body.block_sizes)])
        await db.execute(
            """INSERT INTO file_versions (file_id, version, manifest,
                                          size_bytes, created_by)
               VALUES ($1,$2,$3,$4,$5)""",
            body.file_id, cur + 1, pack(body.block_hashes), body.size, user.id)
        await db.execute(
            "UPDATE files SET current_version=$2, size_bytes=$3, "
            "content_hash=$4 WHERE id=$1",
            body.file_id, cur + 1, body.size, sha256_of_manifest(body))
        seq = await bump_user_cursor(user.id)             # monotonic, not a clock
    await notifier.publish(user.id, seq)                  # wake the devices
    return {"file_id": body.file_id, "version": cur + 1, "conflict": False}
```

```python
# ── Conflict copies: never overwrite, never merge ───────────────────────
async def create_conflict_copy(body, user, cur_version: int):
    original = await db.fetchrow("SELECT path, size_bytes FROM files WHERE id=$1",
                                 body.file_id)
    stem, ext = splitext(original["path"])
    device = await db.fetchval("SELECT name FROM devices WHERE id=$1",
                               body.device_id)
    path = f"{stem} (conflicted copy {date.today().isoformat()}, {device}){ext}"
    new_id_ = uuid7()
    await db.execute("INSERT INTO files (id, owner_id, path, "
                     "current_version, size_bytes, content_hash) "
                     "VALUES ($1,$2,$3,1,$4,$5)",
                     new_id_, user.id, path, body.size, sha256_of_manifest(body))
    await db.execute("INSERT INTO file_versions (file_id, version, manifest, "
                     "size_bytes, created_by) VALUES ($1,1,$2,$3,$4)",
                     new_id_, pack(body.block_hashes), body.size, user.id)
    metrics.inc("sync.conflict_copy")                      # 0.31 % of edits
    return {"file_id": new_id_, "version": 1, "conflict": True,
            "conflict_path": path}
```

```yaml
# ── GC and integrity scrub: the two background jobs that keep it honest ──
gc:
  schedule: "0 3 * * 0"          # weekly, 03:00 Sunday
  steps: [mark_and_sweep]        # referenced set from file_versions + trash
  grace_days: 7                  # protects in-flight uploads
  batch_size: 10000
  dry_run_first: true            # log the reclaimable set before deleting

scrub:
  schedule: "0 4 * * *"          # daily, 2 % of blocks per night
  verify: hmac(server_secret, sha256(block)) == block_id
  on_mismatch: quarantine_and_alert   # never silently serve a bad block

alerts:
  - alert: DedupeRatioDropping
    expr: storage_dedupe_ratio < 0.28
    for: 24h
    severity: ticket              # a chunking regression costs six figures/yr
  - alert: BlockGCReclaimSpike
    expr: rate(blocks_gc_reclaimed[1h]) > 10000
    severity: page                # a refcount bug is deleting user data
  - alert: SyncNotificationLag
    expr: histogram_quantile(0.99, rate(sync_notify_seconds_bucket[10m])) > 30
    severity: page                # the 60 s sync SLO is at risk
```

## 9. Numerical example

BookNook: 8 PB raw, 4 MB chunks, 400 M files, 1.33 GB/user.

**Step 1 — what dedupe is worth, in pounds and in engineering.**

```
  raw storage            8.0 PB  = 8,388,608 GB × £0.021 = £176,161/mo
  dedupe at 28 % (fixed chunks)  6.1 PB = £126,836/mo   (saves £49,325)
  dedupe at 35 % (CDC)           5.2 PB = £114,509/mo   (saves £61,652)
  ⇒ CDC versus fixed chunks is worth £12,327/month for about two weeks of
    work. The payback lands inside the first month. Do the CDC.
  note: at 31 % after the privacy fix (§4.8) the saving is £54,600 — still
    the largest single line in the cost model, and still worth defending.
```

**Step 2 — the versioning arithmetic that makes 30 days affordable.**

```
  without block reuse: 30 days of versions of a 1 GB file edited daily
      = 30 GB, i.e package 30× the file
  with block reuse and a 1 % daily change:
      day 1 = 1 GB · each subsequent version = ~10 MB of new blocks
      ⇒ 30 days = 1 GB + 29 × 10 MB = 1.29 GB = 1.29× the file
  measured across BookNook: version storage is 4.1 % of total storage
      (0.21 PB of the 5.2 PB), yet it is the feature users cite most in
      retention surveys. ⇒ CHEAP FEATURE, HIGH VALUE — because blocks.
```

**Step 3 — why content-defined chunking beats fixed, in bytes.**

```
  a 1 GB file with 40 bytes inserted at the START:
      FIXED 4 MB chunks: every chunk boundary shifts by 40 B ⇒ all 251
          chunks hash differently ⇒ 1 GB re-uploaded and 1 GB of new
          storage per edit.
          cost per edit: 1 GB upload = 32 s at 250 MB/s of aggregate
          capacity, per user edit. At 21,000 edits/month that is 21 TB of
          monthly upload traffic for 40-byte changes.
      CDC chunks: boundaries are chosen from content patterns, so a 40-byte
          insertion changes ONE chunk. 4–8 MB re-uploaded.
          ⇒ 21,000 edits × 6 MB = 126 GB/month instead of 21 TB. A 167×
            reduction, and the entire difference is the chunking function.
```

**Step 4 — why bytes must bypass the application.**

```
  peak download 340 MB/s, upload 250 MB/s
  proxying through application pods:
      · 590 MB/s of pod network throughput ≈ 24 extra pods at 25 MB/s each
      · egress paid TWICE (object store → pod, pod → user) ⇒ at £0.07/GB
        for 340 MB/s over a month: 340 × 86,400 × 30 = 881 TB = ~£61,700/mo
        of avoidable egress
      · a deploy drains connections mid-download
  presigned URLs: the client fetches from object storage / CDN directly.
      egress paid once, and the pods stay small (8 pods handle the metadata).
  ⇒ THE SAVING IS ~£61,700/MONTH AND 24 PODS. There is no version of this
    design where proxying the bytes is correct.
```

**Step 5 — the covert-channel trade, priced.**

```
  HMAC-keyed block ids: dedupe 35 % → 31 %, i.e. 5.2 PB → 5.5 PB
      +0.3 PB = 314,573 GB × £0.021 = +£6,606/month
      + one extra round trip per upload (+14 ms)
  leaked information without the fix: "does your company store this exact
      file?" for ANY file whose bytes an attacker can obtain — which for a
      published document, a leaked spreadsheet, or a common PDF is trivial.
  ⇒ £6,700/month for a closed disclosure channel. The only defensible
    position is to pay it, and to say so with the number attached.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **Block-level storage + manifests** | Dedupe, cheap versions, delta sync | More moving parts than storing files whole | **Always for a sync product** |
| **Fixed 4 MB chunks** | Simple, trivial byte-range mapping | Terrible on insert/prepend (§9 step 3) | Immutable media, video |
| **CDC (1–8 MB, 4 MB target)** | 25–170× less re-upload on edits | 5 % client CPU; variable sizes | **Documents that get edited** |
| **Cross-user dedupe** | Maximum savings (35 %) | Covert channel; complex GC | Only with keyed ids and a documented policy |
| **Per-user dedupe** | No disclosure channel | 31 % saving instead of 35 % | The safe default |
| **HMAC-keyed block ids** | Closes the disclosure channel | +2,083 keying requests/s; +£6.7k/mo | **Chosen** |
| **Presigned URLs** | No bytes through the app; one egress | Authorisation must be perfect | Always |
| **Proxying bytes** | Simple auth; can transform/inspect | ~£61.7k/mo and 24 pods (§9 step 4) | Regulated content needing DLP inspection |
| **Conflict copies** | Never lose data | Folder clutter (0.31 % of edits) | **Always** |
| **Last-writer-wins** | Clean folders | Silent data loss (§7) | Never for user content |
| **Cursor-based sync** | Correct ordering | Needs a monotonic sequence per user | Always (never timestamps) |
| **Long-poll notifications** | Instant sync, cheap | A held connection per device (9 M) | Standard; use a managed pub/sub at scale |
| **30-day version retention** | Cheap (4 % of storage) | Restore tooling; GDPR considerations | Always |

## 11. Failure scenarios

**11.1 — Silent overwrite on concurrent edit.** Last-writer-wins discarded 3 hours of annotations; the user churned publicly (§7). **Fix:** `base_version` on every commit, conflict copies, and an activity view showing versions.

**11.2 — Dedupe covert channel** (§4.8). The upload endpoint answered "does this block exist?" for arbitrary content. **Fix:** HMAC-keyed block ids; retain plain ids only inside a public tenant.

**11.3 — Refcount drift.** Aborted uploads over-counted refs; 21 TB became unreclaimable. **Fix:** weekly mark-and-sweep with a 7-day grace window, plus an alert on reclaim spikes (a spike can also mean a bug is deleting live data — check before celebrating).

**11.4 — Corrupt block served.** A storage-level bit flip returned a PDF that failed to open. **Fix:** every block verified against its hash on download (client and scrub job); a mismatch is quarantined and re-fetched from a replica, never served.

**11.5 — The 1 GB re-upload.** Fixed chunking plus a prepended page meant every edit re-uploaded the whole file, exhausting mobile data plans and generating support tickets (§9 step 3). **Fix:** CDC.

**11.6 — Quota race.** Two concurrent uploads both passed the quota check and exceeded the limit by 4 GB. **Fix:** reserve the quota in the same transaction as the manifest commit (`SELECT ... FOR UPDATE` on the user row, or a reservation table).

**11.7 — Folder-share authorisation blow-up.** A 40,000-file folder share was materialised as 40,000 ACL rows; `list` took 410 ms and the share operation took minutes. **Fix:** ancestor-based shares resolved at read time with a cached recursive CTE (§4.7).

**11.8 — Notification storm after a bulk import.** A user imported 60,000 files; the long-poll fired once per file and the device downloaded manifests for all of them, three times over. **Fix:** coalesce notifications (a 1-second debounce per user), page the delta API, and have the client compare manifest hashes before fetching anything (§4.6 step 2).

**11.9 — Trash and GDPR erasure.** A deletion request must remove content while versions and backups retain it for 30 days. **Fix:** crypto-shredding — per-user content keys, destroyed on erasure — so the blocks become undecryptable without rewriting immutable storage (the L63 lesson, applied here).

## 12. Alternatives

| Instead of | Consider | When |
|---|---|---|
| A sync product | A managed storage API plus your own metadata | You need storage, not sync |
| Block storage + manifests | Whole-file objects | Files are write-once and never edited |
| Own chunking | A client SDK that chunks for you | You control the client and want less code |
| Presigned URLs | A CDN with signed cookies | Downloads dominate and you want edge caching |
| Cross-user dedupe | Per-user dedupe | Any multi-tenant privacy requirement |
| Conflict copies | Operational transform / CRDT for text | You are building collaborative editing, not file sync |
| Own sync protocol | A managed sync engine | Sync is not your differentiator and you have < 100k users |

## 13. When to use it

- **Block + manifest storage** for any product with editing, versions or sync.
- **CDC** whenever files are edited rather than written once.
- **Presigned URLs** whenever bytes dwarf metadata.
- **Conflict copies** for every user-content sync feature, without exception.
- **Cursor-based delta sync** with content-hash comparison, not timestamps.
- **Keyed block ids** whenever dedupe crosses a tenant boundary.

## 14. When *not* to use it

- **Fixed block storage without a manifest** when you also need versions — you will rebuild it.
- **Cross-user dedupe for sensitive content** — the disclosure channel is a real attack.
- **CDC on immutable media** — video uploads gain nothing and pay CPU.
- **Proxying bytes through your servers** unless regulation requires inspection.
- **Merging binaries** — there is no correct merge; use a copy.
- **Timestamps as sync cursors** — clocks disagree and mtimes are client-editable.

## 15. Common mistakes

1. **No `base_version`**, so concurrent edits silently overwrite (§11.1).
2. **Timestamps as ordering** instead of a monotonic cursor (§4.6).
3. **Fixed chunking** for editable documents (§9 step 3).
4. **Dedupe across tenants with plain hashes** (§4.8).
5. **No refcount sweep**, or a sweep with no grace window (§11.3).
6. **Proxying the bytes** (§9 step 4).
7. **No integrity verification on download** (§11.4).
8. **Quota checked outside the commit transaction** (§11.6).
9. **Folder ACLs materialised per file** (§11.7).
10. **One notification per file** on a bulk import (§11.8).
11. **Storing files in the metadata database** (the L93 §7 mistake, with worse consequences at 8 PB).
12. **No crypto-shredding plan** for GDPR erasure of immutable blocks (§11.9).
13. **Assuming the dedupe ratio** instead of measuring it before committing to a cost model (§9 step 1).
14. **A conflict copy without telling the user** — a copy nobody notices is the same as data loss, delayed.

## 16. Interview perspective

**What is actually being tested.** Whether you separate **bytes from metadata**, whether you understand that dedupe is the entire cost model, and whether you can design a sync protocol that cannot silently lose data. Strong candidates: chunks, manifests, presigned URLs, cursor-based delta, conflict copies, keyed block ids. Weak candidates: "store the file in S3, put the path in Postgres".

**Answers that land well:**

- *"How would you store 8 petabytes?"* — "As blocks, not files. Four-megabyte chunks, identified by a keyed hash of their content, stored once. A file is a manifest — an ordered list of block hashes — in a transactional database, because the manifest is what must be atomic. That gives three things at once: dedupe, where 35 % of bytes disappear; versioning, where a new version of a gigabyte file costs ten megabytes; and delta sync, where only changed blocks move."
- *"Why content-defined chunking?"* — "Because fixed boundaries break on insertion. Put 40 bytes at the front of a one-gigabyte file and every fixed chunk shifts, so an edit re-uploads a gigabyte — we measured 21 terabytes a month of pointless traffic. Content-defined boundaries follow the content, so the same edit costs one chunk. That was a 167-times reduction for a different chunking function."
- *"How do you handle two devices editing the same file?"* — "Never merge, never overwrite. Every commit carries the base version it was built from; if it does not match the current version, the server stores the incoming content as a conflict copy with the device and date in the name, and tells the client. It happens on 0.31 % of edits, and it is the difference between a slightly cluttered folder and losing three hours of someone's annotations — which is exactly the bug that cost us a customer."
- *"Where's the money going?"* — "Storage is £114,000 a month even after dedupe. Two decisions dominate: chunking, worth £12,000 a month, and not proxying bytes through our servers, worth about £62,000 a month of double egress plus twenty-four pods. And the privacy fix for dedupe costs £6,700 a month — we pay it, because without it the upload endpoint tells anyone whether a given file exists in our system."
- *"What's the ordering primitive?"* — "A per-user monotonic sequence, not a timestamp. Devices ask for everything after their cursor. Clocks disagree and modification times come from clients, so timestamp-based sync re-uploads forever or, worse, loses changes."

**Common interview tasks:** design the storage model; choose chunking; compute dedupe savings; design upload with resume; design sync and conflict handling; design sharing/ACLs; handle deletion and GDPR; cost it.

**Red flags interviewers watch for:**

- Files as single blobs with no manifest.
- No dedupe, or dedupe without discussing its privacy consequences.
- Bytes through the application servers.
- Last-writer-wins conflict handling.
- Timestamps as the sync cursor.
- No refcount/GC story.
- No integrity verification.
- No mention of what happens when an upload is interrupted.
- No numbers: no PB, no dedupe ratio, no egress cost.

## 17. Knowledge check

**Conceptual**

1. Explain why a file is stored as a manifest plus blocks rather than as a blob, and derive the three capabilities this unlocks (dedupe, cheap versions, delta sync). Then explain why the manifest commit must be a single database transaction.
2. Explain content-defined chunking and why fixed-size chunking fails on an insertion. Give the byte arithmetic for a 1 GB file with 40 bytes prepended, for both schemes.
3. Explain the confirmation-of-a-file attack, why cross-user dedupe enables it, and the three mitigations with their costs. Then explain why convergent encryption alone does not fix it.

**Practical scenario**

4. Design BookNook Library: 6 M users, 8 PB raw, 400 M files, 1.5 devices each, a 60 s sync SLO, 30-day versioning, folder sharing, and a GDPR erasure requirement against immutable object storage. Give the requirements and capacity tables (storage, blocks, metadata, traffic), the API including init/complete and the dedupe hint, the schema with versions and refs, the chunking choice with justification, the write path and its transaction boundary, the sync protocol with cursors and conflicts, the read path with presigned URLs, the sharing authorisation strategy, the GC and scrub jobs, the privacy decision on block ids, the cost model in pounds, and the failure table. State the first thing that breaks at 10× users and why.

**Calculation**

5. 8 PB raw with a measured dedupe ratio. (a) Compute the stored size and the monthly cost at £0.021/GB-month for ratio 28 %, 31 % and 35 %. (b) Compute the annual difference between the best and worst. (c) If average file size is 6.6 MB and the block size is 4 MB, compute blocks per file and the total block instances at 400 M files. (d) State whether the block table (60 B/row, deduped) is a problem at 1.3 B rows, with the memory requirement if fully cached.

6. A 1 GB file is edited 30 times in a month with a 1 % change per edit. (a) Compute the storage used with no block reuse. (b) Compute it with block reuse and 4 MB blocks. (c) Compute the upload volume per edit for fixed versus content-defined chunking given a 40-byte insertion. (d) At 21,000 edits/month, compute the monthly upload traffic in each scheme and the ratio.

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
  - [x] **6. Design file storage (L98) ← you are here**
  - [ ] 7. Design a web crawler (L99)
  - [ ] 8. Design real-time chat (L100)
  - [ ] 9. Design a news feed (L101)
  - [ ] 10. Design video streaming (L102)
  - [ ] 11. Design ride sharing (L103)
  - [ ] 12. Design e-commerce and inventory (L104)
  - [ ] 13. Design payment processing (L105)
  - [ ] 14. Design a metrics and logging platform (L106)

**NEXT LESSON: Phase 5, Topic 7 — Design a web crawler**

---
