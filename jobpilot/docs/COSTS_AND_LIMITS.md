# JobPilot AI — Costs, Limits & Honest Caveats

## Recurring costs

| Component | Cost | Notes |
|---|---|---|
| The app itself | **₹0** | Runs locally on your PC. |
| Job discovery (Greenhouse/Lever APIs) | **₹0** | Official public endpoints, no key. |
| Google Sheets tracker | **₹0** | Free Google account + free Sheets API quota. |
| LLM tailoring (`provider=none`) | **₹0** | Deterministic templates. Fully functional. |
| LLM tailoring (`groq`/`gemini`) | **₹0 (free tier)** | Free API key, rate-limited. Recommended. |

### AI option: Groq or Gemini (free)

Both have a free API tier — no card needed:

| Provider | Get a key | `.env` |
|---|---|---|
| Groq | https://console.groq.com/keys | `JOBPILOT_LLM_PROVIDER=groq`, `GROQ_API_KEY=...` |
| Gemini | https://aistudio.google.com/apikey | `JOBPILOT_LLM_PROVIDER=gemini`, `GEMINI_API_KEY=...` |

Caveats:
- Free tiers are **rate-limited** (requests/minute and per day). JobPilot retries
  429s with backoff; if a call still fails, that item silently uses the free
  rule-based template and the event log notes it. Nothing breaks.
- Free-tier limits and model names change — check the provider's rate-limit
  page and override `JOBPILOT_GROQ_MODEL` / `JOBPILOT_GEMINI_MODEL` if needed.
- Gemini's free tier may use your prompts to improve Google's models. Prompts
  contain your resume content and the job description. If that matters, use Groq.

Only free providers are supported, so JobPilot never runs up an AI bill. Paid
APIs (Anthropic, OpenAI) were removed on purpose. Note that a Claude Pro or
ChatGPT Plus chat subscription does not include API access anyway.

---

## Hard limits & safety caps (configurable in `.env`)

- `JOBPILOT_DAILY_APPLICATION_LIMIT` (default 15) — max applications *prepared*
  per day.
- `JOBPILOT_DAILY_AUTOSUBMIT_LIMIT` (default 0) — max *auto-submitted* per day
  (Mode B only). Default 0 means: never auto-submit until you deliberately raise it.
- `JOBPILOT_MAX_CONCURRENCY` (default 4) — bound on parallel work.

---

## What JobPilot will NOT do (by design)

- It won't **bypass CAPTCHA, OTP, logins, or anti-bot checks** — it pauses and
  asks you.
- It won't **invent** skills, experience, dates, CGPA, publications, or metrics.
  Unverifiable claims are dropped or flagged, never presented as fact.
- It won't **auto-submit** anything in SAFE mode (the default).
- It won't **pay fees, accept offers, sign contracts**, or answer sensitive
  personal/legal questions on your behalf.
- It won't **send recruiter messages/emails** automatically.

---

## Known limitations (be realistic)

1. **Discovery breadth.** Greenhouse + Lever cover a large share of startup/tech
   roles but not everything. LinkedIn, Naukri, and Indeed **prohibit automated
   access** in their terms — JobPilot deliberately does not scrape them. Use
   their own alerts and apply to those by hand, tracking them in the dashboard.
2. **Form variety.** Application forms differ wildly. The browser agent fills
   common fields (name/email/phone/links/resume) on standard forms and **always
   stops for your review** before anything is submitted in SAFE mode. Unusual
   forms may need manual completion — it will tell you.
3. **"Open" status.** A listing can close between discovery and your
   application. JobPilot re-checks on the next cycle but can't guarantee
   real-time accuracy.
4. **No guarantee of eligibility.** The verifier is conservative but
   heuristic. Treat `VERIFIED` as "looks eligible — worth a look", not a legal
   guarantee. Always skim the posting yourself.
5. **Resume quality depends on your inputs.** With no resume file in
   `data/resumes/`, tailoring falls back to your profile YAML and flags it. Add
   your real resumes for good results.
6. **Account risk is yours.** Automating submissions on sites that disallow it
   can get *your* account restricted. Mode B is off by default and allowlisted
   per-source for this reason.

---

## Data & privacy

- Everything stays local: SQLite DB, resumes, logs, browser profile — all on
  your machine.
- Secrets live only in `.env` / credential JSONs, which are git-ignored.
- Logs are redacted for anything that looks like a key/token/password.
- The dashboard/API require your `JOBPILOT_API_KEY`.
