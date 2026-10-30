# Lesson 105 — Design Payment Processing

*Phase 5, Topic 13*

## 1. Simple definition

**The problem.** Take money from a customer's card, put it in a merchant's account, and keep an exact record of what happened — for **8 M payments a day**, with **zero tolerance for a lost or duplicated pound**, across acquirers that fail, networks that time out mid-authorisation, webhooks that arrive twice, and disputes that arrive 90 days later.

**Why it is different from every other lesson in this course.** Everywhere else, a lost request costs a retry. Here a lost request costs money, a duplicated one costs money, and an **unreconciled** one costs an audit. The system's central object is not the payment but the **ledger**: an append-only, double-entry record where every movement appears exactly twice, once as a debit and once as a credit, and the sum over everything is always zero.

Terms defined before use:

- **Authorisation**: the card network's promise to pay, typically valid 7 days. **Not money yet.**
- **Capture**: the claim on the authorisation that actually moves money (usually T+1 or T+2 to settlement).
- **PSP / acquirer**: the processor that talks to the card networks.
- **Idempotency key**: a caller-supplied identifier that makes a retried request a no-op instead of a second charge.
- **Double-entry ledger**: every transaction has postings whose debits and credits sum to zero.
- **Posting**: one leg of a transaction (one account, one amount, one direction).
- **Settlement**: the acquirer's actual transfer of funds, arriving days later with a file listing what it thinks happened.
- **Reconciliation**: matching our ledger against the acquirer's file, and treating every difference as an incident.
- **Chargeback**: a customer dispute that forcibly reverses a payment, with a fee.
- **PCI DSS scope**: the set of systems that touch card numbers; the goal is to make it as small as possible.
- **Tokenisation**: replacing a card number with a reference that only the vault can resolve.
- **Minor units**: integer pence/cents — **money is never a float**.

The one sentence to carry out of this lesson: **the payment itself is a request to an unreliable third party; the only thing you truly own is the ledger — so make the ledger append-only, double-entry and reconciled, make every external call idempotent, and treat every difference between your ledger and the acquirer's file as a bug to be found, not a rounding error to be absorbed.**

## 2. Why it exists

**Because "exactly once" is not available, so it must be manufactured.** Card networks give you at-least-once at best: a request can time out while succeeding, and a retry can then double-charge. The only defence is an **idempotency key that the downstream also honours**, plus a ledger that can tell you whether a charge exists before you make it.

**Because settlement is a distributed system with a 2-day lag.** You authorise at 14:03 on Friday; the acquirer settles on Tuesday with a file that says what it thinks happened. **The file is the truth you must reconcile against**, and it will disagree: 0.02 % of lines do not match, and each unmatched line is real money (§7).

**Because the ledger is the product.** A payments company is a ledger with an API on top. Any design that computes a balance by summing history at query time, or that updates a balance row in place without a posting, will eventually be wrong and un-auditable — and "eventually wrong" in a ledger means an auditor finds it.

**Because the costs are lopsided.** Infrastructure for 8 M payments/day is roughly £420,000/month. Chargeback losses at a 0.06 % rate are **£9.9 M/month** — 24× the infrastructure. The design effort should go where the money goes: fraud, disputes, reconciliation, and idempotency.

**And because the failure modes are the memorable ones.** Double charges, missing webhooks, silently dropped refunds, a retry that captured twice, a payout run that paid a merchant twice. Every one of these is a design defect in the exact machinery this lesson is about.

## 3. Real-world analogy

**A bank with a bound ledger book, numbered pages, and a clerk who never crosses anything out.**

When money moves, the clerk writes **two lines** on the same page: one account gives, another receives. The page is only valid if the two columns balance — that is double-entry, and it is 700 years old because it works: an error in one place must be matched by an error somewhere else, which makes silent corruption almost impossible.

**Nothing is ever erased.** A correction is a new entry that reverses the old one, referencing it. That is an append-only ledger: the history is the truth, and a balance is a *summary* of history, not a variable that gets edited.

**And the ledger has numbered pages.** If page 4,102 is missing, everyone knows — that is a hash chain or a monotonic sequence number. This is what makes "we did not lose a payment" a checkable claim rather than a hope.

The analogy's crucial detail: **the clerk writes in the book first, and only then does the money leave the building.** In the real system, the ledger posting and the settlement instruction are the same transaction, so there is no window where money moved and the book does not say so — that is the outbox pattern (L85) applied to the one place where it is not optional.

## 4. Detailed technical explanation

### 4.1 Requirements

**Functional.**

| # | Requirement |
|---|---|
| F1 | Accept a card payment: authorise, then capture (or auth-and-capture in one step) |
| F2 | Idempotency: a retried request with the same key must not create a second charge |
| F3 | Refund, fully or partially, idempotently |
| F4 | Record every movement in a double-entry ledger that always balances |
| F5 | Settle and pay out to merchants (T+2, with a rolling reserve) |
| F6 | Deliver webhooks to merchants, at-least-once, signed and ordered per object |
| F7 | Reconcile against acquirer settlement files daily, and escalate breaks |
| F8 | Handle the dispute lifecycle: chargeback received, evidence, representment |
| F9 | Multi-currency with an explicit FX rate recorded per payment |
| F10 | Keep card data out of the payment service's scope entirely (tokenisation) |

**Non-functional.**

| # | Requirement | Number |
|---|---|---|
| N1 | Authorisation latency | p50 < 600 ms, p99 < 2.2 s (the card network dominates) |
| N2 | Throughput | 93 payments/s average, 420/s peak, 1,000/s burst |
| N3 | Availability of the authorisation path | 99.99 % (52 min/year) |
| N4 | Money correctness | **zero** lost, zero duplicated, zero unexplained difference |
| N5 | Ledger durability | synchronous replication, 3 AZs, `fsync` before ack |
| N6 | Reconciliation | every transaction matched or escalated within 24 h of settlement |
| N7 | Webhook delivery | 99.9 % within 60 s; every failure retried for 72 h |
| N8 | Audit retention | 7 years, immutable |
| N9 | Idempotency window | 24 h |

### 4.2 Capacity estimation

```
  VOLUME
      8,000,000 payments/day = 93/s average
      peak (Black Friday, 11:00) 420/s; burst 1,000/s for 30 s
      average payment £54 ⇒ 8 M × £54 = £432 M/day = £157 B/year
      merchant fee revenue at 1.4 % = £6.0 M/day

  LEDGER
      postings per payment: 4 (debit customer-receivable, credit merchant-
      payable, debit/credit fee, debit/credit network-fee) — 6 with FX
      8 M × 5.4 = 43 M postings/day
      posting row ≈ 140 B ⇒ 6.0 GB/day = 2.2 TB/year; retain 7 years
      ⇒ the ledger is SMALL. It is small because it is append-only and
        numeric: the entire 7-year record of £1.1 trillion of payments is
        ~15 TB. STORAGE IS NOT THE PROBLEM; CORRECTNESS IS.

  IDEMPOTENCY
      8 M keys/day × 400 B = 3.2 GB/day; 7-day window = 22 GB
      ⇒ in a single Redis/Postgres instance. Rounding error.

  RECONCILIATION
      acquirer file: 8 M lines/day per acquirer; matched against the ledger
      by acquirer reference. 24 h of data fits in memory; the job is CPU-light
      and correctness-heavy.

  WEBHOOKS
      ~2.4 webhook deliveries per payment (payment, capture, refund events)
      = 19.2 M/day = 222/s average, 1,000/s at peak
      retries add 4.1 % ⇒ 232/s average

  WHERE THE MONEY ACTUALLY GOES (monthly)
      infrastructure (auth path, ledger, reconciliation, vault)
                                              ≈ £420,000
      card network + acquirer fees (in, then out)  pass-through
      chargeback losses at 0.06 % of 240 M payments/month
         = 144,000 × (£54 loss + £15 fee)     ≈ £9,940,000
      ⇒ FRAUD AND DISPUTES COST 24× THE INFRASTRUCTURE. Design accordingly.
```

### 4.3 API

```
POST /v1/payments                        # auth + capture, or auth only
     Idempotency-Key: <caller-generated uuid>
     Stripe-style: { "amount": 5400, "currency": "GBP",
                     "payment_method": "pm_…", "capture": true,
                     "reference": "order_o_8f2", "metadata": { … } }
  →  201 { "id": "pay_…", "state": "succeeded|requires_action|failed",
           "authorised_at": "…", "captured_at": "…", "acquirer_ref": "…" }
  →  409 { "code": "idempotency_key_reuse",
           "original": "pay_…" }          # replay of a DIFFERENT body

POST /v1/payments/{id}/capture   { "amount": 5400 }      # partial capture allowed
POST /v1/payments/{id}/refunds   { "amount": 1200, "reason": "returned" }
                                 Idempotency-Key: <uuid>
GET  /v1/payments/{id}
GET  /v1/payments?created_after=&limit=100                # cursor paginated
POST /v1/payment_methods        { "card_token": "tok_…" }  # vault reference

GET  /v1/balance                          # merchant's available + pending
GET  /v1/payouts / POST /v1/payouts/{id}/cancel
GET  /v1/disputes / POST /v1/disputes/{id}/evidence
GET  /internal/ledger/accounts/{id}/postings
POST /internal/ledger/transactions        # internal only; balanced or rejected
GET  /internal/reconciliation/runs/{date} # breaks, matched, escalated

webhooks (signed with HMAC, at least once, ordered per object):
     payment.succeeded · payment.failed · payment.requires_action
     refund.succeeded · dispute.created · dispute.closed · payout.paid
```

### 4.4 Data model

```sql
-- ── THE LEDGER: append-only, double-entry, hash-chained ──────────────────
CREATE TABLE ledger_accounts (
  id          BIGINT PRIMARY KEY,
  owner_type  SMALLINT NOT NULL,   -- customer|merchant|platform|network
  owner_id    BIGINT NOT NULL,
  kind        SMALLINT NOT NULL,   -- receivable|payable|revenue|expense|fx
  currency    CHAR(3) NOT NULL,
  UNIQUE (owner_type, owner_id, kind, currency)
);

CREATE TABLE ledger_transactions (
  id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  kind         SMALLINT NOT NULL,     -- payment|capture|refund|payout|fee|adj
  ref_type     SMALLINT NOT NULL,
  ref_id       TEXT,                  -- payment id, refund id, payout id
  currency     CHAR(3) NOT NULL,
  prev_hash    BYTEA NOT NULL,        -- hash chain over transaction ids
  hash         BYTEA NOT NULL,
  CHECK (length(prev_hash) = 32)
);

CREATE TABLE ledger_postings (
  id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  transaction_id BIGINT NOT NULL REFERENCES ledger_transactions(id),
  account_id     BIGINT NOT NULL REFERENCES ledger_accounts(id),
  amount_minor   BIGINT NOT NULL,     -- signed; never zero
  CHECK (amount_minor <> 0)
);
-- THE INVARIANT, enforced in the same transaction as the insert:
--   SELECT sum(amount_minor) FROM ledger_postings
--    WHERE transaction_id = $1   MUST be 0, else ROLLBACK.
-- Balances are MATERIALISED per account (a running total + a last-posting id)
-- and rebuilt from postings whenever the checksum disagrees.

-- ── THE PAYMENT: the saga, and the idempotency boundary ─────────────────
CREATE TABLE payments (
  id               UUID PRIMARY KEY,
  idempotency_key  TEXT NOT NULL,
  request_hash     BYTEA NOT NULL,        -- sha256 of the canonical body
  merchant_id      BIGINT NOT NULL,
  amount_minor     BIGINT NOT NULL CHECK (amount_minor > 0),
  currency         CHAR(3) NOT NULL,
  captured_minor   BIGINT NOT NULL DEFAULT 0,
  refunded_minor   BIGINT NOT NULL DEFAULT 0,
  state            SMALLINT NOT NULL,     -- created|authorising|authorised|
                                          -- capturing|captured|failed|refunded
  acquirer         SMALLINT NOT NULL,
  acquirer_ref     TEXT,                  -- THE reconciliation key
  auth_code        TEXT,                  -- network authorisation code
  auth_expires_at  TIMESTAMPTZ,           -- ~7 days
  fx_rate          NUMERIC(20,10),        -- recorded, never recomputed
  CHECK (refunded_minor <= captured_minor),
  CHECK (captured_minor <= amount_minor)
);
CREATE UNIQUE INDEX pay_idem ON payments (merchant_id, idempotency_key);
CREATE UNIQUE INDEX pay_acq_ref ON payments (acquirer, acquirer_ref)
  WHERE acquirer_ref IS NOT NULL;

-- ── THE OUTBOX: ledger changes and webhooks are never dual writes ───────
CREATE TABLE outbox (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  topic       TEXT NOT NULL,
  key         TEXT NOT NULL,          -- payment id ⇒ ordering per object
  payload     JSONB NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  dispatched_at TIMESTAMPTZ
);
CREATE INDEX outbox_undispatched ON outbox (id) WHERE dispatched_at IS NULL;

-- ── RECONCILIATION: every acquirer line, matched or escalated ───────────
CREATE TABLE recon_lines (
  id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  acquirer      SMALLINT NOT NULL,
  settlement_date DATE NOT NULL,
  acquirer_ref  TEXT NOT NULL,
  amount_minor  BIGINT NOT NULL,
  fee_minor     BIGINT NOT NULL,
  raw           JSONB NOT NULL,
  match_state   SMALLINT NOT NULL,   -- 0 unmatched|1 matched|2 break|3 resolved
  payment_id    UUID,
  UNIQUE (acquirer, settlement_date, acquirer_ref)
);
```

### 4.5 Idempotency: the exact mechanism, including the hard part

```
  THE THREE CASES a retried POST can be in:
    1. the first request never reached us      → process it now
    2. it reached us and is still in flight    → do not process; wait
    3. it reached us and completed             → return the stored response

  THE IMPLEMENTATION (a single atomic claim, no read-then-write):
    INSERT INTO payments (id, merchant_id, idempotency_key, request_hash,
                          amount_minor, currency, state)
    VALUES ($1,$2,$3,$4,$5,$6, 0)
    ON CONFLICT (merchant_id, idempotency_key) DO NOTHING
    RETURNING id
    · 1 row  ⇒ we own this request: proceed to authorise
    · 0 rows ⇒ somebody already owns the key:
         SELECT request_hash, state, response FROM payments
          WHERE merchant_id=$2 AND idempotency_key=$3
         - request_hash differs   ⇒ 409 idempotency_key_reuse (a CLIENT BUG,
                                    surfaced loudly: same key, different body)
         - state = created/running ⇒ 409 conflict, "in flight", Retry-After: 1
         - state = final           ⇒ replay the stored response, verbatim
  ⇒ THE HARD PART: a request that is IN FLIGHT and stuck (the process died
    mid-authorisation). The key is claimed and nobody will ever finish it.
    Solution: an in-flight lease with a 60 s expiry, plus a CDC-driven state
    machine — and, crucially, an AUTHORISATION-LEVEL idempotency key sent to
    the acquirer (most acquirers accept one), so that finishing the request
    is safe even if the acquirer already saw it.
  MEASURED: 2.1 % of authorisation calls time out client-side; of those, 61 %
  had actually succeeded at the acquirer. Without request-level idempotency
  at the acquirer, retries would double-charge 1.3 % of those — that is the
  arithmetic behind the November incident in §7.
```

### 4.6 The payment state machine, and where the irreversible point is

```
  created ──► authorising ──► authorised ──► capturing ──► captured
     │             │              │              │            │
     │             │              │              │            └─► refunded
     │             │              │              └─ (acquirer accepted the
     │             │              │                 capture; UNKNOWN result
     │             │              │                 until reconciliation)
     │             │              └─ voided (auth released, no money moved)
     │             └─ failed (declined, or timeout ⇒ reconcile before retry)
     └─ abandoned (key claimed, never proceeded; lease expiry)

  THE IRREVERSIBLE POINT: `capturing → captured`. Before it, a void releases
  everything at no cost. After it, the only recovery is a REFUND — which costs
  the network fee, may take 5 days to appear, and counts against the merchant
  in dispute statistics.
  ⇒ therefore capture is ALWAYS the last step, and it is ALWAYS bracketed by
    a durable intent record:
       1. write intent(row: capture requested, amount, acquirer_ref)  + COMMIT
       2. call the acquirer
       3. write outcome
    If the process dies between 1 and 3, the intent record tells the recovery
    job exactly what to reconcile — an UNKNOWN state is a first-class state
    in this state machine, never an exception.
```

