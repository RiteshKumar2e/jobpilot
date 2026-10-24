# JobPilot AI — Multi-Agent Job Search & Application Automation

A personal, production-minded system that **discovers** real jobs from official
job-board APIs, **verifies** eligibility, **scores** fit against your real
profile, **tailors** a truthful resume + cover letter, and **prepares**
applications for your review — with a dashboard, a Google Sheets tracker, and
strict safety controls.

Built for a fresher/entry-level CS candidate targeting ML/AI, computer-vision,
research, and backend roles. **Safe by default: nothing is submitted without
your explicit approval.**

---

## The 8 agents + orchestrator

| # | Agent                         | Does                                                                                     |
| - | ----------------------------- | ---------------------------------------------------------------------------------------- |
| 1 | **Discovery**           | Fetches jobs from official Greenhouse & Lever public APIs (no scraping).                 |
| 2 | **Verification**        | Hard eligibility checks → VERIFIED / NEEDS_REVIEW / EXPIRED / REJECTED.                 |
| 3 | **Matching**            | Transparent 0–100 score (skills 30 / exp 25 / projects 20 / loc 10 / role 10 / comp 5). |
| 4 | **Resume Intelligence** | Reads your real resumes, tailors a truthful ATS one-pager (PDF + editable).              |
| 5 | **Materials**           | Cover letter, recruiter message, safe form answers; asks you for unknowns.               |
| 6 | **Browser Apply**       | Playwright draft-fill; draft-only by default; pauses on CAPTCHA/OTP/login.               |
| 7 | **Sheets Tracker**      | Official Google Sheets API; idempotent upserts; 4 tabs.                                  |
| 8 | **Follow-up / QC**      | Stale/duplicate detection, missing confirmations, reminders, summaries.                  |

The **orchestrator** runs the pipeline with stable job IDs, per-job checkpoints,
idempotent updates (no duplicates on re-run), bounded concurrency, retries with
backoff, crash recovery, and a full audit trail. Any agent can be
enabled/disabled.

```
Discover → Verify → Deduplicate → Score → Select Resume → Tailor Materials
→ Prepare Application → Approval Check → (Submit) → Verify Submission
→ Update Sheets → Follow Up
```

---

## Safety model (two modes)

- **Mode A — SAFE (default):** discover, verify, rank, tailor, draft-fill forms,
  stop at `READY_FOR_REVIEW`. You approve each submission.
- **Mode B — RESTRICTED AUTO:** may auto-submit **only** for sources you
  explicitly allowlist, **only** when the listing is VERIFIED, all mandatory
  answers are known, under the daily cap, and no CAPTCHA/OTP/login appears.

It never bypasses anti-bot protections, never invents facts, never pays fees or
signs anything, and only reports `SUBMITTED_CONFIRMED` when it actually observes
a confirmation.

---

## Truthfulness guarantee

Your real resume files (`data/resumes/`) + `verified: true` profile items are
the **source of truth**. The resume engine:

- drops any skill not found in your real resume,
- marks any `verified: false` project/experience as *unverified — confirm*,
- never alters your CGPA or education,
- flags conflicts instead of guessing.

---

## Tech stack

Python 3.11+ · FastAPI · SQLAlchemy (SQLite → Postgres-ready) · Pydantic ·
APScheduler · Playwright · reportlab · official Google Sheets API ·
pluggable LLM (none / Groq / Gemini — free tiers) · React + Vite dashboard · pytest.

Runs with **zero API keys and zero cost** out of the box.

---

## Quick start

See **`docs/SETUP_WINDOWS.md`** for the full walkthrough. TL;DR:

```powershell
# backend
cd backend
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
copy .env.example .env   # edit JOBPILOT_API_KEY + profile
uvicorn app.main:app --port 8000

# frontend (second window)
cd frontend
npm install ; npm run dev   # http://localhost:5173
```

Then: Settings → paste your API key · add Greenhouse/Lever sources in
`data/profile/candidate_profile.yaml` · **Run discovery cycle**.

---

## Daily use

1. **Run discovery cycle** (or let the scheduler do it on its cron).
2. Open **Jobs**, sorted by match score. Red/amber/green chips show eligibility
   and application status.
3. Click a job → review the tailored resume, cover letter, and any
   **truthfulness flags**.
4. **Approve & submit** (opens a browser to apply) or **Reject**.
5. Track everything in the dashboard and (optionally) the Google Sheet.

---

## Tests

```powershell
cd backend
python -m pytest        # 28 tests: scoring, verification, dedup, status,
                        # discovery parsing, orchestrator, resume + safety gates
```

---

## Project layout

```
jobpilot/
├─ backend/
│  ├─ app/
│  │  ├─ main.py config.py database.py models.py schemas.py
│  │  ├─ orchestrator.py scheduler.py profile_loader.py logging_conf.py
│  │  ├─ agents/  (discovery, verification, matching, resume_intel,
│  │  │           materials, browser_apply, sheets_tracker, followup, base)
│  │  ├─ resume/  (parsing, tailor)
│  │  ├─ llm/     (provider abstraction)
│  │  └─ api/     (routes, deps)
│  ├─ tests/      (+ fixtures/)
│  ├─ requirements.txt  .env.example  pytest.ini
├─ frontend/      (React + Vite dashboard)
├─ data/
│  ├─ profile/candidate_profile.yaml   ← your profile (verify the flagged items)
│  ├─ resumes/    ← drop your real resumes here
│  ├─ tailored/   ← generated resumes + cover letters
│  └─ logs/
├─ docs/  (SETUP_WINDOWS.md, COSTS_AND_LIMITS.md)
├─ run_backend.bat  run_frontend.bat  .gitignore  README.md
```

---

## Important notes

- **Discovery scope:** Greenhouse + Lever only, on purpose. LinkedIn / Naukri /
  Indeed forbid automation — track those manually in the dashboard. See
  `docs/COSTS_AND_LIMITS.md`.
- **Free AI tailoring only:** Groq or Gemini free API tier (or no AI at all).
  No paid LLM provider is wired in. Details in the costs doc.
- **Your profile needs confirming.** Several items ship as `verified: false`
  because they can't be auto-verified. Confirm them against your real
  resume/repos before sending anything.
