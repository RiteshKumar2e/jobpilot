# JobPilot AI

**A multi-agent system that finds entry-level jobs, picks the right resume, and applies for you — truthfully, within safe limits, with an email for every application.**

JobPilot discovers openings from the official Greenhouse and Lever job-board APIs, checks each one against hard eligibility rules, scores it against your profile, chooses the best of your own resumes, fills in the real application form in a headless browser, and submits it when every required answer is known. Anything it can't answer honestly is emailed to you with a link instead of being guessed.

![Python](https://img.shields.io/badge/python-3.11%2B-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-backend-009688) ![React](https://img.shields.io/badge/React-Vite-61dafb) ![Playwright](https://img.shields.io/badge/Playwright-Chromium-2ead33) ![Cost](https://img.shields.io/badge/running%20cost-%E2%82%B90-success)

---

## Contents

- [Features](#features)
- [How it works](#how-it-works)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running](#running)
- [Optional integrations](#optional-integrations)
- [Using the dashboard](#using-the-dashboard)
- [Safety model](#safety-model)
- [Testing](#testing)
- [Project structure](#project-structure)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)

---

## Features

- **Hands-free applying** — Greenhouse and Lever forms are filled and submitted automatically, up to a daily cap.
- **Your own resumes** — keep several role-specific variants in `data/resumes/`; the best one for each role is uploaded.
- **Hard eligibility rules** — rejects senior roles, internships, roles needing more experience than you have, and onsite/hybrid jobs outside India (remote jobs anywhere are accepted).
- **Transparent scoring** — 0–100 match score with a per-component breakdown.
- **Email notifications** — one email per submitted application, plus a digest of applications that need your input.
- **Google Sheets tracker** — pipeline, application history, interviews and a dashboard tab, kept in sync.
- **Free AI** — optional Groq or Gemini (free tiers) for cover letters; works fully without any AI key.
- **Resilient** — checkpointed pipeline, crash recovery, idempotent re-runs, no duplicate applications.

## How it works

Nine agents run as one pipeline, orchestrated on a schedule (every 6 hours by default):

```
Discover → Verify → Score → Select resume → Prepare materials → Fill form → Submit → Notify → Track
```

| # | Agent | Responsibility |
|---|---|---|
| 1 | **Discovery** | Fetches listings from official Greenhouse and Lever public APIs. |
| 2 | **Verification** | Applies hard eligibility rules → `VERIFIED` / `NEEDS_REVIEW` / `REJECTED`. |
| 3 | **Matching** | Scores fit: skills 30 · experience 25 · projects 20 · location 10 · role 10 · compensation 5. |
| 4 | **Resume Intelligence** | Labels your resumes (GenAI, ML, Backend, …) and picks the closest one per job. |
| 5 | **Materials** | Cover letter and form answers built only from your profile. |
| 6 | **Browser Apply** | Opens the real form in headless Chromium, uploads your resume, fills known fields, submits, and verifies the confirmation page. |
| 7 | **Sheets Tracker** | Keeps the Google Sheet up to date. |
| 8 | **Follow-up / QC** | Flags stale or unconfirmed applications and duplicates. |
| 9 | **Notifier** | Sends the "Applied" and "needs you" emails. |

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Windows | 10 / 11 | Launch scripts are `.bat`; the code itself is cross-platform. |
| Python | 3.11+ | [python.org/downloads](https://www.python.org/downloads/) |
| Node.js | 18+ (LTS) | [nodejs.org](https://nodejs.org/) |
| Gmail account | — | With 2-Step Verification, for notification emails. |

## Installation

### 1. Clone the repository

```powershell
git clone https://github.com/RiteshKumar2e/jobpilot.git
cd jobpilot
```

### 2. Set up the backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
copy .env.example .env
```

> Everything is installed inside `backend\.venv`; your system Python is not modified.
> Alternatively, just run `run_backend.bat` from the project root — it does all of the above on first launch.

### 3. Set up the frontend

```powershell
cd ..\frontend
npm install
```

---

## Configuration

### 1. Environment — `backend/.env`

Generate an API key for the dashboard:

```powershell
cd backend
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

| Variable | Example | Purpose |
|---|---|---|
| `JOBPILOT_API_KEY` | *(generated above)* | Protects the API; pasted into the dashboard's Settings. |
| `JOBPILOT_APPROVAL_MODE` | `RESTRICTED_AUTO` | `SAFE` = prepare only, you approve each one. `RESTRICTED_AUTO` = auto-submit. |
| `JOBPILOT_AUTO_APPLY_ALLOWLIST` | `greenhouse,lever` | Sources allowed to auto-submit. |
| `JOBPILOT_DAILY_AUTOSUBMIT_LIMIT` | `15` | Max automatic submissions per day. |
| `JOBPILOT_MIN_AUTOSUBMIT_SCORE` | `55` | Only auto-submit jobs scoring at least this. |
| `JOBPILOT_BROWSER_HEADLESS` | `true` | Run the browser in the background. |
| `JOBPILOT_NOTIFY_EMAIL` | `you@gmail.com` | Where notification emails go. |
| `JOBPILOT_SMTP_USER` | `you@gmail.com` | Gmail account that sends them. |
| `JOBPILOT_SMTP_PASSWORD` | *(App Password)* | See [Email notifications](#email-notifications). |
| `JOBPILOT_LLM_PROVIDER` | `groq` | `none`, `groq` or `gemini`. |
| `GROQ_API_KEY` / `GEMINI_API_KEY` | | Only for the chosen provider. |
| `JOBPILOT_DISCOVERY_CRON` | `0 */6 * * *` | How often the pipeline runs. |

`.env` is git-ignored. Never commit it.

### 2. Profile — `data/profile/candidate_profile.yaml`

This file is the single source of truth for everything submitted on your behalf.

```yaml
candidate:
  name: "Your Name"
  email: "you@gmail.com"      # companies send confirmations here
  phone: "+91XXXXXXXXXX"      # required by most forms
  city: "Jamshedpur"          # Lever forms require a city
```

Also review:

- **`application_answers`** — standing answers to common form questions, matched by regex against the question text. Uncomment only the ones that are **true** for you (work authorization, sponsorship, notice period); each one unlocks many more automatic applications.
- **`sources`** — the Greenhouse board tokens and Lever account names to watch. A company at `boards.greenhouse.io/acme` has token `acme`; one at `jobs.lever.co/acme` has account `acme`.
- **`target_roles`, `exclude`, `locations`** — what counts as a match.

Profile edits take effect on the next run — no restart needed.

### 3. Resumes — `data/resumes/`

Put your resume PDFs here (git-ignored). Name each by its focus so the right one is chosen:

```
Resume_GENAI.pdf   Resume_ML.pdf   Resume_AI_ML.pdf   Resume_SD.pdf   Resume_FullStack.pdf
```

New or replaced files are picked up automatically.

---

## Running

### One click

Double-click **`start_jobpilot.bat`** in the project root. It starts the backend and the dashboard in minimized windows, waits until both are ready, and opens http://localhost:5173. On start-up the backend immediately runs a full cycle — applies to queued jobs, discovers new ones, and syncs Google Sheets — then repeats every 6 hours (`JOBPILOT_DISCOVERY_CRON`).

| Script | What it does |
|---|---|
| `start_jobpilot.bat` | Start everything (safe to click again; running parts are skipped). |
| `stop_jobpilot.bat` | Stop the backend, dashboard and browser. |
| `autostart_jobpilot.bat` | Start JobPilot automatically at Windows login. `autostart_jobpilot.bat off` undoes it. |

Set `JOBPILOT_RUN_ON_STARTUP=false` in `backend/.env` to wait for the schedule instead of running a cycle at start-up.

### Separately

```powershell
# Terminal 1 — backend (http://127.0.0.1:8000)
.\run_backend.bat

# Terminal 2 — dashboard (http://localhost:5173)
.\run_frontend.bat
```

Or manually:

```powershell
cd backend;  .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
cd frontend; npm run dev
```

On first use, open **Settings** in the dashboard and paste your `JOBPILOT_API_KEY`. Keep the backend window open (minimized is fine) — automation stops when it closes.

Health check: http://127.0.0.1:8000/api/health · API docs: http://127.0.0.1:8000/docs

---

## Optional integrations

### Email notifications

1. Turn on 2-Step Verification for your Google account.
2. Create an App Password at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).
3. Put the 16-character password in `JOBPILOT_SMTP_PASSWORD` and restart the backend.

### AI cover letters (free)

| Provider | Get a key | `.env` |
|---|---|---|
| Groq | [console.groq.com/keys](https://console.groq.com/keys) | `JOBPILOT_LLM_PROVIDER=groq` · `GROQ_API_KEY=…` |
| Gemini | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | `JOBPILOT_LLM_PROVIDER=gemini` · `GEMINI_API_KEY=…` |

Rate-limited calls are retried; if they still fail, a built-in template is used. Note that Gemini's free tier may use prompts to improve Google's models.

### Google Sheets tracker

1. In [Google Cloud Console](https://console.cloud.google.com), create a project and enable the **Google Sheets API**.
2. Under [Google Auth Platform → Audience](https://console.cloud.google.com/auth/audience), choose **External** and add your Gmail as a **Test user**.
3. Under **Credentials → Create credentials → OAuth client ID**, choose **Desktop app** and download the JSON.
4. Save it as `data/google_credentials.json` and restart the backend.
5. Approve the sign-in page that opens (choose **Advanced → Go to JobPilot** on the "unverified app" screen).

The sheet **"JobPilot — Application Tracker"** is created in your Google Drive, and a link to it appears in the dashboard banner.

---

## Using the dashboard

| View | What you can do |
|---|---|
| **Overview** | Totals: discovered, verified, shortlisted, submitted, rejected. |
| **Jobs** | Every job with score and status; open one to see the resume and cover letter used, then **Approve** or **Reject**. |
| **Activity & Logs** | Live event log of every agent and every application attempt. |
| **Settings** | API key, schedule, pause/resume automation, reload profile. |

Application statuses: `READY_FOR_REVIEW` → `SUBMITTED_CONFIRMED` (confirmation page seen) · `SUBMISSION_UNVERIFIED` (submitted, no confirmation page) · `ACTION_REQUIRED` (needs your input — you were emailed) · `FAILED`.

---

## Safety model

| | SAFE | RESTRICTED_AUTO |
|---|---|---|
| Discovers, verifies, scores, prepares | ✅ | ✅ |
| Submits applications | Only when you click **Approve** | Automatically, within limits |

An automatic submission happens only when **all** of these hold:

- the source is on the allowlist and the listing is `VERIFIED`;
- the match score is at least `JOBPILOT_MIN_AUTOSUBMIT_SCORE`;
- every **required** form field has an answer from your profile;
- the daily cap has not been reached;
- no CAPTCHA, OTP or login wall appears.

JobPilot never bypasses anti-bot checks, never invents skills, experience or answers, never pays fees or signs anything, and makes one automatic attempt per job. A job that stopped only for a missing answer retries by itself after you update your profile.

---

## Testing

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q
```

Covers parsing, verification rules, scoring, resume selection, deduplication, status ordering, the auto-submit policy, LLM fallbacks and notifications. Tests use an isolated temporary database.

---

## Project structure

```
jobpilot/
├── backend/
│   ├── app/
│   │   ├── agents/          # discovery, verification, matching, resume_intel, materials,
│   │   │                    # browser_apply, sheets_tracker, followup, notifier
│   │   ├── api/             # REST routes and auth
│   │   ├── llm/             # Groq / Gemini / rule-based providers
│   │   ├── resume/          # resume parsing and tailoring
│   │   ├── orchestrator.py  # pipeline, checkpoints, recovery
│   │   ├── scheduler.py     # recurring runs
│   │   └── main.py          # FastAPI entry point
│   ├── tests/
│   ├── requirements.txt
│   └── .env.example
├── frontend/                # React + Vite dashboard
├── data/
│   ├── profile/candidate_profile.yaml
│   ├── resumes/             # your resumes (git-ignored)
│   ├── tailored/            # generated cover letters (git-ignored)
│   └── logs/                # logs and screenshots (git-ignored)
├── run_backend.bat
└── run_frontend.bat
```

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Dashboard shows **500** or `ECONNREFUSED 127.0.0.1:8000` | The backend isn't running. Start it and keep its terminal open. |
| **401** on actions | The API key in Settings doesn't match `JOBPILOT_API_KEY`. |
| Jobs stuck at `ACTION_REQUIRED` | A required question has no answer — fill `phone`/`city` or uncomment the matching `application_answers`. |
| "Daily cap reached" | `JOBPILOT_DAILY_AUTOSUBMIT_LIMIT` hit; the rest continue tomorrow. |
| No emails | `JOBPILOT_SMTP_PASSWORD` must be an App Password, not your Gmail password. Check Spam. |
| Google "Access blocked" | Add your Gmail as a Test user (see [Google Sheets tracker](#google-sheets-tracker)). |
| `No Python at '...'` from `.venv` | The project folder was moved; delete `backend\.venv` and run `run_backend.bat` again. |

---

## Limitations

- Only Greenhouse and Lever are supported. LinkedIn, Naukri and Indeed prohibit automation.
- Essay-style questions and CAPTCHAs always require you; those applications are emailed rather than submitted.
- Form layouts vary by company, so some applications will need a manual finish.
- `SUBMISSION_UNVERIFIED` means the form was submitted but no confirmation page was detected — check your inbox for the company's email.