### 4.7 The double-entry ledger, and why balances are derived

```
  EVERY MONEY MOVEMENT IS A BALANCED TRANSACTION. A £54 payment, 1.4 % fee:
      Dr  customer_receivable       5400     (we are owed £54)
      Cr  merchant_payable         -5324     (we owe the merchant £53.24)
      Cr  platform_revenue           -76     (our fee)
      ────────────────────────────────────
      sum = 0   ← MUST be zero, enforced in the same transaction

  THEN THE CAPTURE SETTLES IT:
      Dr  acquirer_clearing         5400     (the acquirer owes us)
      Cr  customer_receivable      -5400

  AND THE PAYOUT:
      Dr  merchant_payable          5324
      Cr  acquirer_clearing        -5324

  WHY THIS IS NOT OVER-ENGINEERING:
    · a single "balance" column can be wrong with no trace; a balanced ledger
      cannot silently lose money — an error must appear twice
    · refunds, partial captures, FX, fees, reserves and disputes all compose
      without special cases: they are just more postings
    · "what is our exposure to acquirer X today?" is a query, not a meeting
  WHY BALANCES ARE STILL MATERIALISED:
    · summing 43 M postings/day for a balance read is unacceptable
    · so each account keeps `balance_minor` and `last_posting_id`, updated in
      the same transaction as the posting
    · and a nightly job recomputes from postings and compares: any drift is
      a PAGE (§8 alert), because drift means either the cache is wrong or a
      posting bypassed the ledger write path
  MEASURED: 0 drift events in 18 months after the rebuild; before it, the
  "balance" table had drifted by £41,000 across 2,900 merchant accounts,
  discovered by an audit rather than by monitoring.
```

### 4.8 Reconciliation: the acquirer's file is the truth you have to face

```
  THE DAILY LOOP (per acquirer, per settlement date):
    1. ingest the settlement file (8 M lines; SFTP/API; arrives T+1 by 06:00)
    2. match each line to a payment by (acquirer, acquirer_ref)
    3. classify:
         matched            97.98 %
         in acquirer, not in ledger   0.9 %   ← WE MISSED SOMETHING
         in ledger, not in acquirer   0.9 %   ← THEY MISSED SOMETHING
         amount mismatch              0.02 %  ← fees, FX, partial capture
    4. auto-resolve the explicable subset (fee differences within tolerance,
       timing differences by settlement date)
    5. escalate the rest: 0.02 % of 8 M = 1,600 breaks/day, each with an
       owner and a 24-hour SLA
  WHY THE 0.9 % CATEGORIES EXIST AT ALL: file timing. An authorisation at
  23:59 settles on the next file; a capture finalised at 00:02 lands the day
  after. Matching must allow a ±1 settlement-day window before calling
  something a break.
  ⇒ THE METRIC THAT MATTERS: unresolved breaks older than 24 h. It was 380
    per day in v1 (because retries created acquirer_ref collisions and the
    matcher could not decide which was real) and is 0-4 per day now.
```

### 4.9 PCI scope: the cheapest possible architecture

```
  GOAL: the payment service must never see a card number.
    1. The card number is entered in a hosted field (an iframe from the
       vault provider, or our own vault service in a separate, tiny, heavily
       restricted enclave).
    2. It is exchanged for a single-use token.
    3. The payment service, the ledger, the webhook path — none of them ever
       handle the PAN. Their PCI scope is SAQ-A/SAQ-A-EP instead of SAQ-D.
  MEASURED COST DIFFERENCE: SAQ-D scope (the whole platform in scope) means
  quarterly ASV scans of 340 hosts, code reviews of every service, and an
  annual on-site assessment — roughly £180,000/year and 4 months of engineering
  attention. Scope reduction took it to 6 hosts and £22,000/year.
  ⇒ NOTHING ELSE IN THIS LESSON HAS A BETTER RETURN PER WEEK OF WORK.
```

## 5. Request / data flow

```
 AUTHORISE + CAPTURE (p99 2.2 s; the network is 86 % of it)
 ─────────────────────────────────────────────────────────────────────────
 t+0      POST /v1/payments with Idempotency-Key
 t+2 ms   atomic key claim (INSERT … ON CONFLICT DO NOTHING)
          ⇒ 0 rows: replay or 409 (see §4.5); 1 row: proceed
 t+6 ms   request_hash compared with the stored one
 t+10 ms  fraud score (synchronous, 40 ms budget, §7): allow / challenge / block
 t+50 ms  3-D Secure decision: 22 % of EU payments step up
 t+80 ms  acquirer authorisation call (p50 420 ms, p99 1.9 s)
 t+500 ms authorisation result: write intent, then outcome
          ⇒ the LEDGER TRANSACTION and the `payment.succeeded` outbox row are
            written in the SAME database transaction as the state change
 t+520 ms postings: Dr receivable 5400 / Cr merchant_payable 5324 /
          Cr platform_revenue 76 — sum checked = 0, else ROLLBACK
 t+540 ms webhook dispatched from the outbox (competing consumer per key)
 ⇒ p50 640 ms, p99 2.2 s

 DECLINED (6.1 % of attempts)
 ─────────────────────────────────────────────────────────────────────────
 same path until the acquirer answers `declined`; NO ledger postings, NO
 webhook for money, one `payment.failed` event, and a structured reason code
 the merchant can act on. A decline is a normal outcome, not an error.

 SETTLEMENT AND RECONCILIATION (T+1, 06:00)
 ─────────────────────────────────────────────────────────────────────────
 file arrives → recon_lines → matcher → breaks → escalation queue
 matched lines move acquirer_clearing toward the payout; the payout run
 (T+2) pays merchants `available - reserve`.

 DISPUTE (arrives up to 120 days later)
 ─────────────────────────────────────────────────────────────────────────
 dispute.created webhook → freeze the amount in merchant_payable (a posting,
 not a flag) → evidence window (7 days) → representment → closed:
   won:  release the freeze;   lost: post the loss + the £15 fee
```

## 6. ASCII diagram

```
   MERCHANT ──POST /v1/payments (Idempotency-Key)──┐
                                                   ▼
                          ┌───────────────────────────────────────┐
                          │ PAYMENT SERVICE (stateless, 3 AZs)     │
                          │  1 claim idempotency key (atomic)      │
                          │  2 fraud score  ──► FRAUD (40 ms)      │
                          │  3 3-D Secure   ──► challenge?         │
                          │  4 state machine: created → authorised │
                          └───────┬───────────────────────┬────────┘
                                  │                       │
                        ┌─────────▼─────────┐   ┌─────────▼─────────┐
                        │ ACQUIRER ROUTER   │   │ LEDGER (append-   │
                        │ primary/secondary │   │ only, double-     │
                        │ by BIN, error rate│   │ entry, hash-      │
                        └────┬─────────┬────┘   │ chained)          │
                             │         │        │ Dr receivable 5400│
                     ┌───────▼──┐ ┌────▼─────┐  │ Cr payable   -5324│
                     │ PSP A    │ │ PSP B    │  │ Cr revenue     -76│
                     └───────┬──┘ └────┬─────┘  │ Σ = 0 or ROLLBACK │
                             │         │        └─────────┬─────────┘
                             ▼         ▼                  │ same txn
                       CARD NETWORKS                      ▼
                             │                ┌───────────────────────┐
                             │                │ OUTBOX → webhooks      │
                             │                │ (signed, per-object    │
                             ▼                │  ordered, 72 h retry)  │
                    ┌──────────────────┐      └───────────────────────┘
                    │ SETTLEMENT FILES │
                    │ T+1, 8 M lines   │──► RECONCILER ──► breaks ──► SLA
                    └──────────────────┘           │
                                                   ▼
                                        PAYOUT RUN (T+2, reserve held)

   THE THREE INVARIANTS, AND WHERE EACH IS ENFORCED:
     1. Σ postings per transaction = 0        in the DB transaction
     2. one payment per (merchant, key)       unique index
     3. every acquirer line matched or owned  recon queue + alert on age

   THE ONE THING YOU CANNOT BUY: the acquirer's answer. 71 % of latency and
   100 % of the uncertainty live behind that arrow.
```

## 7. Detailed example

**LedgerPay**: card payments for 40,000 merchants, 8 M payments/day, £157 B/year of volume.

```
  measured:
      93 payments/s average, 420/s peak, 1,000/s burst
      auth p50 640 ms end to end · p99 2.2 s
      decline rate 6.1 % (of which 1.4 % are "soft" and retryable)
      3-D Secure step-up 22 % of EU payments, +1.8 s when triggered
      chargeback rate 0.06 % of payments ⇒ £9.9 M/month of losses
      recon: 97.98 % matched, 1,600 breaks/day, 4 unresolved after 24 h
```

**The double-charge incident (the one that rewrote the design):**

```
  2025-11-07, Black Friday peak: 1,400 merchants double-charged.
      cause: the client SDK retried on a 3 s client timeout, and the retry
      carried the SAME X-Request-Id but the server only used it for LOGGING —
      the payment service had no idempotency store at all.
      of 21,400 requests that timed out client-side, 61 % had succeeded at
      the acquirer; the retries created a second authorisation for 13,050 of
      them, and 1,400 were captured twice.
      cost: £151,000 of refunds, £40,000 of network fees that are not
      refundable, 3 weeks of support, and one enterprise merchant who left
      (worth £340,000/year of fees).
  fixes, in the order they were shipped:
      1. the atomic idempotency claim (§4.5) with a unique index — 2 days
      2. request-level idempotency keys SENT TO THE ACQUIRER, so a retry is a
         no-op even at the network — 3 weeks, and it is the one that actually
         makes retries safe
      3. an "unknown" state (§4.6) so a timeout is never treated as a failure
         — retrying a success is the bug; reconciling an unknown is the fix
  ⇒ THE LESSON: an idempotency key that is not honoured DOWNSTREAM is
    documentation, not a mechanism.
```

**The fraud model that paid for itself in 11 days:**

```
  v1: rules only (velocity, BIN country, amount ceiling)
      chargeback rate 0.31 % ⇒ £51 M/month of losses (!!)
  v2: gradient-boosted model on 40 features, synchronous, 40 ms budget
      chargeback rate 0.06 % ⇒ £9.9 M/month
      cost: £41,000/month of feature infrastructure + a 3-person team
      ⇒ £41 M/month of avoided losses. The model is the highest-leverage
        component in the entire platform.
  ⚠ AND THE TRAP: the model also blocked 0.4 % of GOOD payments at launch.
    That is 32,000 customers a day who could not buy — measured as
    "false-positive friction", it cost £1.7 M/month of lost merchant revenue
    and produced the loudest support queue in company history. The fix was a
    challenge instead of a block (3-D Secure step-up) for the middle score
    band, which cut false positives by 71 % at half the fraud capture.
  ⇒ IN FRAUD, THE COST OF A FALSE POSITIVE IS NOT ZERO. Model the loss
    function, not just the detection rate.
```

**The reconciliation break that found a real bug:**

```
  2026-02, a persistent 0.4 % "in ledger, not in acquirer" break for one PSP:
      cause: our capture call succeeded, we wrote `captured`, and the PSP later
      reversed the capture because the authorisation had expired (7 days).
      We had no expiry check before capture. 3,100 payments were captured
      against expired authorisations over 9 days; the money never settled.
      cost: £167,000 of receivables we had already told merchants were theirs.
  fix: `auth_expires_at` checked before capture, with a 24-hour pre-expiry
  sweep that captures or voids every authorisation about to lapse.
  ⇒ RECONCILIATION IS NOT ACCOUNTING HYGIENE; IT IS THE MONITORING SYSTEM
    FOR THE ENTIRE BUSINESS. It found in 9 days what 340 unit tests did not.
```

## 8. Code or configuration example

```python
# ── The ledger write: balanced or not at all. One function. ──────────────
async def post_transaction(conn, kind, ref_type, ref_id, currency, postings):
    total = sum(p["amount_minor"] for p in postings)
    if total != 0:
        raise LedgerImbalance(total)          # never insert an unbalanced txn
    async with conn.transaction():
        prev = await conn.fetchval(
            "SELECT hash FROM ledger_transactions ORDER BY id DESC LIMIT 1")
        prev = prev or b"\x00" * 32
        tid = await conn.fetchval(
            """INSERT INTO ledger_transactions
                 (kind, ref_type, ref_id, currency, prev_hash, hash)
               VALUES ($1,$2,$3,$4,$5,$6) RETURNING id""",
            kind, ref_type, ref_id, currency, prev, b"\x00" * 32)
        await conn.executemany(
            """INSERT INTO ledger_postings
                 (transaction_id, account_id, amount_minor)
               VALUES ($1,$2,$3)""",
            [(tid, p["account_id"], p["amount_minor"]) for p in postings])
        # materialised balances, in the same transaction
        for p in postings:
            await conn.execute(
                """UPDATE ledger_account_balances
                     SET balance_minor = balance_minor + $2, last_posting_id = $3
                   WHERE account_id = $1""",
                p["account_id"], p["amount_minor"], tid)
        new_hash = chain_hash(prev, tid, postings)
        await conn.execute(
            "UPDATE ledger_transactions SET hash = $2 WHERE id = $1",
            tid, new_hash)
    return tid

# ── Idempotency claim + replay, atomic, with request-hash checking ───────
async def claim(pool, merchant_id, key, body) -> Claim:
    rh = sha256(canonical(body))
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO payments (id, merchant_id, idempotency_key,
                                     request_hash, amount_minor, currency,
                                     state, lease_until)
               VALUES ($1,$2,$3,$4,$5,$6, 0, now() + interval '60 seconds')
               ON CONFLICT (merchant_id, idempotency_key) DO NOTHING
               RETURNING id""",
            new_id(), merchant_id, key, rh, body.amount, body.currency)
        if row:
            return Claim(owned=True, payment_id=row["id"])
        existing = await conn.fetchrow(
            """SELECT id, request_hash, state, response FROM payments
                WHERE merchant_id=$1 AND idempotency_key=$2""",
            merchant_id, key)
    if existing["request_hash"] != rh:
        raise IdempotencyKeyReuse(existing["id"])   # client bug: make it loud
    if existing["state"] in (0, 1, 2):              # created/authorising/capturing
        raise InFlight(existing["id"])              # 409 + Retry-After: 1
    return Claim(owned=False, replay=existing["response"])

# ── Capture: intent first, then the call, then the outcome (never skip) ──
async def capture(payment_id, amount_minor):
    p = await repo.get(payment_id)
    if p.auth_expires_at and p.auth_expires_at < utcnow() + timedelta(hours=24):
        await void_or_recapture(p)                  # the §7 expiry bug
        return
    intent = await repo.write_intent(payment_id, "capture", amount_minor)
    try:
        res = await acquirer(p.acquirer).capture(
            acquirer_ref=p.acquirer_ref,
            amount=amount_minor,
            # THE downstream idempotency key — this is the mechanism
            idempotency_key=f"{p.id}:capture:{amount_minor}")
        await repo.finalise(intent, outcome=res, new_state="captured")
        await post_transaction(db, kind=CAPTURE, ref_type=PAYMENT, ref_id=p.id,
            currency=p.currency, postings=[
              {"account_id": acc.acquirer_clearing(p.acquirer), "amount_minor": amount_minor},
              {"account_id": acc.customer_receivable,           "amount_minor": -amount_minor},
            ])
        await outbox.publish("payment.succeeded", key=p.id, payload=res)
    except (Timeout, ConnectionError):
        await repo.finalise(intent, outcome="UNKNOWN", new_state="unknown")
        # the reconciler owns it from here; NEVER blind-retry a capture
```

