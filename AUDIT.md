# Repo audit — `system-design-course`

**Audited:** 2026-10-30 · **Scope:** `system-design-course/` (the learning database built with Opus). The `data-engineering-learning/` and `customer-sales-lakehouse/` directories are untouched and were out of scope.

---

## 1. Headline status

| Phase | Lessons | Before this audit | Now |
|---|---|---|---|
| 1 — Foundations | 1–17 + wrap-up | ✅ Complete | ✅ Complete |
| 2 — Scaling | 18–39 + wrap-up | ✅ Complete (index wrongly said "wrap-up pending") | ✅ Complete |
| 3 — Data & storage | 40–63 + wrap-up | ⚠️ **3 lessons missing** (L55, L59, L63), wrap-up written but referenced the missing lessons | ✅ **Complete** |
| 4 — Distributed systems | 64–92 + wrap-up | ⚠️ 5 of 29 lessons existed as **unmerged drafts** (L64, 67, 70, 73, 85) in `_chunks/` | ✅ **Complete**: L64–92 + wrap-up written and merged; all drafts consumed |
| 5 — Design exercises | 93–106 + wrap-up | ❌ Nothing | 🔄 **6 of 14 done**: L93–98 (Pastebin, shortener, rate limiter, notifications, autocomplete, file storage) |

**Course body before:** 54,581 lines / ~2.9 MB. **Added in this pass:** 4 lessons (L55, L59, L63, L65, L66) plus a phase file structure — see `git log`.

---

## 2. What was actually broken

### 2.1 Phase 3 had three holes in the middle of the sequence

The inline progress checklists at the end of L54, L56 and L62 all claimed Phase 3 topics 1–24 were (or would be) complete, and the **Phase 3 wrap-up already cross-referenced the missing lessons** — e.g. its "topics to revisit" table points readers at "L55 (query plans)", "L59 (CAP)" and "L63 (RPO/RTO)". A reader following the course would have hit three dead ends:

| Gap | Between | Symptom for a reader |
|---|---|---|
| **L55 — Query plans and index trade-offs** | L54 → L56 | L54 ends with "NEXT LESSON: Topic 16 — Query Plans"; the file jumps straight to normalisation. L56 references "Lesson 55" for planner estimates. |
| **L59 — CAP theorem** | L58 → L60 | PACELC (L60) opens by assuming CAP; the wrap-up assumes it too. This was the biggest conceptual hole in the phase. |
| **L63 — Retention, archival, backups, DR** | L62 → wrap-up | L62 ends with "NEXT LESSON: Topic 24 — Data retention…"; the wrap-up's RPO/RTO discussion had nothing to point at. |

### 2.2 Phase 4 was five orphaned drafts, not a course

`_chunks/` contained full drafts of **L64, L67, L70, L73 and L85 only** — one lesson per chunk file, each ending with a "NEXT LESSON" marker pointing at a lesson that was never written. Nothing was merged into a `phase-4-part-*.md` file, so Phase 4 did not exist as a readable part of the course at all. Missing: **65, 66, 68, 69, 71, 72, 74–84, 86–92** (24 lessons) plus the Phase 4 wrap-up.

### 2.3 `index.md` was stale in three separate ways

- Phase 2 was marked "wrap-up pending" — the wrap-up **was already written** (inside `phase-2-part-5.md`, all 7 sections). Only the roadmap table was out of date.
- Phase 3 was marked "in progress" with every checkbox empty, while 21 of its 24 lessons existed.
- The Files table listed **only phases 1–2**; none of the five Phase 3 files were listed, so a reader had no map of the largest part of the course.

### 2.4 Inline progress checklists drifted from the actual topic map

Lessons L40–L54 and L56 carry trailing checklists built from an **older Phase 3 topic map**, e.g.:

| Inline checklist says | The lesson actually is |
|---|---|
| Topic 3 = Joins (L42) | L42 = SQL vs NoSQL |
| Topic 4 = Constraints (L43) | L43 = Key-value databases |
| Topic 18 = Partitioning & sharding | Partitioning is Phase 2; Phase 3 Topic 18 = OLTP vs OLAP |

The authoritative map is the one in `index.md` (and in the wrap-up), and the new lessons follow it. The stale checklists in L40–L54/L56 are cosmetic but confusing; they should be regenerated from the canonical map in one pass (see §4).

### 2.5 Smaller issues

- **`README.md` at the repo root is UTF-16LE with a BOM**, so it renders as `#  L e a r n` on GitHub and in most editors. It should be UTF-8.
- **`system-design-course.7z` (1.2 MB) is committed** to the repo. It appears to be a compressed copy of the course, so it duplicates content that is already in the tree and will grow stale. Consider removing it and adding `*.7z` to `.gitignore`.
- **`_chunks/` is committed.** It is generation scratch space: it contains full duplicate copies of lessons that are also merged (L40–L63) plus the four unmerged Phase 4 drafts. Keeping it is fine, but it needs a README explaining which files are sources and which are drafts, or it should be pruned once Phase 4 is merged.
- **`customer-sales-lakehouse/` and `data-engineering-learning/`** are unrelated to the system-design course and were not reviewed.

---

## 3. What was added in this pass

