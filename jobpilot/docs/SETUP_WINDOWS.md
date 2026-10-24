# JobPilot AI — Windows 11 Setup

This gets JobPilot running on your own Windows 11 machine. Budget ~20 minutes
for the first run. You can run the whole thing with **zero paid services** (no
LLM key, no Google) and add those later.

---

## 0. What you need

- **Python 3.11+** — https://www.python.org/downloads/ (tick "Add python.exe to PATH" during install)
- **Node.js 18+** — https://nodejs.org/ (LTS)
- A browser (Chrome/Edge) for the apply step
- Optional later: a free Groq or Gemini API key, a Google account

Check in PowerShell:

```powershell
python --version
node --version
```

---

## 1. Get the project & create a virtual environment

```powershell
cd $HOME\Downloads\jobpilot          # wherever you unzipped it
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # you should see (.venv) in the prompt
```

> If PowerShell blocks the activate script:
> `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned` then retry.

Install backend dependencies:

```powershell
pip install -r requirements.txt
```

Install the browser engine (only needed for the apply step):

```powershell
playwright install chromium
```

---

## 2. Configure

```powershell
copy .env.example .env
notepad .env
```

Minimum to change:
- `JOBPILOT_API_KEY` → set a long random string (protects your dashboard/API).
  Generate one: `python -c "import secrets; print(secrets.token_urlsafe(32))"`
- Leave `JOBPILOT_APPROVAL_MODE=SAFE` (recommended).
- Leave `JOBPILOT_LLM_PROVIDER=none` for now (works without any AI key).

Then fill in your **real profile**:

```powershell
notepad ..\data\profile\candidate_profile.yaml
```
- Add your email/phone.
- Set each `verified: false` item to `true` **only after** you confirm it
  matches your real resume/repos. Anything left `false` is treated as
  "flag for review" and marked unverified on tailored resumes.
- Add the companies you want to track under `sources:` (see §5).

Drop your real resume files into `data\resumes\` (PDF/DOCX/LaTeX). The system
reads them as the source of truth and will not put any skill on a tailored
resume that isn't in your real resume.

---

## 3. Run the backend

From `backend\` with the venv active:

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Leave this window open. Check http://127.0.0.1:8000/api/health → `{"status":"ok"}`.
Interactive API docs: http://127.0.0.1:8000/docs

---

## 4. Run the dashboard (second PowerShell window)

```powershell
cd $HOME\Downloads\jobpilot\frontend
npm install
npm run dev
```

Open http://localhost:5173 → go to **Settings** → paste the same
`JOBPILOT_API_KEY` value → Save.

---

## 5. Add real job sources (official public APIs — no scraping)

JobPilot pulls from the **official** Greenhouse and Lever job-board APIs. You
list the companies you want to follow by their board token / account slug.

How to find them:
- **Greenhouse**: if a company's careers page is `boards.greenhouse.io/COMPANY`,
  the token is `COMPANY`. Test: `https://boards-api.greenhouse.io/v1/boards/COMPANY/jobs`
- **Lever**: if postings live at `jobs.lever.co/COMPANY`, the slug is `COMPANY`.
  Test: `https://api.lever.co/v0/postings/COMPANY?mode=json`

Put them in `candidate_profile.yaml`:

```yaml
sources:
  greenhouse_boards: ["razorpay", "postman"]
  lever_accounts: ["swiggy"]
```

Save, then in the dashboard click **Run discovery cycle** (or POST
`/api/cycle/run`). Jobs appear under **Jobs**, scored and verified.

---

## 6. (Optional) Enable AI tailoring

Free: get a Groq key (https://console.groq.com/keys) or a Gemini key
(https://aistudio.google.com/apikey), then in `.env` set
`JOBPILOT_LLM_PROVIDER=groq` + `GROQ_API_KEY=...` (or `gemini` +
`GEMINI_API_KEY=...`) and restart the backend. Free-tier limits are covered in
`COSTS_AND_LIMITS.md`. Without this, tailoring uses a free deterministic template.

---

## 7. (Optional) Enable Google Sheets tracker

1. Install the Google libraries (already in requirements.txt).
2. Google Cloud Console → create a project → enable **Google Sheets API**.
3. Create **OAuth client ID** (type: Desktop app) → download the JSON.
4. Save it as `data\google_credentials.json`.
5. Restart backend. On the first sheets sync a browser window asks you to
   authorize; a token is cached to `data\google_token.json`.
6. The app creates "JobPilot — Application Tracker". Copy its ID into
   `JOBPILOT_SHEET_ID` in `.env` to reuse it.

---

## 8. Applying to jobs

- **SAFE mode (default):** open a job in the dashboard → review the tailored
  resume, cover letter, and truthfulness flags → click **Approve & submit**.
  A real browser opens, fills the form, uploads your resume, and **stops for
  your review / submits only the single approved application**.
- If the site shows a CAPTCHA/login/OTP, JobPilot **pauses** and marks the job
  `ACTION_REQUIRED` — you finish that one by hand. It never bypasses these.

See `README.md` → "Daily use" and `COSTS_AND_LIMITS.md` → "Limits".

---

## Quick-start scripts

From the project root you can also just double-click / run:
- `run_backend.bat` — sets up venv on first run and starts the API
- `run_frontend.bat` — installs and starts the dashboard

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `uvicorn: command not found` | venv not active → `.\.venv\Scripts\Activate.ps1` |
| Dashboard shows `401` | Settings → paste the `JOBPILOT_API_KEY` from `.env` |
| `playwright` launch error | run `playwright install chromium`; keep `JOBPILOT_BROWSER_HEADLESS=false` on desktop |
| No jobs after a cycle | add real tokens under `sources:` in the profile, then re-run |
| Greenhouse/Lever 404 | that company isn't on that board; verify the test URL in §5 |
| Sheets disabled | `google_credentials.json` missing or libraries not installed |
| Port 8000 busy | run uvicorn with `--port 8001` and update `vite.config.js` proxy target |

## Hands-free auto-apply (Mode B) + email

`.env` is set to `JOBPILOT_APPROVAL_MODE=RESTRICTED_AUTO` with
`JOBPILOT_AUTO_APPLY_ALLOWLIST=greenhouse,lever`. Every 6 hours the agents
discover → verify → score → pick one of your resumes in `data/resumes/` →
open the real application form (headless Chrome) → fill it → submit.

An application is only submitted when every **required** question has a true
answer from `data/profile/candidate_profile.yaml` (`candidate:` +
`application_answers:`). Otherwise it stops at ACTION_REQUIRED, you get one
email listing the links, and it retries by itself after you edit the profile.
CAPTCHAs are never bypassed.

Email: set `JOBPILOT_SMTP_PASSWORD` in `backend/.env` to a Gmail **App
Password** (https://myaccount.google.com/apppasswords, needs 2-Step
Verification). Companies also send their own confirmation to `candidate.email`.

Caps: `JOBPILOT_DAILY_AUTOSUBMIT_LIMIT` (15/day) and
`JOBPILOT_MIN_AUTOSUBMIT_SCORE` (55).