```yaml
# ── Rails ────────────────────────────────────────────────────────────────
payments:
  idempotency: { window_hours: 24, key_scope: merchant, lease_seconds: 60 }
  acquirer_idempotency: required          # refuse an acquirer that lacks it
  capture: { require_unexpired_auth: true, expiry_sweep_hours: 24 }
  states_include_unknown: true            # a first-class state, not an error
ledger:
  append_only: true                       # no UPDATE/DELETE grants, ever
  balance_reads: materialised             # rebuilt nightly from postings
  posting_sum_must_be_zero: enforced
  hash_chain: sha256
reconciliation:
  schedule: "0 6 * * *"                   # T+1, after files land
  settlement_window_days: 1               # ±1 day before calling a break
  break_sla_hours: 24
  fee_tolerance_minor: 2
webhooks:
  signing: hmac-sha256, timestamped, 5-min tolerance
  retry: { schedule: "10s,30s,2m,10m,1h,6h,24h", max_age_hours: 72 }
  ordering: per object key, single consumer per key
alerts:
  - alert: LedgerImbalance
    expr: increase(ledger_imbalance_total[5m]) > 0
    severity: page        # structurally impossible; if it fires, stop writes
  - alert: LedgerBalanceDrift
    expr: abs(ledger_balance_drift_minor) > 0
    severity: page        # nightly rebuild disagreed with materialised balance
  - alert: ReconBreakAge
    expr: max(recon_break_age_hours) > 24
    severity: page        # money unexplained for a day is an incident
  - alert: UnknownPaymentAge
    expr: max(payment_unknown_age_minutes) > 15
    severity: page        # a timeout we have not resolved either way
  - alert: IdempotencyKeyReuse
    expr: increase(idempotency_key_reuse_total[1h]) > 0
    severity: ticket      # always a merchant-side bug; tell them
  - alert: AuthTimeoutRate
    expr: rate(acquirer_timeout_total[5m]) / rate(acquirer_calls_total[5m]) > 0.05
    severity: page        # the acquirer is degrading; route to secondary
  - alert: FraudFalsePositiveRate
    expr: rate(fraud_challenge_converted_back_total[1h]) / rate(fraud_blocked_total[1h]) > 0.02
    severity: ticket      # we are blocking good customers
```

## 9. Numerical example

LedgerPay: 8 M payments/day, £54 average, 0.06 % chargebacks.

**Step 1 — where the money goes, and why infrastructure is not the fight.**

```
  volume            8 M × £54 = £432 M/day
  fee revenue       1.4 % = £6.05 M/day = £181 M/month
  infrastructure    £420,000/month = 0.23 % of revenue
  chargebacks       240 M payments/month × 0.06 % = 144,000 disputes
                    × (£54 loss + £15 fee) = £9.94 M/month = 5.5 % of revenue
  ⇒ THE LEDGER COSTS 0.23 % OF REVENUE AND DISPUTES COST 5.5 %. Every hour
    spent micro-optimising ledger throughput instead of fraud and dispute
    handling is an hour spent on the wrong 4 %.
```

**Step 2 — the idempotency arithmetic, in money.**

```
  21,400 client-timeout retries/day — 1.6 % of the ~1.3 M attempts that
  happen during the four busiest hours, 0.27 % of the whole day
  of those, 61 % had succeeded at the acquirer = 13,054
  without request-level idempotency: 13,054 second authorisations
      → the ones later captured become double charges: 1,400 in the incident
      → refund cost £151,000 + £40,000 of non-refundable network fees
  with idempotency at the acquirer: 13,054 no-ops, £0
  ⇒ THE MECHANISM COSTS: one unique index (free) + a 400 B row per payment
    for 24 h (3.2 GB, ≈ £4/month). It prevents £191,000 per incident.
```

**Step 3 — reconciliation tolerance, and why ±1 day matters.**

```
  daily file: 8,000,000 lines
  naive exact match, same settlement day only:
      breaks = 0.9 % + 0.9 % + 0.02 % = 1.82 % = 145,600 breaks/day
      at 90 seconds of human handling each = 3,640 operator-hours/day
      ⇒ 455 full-time staff. Absurd.
  with a ±1 settlement-day window:
      the two 0.9 % "timing" categories collapse to ~0.02 % genuine
      breaks = 1,600/day; auto-resolve 1,596 within tolerance
      ⇒ 4 escalated/day at 30 min each = 2 operator-hours/day
  ⇒ THE SETTLEMENT WINDOW IS THE DIFFERENCE BETWEEN 455 STAFF AND ONE PERSON.
```

**Step 4 — auth latency budget, decomposed.**

```
  p99 = 2.2 s end to end (payments with no step-up)
      acquirer call incl. network   1.90 s   86 %
      our work (claim, fraud, DB)   0.30 s   14 %
  and the 22 % of payments that get a 3-D Secure step-up: p99 3.9 s,
  of which 1.7 s is the challenge (the customer, a redirect and a bank page)
  ⇒ OUR CODE IS 14 % OF THE USER'S WAIT. Halving it saves 150 ms on a 2.2 s
    interaction — 7 %. The only levers that matter are acquirer routing and
    the step-up rate, which is why the router in §4.5 exists and why the fraud
    team's challenge threshold is a latency decision as much as a risk one.
```

**Step 5 — the payout reserve, and the cash-flow arithmetic.**

```
  payouts are T+2 with a 5 % rolling reserve held 90 days
  monthly volume £13.0 B (processed) ⇒ payouts ≈ £12.2 B/month
  reserve held at steady state ≈ 5 % × 3 months = £1.83 B
  why: chargebacks can arrive up to 120 days later. A 5 % / 90-day reserve
  covers a chargeback rate of up to 5 % with certainty; our 0.06 % is covered
  80× over. Smaller reserves are a competitive selling point, so the reserve
  is DETERMINED BY the measured dispute curve, not by policy taste:
      expected dispute exposure at day d = 0.06 % × volume × F(d)
      where F(90 d) = 0.94 of all disputes
      ⇒ reserve 5 % is ~80× the needed exposure — i.e. it is commercial, not
        prudential. Publishing that arithmetic to merchants won the two
        largest accounts in 2026.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **Double-entry ledger** | Audit, composability, no silent loss | More rows, more code | **Always** for money |
| **Single balance column** | Simple | Unauditable drift (§4.7) | Never |
| **Materialised balances + rebuild** | Fast reads, verifiable | A nightly job and an alarm | **Always** |
| **Atomic idempotency claim** | Exactly-once at the API | A unique index; 3.2 GB | **Always** |
| **Downstream idempotency keys** | Safe retries against the network | Acquirer must support them | **Always** — refuse acquirers that do not |
| **`unknown` as a state** | Timeouts resolve correctly | Reconciler must own it | **Always** |
| **Blind retry on timeout** | Looks simple | Double charges (§7) | Never |
| **Auth-then-capture** | Lower risk; delayed capture | Expiry management | Physical goods |
| **Auth-and-capture** | One call, simplest | Refund is the only undo | Digital goods |
| **Hosted fields / tokenisation** | SAQ-A instead of SAQ-D, £158k/yr | An iframe and a vault | **Always** |
| **Sync fraud scoring (40 ms)** | One decision, one call | Latency and a hard budget | **Always** |
| **Async fraud review** | No latency cost | You already shipped the goods | Never for authorisation |
| **Recon with ±1 day window** | 4 breaks/day instead of 145,600 (§9 step 3) | Explicit nuance | **Always** |
| **Webhook at-least-once + signatures** | Delivery and authenticity | Consumers must dedupe | **Always** |
| **Exactly-once webhooks** | Not achievable | — | Never claim it |

## 11. Failure scenarios

**11.1 — Double charge on retry** (§7). 1,400 merchants, £191,000. **Fix:** atomic key claim, downstream keys, `unknown` state.

**11.2 — Capture against an expired authorisation** (§7). £167,000 of receivables that never settled. **Fix:** `auth_expires_at` check plus a 24 h pre-expiry sweep.

**11.3 — Ledger balance drift.** £41,000 across 2,900 accounts, found by an auditor. **Fix:** append-only postings, single write path, nightly rebuild-and-compare with a page.

**11.4 — Acquirer outage at peak.** PSP A failed for 11 minutes at 380/s. The router moved traffic to PSP B after a 2 % error-rate trigger (30 s), losing 41 seconds of authorisations = 15,600 attempts, of which 62 % succeeded on retry. **Fix:** per-BIN routing, error-rate triggers, a warm secondary, and pre-negotiated limits on the secondary (a failover to a line with a 100/s cap is not a failover).

**11.5 — Webhook storm after a deploy.** A consumer outage caused 19.2 M deliveries to fail and retry; the retry schedule saturated the egress tier. **Fix:** per-merchant circuit breaking on webhook delivery, exponential backoff with jitter, and a DLQ after 72 h with a self-serve replay endpoint.

**11.6 — Duplicate settlement file ingested twice.** A retry after an SFTP timeout ingested 8 M lines twice; the matcher double-counted £432 M of expected settlement. **Fix:** the `UNIQUE (acquirer, settlement_date, acquirer_ref)` constraint and a file-level checksum (the constraint made the second ingest a no-op instead of a disaster).

**11.7 — A refund that was never delivered.** The refund API returned 200 and the ledger was correct, but the acquirer call timed out and the refund was not retried because it was not marked `unknown`. 3,100 customers waited 14 days. **Fix:** refunds use the same intent → call → outcome pattern as captures; no money-moving call may end without a durable outcome, including `unknown`.

**11.8 — FX rate recomputed on read.** A report recomputed historical rates, so the same payment showed two different GBP values in two reports and the ledger "did not balance" in a finance export. **Fix:** `fx_rate` is recorded on the payment row and never recomputed; every report reads the stored rate.

## 12. Alternatives

| Instead of | Consider | When |
|---|---|---|
| Building the ledger | A ledger-as-a-service (Modern Treasury, Formance) | You are not a payments company |
| Being a PSP | A payment orchestrator over PSPs (e.g. Primer-style) | You want routing without the licences |
| Card rails | Bank transfer / open banking / wallets | Fees matter and latency does not |
| Auth-then-capture | Auth-and-capture | Digital goods, low dispute risk |
| Synchronous fraud scoring | Rules + step-up challenge | You have no training data yet |
| Rolling reserve | Per-merchant risk-based reserves | You know your merchant risk distribution |
| Own vault | Provider vault with hosted fields | **Almost always** (PCI scope) |
| Kafka for the ledger | A relational DB with a unique index | It fits — 43 M rows/day is nothing |

## 13. When to use it

- **A double-entry ledger** for anything that moves money, credits, points, or inventory-like balances.
- **Idempotency keys with a downstream mechanism** for every money-moving call, including refunds and payouts.
- **An `unknown` state and an intent record** for every irreversible external call.
- **Daily reconciliation against the counterparty's file** wherever a counterparty exists.
- **Tokenisation and hosted fields** to keep PCI scope minimal.
- **A settlement window before declaring a break.**

## 14. When *not* to use it

- **Double-entry accounting for non-financial counters** — it is overkill for a like counter (L104's stock ledger is the lightweight cousin).
- **Idempotency keys for idempotent-by-construction operations** — a GET needs none.
- **A fraud ML model before you have labelled chargebacks** — rules first, model later.
- **Sub-second payout cycles** — settlement lag is the network's, not yours.
- **Blind retries of captures** — ever.
- **Exactly-once webhooks** — do not promise what the network cannot deliver.

## 15. Common mistakes

1. **An idempotency key used only for logging** (§7).
2. **Retrying a timed-out capture** (§4.6, §11.7).
3. **No `unknown` state** — treating a timeout as a failure.
4. **Computing balances by summing history at read time** — or, worse, storing a balance with no postings.
5. **Floating-point money.** Ever.
6. **No expiry check before capture** (§11.2).
7. **Reconciliation without a settlement window** (§9 step 3).
8. **Reconciliation as a finance task, not an engineering on-call** — breaks have owners and SLAs.
9. **Webhooks without signatures or dedupe guidance**.
10. **Recomputing FX rates on read** (§11.8).
11. **Failover to a secondary with no capacity** (§11.4).
12. **PCI scope left at SAQ-D because "it works"** (§4.9).
13. **Refunds treated as a different, simpler code path** than payments — they are the same machine mirrored.
14. **No capture of the acquirer's reference** — then reconciliation is impossible.
15. **Blocking instead of challenging** in the fraud middle band (§7).

## 16. Interview perspective

**What is actually being tested.** Whether you understand that the network is unreliable and the ledger is the only truth you own. Strong candidates talk about idempotency at the API and downstream, `unknown as a state`, double-entry postings, reconciliation with a settlement window, and the capture-before-refund cost asymmetry. Weak candidates draw a box labelled "payment service", say "use a transaction", and stop.

**Answers that land well:**

- *"How do you make payment retries safe?"* — "Three layers, and all three are needed. At our API, an atomic insert on the idempotency key: one row means we own the request, zero rows means we replay the stored response — or return a conflict if it is still in flight, or a loud error if the same key arrived with a different body, because that is a client bug. At the acquirer, we send a request-level idempotency key so that our retry is their no-op. And in between, a timeout is recorded as an explicit unknown state rather than a failure, with an intent row written before every irreversible call, so the reconciler can resolve it instead of us guessing. Without the downstream key, the first layer is documentation."
- *"Why a double-entry ledger?"* — "Because a single balance can be wrong silently, and a balanced ledger forces any error to appear twice. Every movement is postings that sum to zero, enforced in the same transaction, and nothing is ever updated or deleted — corrections are reversals. Balances are materialised for read speed and rebuilt from postings nightly, with an alert on any drift. That combination means an auditor cannot find a discrepancy that our monitoring has not already paged about."
- *"What happens when the acquirer's file disagrees with your ledger?"* — "That is the expected state, and the whole point of the daily run. Ninety-eight percent match exactly; about one point eight percent differ only by settlement-day timing, which is why the matcher waits a day before calling a break; the remaining four a day are genuine and get an owner and a twenty-four-hour SLA. In February that process found captures against expired authorisations nine days before anyone else would have — a hundred and sixty-seven thousand pounds of receivables that would never have settled."
- *"Where does the money go?"* — "Not to infrastructure. Four hundred and twenty thousand a month to run the thing against a hundred and eighty-one million a month of fee revenue, which is a fifth of a percent. Chargebacks at six basis points cost nine point nine million a month — twenty-four times the infrastructure. So the design effort goes to fraud scoring, dispute handling and reconciliation, and the ledger's job is to make sure none of that is invisible."

**Common interview tasks:** design a payment API; make retries safe; design the ledger; design reconciliation; handle a timeout; design refunds and partial captures; design the dispute flow; reduce PCI scope; handle multi-currency; place the payout reserve.

**Red flags interviewers watch for:**

- "Just retry" with no idempotency.
- Idempotency only at the API, not downstream.
- No `unknown` state.
- Money as a float.
- A balance with no ledger.
- No reconciliation, or reconciliation without a settlement window.
- Webhooks promised as exactly-once.
- No acquirer reference stored.
- Fraud discussed only as "block bad transactions" with no false-positive cost.
- No numbers: no volume, no decline rate, no dispute rate.

## 17. Knowledge check

**Conceptual**

1. Explain the three-layer idempotency design and why the downstream layer is the one that makes retries safe. Then explain what an `unknown` state buys and who owns it.
2. Explain double-entry postings for a £54 payment with a 1.4 % fee, then for its capture, refund and payout. Explain why balances are derived and how drift is detected.
3. Explain the capture-before-refund asymmetry, and why every irreversible external call needs an intent record.

**Practical scenario**

4. Design LedgerPay: 8 M payments/day, 420/s peak, 40,000 merchants, two acquirers, multi-currency, a 7-year audit requirement, T+2 payouts, and webhooks to merchants. Give requirements, capacity, API, schema (ledger, payments, outbox, recon), the state machine with `unknown`, the idempotency mechanism, the routing and failover policy, the reconciliation run with its tolerance rules, the payout and reserve model, the PCI scope decision, the alert set, and the incident replays. Then state what changes if you must support recurring subscriptions with off-session charges.

**Calculation**

5. Timeouts and duplicates. 21,400 client-side timeouts per day, 61 % of which succeeded at the acquirer. (a) How many second authorisations occur without downstream idempotency? (b) If 1 in 9 of those is later captured, how many double charges? (c) At £54 average and £15 per refund's non-refundable fee, compute the direct cost. (d) Compute the storage cost of a 24-hour idempotency window at 400 B per key and 8 M payments/day if storage is £0.021/GB-month.

6. Reconciliation. 8 M lines/day, 0.9 % in acquirer only, 0.9 % in ledger only, 0.02 % amount mismatch. (a) Compute total breaks with a same-day-only match. (b) Compute operator-hours at 90 s each. (c) Compute the same with a ±1 day window, assuming the two 0.9 % categories fall to 0.02 % combined. (d) State the staffing implication in one sentence.

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
  - [x] 12. Design e-commerce and inventory (L104)
  - [x] **13. Design payment processing (L105) ← you are here**
  - [ ] 14. Design a metrics and logging platform (L106)

**NEXT LESSON: Phase 5, Topic 14 — Design a metrics and logging platform**

---

---

# Lesson 106 — Design a Metrics and Logging Platform

*Phase 5, Topic 14*

## 1. Simple definition

**The problem.** Collect telemetry from 180,000 hosts and 9,000 services — **48 M metric series, 120 TB/day of raw logs, 4.5 M spans/s** — store it cheaply enough that it can be kept for months, and make it queryable in under two seconds **while the systems producing it are on fire**, because that is the only time anyone cares.

**Why it is the hardest of the fourteen designs.** Not because any single component is novel — it is a time-series database, an inverted index, and a columnar blob store, all of which you have met — but because of four constraints that apply simultaneously:

- **The write path is enormous and never stops.** 3.2 M metric samples/s and 3.3 M log lines/s, with no ability to ask writers to slow down, because the writers are the production systems.
- **The read path must be fast on data that is mostly cold.** 94 % of queries touch the last hour; the remaining 6 % touch months.
- **The platform must not be able to take down what it observes.** A monitoring system that consumes host CPU during an incident has caused more than one outage.
- **Every design shortcut becomes a bill.** Cardinality, log volume and trace sampling are *cost* decisions before they are technical ones, and they are made by the engineers writing `log.debug(...)` in a loop.

Terms defined before use:

- **Time series**: a metric name plus a label set, identified by a hash of the sorted label pairs.
- **Cardinality**: the number of distinct series. The single number that governs cost and stability.
- **Sample**: `(series_hash, timestamp, value)` — typically 16 bytes raw, ~2 bytes compressed.
- **Head block**: the in-memory, recently-written part of a TSDB; the part that dies under cardinality.
- **Downsampling / rollup**: pre-aggregating old raw samples into coarser ones (1 min, 1 h).
- **Ingest pipeline**: the agents, gateways, and sharders between producers and storage.
- **Tail-based sampling**: deciding whether to keep a trace *after* it completes, based on its outcome.
- **Label**: a key/value dimension on a series. Unbounded labels (`user_id`, `request_id`) are the cardinality bomb.
- **Retention tier**: hot (searchable), warm (queryable but slower), cold (object storage, restorable).
- **Exemplar**: a link from an aggregated metric to a specific trace.
- **Fail-open agent**: one that drops telemetry rather than blocking the application.

The one sentence to carry out of this lesson: **metrics are cheap per series and catastrophic per cardinality, logs are cheap per line and catastrophic per volume, and traces are cheap per span and catastrophic per sampling mistake — so the design is a set of budgets enforced at the source, a write path that sheds load before it breaks, and a read path that always knows whether it is reading raw samples or a rollup.**

## 2. Why it exists

**Because you cannot debug a distributed system with `grep` on 180,000 hosts.** Phase 4's observability lesson (L88) established the three pillars and the joins between them. This lesson is the platform: how those three streams are ingested, stored, indexed, tiered, queried, and paid for at a scale where naive choices cost millions.

**Because telemetry grows faster than traffic.** Requests grow linearly; telemetry grows with **requests × labels × services × deployments**. A new label that looks harmless — `pod_name`, then `git_sha`, then `user_tier`, then `request_id` — multiplies the series count. §7's cardinality incident added 41 M series in 20 minutes and took the platform down during a production incident.

**Because the bill is real and large.** £610,000/month at this scale, 71 % of it logs. The single most effective cost lever in the entire platform is **not** compression or hardware — it is an engineer deleting one `log.debug` line from a retry loop (§7). A platform that does not expose per-service telemetry cost will not get that line deleted.

**Because the platform is a dependency of the on-call engineer, and dependencies fail.** When the metrics platform is down, nobody can see why production is down. So the platform must be **more available than production**, must **fail open** at the agent, and must not share failure domains with what it monitors.

**And because alerting quality determines whether anyone looks at all.** 40,000 alerts/day with a 71 % ignore rate is worse than no alerting: it trains people to ignore the pager. §11.4.

## 3. Real-world analogy

**A hospital's monitoring system: bedside monitors, a nursing station, and an archive.**

The **bedside monitor** is the agent: cheap, always on, dumb, and it must never affect the patient. If it needs to raise an alarm, it does so locally — it does not wait for the nursing station to answer. That is a fail-open agent with local buffering.

The **nursing station** is the alerting and query layer: it shows the last few minutes at high resolution, because that is what decisions are made from. Nobody tries to render a year of heart rate at one-second resolution on the station's screen — that is the hot tier, and the resolution limit is a *design decision*, not a limitation.

The **archive** is cold storage: months of records, retrievable when a question is asked later, at a resolution of one minute. The archive is cheap because it is coarse and compressed, and it is *deliberately* not interchangeable with the live monitor.

The analogy's crucial detail: **the monitors are on a separate circuit from the lighting.** When the ward's power trips, the monitors stay on — that is failure-domain separation, and it is why the observability platform must not run in the same cluster, on the same network path, or on the same etcd as the thing it observes.

## 4. Detailed technical explanation

### 4.1 Requirements

**Functional.**

| # | Requirement |
|---|---|
| F1 | Metrics: scrape/push 48 M active series at 15 s resolution; query by label matcher and range |
| F2 | Logs: ingest, index and search 120 TB/day raw; full-text and structured filters |
| F3 | Traces: accept 4.5 M spans/s, retain a representative and complete-error subset; look up a trace by id |
| F4 | Alerting: evaluate 60,000 rules, route, dedupe, silence, and notify |
| F5 | Dashboards: 40,000 dashboards, 2.5 M panel loads/day |
| F6 | Retention: raw metrics 15 d, rollups 15 months; logs hot 7 d, warm 30 d, cold 12 months; traces 14 d |
| F7 | Multi-tenancy: per-team quotas, isolation, and cost attribution |
| F8 | Joins across pillars: a metric point → exemplar → trace → its logs |
| F9 | Ingest APIs must be compatible with OpenTelemetry |

**Non-functional.**

| # | Requirement | Number |
|---|---|---|
| N1 | Ingest durability | 99.99 % of accepted samples persisted; agents buffer 30 min on local disk |
| N2 | Query latency | p99 < 2 s for a 6-hour range; p99 < 8 s for a 30-day rollup query |
| N3 | Platform availability | 99.95 % (higher than the median service it monitors) |
| N4 | Alert evaluation lag | p99 < 30 s from breach to notification |
| N5 | Isolation | one tenant cannot degrade another (hard quotas, per-tenant shard groups) |
| N6 | Agent overhead | < 1 % host CPU, < 150 MB RSS, hard-capped |
| N7 | Cost attribution | every GB and every series attributable to a service within 1 hour |

### 4.2 Capacity estimation

```
  METRICS
      180,000 hosts / 9,000 services / 48 M active series
      15 s scrape ⇒ 48 M / 15 = 3,200,000 samples/s average
      each sample: 16 B in the head block, ~1.8 B compressed in a block
      daily: 3.2 M × 86,400 = 276 B samples = 497 GB compressed/day (raw tier)
      retention raw 15 d = 7.5 TB
      1-min rollup 90 d: 69 B samples = 12.4 TB
      1-h rollup 15 mo: 1.15 B samples/day × 450 = 518 B samples = 1.0 TB
      ⇒ TOTAL METRIC FLOOR ≈ 21 TB. Cheap. The cost is RAM and compute.
      head block RAM: ~3 KB per active series ⇒ 48 M × 3 KB = 144 GB
      ⇒ the head block spans ~48 shards of 3 GB RAM each (see §4.5)

  LOGS
      3,300,000 lines/s average, 4,600,000/s peak, 420 B/line
      ⇒ 1.39 GB/s average = 120 TB/day raw; 12:1 compression = 10 TB/day
      but the LOG VOLUME GROWTH is what matters: 2024 was 22 TB/day,
      2025 40 TB/day, 2026 120 TB/day (a 5.4× growth on a 1.9× traffic growth)
      ⇒ 120 TB/day raw = 10 TB/day compressed = 300 TB/month
      hot 7 d (70 TB) · warm 30 d (300 TB) · cold 12 mo (3.65 PB)
      index: inverted index over 4.2 B token postings/day ≈ 61 GB/day

  TRACES
      4.5 M spans/s generated across the fleet (350 TB/day raw)
      tail sampling: 100 % of errors + 2.2 % of the rest ⇒ 99,000 spans/s kept
      900 B/span ⇒ 89 MB/s = 8.7 TB/day incl. all error spans = 261 TB/month

  ALERTING
      60,000 rules; 50 % evaluated every 30 s, 50 % every 60 s
      ⇒ 1,500 rule evaluations/s; each is a range query, p50 40 ms
      ⇒ 60 query workers + a queue (rule evaluation is embarrassingly parallel)

  THE MONTHLY BILL
      logs     £434,000   71 %
      metrics  £ 86,000   14 %
      traces   £ 62,000   10 %
      query/alert/UI £28,000  5 %
      ─────────────────────────
      total    £610,000/month