| Item | File | Lines | Notes |
|---|---|---|---|
| **Phase 2 wrap-up** | — | 0 | Discovered already complete; no work needed |
| **Lesson 55 — Query plans and index trade-offs** | `phase-3-part-4.md` (appended) | +674 | `EXPLAIN`/`ANALYZE` reading, plan nodes, the cost model, estimation failures, index write-cost arithmetic |
| **Lesson 59 — CAP theorem** | `phase-3-part-5.md` (inserted before L60) | +536 | Precise Gilbert–Lynch definitions, quorum table, per-operation CAP at BookNook, the "CA is a category error" section, PACELC pointer |
| **Lesson 63 — Retention, archival, backups, DR** | `phase-3-part-5.md` (inserted before the wrap-up) | +700 | Replication-is-not-backup, PITR mechanics, 3-2-1-1, GDPR crypto-shredding, DR tiers, measured-RTO arithmetic |
| **Phase 4 part 1 assembled** | `phase-4-part-1.md` (new) | 2,049 | Written Phase 4 intro + merged L64 draft + new L65, L66 |
| **Lesson 65 — Partial failures and network partitions** | `phase-4-part-1.md` | +712 | Three RPC outcomes, failure taxonomy, timeout-as-a-guess, gray failures, metastable collapse, retry amplification, fencing |
| **Lesson 66 — Clock differences and ordering events** | `phase-4-part-1.md` | +644 | Skew/drift, wall vs monotonic, Lamport vs vector vs HLC, LWW data loss, lease-safety inequality, TrueTime |
| **`index.md` rewritten** | `system-design-course/index.md` | — | Correct roadmap, full Files table incl. Phase 3 + Phase 4, accurate checklists, flagged drafts |
| **`AUDIT.md`** | this file | — | State, defects, plan |

Every new lesson follows the course's 17-section template and the BookNook example, with worked numbers derived from the course's own established figures (50 M-row `orders`, 1.2 TB database, 5,000 writes/sec, 8 KB pages) so the arithmetic stays consistent across lessons.

---

## 4. Remaining work

### 4.1 Phase 4 — COMPLETE (0 lessons to write, 0 drafts to merge, wrap-up done)

Drafts in `_chunks/` (complete, 17-section, BookNook-consistent) still to merge:

| Draft | Lesson | Merge into | Status |
|---|---|---|---|
| `_chunks/L67-69.md` | 67 — Sync vs async workflows | `phase-4-part-2.md` | ✅ merged |
| `_chunks/L70-72.md` | 70 — Event streams | `phase-4-part-3.md` | ✅ merged |
| `_chunks/L73-75.md` | 73 — At-most/at-least/exactly-once | `phase-4-part-4.md` | ✅ merged |
| `_chunks/L85-87.md` | 85 — Transactional outbox & CDC | `phase-4-part-6.md` | ✅ merged (draft extended §8–17) |

Lessons written this cycle (3 + wrap-up):

```
90  High availability & DR                    ✅ phase-4-part-8.md
91  Active-passive vs active-active           ✅ phase-4-part-8.md
92  RTO and RPO                               ✅ phase-4-part-8.md
+   Phase 4 wrap-up (7-section)               ✅ phase-4-part-8.md
```

Proposed part-file layout (5–6 lessons per file, matching the Phase 1–3 convention):

| File | Lessons |
|---|---|
| `phase-4-part-1.md` ✅ | 64–66 (done) |
| `phase-4-part-2.md` ✅ | 67–69 (done) |
| `phase-4-part-3.md` ✅ | 70–72 (done) |
| `phase-4-part-4.md` ✅ | 73–75 (done) |
| `phase-4-part-5.md` ✅ | 76–79 (done) |
| `phase-4-part-6.md` ✅ | 80–85 (done) |
| `phase-4-part-7.md` ✅ | 86–89 (done) |
| `phase-4-part-8.md` ✅ | 90–92 + Phase 4 wrap-up (done) |

### 4.2 Phase 5 — 14 designs + wrap-up (IN PROGRESS — 3 of 14 done)

| File | Designs | Status |
|---|---|---|
| `phase-5-part-1.md` ✅ | 93 Pastebin · 94 URL shortener · 95 Rate limiter | done |
| `phase-5-part-2.md` ✅ | 96 Notifications · 97 Autocomplete · 98 File storage | done |
| remaining | 99 crawler · 100 chat · 101 news feed · 102 video · 103 ride sharing · 104 e-commerce · 105 payments · 106 metrics platform | 8 designs + wrap-up |

Each design should follow the course's Phase 5 template (requirements → capacity estimation → API → data model → high-level design → deep dives → bottlenecks → evolution). Order: Pastebin, URL shortener, rate limiter, notification service, search autocomplete, file storage, web crawler, real-time chat, news feed, video streaming, ride sharing, e-commerce/inventory, payment processing, metrics & logging.

### 4.3 Hygiene tasks (small, do once)

1. Re-encode `README.md` from UTF-16LE to UTF-8.
2. Regenerate the trailing "Progress checklist" blocks in L40–L54 and L56 from the canonical 24-topic map in `index.md`.
3. Decide the fate of `_chunks/`: prune files whose content is fully merged, keep the four unmerged drafts, add `_chunks/README.md` explaining the distinction.
4. Remove `system-design-course.7z` from the repo (it is a duplicate snapshot) or move it to a release asset.
5. Add a `.gitattributes` entry for `*.md text eol=lf` so the UTF-16 problem cannot recur.

---

## 5. How to verify the claim in §1

```bash
cd system-design-course
grep -ohE "^# Lesson [0-9]+" phase-*.md | grep -oE "[0-9]+" | sort -n | uniq | tr '\n' ' '
# expected now: 1..66 with no gaps
grep -cE "^# Phase [0-9] Wrap-Up" phase-*.md   # 3 wrap-ups (phases 1, 2, 3)
```