```

### 4.3 API

```
# ── ingest (OpenTelemetry-compatible) ───────────────────────────────────
POST /v1/otlp/v1/metrics       # protobuf, gzip, batched, 15 s cadence
POST /v1/otlp/v1/logs          # batched, 1 MB per request max
POST /v1/otlp/v1/traces        # batched spans
  Headers: X-Tenant-Id, X-Scope-Org-Id, Authorization

# ── query ───────────────────────────────────────────────────────────────
POST /api/v1/query            { "query": "rate(http_requests_total{svc=\"api\"}[5m])",
                                "time": "…" }
POST /api/v1/query_range      { "query": "…", "start": "…", "end": "…", "step": "15s" }
  →  { "result": [ { "metric": {"svc":"api"}, "values": [[ts, "41.2"], …] } ],
       "meta": { "resolution": "raw|1m|1h", "series_scanned": 8120,
                 "samples_scanned": 1948800, "bytes_scanned": 3400000 } }
                                        # ← ALWAYS return the cost of the query

GET  /api/v1/series?match[]=…&start=&end=
GET  /api/v1/labels   /api/v1/label/{name}/values

POST /api/v1/logs/query       { "tenant": "…", "filters": [{"field":"level","eq":"error"}],
                                "text": "timeout", "start": "…", "end": "…", "limit": 100 }
  →  { "hits": [ { "ts": …, "labels": {…}, "line": "…", "trace_id": "…" } ],
       "meta": { "scanned_bytes": 1840000000, "shards": 41, "cache": "hit|miss" } }

GET  /api/v1/traces/{trace_id}          # all spans of a trace, cross-service
GET  /api/v1/traces?service=&op=&min_duration=&status=error

# ── alerting ────────────────────────────────────────────────────────────
POST /api/v1/rules            { "expr": "…", "for": "5m", "labels": {…}, "annotations": {…} }
POST /api/v1/silences         { "matchers": […], "starts_at": …, "ends_at": …, "reason": "…" }
GET  /api/v1/alerts
POST /internal/tenants/{id}/quota  { "series_max": …, "log_gb_per_day": …, "span_rate_max": … }
GET  /internal/cost?group_by=service&window=30d
```

### 4.4 Data model

```sql
-- ── METRICS: series dictionary + inverted index + time blocks ───────────
CREATE TABLE metric_series (               -- the dictionary, one row per series
  series_hash  BYTEA PRIMARY KEY,          -- sha256 of sorted label pairs
  tenant_id    BIGINT NOT NULL,
  metric_name  TEXT NOT NULL,
  labels       JSONB NOT NULL,             -- {"svc":"api","code":"500"}
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen    TIMESTAMPTZ NOT NULL
);
CREATE INDEX series_by_metric ON metric_series (tenant_id, metric_name);
-- inverted index: (label_name, label_value) → roaring bitmap of series_hash
CREATE TABLE label_postings (
  label_name  TEXT NOT NULL,
  label_value TEXT NOT NULL,
  series_bits BYTEA NOT NULL,              -- compressed roaring bitmap
  PRIMARY KEY (label_name, label_value)
);

-- Time-series data itself is NOT in SQL: it lives in immutable blocks.
--   block = { series_hash[], timestamps[], values[] } columnar, zstd
--   path  = s3://tsdb/{tenant}/{shard}/{window_start}/block-{seq}.zst
--   index = a per-block sparse index: every 128 samples, one (t, series offset)
-- ⇒ a 6-hour query touches ~24 blocks per shard, not 86,400 rows.

-- ── LOGS: columnar segments, and the index is OPTIONAL per stream ───────
CREATE TABLE log_streams (
  id            BIGINT PRIMARY KEY,
  tenant_id     BIGINT NOT NULL,
  fingerprint   BYTEA NOT NULL,            -- hash of the label set (svc, pod, level)
  labels        JSONB NOT NULL,
  retain_days   SMALLINT NOT NULL DEFAULT 30,
  UNIQUE (tenant_id, fingerprint)
);
-- segment: 1–4 M lines, columnar (ts, stream_id, flags, message bytes),
-- written once, sealed at 256 MB, then immutable in object storage.
-- Full-text index: per-segment token dictionary → posting lists (roaring)
-- with a bloom filter per token for "is this token here at all?".

-- ── TRACES: spans in a columnar store keyed by trace_id ────────────────
CREATE TABLE trace_index (
  trace_id     BYTEA PRIMARY KEY,
  tenant_id    BIGINT NOT NULL,
  root_service TEXT NOT NULL,
  started_at   TIMESTAMPTZ NOT NULL,
  duration_ms  INT NOT NULL,
  span_count   SMALLINT NOT NULL,
  status       SMALLINT NOT NULL,          -- 0 unset|1 ok|2 error
  kept_reason  SMALLINT NOT NULL           -- 0 error|1 slow|2 sampled|3 pinned
);
-- span payloads go to columnar object storage, partitioned by started_at hour
-- and sharded by hash(trace_id); the index above is the only B-tree.

-- ── ALERTING: rules, state, and notifications ──────────────────────────
CREATE TABLE alert_rules (
  id           BIGINT PRIMARY KEY,
  tenant_id    BIGINT NOT NULL,
  expr         TEXT NOT NULL,
  for_seconds  INT NOT NULL DEFAULT 300,
  severity     SMALLINT NOT NULL,          -- page|ticket|info
  labels       JSONB NOT NULL,
  eval_interval_s SMALLINT NOT NULL DEFAULT 30,
  enabled      BOOLEAN NOT NULL DEFAULT true
);
CREATE TABLE alert_instances (
  rule_id      BIGINT NOT NULL,
  fingerprint  BYTEA NOT NULL,             -- hash of the resolved label set
  state        SMALLINT NOT NULL,          -- 0 inactive|1 pending|2 firing|3 resolved
  active_since TIMESTAMPTZ,
  last_notified_at TIMESTAMPTZ,
  PRIMARY KEY (rule_id, fingerprint)        -- dedupe is a primary key, not a job
);

-- ── COST ATTRIBUTION: the table that changes behaviour ─────────────────
CREATE TABLE tenant_usage_hourly (
  tenant_id      BIGINT NOT NULL,
  hour           TIMESTAMPTZ NOT NULL,
  series_written BIGINT NOT NULL,
  log_gb         NUMERIC(12,3) NOT NULL,
  span_count     BIGINT NOT NULL,
  scanned_bytes  BIGINT NOT NULL,
  PRIMARY KEY (tenant_id, hour)
);
```

### 4.5 The metric write path: shard by series hash, and cap the head

```
  FLOW
    agent (per host) → gateway (stateless, auth, quota, relabel, drop) →
    sharder (hash series_hash → shard) → head block (in-memory, per shard) →
    WAL (for durability) → sealed block → object storage
  SHARDING
    48 M series / 1 M series per shard = 48 shards
    each head block: 1 M series × 3 KB = 3 GB RAM ⇒ a 16 GB node with room
    ⇒ SHARD BY SERIES HASH, NOT BY TIME. Time-sharding means every query
      touches every shard; hash-sharding means a query fans out to only the
      shards owning the matching series (typically 4–12 of 48).
  DURABILITY
    every append goes to a per-shard WAL before the ack; blocks are written to
    object storage every 2 hours and the WAL is truncated after. A lost shard
    loses at most 2 hours of one shard's series, and replicas cover that.
  THE CARDINALITY GUARD (the most important code in the platform)
    before a new series is created:
      · per-tenant series limit (hard): 6 M
      · per-(tenant, metric_name) limit: 200,000
      · per-series label names that are FORBIDDEN at the gateway:
        request_id, trace_id, user_id, session_id, url_path_with_id, pod_uid
        (allow-listed: svc, env, region, code, route, method, version)
      · if a limit is hit: DROP the series, increment a counter, and emit ONE
        alert to the tenant — never block the write, never OOM the shard
    ⇒ measured: the guard turns a 20-minute platform outage into one ticket.
```

### 4.6 The metric read path: rollups, and telling the truth about resolution

```
  A query picks its tier by RANGE and STEP:
      ≤ 6 h and step ≥ 15 s   → raw blocks (last 15 days)
      ≤ 90 d  and step ≥ 60 s → 1-min rollups
      > 90 d  and step ≥ 1 h  → 1-h rollups
  ⇒ A QUERY THAT ASKS FOR 90 DAYS AT 15-SECOND RESOLUTION IS REJECTED, with
    a message: "that is 7.8 M samples per series; rerun at 1-minute steps,
    or narrow the range." Rejecting is a FEATURE: the alternative is one query
    scanning 2 TB and OOMing a query node for everyone.
  THE ROLLUP IS NOT A LOSSY AVERAGE
    each rollup point stores (min, max, sum, count) so that any aggregate —
    rate, average, percentile approximation via histograms — is computable
      ⇒ storing only the mean makes `p99 over 90 days` impossible, and
        somebody will ask for it. Store the four-tuple.
  QUERY GUARDS
      max samples per series: 11,000  (anything coarser than 1 point per 15 s
                                       over 2 hours exceeds it — intentional)
      max series per query:   50,000
      max wall time:          30 s (then partial results + a warning)
      max bytes scanned:      8 GB
  MEASURED: median query 340 ms; p99 1.9 s (6 h); cache hit rate 91 % for
  dashboard queries because 2.5 M panel loads/day touch only 41,000 distinct
  (query, step, hour) tuples ⇒ cache the RESULT, keyed by query+step+window.
```

### 4.7 Logs: index the fields, not the words, until you must

```
  THE COST STRUCTURE OF A LOG PLATFORM
      storing bytes is cheap (£0.004/GB/month cold)
      INDEXING bytes is expensive (£1.05/GB for indexed hot + query compute)
      ⇒ every design decision is "what do we index, and for how long?"
  THE CHOSEN DESIGN
      · structured logs only in the ingest path: level, svc, trace_id, plus a
        JSON body. Fields are indexed; free text is tokenised lazily.
      · per-stream index policy: `error` and `warn` streams are indexed in
        full (they are 3.1 % of volume); `info` is indexed by field only;
        `debug` is SAMPLED 1-in-100 AT THE AGENT and indexed by field only
      · the whole message is always searchable, but via a bloom filter +
        segment scan, which is 40× slower and 12× cheaper than a full
        inverted index (§9 step 3)
      · retention tiers: hot 7 d (SSD, full index), warm 30 d (HDD, field
        index only), cold 12 mo (object storage, restore-to-query in 90 s)
  THE BUDGET ENFORCEMENT
      every stream has a daily GB budget; at 80 % the agent samples
      progressively (head 100 %, tail dropped first: debug → info → warn);
      at 100 % it samples hard and emits ONE alert. It never blocks the app
      and never silently stops — silent stopping is how you lose the incident
      logs for the incident.
  MEASURED: logs went from 40 TB/day (2025) to 120 TB/day (2026) with only a
  1.9× traffic increase. The cause was three services logging at debug in
  production and one new "audit" stream that logged full request bodies
  (§7). After sampling and one schéma change: 46 TB/day at the same traffic.
```

### 4.8 Traces: tail-based sampling, because head sampling loses the errors

```
  HEAD SAMPLING (decide at the root, before the outcome is known)
      1 % of all traces ⇒ you keep 1 % of the ERRORS too, and errors are the
      thing you wanted. At a 0.3 % error rate and 4.5 M spans/s, head
      sampling at 1 % gives you ~140 error traces/minute across the fleet —
      and ZERO for a service handling 40 requests/s.
  TAIL SAMPLING (decide after the trace completes, at the collector)
      keep 100 % of traces with status=error
      keep 100 % of traces slower than the p99.5 duration for their endpoint
      keep 2.2 % of the rest uniformly (a representative baseline)
      ⇒ 99,000 spans/s retained out of 4.5 M/s = 2.2 %, and EVERY error is
        present for EVERY endpoint. This is the whole reason to sample late.
  THE COST OF TAIL SAMPLING
      all spans must be buffered at the collector until the trace completes:
      buffer = 4.5 M spans/s × 8 s (p99.9 trace duration) × 900 B = 32 GB of
      buffer across the collector fleet, with a 3-replica pipeline because the
      decision needs the whole trace
      ⇒ viable, and cheaper than storing everything (231 TB/month vs 10.5 PB)
  TRACE LOOKUP BY ID
      traces are written sharded by hash(trace_id) into 8-hour partitions with
      a 14-day index; a lookup touches exactly one shard
      ⇒ p99 210 ms for a trace by id, which is why exemplars work: a spike on
        a dashboard can link straight to a specific trace (L88).
```

### 4.9 Alerting: dedupe as a primary key, and evaluation as a fan-out

```
  RULE EVALUATION
    60,000 rules, half every 30 s ⇒ 1,500 evaluations/s
    each evaluation is a range query over the last ~10 minutes
    ⇒ sharded by rule id across 60 workers; the query cache absorbs most of it
  DEDUPE AND STATE
    an alert instance is keyed by (rule_id, fingerprint(labels)) in a PRIMARY
    KEY: a rule that fires for 4,000 pods produces 4,000 instances, and the
    routing layer groups them by `svc` before notification
    ⇒ measured: 600,000 raw instances/day → 1,900 routed notifications/day,
      a 315× reduction, and it is the difference between an alerting system
      and a denial-of-service attack on the on-call engineer
  GROUPING AND SILENCE
    group by alertname+svc, wait 30 s to collect a burst, THEN notify
    silences are first-class rows with an owner and an expiry; a silence
    without an expiry is rejected by the API
  THE QUALITY METRIC
    precision = alerts that led to an action / alerts fired
    v1 precision: 29 % (71 % of pages led to no action) ⇒ alert fatigue
    v2 (after rewriting to symptom-based SLO burn-rate alerts, L89): 84 %
    ⇒ page volume fell 96 % (40,000/day → 1,600/day) at unchanged incident
      detection time. That is the single most valuable change in the platform.
```

## 5. Request / data flow

```
 METRICS (3.2 M samples/s)
 ─────────────────────────────────────────────────────────────────────────
 host agent (cap 150 MB RSS, 1 % CPU)
   → gateway: auth, relabel, CARDINALITY GUARD, quota
   → sharder: hash(series_hash) % 48
   → head block (append + WAL) → ack
   every 2 h: seal block → object storage → truncate WAL
 ⇒ ack latency p99 40 ms; the agent batches 15 s of samples per request

 LOGS (3.3 M lines/s average, 4.6 M/s peak)
 ─────────────────────────────────────────────────────────────────────────
 agent (per-stream budget, sampling at 80 %)
   → gateway (parse fields, drop forbidden, per-tenant quota)
   → partitioner: hash(tenant, stream) → 96 partitions
   → ingester: build a 256 MB segment (~1.4 M lines)
        + inverted index for indexed streams
        + bloom filter per token for all streams
   → seal → object storage → index published to the metadata service
 ⇒ visible to query in p99 4.5 s (near real-time enough for an incident)

 TRACES (4.5 M spans/s → 99 k/s kept)
 ─────────────────────────────────────────────────────────────────────────
 agent (head sample 100 % errors / 20 % baseline to the collector)
   → collector: buffer the whole trace (8 s), apply TAIL POLICY, keep or drop
   → writer: shard by trace_id, 8-hour partitions, columnar
   → trace_index row per retained trace

 ALERT PATH (< 30 s breach → notification)
 ─────────────────────────────────────────────────────────────────────────
 rule worker (every 30 s) → query cache/TSDB → instance state machine
   → routing (group by svc, 30 s window) → dedupe by primary key
   → notification (pager/ticket) → silence check → deliver
```

## 6. ASCII diagram

```
  PRODUCERS (180k hosts, 9k services)
      │  metrics         │ logs            │ traces
      ▼                  ▼                 ▼
  ┌────────────┐   ┌────────────┐   ┌────────────┐
  │ AGENT      │   │ AGENT      │   │ AGENT      │  fail-open, hard caps,
  │ disk buffer│   │ 1-in-100   │   │ head 20 %  │  local ring buffer
  └─────┬──────┘   └─────┬──────┘   └─────┬──────┘
        └────────┬───────┴────────────────┘
                 ▼
        ┌─────────────────────────────────┐
        │ GATEWAY (stateless)             │  auth · relabel ·
        │ CARDINALITY GUARD · quotas      │  LOAD SHEDDING
        └───┬─────────────┬───────────┬───┘
            │             │           │
     ┌──────▼─────┐ ┌─────▼──────┐ ┌──▼────────────┐
     │ SHARDER    │ │ PARTITION  │ │ COLLECTOR     │
     │ hash(series)│ │ hash(tenant│ │ buffer whole  │
     │ → 48 shards│ │ ,stream)→96│ │ trace, TAIL   │
     └──────┬─────┘ └─────┬──────┘ │ SAMPLE        │
            ▼             ▼        └──┬────────────┘
   ┌────────────────┐ ┌────────────────┐ │
   │ HEAD BLOCK     │ │ INGESTER       │ ▼
   │ 3 GB RAM/shard │ │ 256 MB segment │ ┌─────────────┐
   │ + WAL          │ │ + inverted idx │ │ TRACE STORE │
   └───────┬────────┘ └───────┬────────┘ │ by trace_id │
           ▼ (2 h)            ▼ (seal)   └──────┬──────┘
   ┌────────────────┐ ┌────────────────┐       │
   │ BLOCK STORE    │ │ SEGMENT STORE  │       │
   │ raw/1m/1h      │ │ hot→warm→cold  │       │
   └───────┬────────┘ └───────┬────────┘       │
           └──────────┬───────┴────────────────┘
                      ▼
          ┌───────────────────────────┐      ┌──────────────────────┐
          │ QUERY (guards, cache,     │◄─────┤ ALERT EVALUATOR      │
          │ tier routing)             │      │ 60k rules, 30 s      │
          └───────────┬───────────────┘      └──────────┬───────────┘
                      ▼                                 ▼
              DASHBOARDS / UI                     ROUTER → dedupe →
                                                  group → pager/ticket

  FAILURE-DOMAIN RULE: this whole diagram runs in a SEPARATE cluster, network
  path and control plane from the systems it observes. When production is
  down, the only thing that must still work is the top-right arrow.
```

## 7. Detailed example

**TelemetryCo platform**: 180,000 hosts, 9,000 services, 3.2 M samples/s, 120 TB/day of logs.

```
  measured:
      48 M active series · 3.3 M log lines/s average, 4.6 M/s peak
      4.5 M spans/s generated, 99 k/s retained
      median query 340 ms, p99 1.9 s; alert lag p99 26 s
      bill £610,000/month: logs 71 %, metrics 14 %, traces 10 %
```

**The cardinality bomb (20 minutes blind, at the worst moment):**

```
  2026-01-19, 02:40 UTC, during a production incident:
      a service deployed an HTTP metric with a `request_id` label
      4,200 requests/s × 20 minutes = 5.0 M new series... in ONE metric, and
      the series never expire because Prometheus-style series live until 5
      minutes of inactivity — request ids never repeat, so the head block
      grew at 4,200 series/s and every one was allocated
      at 124 s: shard 17's head block hit 41 M series = 123 GB of RAM on a
      16 GB node ⇒ OOM
      consequence: the METRICS PLATFORM went down for 19 minutes DURING a
      customer-facing incident. The on-call engineer lost both the pager
      detail and the dashboards.
  cost: not measured in pounds, measured in MTTR — the incident took 71
  minutes instead of the usual 12.
  fixes, in order:
      1. the cardinality guard (§4.5): forbidden label names at the gateway,
         per-metric and per-tenant series limits — shipped in 4 days
      2. series-id allocation moved behind a dictionary with a cap, so an
         unknown series beyond the cap is dropped and counted, never
         allocated
      3. a CI check that fails a build if a metric definition contains a
         high-cardinality label template
      4. per-tenant shard groups, so one tenant's cardinality cannot touch
         another's head block
  ⇒ AND THE GENERAL LESSON: in a TSDB, cardinality is a MEMORY problem first
    and a money problem second. 41 M series cost £61/month in storage and
    123 GB of RAM in the only place there is no room.
```

**The debug-log bill (the £148,000 line):**

```
  2025-09: the bill rose £186,000/month in one week with no traffic change.
      cause: a retry loop logged `log.debug` for each attempt, and the retry
      storm of a flaky downstream (L75) multiplied it: one service went from
      0.3 TB/day to 4.9 TB/day compressed
      the debug lines were 97 % of the service's log volume and had never
      been read by anyone — the service's `error` lines were 0.2 %
  fixes:
      1. debug sampled 1-in-100 at the agent (keeps distribution, kills cost)
      2. a per-stream daily GB budget with progressive tail sampling
      3. the `tenant_usage_hourly` table exposed per-service cost in the
         weekly engineering review — the team that owned the service deleted
         the line themselves within 48 hours
  ⇒ logs fell from 120 TB/day to 46 TB/day; the bill fell £211,000/month.
    ENFORCEMENT DID NOTHING; VISIBILITY DID EVERYTHING (§9 step 2).
```

**The alerting rewrite (page volume −96 %, precision 29 % → 84 %):**

```
  v1: 40,000 alerts/day, 71 % ignored, precision 29 %
      every one of the 60,000 rules had been written by somebody during an
      incident, and nobody had deleted one since
  v2: symptom-based alerts on SLO burn rate (L89), rules tied to a user-
      visible symptom, and a "delete a rule" review every quarter
      1,600 pages/day, precision 84 %, mean time to acknowledge 4.1 min → 51 s
      ⇒ fewer pages, faster response, and — the point — engineers started
        trusting the pager again.
```

## 8. Code or configuration example

```python
# ── The cardinality guard: the most important function in the platform ──
FORBIDDEN = {"request_id", "trace_id", "span_id", "user_id", "session_id",
             "pod_uid", "email", "url", "order_id"}
PER_METRIC_MAX, PER_TENANT_MAX = 200_000, 6_000_000

async def admit_series(tenant_id, metric_name, labels) -> bool:
    for k in labels:
        if k in FORBIDDEN:
            metrics.inc("series_rejected_forbidden_label", tags={"label": k})
            audit_reject(tenant_id, metric_name, k)     # ONE alert to the team
            return False
    h = series_hash(metric_name, labels)
    if await cache.exists(h):
        return True
    async with tenant_series_lock(tenant_id):          # per-tenant, not global
        n_metric = await cache.count(tenant_id, metric_name)
        n_tenant = await cache.count(tenant_id)
        if n_metric > PER_METRIC_MAX or n_tenant > PER_TENANT_MAX:
            metrics.inc("series_rejected_over_cap", tags={"tenant": tenant_id})
            await alert_once(tenant_id, "series cap reached for " +
                             metric_name)              # never per-request spam
            return False
        await cache.add(h, tenant_id, metric_name)     # dictionary insert
    return True
    # NOTE: a rejection DROPS the sample and continues. The platform must never
    # propagate a cost problem into the application's write path.

# ── Tail-based trace sampling: keep every error, sample the rest ────────
class TailPolicy:
    BASELINE = 0.022
    def keep(self, trace) -> tuple[bool, str]:
        if trace.status == "error":
            return True, "error"                     # 100 % of errors
        if trace.duration_ms >= SLOW_P99_5[trace.endpoint]:
            return True, "slow"                      # every outlier
        if trace.pinned or trace.has_exemplar:
            return True, "pinned"
        return (hash64(trace.trace_id) < int(2**64 * self.BASELINE)), "sampled"

# ── Log sampling at the agent: keep the head, drop the tail ─────────────
def should_emit(line, stream) -> bool:
    if line.level in ("error", "warn"):
        return True                                   # never sample these
    pct = stream.daily_budget_used_pct
    if pct >= 100: return line.level == "error"
    if pct >= 80:  return line.level != "debug" and random() < 0.25
    if line.level == "debug":
        return random() < 0.01                        # 1-in-100 at source
    return True
```

```yaml
# ── Platform rails ──────────────────────────────────────────────────────
ingest:
  agent: { cpu_cap_pct: 1.0, rss_cap_mb: 150, disk_buffer_minutes: 30,
           fail_mode: drop-oldest }                  # never block the app
  gateway: { max_request_mb: 1, timeouts_ms: 500, shed_at_queue_depth: 20000,
             shed_strategy: drop-debug-then-info }   # shed, never queue forever
metrics:
  scrape_interval_s: 15
  head_shard_series_max: 1_000_000
  head_shard_ram_gb: 3
  shards: 48
  retention: { raw_days: 15, rollup_1m_days: 90, rollup_1h_days: 450 }
  rollup_fields: [min, max, sum, count]              # never a bare mean
  query_guards: { max_samples_per_series: 11000, max_series: 50000,
                  max_wall_seconds: 30, max_bytes_scanned_gb: 8 }
logs:
  index_policy: { error: full, warn: full, info: fields, debug: fields+sampled }
  retention_days: { hot: 7, warm: 30, cold: 365 }
  stream_daily_budget_gb: 400
traces:
  sampling: tail
  keep: { errors: 1.0, slow_over_p99_5: 1.0, baseline: 0.022 }
  retention_days: 14
alerting:
  group_wait_s: 30
  group_interval_s: 300
  repeat_interval_h: 4
  silence_requires_expiry: true
alerts:
  - alert: PlatformIngestLag
    expr: histogram_quantile(0.99, rate(ingest_ack_seconds_bucket[5m])) > 2
    severity: page          # we are the dependency of the on-call engineer
  - alert: TenantSeriesCapReached
    expr: increase(series_rejected_over_cap_total[10m]) > 0
    severity: ticket        # always a real engineering change upstream
  - alert: ForbiddenLabelRejected
    expr: increase(series_rejected_forbidden_label_total[10m]) > 0
    severity: ticket        # a deploy shipped request_id into a metric
  - alert: LogBudgetExhausted
    expr: stream_daily_budget_used_pct > 100
    severity: ticket        # sampling is already protecting the bill
  - alert: QueryOOMRisk
    expr: query_bytes_scanned_gb > 6
    severity: info          # surface the cost BEFORE the rejection
  - alert: AlertPrecisionLow
    expr: alerts_actioned_ratio_7d < 0.5
    severity: ticket        # the platform is training people to ignore it
```

## 9. Numerical example

TelemetryCo: 48 M series, 120 TB/day raw logs, 4.5 M spans/s.

**Step 1 — why the metric bill is small and the cardinality risk is not.**

```
  storage: 48 M series × 15 s for 15 days, ~1.8 B/sample compressed
      = 3.2 M samples/s × 86,400 × 15 × 1.8 B = 7.5 TB
      at £0.09/GB-month (hot SSD) = £675/month   ← trivial
  but the HEAD BLOCK must hold every active series in RAM at ~3 KB:
      48 M × 3 KB = 144 GB of RAM = 48 shards × 3 GB
      ⇒ a 16 GB node holds 5 M series with room for the WAL and the index.
  ⇒ THE METRIC BILL IS DOMINATED BY RAM, NOT DISK: £86,000/month is 78 %
    compute and memory. And the failure mode of exceeding the RAM is an OOM,
    not a bill — which is why §7's incident was an outage, not a surprise
    invoice.
```

**Step 2 — the debug-log arithmetic, in £/month.**

```
  baseline logs 120 TB/day raw = 10 TB/day compressed = 300 TB/month
  indexed-hot price £1.05/GB ⇒ 300,000 GB × £1.05 = £315,000/month
  plus query compute £60,000 and cold retention £59,000 = £434,000/month
  THE BAD SERVICE: 4.9 TB/day compressed instead of 0.3 TB/day
      extra 4.6 TB/day = 138 TB/month × £1.05 = £145,000/month
  SAMPLING DEBUG 1-in-100 AT THE AGENT: −97 % of that stream's volume
      ⇒ −4.46 TB/day = −£140,000/month, at zero loss of error visibility
  ⇒ A ONE-LINE LOG STATEMENT WAS 23 % OF THE ENTIRE PLATFORM BILL. This is
    why `tenant_usage_hourly` exists and why it is reviewed in public.
```

**Step 3 — index versus scan, and why the index policy looks odd.**

```
  1 TB of logs, one token ("timeout"):
      full inverted index:    index build cost £1.05/GB... the index itself
                              adds ~18 % volume, and a query touches
                              ~400 MB of postings ⇒ 90 ms
      bloom filter + scan:    no index cost; 1 TB at 1.4 GB/s per query node
                              with 12 nodes = 60 s for a cold scan
      ⇒ INDEXING IS 660× FASTER AND ~5× MORE EXPENSIVE OVER THE RETENTION.
        Answer: index the 3.1 % of volume that is error/warn for the 7-day hot
        tier; for the older 93 % of volume, scan with bloom filters.
  the arithmetic on 120 TB/day over 30 days:
      index everything: (3,600 TB × 0.18 index overhead × £1.05/GB)/1000
                        ≈ £680,000/month → not affordable
      index 3.1 %:      ≈ £21,000/month of index + scan for the rest
  ⇒ "INDEX THE ERRORS AND SCAN THE REST" is a 32× cost reduction with a
    60-second worst case on cold text search, which is the right trade for a
    query that is asked once a week.
```

**Step 4 — tail sampling versus head sampling, in errors retained.**

```
  4.5 M spans/s, 0.3 % of traces are errors
      error traces/s ≈ 4.5 M / 11 spans per trace × 0.003 = 1,227 error
      traces/s = 106 M/day
  HEAD SAMPLING at 1 %:
      keeps 1 % of traces ⇒ 10,600 error traces/day fleet-wide
      for a service at 40 requests/s with a 0.3 % error rate:
        40 × 86,400 × 0.003 = 10,368 errors/day × 1 % = 104 error traces/day
        ⇒ and at 1 % of THOSE being sampled... none are missing, but a
          0.01 error-rate service gets 3/day, i.e. effectively none.
  TAIL SAMPLING:
      keeps 100 % of errors ⇒ 106 M error traces/day
      cost: 106 M error traces × 11 spans × 900 B = 1.05 TB/day of error spans
      plus 2.2 % baseline of the rest ≈ 7.65 TB/day
      total 8.7 TB/day = 261 TB/month × £0.23/GB = £60,000/month
  versus keeping everything: 4.5 M spans/s × 900 B = 350 TB/day
      = 10.5 PB/month ⇒ £2.4 M/month. NOT A VIABLE PRODUCT.
  ⇒ TAIL SAMPLING BUYS COMPLETE ERROR VISIBILITY AT 2.2 % OF STORAGE. The
    32 GB of collector buffer that makes it possible costs £2,100/month.
```

**Step 5 — dedupe, and the alert-volume arithmetic.**

```
  60,000 rules; during a regional failure, 4,000 pods fail simultaneously
      raw instances: 40 rules × 4,000 pods = 160,000 instances in 2 minutes
      without grouping: 160,000 notifications = the pager is useless and
      the on-call engineer mutes the channel (which is the real outage)
  with (rule_id, fingerprint) as a primary key and group_by=svc, 30 s wait:
      160,000 raw ⇒ 41 notifications
  over a full day: 600,000 raw instances ⇒ 1,900 routed notifications
      a 315× reduction, and the reduction is a SCHEMA DECISION.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **Hash-shard series** | Queries touch 4–12 of 48 shards | Resharding moves series | **Always** |
| **Time-shard series** | Simple retention | Every query hits every shard | Never at this scale |
| **Cardinality guard that drops** | Platform survives (§7) | Some telemetry silently missing | **Always** — count it |
| **Blocking writes on cardinality** | No data loss | Takes down the app | Never |
| **Rollups with (min,max,sum,count)** | Any aggregate over months | 4× the storage of a mean | **Always** |
| **Rollup of means only** | Small | p99 over 90 d is impossible | Never |
| **Query guards that reject** | One query cannot OOM a fleet | Users must learn step sizes | **Always** |
| **Index errors, scan the rest** | 32× cheaper (§9 step 3) | 60 s cold text search | **Always** |
| **Index everything** | 90 ms any query | £680k/month at this scale | Small tenants only |
| **Tail sampling** | All errors, 2.2 % of storage | 32 GB buffer, 3 replicas | **Always** when errors matter |
| **Head sampling** | Simplest pipeline | Misses the rare errors | Very high volume, uniform |
| **Dedupe by primary key** | 315× fewer pages | Grouping hides instance detail | **Always** |
| **One alert per instance** | Complete detail | The pager becomes noise | Never |
| **Fail-open agent** | App never blocked | Telemetry gaps | **Always** |
| **Fail-closed agent** | No gaps | The observability platform causes outages | Never |
| **Separate failure domain** | Survives production outages | Money and complexity | **Always** |
| **Cold tier in object storage** | 12 months for £14,600/mo | 90 s restore | Older than 30 days |

## 11. Failure scenarios

**11.1 — Cardinality bomb** (§7). 41 M series, 123 GB → OOM, 19 minutes blind during an incident. **Fix:** gateway label guard, per-metric and per-tenant caps, series dictionaries with a hard ceiling, CI check, per-tenant shard groups.

**11.2 — Debug log in a retry loop** (§7). £145,000/month of one line. **Fix:** 1-in-100 sampling at the agent, per-stream budgets, and public per-service cost reporting.

**11.3 — The monitoring agent causing the outage.** During a CPU-saturation incident, the agent's own flush used 40 % of a core per host on 12,000 hosts, deepening the saturation. **Fix:** cgroup-capped agents (1 % CPU, 150 MB RSS), local ring buffers, and drop-oldest under pressure — the agent must be the most boring process on the host.

**11.4 — Alert fatigue** (§7). 40,000 alerts/day, 71 % ignored, precision 29 %. **Fix:** symptom-based SLO burn alerts, dedupe as a primary key, quarterly rule deletion, and precision as a tracked platform SLI.

**11.5 — A hot tenant starving others.** One team's logs were 31 % of ingest; their backfill saturated the partitioners and every tenant's logs arrived 40 minutes late. **Fix:** per-tenant partition groups with independent ingest capacity, plus per-tenant quotas that shed rather than queue.

**11.6 — Query-induced OOM.** A dashboard asked for 90 days at 15 s across 40,000 series: 2.1 TB scanned, the query node OOMed, and 340 concurrent queries failed with it. **Fix:** pre-flight estimation from block metadata (reject before scanning), per-tenant query concurrency limits, and a bulkhead between interactive and dashboard queries.

**11.7 — Clock skew breaking trace assembly.** Span timestamps from hosts with 4 s of clock skew (L66) produced traces whose children appeared to start before their parents; the UI rendered impossible waterfalls. **Fix:** order spans by a logical parent/child structure rather than timestamps, correct duration with a monotonic clock, and alert on fleet clock skew.

**11.8 — The silence that outlived the change.** A silencing rule from a 2025 migration silenced `DiskFull` for 14 months; a disk filled and the alert did not fire. **Fix:** silences require an expiry and an owner; the API rejects anything longer than 30 days.

## 12. Alternatives

| Instead of | Consider | When |
|---|---|---|
| Building all three pillars | A vendor for logs, self-hosted metrics | Logs are the usual build/buy line |
| A custom TSDB | Prometheus/Mimir/VictoriaMetrics-style OSS | You are not a database company |
| Custom log search | ClickHouse/OpenSearch/Loki-style | Almost always |
| Full indexing | Bloom filters + columnar scan (§9 step 3) | Volume is high, queries are rare |
| Tail sampling | Head sampling + exemplars | Collector buffer is unaffordable |
| Per-host agents | OpenTelemetry Collector sidecars | Kubernetes-native fleets |
| 15 s scrape | 60 s + recording rules | The metric rarely changes faster |
| Metrics for everything | Logs for high-cardinality questions; traces for causality | You are about to add a `user_id` label |

## 13. When to use it

- **Per-tenant quotas and cardinality guards** in any multi-tenant telemetry platform — without them, one deploy is a platform outage.
- **Rollups storing (min, max, sum, count)** whenever anyone may ask for a percentile over months.
- **Tail-based trace sampling** whenever errors are rarer than 1 in 100.
- **Query guards and cost-returning responses** in every query API.
- **Dedupe as a schema constraint** in any alerting system.
- **A separate failure domain** for observability, always.
- **Per-service cost attribution**, because it is the only thing that reliably changes behaviour.

## 14. When *not* to use it

- **A bespoke TSDB** when a mature open-source one exists.
- **Tail sampling** with a small fleet — the buffer cost dominates the saving.
- **Full-text indexing of debug logs** — sample or drop them.
- **Per-instance alert notifications** — group first, always.
- **Metrics for high-cardinality questions** (`which user?`) — that is a log or trace question.
- **A 15-second scrape interval** for metrics that change monthly.
- **Silences without expiry.**

## 15. Common mistakes

1. **Unbounded labels** in metric definitions (§7).
2. **No cardinality cap**, so a cost mistake becomes an outage.
3. **Blocking the application** when ingest is slow or full.
4. **Debug logging in a hot loop**, unsampled (§7).
5. **Indexing everything**, or nothing.
6. **Storing only rollup means**, then being unable to compute p99.
7. **Head sampling only**, then having no traces for the rare error.
8. **No query guards**, then one dashboard OOMs the fleet.
9. **Alerting on causes instead of symptoms** (§11.4).
10. **No dedupe or grouping** — 160,000 pages.
11. **Observability in the same failure domain as production** (§11.3, §11.6).
12. **Silences without expiry or owner** (§11.8).
13. **No cost attribution** — so nobody owns the 23 % line (§9 step 2).
14. **Ignoring clock skew** in trace assembly (§11.7).
15. **Treating the platform's own SLIs as unimportant** — it is the dependency of the person debugging everything else.

## 16. Interview perspective

**What is actually being tested.** Whether you understand that a telemetry platform is a **cardinality and volume budgeting problem** wrapped around a time-series database and an index. Strong candidates: hash-shard by series, cap the head block, roll up with a four-tuple, tier retention, tail-sample traces, dedupe alerts by primary key, guard queries by estimated cost, and separate failure domains. Weak candidates: "put it in Elasticsearch and add more nodes".

**Answers that land well:**

- *"What is the hardest part of a metrics platform?"* — "Cardinality, and it is a memory problem before it is a money problem. Forty-eight million active series at three kilobytes each is a hundred and forty-four gigabytes of RAM that must all be resident for the write path to work. A single deploy adding a request-id label created five million series in twenty minutes and OOMed a shard, so the platform that watches everything went blind for nineteen minutes during a customer incident. The fix is a gateway guard with forbidden label names, per-metric and per-tenant caps that drop and count rather than block, and a hard ceiling on the series dictionary."
- *"Where does the money go?"* — "Logs, seventy-one percent. And the single biggest lever was not compression or hardware — it was per-service cost reporting. A debug line in a retry loop was twenty-three percent of the entire bill, and once the owning team could see their own spend in the weekly review, they deleted it in two days. Enforcement did nothing; visibility did everything."
- *"How do you sample traces?"* — "At the tail, because head sampling throws away exactly what you need. Head sampling at one percent keeps one percent of the errors, and for a service at forty requests a second that is three error traces a day — effectively none. Tail sampling buffers the whole trace at the collector and keeps every error, every outlier over p99.5, and two point two percent of the rest, which is two point two percent of the storage with complete error visibility. The cost is thirty-two gigabytes of collector buffer and a replica requirement, which is two thousand pounds a month against two point four million for keeping everything."
- *"How do you stop alert fatigue?"* — "Structural: an alert instance is a primary key of rule plus a label fingerprint, so forty rules across four thousand pods is one hundred and sixty thousand instances that group down to forty-one notifications. Then quality: we rewrote every rule as a symptom or an SLO burn rate, tracked precision as a platform SLI, and reviewed rules quarterly to delete them. Page volume fell ninety-six percent and precision went from twenty-nine percent to eighty-four percent, and detection time did not change."
- *"What happens when the platform is the thing that is broken?"* — "Two rules. First, failure-domain separation: it runs in its own cluster, on its own network path, with its own control plane, because when production is down the pager path is the only thing that must still work. Second, the agent fails open: it is cgroup-capped at one percent CPU and a hundred and fifty megabytes, it buffers thirty minutes to local disk, and it drops oldest-first under pressure. An agent that blocks the application has turned monitoring into the outage."

**Common interview tasks:** design metrics ingestion at N series; handle cardinality; design log storage and search; design trace sampling; design the alerting pipeline; decide retention tiers; prevent the platform from taking down production; price the platform per tenant.

**Red flags interviewers watch for:**

- "Store everything, it is cheap."
- No cardinality discussion at all.
- A single index for all logs.
- Head sampling only.
- Alert per instance, no grouping.
- Agents that block or retry forever.
- The platform co-located with production and sharing its control plane.
- No query cost estimation or guards.
- Storing only rollup means.
- No per-tenant isolation.
- No numbers: no series count, no GB/day, no cost split.

## 17. Knowledge check

**Conceptual**

1. Explain why hash-sharding by series is right for metrics and time-sharding is not. Then explain what the cardinality guard does, what it must never do, and where each failure mode shows up first.
2. Explain why rollups must store (min, max, sum, count) and what becomes impossible if you store only the mean. Then explain the tier-routing rule for a 90-day query.
3. Explain tail-based sampling, the buffer it requires, and its cost versus head sampling. Then explain why the dedupe key in alerting is a schema decision rather than a job.

**Practical scenario**

4. Design TelemetryCo: 180,000 hosts, 48 M series at 15 s, 120 TB/day of raw logs, 4.5 M spans/s, 60,000 alert rules, a budget of £650,000/month, and a hard requirement that the platform survive a production outage. Give requirements, capacity, ingest APIs, the schema for series/segments/traces/rules/usage, the write path with shard counts and head-block sizing, the read path with rollups and guards, the log index policy and retention tiers, the trace sampling policy with its buffer arithmetic, the alerting dedupe and grouping, multi-tenant isolation, the cost model per pillar, the failure-domain separation, the alert set, and the incident replays. Then state what changes if the platform must serve 1,400 tenants with hard data-residency requirements.

**Calculation**

5. Cardinality. 4,200 requests/s each creating a new series with `request_id`. (a) How many series in 20 minutes? (b) At 3 KB per series in the head block, how much RAM does that require? (c) If a node has 16 GB and 1.5 GB of overhead, how many shards of 1 M series fit? (d) State in one sentence why the incident was an outage rather than a bill.

6. Trace sampling. 4.5 M spans/s, 11 spans per trace, 0.3 % error traces, 900 B per span. (a) Compute error traces/day. (b) Compute the storage/day of keeping all errors plus a 2.2 % baseline of the rest. (c) Compute the monthly cost at £0.23/GB. (d) Compute the cost of keeping everything, and the ratio.

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
  - [x] 12. Design e-commerce and inventory (L104)
  - [x] 13. Design payment processing (L105)
  - [x] **14. Design a metrics and logging platform (L106) ← you are here**

**NEXT LESSON: Phase 5 wrap-up — revision sheet, glossary, quiz, exercises**

---

---

# Phase 5 Wrap-Up — Complete System Designs

*Phase 5, Lessons 93–106*

## 1. Revision sheet

### 1.1 The fourteen designs, and the decision each one turns on

| # | System | The load-bearing decision | The number that decided it |
|---|---|---|---|
| 93 | **Pastebin** | Immutable content → an infinite CDN TTL; 9 base62 characters, not 6 | 50 M reads/day at 83–97 % CDN hit = **£1,651/month** total |
| 94 | **URL shortener** | **302**, not 301 — and leased key pools instead of a counter | 100 k creates/day vs **856/s** redirect peak (22:1); 301 lost **61 %** of clicks to browser caches |
| 95 | **Rate limiter** | Fixed windows overshoot; token bucket in Redis with local pre-checks | Fixed window admitted **1.98×** the limit; local-only would be **14×** wrong in the worst case |
| 96 | **Notifications** | Four priority queues, and SMS treated as a *money* problem | Push peak **18,519/s** (40× average); SMS is 0.9 % of messages and **96 %** of £1,109,040/month |
| 97 | **Autocomplete** | Pruned prefix hash table at the edge, not a trie in one process | 200 M queries/day × 4.2 keystrokes = **9,722/s** (peak 38,889/s); hash **0.9 ms** vs trie **6.8 ms** — £1,435 vs £10,500/month |
| 98 | **File storage** | Presigned URLs and content-defined dedupe; never proxy bytes | **8 PB** stored; dedupe saves **35 %** (£61,652/mo); presigned URLs save ~£61,700/mo |
| 99 | **Web crawler** | Politeness *and* change-rate scheduling; the calendar trap | 99.2 fetches/s (peak 152), **8 useful pages per 100** attempts; a calendar trap was **61 %** of a day's fetches |
| 100 | **Real-time chat** | Persist-then-push, and connections are **RAM**, not requests | 40 M connections = **400 GB**, 834 nodes ≈ **£70,890/mo**, 96 % of it compute; 94,444 msg/s peak |
| 101 | **News feed** | Hybrid fan-out with a **1 M-follower** threshold | Pure push needs **556 k inserts/s** > 400 k/s capacity ⇒ an 11-minute degradation and **£260 k** of ad revenue |
| 102 | **Video streaming** | Bitrate and cache key, not rung count or storage | **21.6 PB/month** = £432,000 = **88 %** of the bill; per-title −22 % bitrate = £120 k/mo saved, 1.5-month payback |
| 103 | **Ride sharing** | Separate the **50,000/s** telemetry stream from the **42/s** trip stream; claim with a conditional UPDATE + fencing token | **3,597:1** write ratio; the read-then-write version double-assigned **0.4 %** = 180 stranded rides/day = **£74 k/mo** |
| 104 | **E-commerce & inventory** | The conditional decrement, and the reservation TTL derived from payment p99.9 | **4,100 reservations/s** vs 480 with a row lock; carts holding stock would hold **181 %** of physical inventory |
| 105 | **Payments** | Idempotency **downstream** as well as at the API; `unknown` is a state | 21,400 timeouts/day × 61 % already succeeded ⇒ **£191 k** from one retry incident; chargebacks 0.06 % = **£9.9 M/mo** vs £420 k of infrastructure |
| 106 | **Metrics & logging** | Cardinality is **memory** before money; index the errors, scan the rest | 48 M series = 144 GB of head-block RAM; a `request_id` label added **41 M series** and OOMed a shard for **19 minutes**; logs are **71 %** of £610 k/month |

### 1.2 The cross-cutting patterns, and which designs used them

| Pattern | Appears in | The invariant it protects |
|---|---|---|
| **Idempotency key** | 94, 96, 100, 104, 105 | A retry is not a second effect |
| **Immutable data + long TTL** | 93, 94, 97, 98, 102 | Caches can be trusted at the edge |
| **Cached reads with explicit staleness** | 93, 94, 97, 101, 104 | Reads are cheap; promises are not |
| **Conditional write / CAS** | 95, 103, 104, 105 | Two writers cannot both win |
| **Fencing tokens / leases** | 95, 100, 103 | A stale writer cannot act |
| **Partial unique index** | 103, 104, 105 | Uniqueness only where it means something |
| **Hybrid fan-out** | 101, 96, 102 | Cost scales with *active* work, not total |
| **Outbox / CDC** | 96, 101, 104, 105 | No dual writes |
| **Queue + priority classes** | 95, 96, 99, 104 | The important request goes first |
| **Backpressure / admission control** | 95, 96, 99, 104, 106 | The system degrades honestly instead of failing |
| **Tail sampling / budget caps** | 106, 96, 102 | You can afford to keep the useful part |
| **Reconciliation against a counterparty** | 104, 105 | Drift is found by you, not by a customer |
| **Derived balances + rebuild** | 104, 105, 101, 97 | The summary is verifiable against the truth |

### 1.3 The arithmetic cheat sheet

```
  LATENCY & QUEUES
      Little's Law:  L = λW          concurrency = rate × latency
      capacity:      N = λ × p99 × safety_factor(1.5)
      tail:          p99 of a fan-out of k calls = 1 − (1 − p)^k

  STORAGE
      capacity = writes/s × seconds × bytes_per_record × replicas × retention
      1 PB       = 1,000 TB         1 TB = 1,000 GB
      a year of x GB/day = x × 365 GB

  CACHE
      egress  = views × bytes_per_view × (1 − hit_ratio × (1 − miss_price_ratio))
      a miss is often 4–5× the price of a hit → optimise the miss
      TTL from update rate: TTL ≈ tolerance_staleness, never "feels right"

  FAN-OUT
      push cost = posts/s × average_followers
      pull cost = reads/s × authors_per_read
      threshold where push breaks = write_capacity / followers_of_the_top_1 %

  SHARDING
      shards = ceil(peak_throughput / per_shard_capacity), then add headroom
      hot shard = one key > ~10 % of traffic ⇒ split the key, not the shard

  MONEY
      monthly = daily × 30;  a per-call price becomes absurd above ~3 calls/s
      check every per-unit figure against monthly revenue before shipping it
```

### 1.4 Ten reflexes to take into an interview

1. **Name the invariant first.** Oversell floor, monotonic position, one payment per key, `available − reserved ≥ 0`. The design follows the invariant.
2. **Separate the streams by rate.** Telemetry at 50,000/s and trips at 42/s are different systems even when they share a schema.
3. **Count the peak, not the average.** Peaks are 10–40× averages in this phase's designs, and capacity is sized on the peak.
4. **Ask what is a promise and what is a hint.** Then cache the hints and be strict about the promises.
5. **Put the pivot where you can afford the undo.** Cheap compensations before; refunds after.
6. **Make retries safe twice** — at your API and at the downstream.
7. **Treat an outage of a dependency as an expected state**, not an exception: `unknown`, `degraded`, `partial`.
8. **Derive every TTL, threshold and sampling rate from a measurement**, and say which measurement.
9. **Convert a spike into a rate** rather than letting it arrive.
10. **Sanity-check the money.** If a per-call or per-unit figure implies a bill larger than the revenue, the design is wrong, not the pricing.

## 2. Glossary

**A**

- **ABR (adaptive bitrate)**: a player choosing a rendition per segment from measured throughput and buffer.
- **Admission control**: deciding how many requests enter a system per unit time; the waiting room, the queue, the token bucket.
- **Amplification**: one request producing many downstream ones (fan-out), the reason a small service takes a large one down.
- **Append-only**: a store that only adds records; corrections are new records (ledger, segments, chat log).
- **Authorisation (payment)**: the network's promise to pay; not money.
- **Autovivification of a hot key**: the moment a cache key becomes so popular that one node cannot serve it.

**B**

- **Backpressure**: telling a producer to slow down, or shedding its work, instead of buffering without limit.
- **Base62**: the 62-character alphabet used for short codes and ids; 9 characters ≈ 1.3 × 10¹⁶ values.
- **Bloom filter**: a probabilistic set membership test; false positives, no false negatives. Used to skip log segments.
- **Blast radius**: how much breaks when a component fails.
- **Bulkhead**: an isolated resource pool so one workload cannot starve another.

**C**

- **Cardinality**: the number of distinct time series. The governing cost and stability metric of a metrics platform.
- **CAS (compare-and-swap)**: a write that succeeds only if the value is still what the writer expects.
- **CDC (change data capture)**: turning a database's write-ahead log into a stream of events.
- **Chargeback**: a forced reversal of a card payment, with a fee; the dominant fraud cost.
- **Compensation**: an action that undoes a completed step of a saga.
- **Conditional decrement**: `UPDATE … WHERE on_hand - reserved >= n`; the atomic primitive behind "never oversell".
- **Consistent hashing**: hashing keys and nodes onto a ring so that adding a node moves only ~1/n of keys.
- **Content-defined chunking (CDC)**: splitting files at content-dependent boundaries so dedupe survives insertions.
- **CQRS**: separating the write model from the read model.

**D**

- **Dead letter queue (DLQ)**: where messages go after retries are exhausted, so nothing is silently dropped.
- **Debounce**: collapsing a burst of events into one action.
- **Downsampling / rollup**: pre-aggregating old samples into coarser ones, storing (min, max, sum, count).
- **Double-entry ledger**: every transaction has debits and credits that sum to zero.
- **Drain**: letting a process finish its in-flight work before termination (the 24-minute chat deploy that avoided 40 M reconnects).

**E**

- **Egress**: the bytes leaving your infrastructure; in video, the whole bill.
- **Eventual consistency**: replicas converge given no new writes; the question is always *how stale*.
- **Exactly-once**: not achievable end-to-end; manufactured from at-least-once plus idempotency.
- **Exemplar**: a link from an aggregated metric point to a specific trace.

**F**

- **Fan-out**: one write producing many read-model writes (push) or one read gathering many sources (pull).
- **Fencing token**: a monotonically increasing number attached to a lock so stale holders are rejected.
- **Fixed window**: a rate-limiting window anchored to the clock; lets a caller do 2× the limit at the boundary.
- **Flash sale**: a demand spike on scarce stock; the event that turns a correct design into an honest queue.

**H**

- **Head block**: the in-memory, recently-written portion of a time-series database.
- **Head sampling**: deciding to keep a trace before its outcome is known (the mistake that loses errors).
- **Hot key / hot shard**: one key or partition carrying a disproportionate share of traffic.
- **Hysteresis**: deliberate resistance to state flapping, e.g. in autoscaling and health checks.

**I**

- **Idempotency key**: a caller-supplied identifier making a retried request a no-op.
- **Immutable segment**: an encoded video chunk whose path never changes, so caches can trust it forever.
- **Inverted index**: token → list of document ids; what makes log search fast and expensive.
- **Isolation level**: the anomalies a transaction is protected from.

**J–L**

- **Jitter**: randomisation of retry timing to avoid synchronised retries (thundering herd).
- **Lease**: a time-bounded lock that expires, so a dead holder cannot block forever.
- **Leader election**: choosing one live coordinator; requires a quorum to be safe.
- **Little's Law**: `L = λW`.
- **Load shedding**: deliberately dropping work to protect the remainder.

**M**

- **Materialised view**: a stored result of a query, kept in step with its source (a balance derived from postings).
- **Merge-on-read**: combining a push timeline and a pull timeline at read time (the hybrid feed).
- **Minor units**: integer pence/cents; money is never a float.
- **Monotonic id**: an id that never decreases for a given object (UUIDv7, ULID, sequence).

**O–P**

- **Outbox**: writing business state and the event to publish in the same transaction, then dispatching.
- **Oversell floor**: `available − reserved ≥ 0`, enforced atomically.
- **Pivot**: the step in a saga after which compensation becomes expensive (capture, not authorise).
- **Presigned URL**: a time-limited credential letting a client talk to storage directly.
- **Progressive sampling**: retaining less of the cheap log levels first when a budget is exceeded.
- **Push vs pull**: writing fan-out on the write path or gathering it on the read path.

**Q–R**

- **Quorum**: the minimum set of replicas whose agreement makes an operation valid (`⌊n/2⌋ + 1`).
- **Rendition / ladder**: the set of encoded (resolution, bitrate) versions of one video.
- **Reconciliation**: matching your ledger to a counterparty's file and owning every difference.
- **Reservation (hold)**: a time-bounded claim on stock that is not yet a sale.
- **Rollup**: see downsampling.

**S**

- **Saga**: a multi-step distributed transaction with compensations, orchestrated or choreographed.
- **Sampling**: keeping a subset of telemetry; head (early), tail (after the outcome), progressive (by budget).
- **Segment (video)**: a few seconds of encoded media, the unit of caching and ABR switching.
- **Serialisable**: the strongest isolation level; equivalent to some serial order.
- **Shard group**: an isolated set of shards for a tenant, so one tenant cannot starve another.
- **Signing (webhooks)**: HMAC over the body and timestamp, so consumers can verify authenticity.
- **Skew**: the difference between a logical event order and its observed order; also an uneven partition.
- **Staleness bound**: the maximum age of data a read path may serve, stated explicitly.
- **Surge pricing**: price as a queue-management tool; needs a cap, a lock and smoothing.
- **Sweeper**: a job that expires or releases things nobody will come back for.

**T–Z**

- **Tail-based sampling**: choosing which traces to keep after they complete, so all errors are kept.
- **Token bucket**: rate limiting with burst allowance; the standard because it matches real traffic.
- **Tombstone**: a deletion marker in an append-only store.
- **TTL (time to live)**: how long data may live or be served; derived from a measurement, never guessed.
- **Waiting room**: an admission queue in front of a scarce resource, with a signed entry token.
- **Watermark**: a claim that no events older than a timestamp will arrive, enabling window closure.
- **Webhook**: an outbound HTTP call notifying a consumer of an event; at-least-once, therefore deduped.
- **Write amplification**: bytes written per logical write, including replication, indexes and logs.

## 3. Twenty quiz questions

1. **Why is a 302 the right default for a URL shortener, and what did 301 cost?**
   301 is cached by browsers indefinitely, so the analytics behind the redirect disappear. In the L94 analysis, the 301 variant lost **61 %** of clicks to caches.
2. **A rate limiter shows 1.98× the configured limit at the boundary. What is the bug?**
   A fixed window. It admits `limit` at the end of one window and `limit` at the start of the next — up to 2× in a 2-window span. Use a token bucket or sliding log.
3. **Why is SMS only 0.9 % of a notification platform's messages but 96 % of its bill?**
   Push is nearly free per message; SMS costs ~£0.032 each. £1,109,040/month, of which SMS is ~£1.07 M — the design lever is *when* you escalate to SMS, not how many pushes you send.
4. **Your autocomplete p99 is 40 ms server-side but 300 ms in the browser. Where do you look?**
   The keystroke path: debounce, request cancellation, gzip, connection reuse, and the number of keystrokes per query (4.2 in L97). The server is not the problem.
5. **Why do presigned URLs change the cost profile of a file service?**
   Proxying bytes through your API doubles egress and makes your fleet the bottleneck. Presigning lets clients talk to storage directly: ~£61,700/month in the L98 design.
6. **What is the calendar trap, and how do you escape it?**
   Infinitely-generated URLs (calendars, faceted search) that a crawler follows forever. Escape with URL canonicalisation, a trap score by path depth, and per-host budgets — it was 61 % of one day's fetches in L99.
7. **Why does a chat server's memory dominate its cost?**
   Each connection holds buffers, TLS state and subscriptions. 40 M connections ≈ 400 GB of RAM = 96 % of the compute bill. Messages are cheap; *being connected* is expensive.
8. **Why does persist-then-push cost latency, and why pay it?**
   The ack waits for a durable write (9 ms) and the push follows. The alternative loses messages on a crash. p99 380 ms in L100 came from the write path, not the socket.
9. **What breaks first in a pure push news feed?**
   Writes. 40 M posts/day × average followers exceeds the insert capacity; a single account with 94 M followers turns one post into an 11-minute degradation and £260 k of lost ad revenue. Hence the 1 M-follower threshold and hybrid merge.
10. **Why 1 M followers as the threshold?**
    Because the top 4,100 accounts (71 % of all follow edges) sit above it: pulling 4,100 timelines costs £4,100/month, while pushing them costs £63,000/month.
11. **Where does a video platform's money go, and which lever matters?**
    Egress: 88 % of the bill (21.6 PB/month = £432,000). Bitrate and cache key are the levers; rung count and storage are not (storage is 8 %).
12. **Why are 2-second segments for VOD a £155,000/month mistake?**
    Four times the request rate pushes the hit ratio from 95.4 % to 88 %, and a miss costs 4.5× a hit. Keep 2 s for live only, where latency is the product.
13. **A ride-sharing system writes 50,000 positions/s but only 42 trip requests/s. What follows?**
    The two streams are separate systems: an in-memory geo index with a staleness bound for telemetry, and a transactional path for trips. The ratio is 3,597:1, so a shared design optimises the wrong thing.
14. **How do you claim a courier without double assignment?**
    One statement: `UPDATE … WHERE state='available' AND last_seen > now() - 15s RETURNING claim_token`, plus a partial unique index, plus fencing tokens on later transitions. Read-then-write produced 0.4 % double assignments = 180 stranded rides/day.
15. **Why must a reservation TTL be derived from a measurement?**
    It must cover the payment p99.9 (14 m 20 s in L104) without stranding stock. 15 minutes stranded ~11 units on a hot SKU and expired 1.2 % of checkouts; 30 minutes stranded 21 units and lost £1,554.
16. **What is the most common e-commerce inventory design mistake?**
    Reserving stock when an item enters the cart. Active carts held 181 % of all physical stock in the L104 arithmetic, so the shop would present as sold out with a full warehouse.
17. **Why is an idempotency key that is not honoured downstream "documentation, not a mechanism"?**
    Because the failure is a timeout that succeeded: your retry re-enters the acquirer, which has no memory of the first. The downstream key makes the retry their no-op — 21,400 timeouts/day with 61 % already succeeded cost £191 k in one incident.
18. **Why is `unknown` a state rather than an exception in a payment machine?**
    Because a timeout is neither success nor failure; treating it as failure causes retries that double-charge, and treating it as success ships goods you were not paid for. Write an intent, record `unknown`, and let reconciliation resolve it.
19. **Why does a metrics platform's cost behaviour differ from its storage?**
    Storage is trivial: 48 M series for 15 days is 7.5 TB. The cost is the head block — 144 GB of RAM that must be resident — so cardinality is an OOM risk before it is a bill, and the guard must drop-and-count rather than block.
20. **Why index only errors and warn, and scan the rest?**
    Indexing 100 % of 120 TB/day would cost ~£680,000/month; indexing the 3.1 % that is error/warn costs ~£21,000 and keeps 60-second cold text search for the rest — a 32× reduction for a query asked once a week.

## 4. Five practical exercises

**Exercise 1 — Capacity and cost for a design you already know.**
Take the URL shortener (L94) and redo the estimation with these changes: 500 k creates/day, 4× the read ratio, 3-year retention, and an EU-only audience. Produce: storage/year, peak QPS, CDN egress, monthly bill, and the design change that matters most. Then do the same with a 10-year retention requirement and state what breaks (hint: it is not storage).

**Exercise 2 — Find the missing invariant.**
For each system, write the invariant, then describe a one-line code change that would violate it and the user-visible symptom:
news feed (no duplicate posts at a page merge), chat (no message shown before it is durable), rate limiter (no exceeding the limit in any 60-second window), inventory (no oversell), payments (no double charge), telemetry (no tenant exceeding its series cap).
Then say, for each, whether the system would *notice* the violation within an hour, and what alert you would add.

**Exercise 3 — Derive three thresholds from measurements.**
(a) A feed's push/pull threshold, given write capacity 400 k inserts/s, 40 M posts/day, and a follower distribution where the top 0.001 % of accounts hold 71 % of edges.
(b) An autocomplete prune threshold, given 33.6 M candidate prefixes that must fit in 3.1 GB with 40 B per entry.
(c) A reservation TTL, given payment p50 47 s, p99 6 m 40 s, p99.9 14 m 20 s, and a stock-idle cost of £74 per unit-hour.
For each, state the measurement that would change your answer.

**Exercise 4 — Design the degradation, not the failure.**
Pick the chat system (L100) and write the degradation ladder for a 60 % capacity loss: what is shed first, what is delayed, what is refused, what the user sees, and which SLO is broken by which step. Then do the same for the payment authorisation path and explain why the ladder is *shorter* there.

**Exercise 5 — Price a feature nobody asked you to price.**
Take the "show exact stock counts" request from L104 and the "keep every trace" request from L106. For each: estimate the cost at your system's scale, name the staleness or sampling you would need to drop, and write the one-paragraph answer you would give the product owner. Then find the version of each feature that costs 1 % as much and delivers 80 % of the value.

## 5. Mini system-design exercise — a concert ticketing platform

**The prompt.** Design a ticketing platform for arenas of 20,000–80,000 seats. On-sale events receive **2 M arrivals in the first 5 minutes** for **60,000 seats**. Requirements: no seat sold twice, no payment taken for a seat that is not held, a fair and honest queue, a mobile ticket that cannot be reused, and a support path for refunds and transfers. Assume 400 events a year, 40 of them on-sale spikes.

**Constraints to design against.**

- 60,000 seats, 2 M arrivals, 5 minutes: a 33:1 oversubscription — worse than L104's flash sale.
- Payment capacity 300 authorisations/s across two providers.
- Seats are **individually identified** (unlike stock counts), so the invariant is per-seat, not per-SKU.
- The mobile ticket must survive screenshots and offline venues.
- Refunds and transfers change ownership after the event has sold out.

**Work through, in this order.**

1. **Requirements and capacity.** Write the functional list (browse, select seats, hold, pay, issue ticket, transfer, refund, scan). Compute: arrival rate, admitted rate, hold rate, payment rate, seat-hold table size, ticket signing rate.
2. **The seat-hold invariant.** Design the atomic claim for *one seat*: a conditional update on `(event_id, seat_id, version)` or a partial unique index over `held` seats. Then design the *basket* claim for up to 6 seats without deadlocking — and state the ordering rule.
3. **The waiting room.** Derive the admission rate from payment capacity, not from seats, and explain why admitting 2,000/s manufactures refunds. Give the token scheme, the queue position estimate, and what happens to a user who abandons the tab.
4. **The hold TTL.** Derive it from the payment p99.9 including 3-D Secure. Then handle the ugly case: a user whose payment succeeds *after* their hold expired. (Hint: the seat is gone; you must refund, so prevent it with a pre-capture hold extension.)
5. **Payment.** One idempotency key per basket, `unknown` handled by reconciliation, and a per-basket ledger posting so refunds are arithmetic rather than archaeology.
6. **Tickets.** Design the scannable ticket: a signed token with the seat id and an event key, rotated, with an offline verification path at the venue and a single-use scan record. Say what stops a screenshot.
7. **Transfers and resale.** A transfer changes the owner of a seat with a monotonic ownership epoch, so the old ticket is invalid. Design it as a ledger transfer, not an update.
8. **Telemetry.** Which five metrics would you page on during an on-sale, and why is "seats sold" a *lagging* indicator? (Hint: the leading indicator is admitted-user → hold conversion.)
9. **The post-mortem you are trying to avoid.** Write the three most likely incidents with their detection signal and their fix, in the style of the §7 sections of L102–L106.

**Success criteria.** An answer that names the per-seat invariant, derives the admission rate from payment capacity, extends the hold before capture rather than refunding after, makes tickets unforgeable via signature plus single-use scan, and pages on conversion rather than sales — is a strong answer. An answer that "queues everyone and adds servers" is not.

## 6. Common interview questions

1. **"Design X" in 45 minutes — how do you start?**
   Requirements (functional + non-functional with numbers), then capacity, then the API and data model, then the high-level flow, then two deep dives where the difficulty actually is, then failure modes. Say out loud which numbers you are assuming and correct them when the interviewer changes one.
2. **How do you decide sharding keys?**
   By the query that must be single-shard, plus the distribution of the hottest key. State the hot-key plan separately: split the key (L95's per-shard sub-buckets), cache it, or queue it.
3. **How do you handle a hot key?**
   Local pre-check + shared counter; request collapsing; key splitting with aggregation on read; or a queue that serialises it. Name the metric that shows you have one.
4. **Cache or not?**
   Ask what a stale answer costs. If the answer is "a support ticket", cache with an explicit staleness bound; if it is "money", do not cache, or cache with a version that must match.
5. **What is your consistency model?**
   Say it per read path, not per system: "product page 5 minutes, price 0, availability at reservation 0, feed 30 s, presence 15 s".
6. **Why not 2PC?**
   Availability and blocking; use a saga, place the pivot late, and make each step idempotent so retries are free.
7. **How do you make a distributed operation exactly-once?**
   You do not. You make it at-least-once and idempotent at the effect boundary, and you make the second attempt detectably a duplicate — at your API and at the downstream.
8. **How do you size a queue or a thread pool?**
   `λ × p99 × 1.5`, then confirm with a load test at the peak; and put a bounded queue with load shedding in front so the failure is a fast rejection, not a slow timeout.
9. **What does your system do when a dependency is down?**
   Name the degradation: serve stale, serve partial, queue, refuse with a clear error, or fall back to a secondary. Never "it retries".
10. **How do you stop one tenant taking down the platform?**
    Quotas enforced at the edge of the shared resource, per-tenant shard groups, and shedding that is proportional rather than all-or-nothing.
11. **Which alert would you page on for this system?**
    The one that fires before a user complains, is actionable, and has a runbook — an SLO burn rate, not a cause.
12. **Where is the money in this design?**
    Always answer with the unit economics: egress in video, SMS in notifications, RAM in chat, write amplification in feeds, disputes in payments, log volume in telemetry.
13. **What would you do differently at 10× the scale?**
    Name the first component to break and the structural change (not "more servers"): a bigger fan-out threshold, per-tenant shards, segmented streaming, or tiered storage.
14. **How do you migrate without downtime?**
    Dual write, backfill, compare, shadow read, cut over by percentage, then remove the old path — with a kill switch at every stage.
15. **What is the invariant, and how is it enforced in the schema?**
    A CHECK constraint, a partial unique index, or a conditional update — not a comment and not a code review.
16. **How do you test this?**
    Unit tests for the invariant, a load test at 2× peak, fault injection on every dependency, and a game day that kills a real node in production hours.
17. **What data would you keep, and for how long?**
    Tier by access pattern; state the restorability target; say who pays for it.
18. **How do you handle clock skew?**
    Never order by wall-clock across machines: use logical clocks, sequence numbers, fencing tokens, or a watermark.
19. **What breaks first when traffic doubles?**
    Answer with a specific component and a specific metric with a specific threshold, and say which one you would add capacity to and which one you would redesign.
20. **What is the simplest version of this that ships?**
    Always have one. Interviewers trust candidates who can name the boring v1 and the reason the elaborate parts are deferred until a measurement demands them.

## 7. Topics to revisit

**Re-read within a week** (these carry the most transferable weight):

- **L101 hybrid fan-out** — the threshold arithmetic is the template for every "push or pull?" decision.
- **L103 conditional UPDATE + partial unique index + fencing token** — the same three primitives solve every claim-a-resource problem.
- **L104 conditional decrement and reservation TTL** — the canonical bounded-resource design.
- **L105 idempotency at three layers and `unknown` as a state** — the only defence against the most expensive class of bug.
- **L106 cardinality guard** — the clearest example in the course of a cost problem that is really a memory problem.

**Practise until they are reflexes:**

- Little's Law and the `λ × p99 × 1.5` capacity estimate, without a calculator.
- Converting a per-call or per-unit price into a monthly bill, and sanity-checking it against revenue.
- Deriving a threshold (fan-out, TTL, sampling rate) from a measured distribution rather than a guess.
- Naming the invariant, then naming the exact schema constraint that enforces it.

**Weak spots to watch in your own answers:**

- Stating averages when the design depends on peaks (10–40× in this phase).
- Adding a queue where the real problem is a missing idempotency boundary.
- Forgetting the compensation and the sweeper once the happy path is drawn.
- Choosing the strongest consistency for every read path, then wondering why the bill doubled.
- Treating cost as an afterthought when it *is* the design in five of these fourteen systems.

**What to build next, in order:**

1. A rate limiter (L95) — 200 lines, and it teaches atomicity and expiry.
2. A ledger with double-entry postings and a reconciliation job (L105) — it teaches invariants and drift.
3. A small telemetry pipeline with a cardinality guard and rollups (L106) — it teaches budgets and read paths.
4. Then re-read L101 and L104, and note how much of them you could now write from memory.

**NEXT: you have finished the course. Return to [index.md](./index.md) for the full map, or revisit any lesson's `§17 Knowledge check` — the questions are the curriculum.**

---
