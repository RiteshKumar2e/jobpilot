"""Agent 6 — Browser Application (Playwright).

SAFETY-FIRST DESIGN. Behaviour:

  * Mode A (SAFE, default): fills a draft, screenshots the completed-but-
    unsubmitted form, and stops at READY_FOR_REVIEW.
  * Mode B (RESTRICTED_AUTO): may click submit ONLY when ALL hold:
      - the job's source is on JOBPILOT_AUTO_APPLY_ALLOWLIST
      - the listing is VERIFIED and scores >= JOBPILOT_MIN_AUTOSUBMIT_SCORE
      - every REQUIRED form field got a truthful answer (from your profile or
        your `application_answers`) — otherwise it stops at ACTION_REQUIRED
      - the daily autosubmit cap is not exceeded
      - no CAPTCHA / OTP / login wall appeared
    If any of these fail it PAUSES -> ACTION_REQUIRED and you get an email.

It never bypasses CAPTCHA/OTP/auth, never invents answers, never pays fees, and
reports an honest outcome (SUBMITTED_CONFIRMED only when the site's own
confirmation page is actually observed).

Playwright is optional: without it the agent returns ACTION_REQUIRED telling
you to `playwright install chromium`, so the app still runs and tests pass.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from ..config import ApprovalMode, get_settings
from ..models import ApplicationStatus
from .base import log_event

# Login/verification walls, checked against VISIBLE text (the raw HTML of almost
# every Greenhouse page mentions "recaptcha" in a script tag).
_WALL_SIGNALS = ["verify you are human", "are you a robot", "sign in to continue",
                 "log in to apply", "one-time password", "enter the otp",
                 "two-factor", "security check"]

# Required privacy / data-processing acknowledgements (profile: auto_acknowledge_privacy)
_ACK_RE = re.compile(r"acknowledg|privacy (notice|policy|statement)|i (have read|consent|agree)"
                     r"|consent to (the )?(processing|collection)|data protection", re.I)
_ACK_OPTION_RE = re.compile(r"^\s*(yes|i (acknowledge|agree|consent|accept|have read)"
                            r"|acknowledge|agree|accept)", re.I)

_CONFIRM_TEXT = ["thank you for applying", "application received", "application submitted",
                 "successfully submitted", "we received your application",
                 "thanks for applying", "we've received your application"]

# Question text for a field: explicit <label for>, aria, then the nearest
# question container (Lever: .application-label, Greenhouse: label/legend).
_LABEL_OF_JS = r"""e => {
  let l = '';
  if (e.id) { const x = document.querySelector(`label[for="${CSS.escape(e.id)}"]`); if (x) l = x.innerText; }
  if (!l && e.getAttribute('aria-labelledby')) {
    const x = document.getElementById(e.getAttribute('aria-labelledby').split(' ')[0]); if (x) l = x.innerText; }
  if (!l && e.getAttribute('aria-label')) l = e.getAttribute('aria-label');
  if (!l) {
    const box = e.closest('.application-question, fieldset, .field, li');
    if (box) for (const sel of ['.application-label', 'legend', 'label', '.text']) {
      const x = box.querySelector(sel); if (x && x.innerText.trim()) { l = x.innerText; break; } }
  }
  return (l || '').replace(/\s+/g, ' ').replace(/[*✱]/g, '').trim();
}"""

# Tags every visible, fillable field with data-jp=<n> and describes it.
_SCAN_JS = r"""() => {
  const out = [];
  const els = [...document.querySelectorAll('input, textarea, select')];
  els.forEach((e, i) => {
    const t = (e.type || '').toLowerCase();
    if (['hidden', 'file', 'submit', 'button', 'search'].includes(t)) return;
    if (!(e.offsetWidth || e.offsetHeight || e.getClientRects().length)) return;
    e.setAttribute('data-jp', String(i));
    const label = (LABEL_OF)(e) || e.placeholder || '';
    out.push({jp: String(i), tag: e.tagName.toLowerCase(), type: t,
              name: e.name || '', id: e.id || '',
              combo: e.getAttribute('role') === 'combobox' || !!e.getAttribute('aria-autocomplete'),
              required: e.required || e.getAttribute('aria-required') === 'true',
              value: (e.value || '').trim(),
              label: label.slice(0, 160)});
  });
  return out;
}""".replace("LABEL_OF", _LABEL_OF_JS)

# Labels of REQUIRED visible fields that are still empty.
_MISSING_JS = r"""() => {
  const missing = new Set();
  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const labelOf = e => ((LABEL_OF)(e) || e.name || e.id || 'unnamed field').slice(0, 120);
  const groups = {};
  document.querySelectorAll('input, textarea, select').forEach(e => {
    const req = e.required || e.getAttribute('aria-required') === 'true';
    const t = (e.type || '').toLowerCase();
    if (!req || t === 'hidden' || t === 'file' || !vis(e)) return;
    if (t === 'checkbox' || t === 'radio') { (groups[e.name] = groups[e.name] || []).push(e); return; }
    if (e.getAttribute('role') === 'combobox') {
      const box = e.closest('.select__control, [class*="select__control"], .select-shell');
      if (box && box.querySelector('[class*="single-value"], [class*="multi-value"]')) return;
    }
    if (!(e.value || '').trim()) missing.add(labelOf(e));
  });
  Object.values(groups).forEach(g => { if (!g.some(x => x.checked)) missing.add(labelOf(g[0])); });
  return [...missing];
}""".replace("LABEL_OF", _LABEL_OF_JS)


@dataclass
class ApplyOutcome:
    status: str
    detail: dict = field(default_factory=dict)
    screenshot: str = ""
    error: str = ""


def form_url(job) -> str:
    """Greenhouse's hosted form works for every board, even when the posting's
    absolute_url points at the company's own careers site."""
    if (getattr(job, "source", "") == "greenhouse" and getattr(job, "source_id", "")
            and getattr(job, "company", "")):
        return (f"https://job-boards.greenhouse.io/embed/job_app"
                f"?for={job.company}&token={job.source_id}")
    return getattr(job, "application_url", "") or getattr(job, "job_url", "")


class BrowserApplyAgent:
    name = "browser_apply"

    def __init__(self, profile):
        self.profile = profile
        self.settings = get_settings()

    # ---- policy gate (pure, unit-tested) --------------------------------- #
    def may_autosubmit(self, job, materials, autosubmit_count_today: int) -> tuple[bool, str]:
        s = self.settings
        if s.approval_mode != ApprovalMode.RESTRICTED_AUTO:
            return False, "Mode A (SAFE): submission always requires your approval."
        if job.source.lower() not in s.auto_apply_sources and \
           getattr(job, "company", "").lower() not in s.auto_apply_sources:
            return False, f"Source '{job.source}' not on auto-apply allowlist."
        if job.eligibility_status != "VERIFIED":
            return False, "Listing not VERIFIED."
        score = getattr(job, "match_score", None)
        if score is not None and score < s.min_autosubmit_score:
            return False, f"Score {score} below auto-submit minimum {s.min_autosubmit_score}."
        # Missing phone etc. is caught live by the required-field check; without
        # an email the company can't even reach you, so never send one.
        if materials and "email" in (materials.unknown_fields or []):
            return False, "No email in your profile (candidate.email)."
        if autosubmit_count_today >= s.daily_autosubmit_limit:
            return False, "Daily auto-submit cap reached."
        return True, "Auto-submit policy satisfied."

    # ---- answers --------------------------------------------------------- #
    def answer_rules(self, materials, cover_text: str = "") -> List[tuple]:
        """(regex over label/name/id, value). First match wins, so specific
        rules come before generic ones. Only facts you gave us — never guesses."""
        raw = self.profile.raw()
        cand = raw.get("candidate", {}) or {}
        fa = (materials.form_answers if materials else {}) or {}
        full = (fa.get("full_name") or cand.get("name") or "").strip()
        first, _, last = full.partition(" ")
        edu = (raw.get("education") or [{}])[0]
        rules = [
            (r"preferred.*name", first),
            (r"first.?name|given.?name", first),
            (r"last.?name|surname|family.?name", last),
            (r"^name$|full.?name|your name|^name\b", full),
            (r"e-?mail", fa.get("email") or cand.get("email", "")),
            (r"phone|mobile|contact number", fa.get("phone") or cand.get("phone", "")),
            (r"linkedin", fa.get("linkedin") or cand.get("linkedin", "")),
            (r"github", fa.get("github") or cand.get("github", "")),
            (r"website|portfolio|personal (site|url)|urls\[portfolio\]|urls\[other\]",
             fa.get("portfolio") or cand.get("website", "")),
            (r"school|university|college|institution", edu.get("institution", "")),
            (r"^(location|current location|city)\b", cand.get("city", "")),
            (r"^country\b", cand.get("location", "")),
            (r"cover letter|additional information|^comments$", cover_text),
        ]
        # Your own standing answers (candidate_profile.yaml -> application_answers)
        custom = [(a.get("match", ""), str(a.get("answer", "")))
                  for a in (raw.get("application_answers") or []) if a.get("match")]
        # custom first: they're more specific than the generic rules above
        return [(re.compile(p, re.I), v) for p, v in custom + rules if v]

    # ---- main entry ------------------------------------------------------- #
    def apply(self, db, job, resume_pdf: str, cover_path: str,
              materials, autosubmit_count_today: int = 0,
              approved: bool = False) -> ApplyOutcome:
        try:
            from playwright.sync_api import sync_playwright  # noqa
        except Exception:
            msg = ("Playwright not installed. Run `pip install playwright` then "
                   "`playwright install chromium` to enable browser applying.")
            log_event(db, level="warn", agent=self.name, message=msg,
                      job_id=getattr(job, "job_id", ""))
            return ApplyOutcome(status=ApplicationStatus.ACTION_REQUIRED.value,
                                error=msg, detail={"reason": "playwright_missing"})

        url = form_url(job)
        if not url:
            return ApplyOutcome(status=ApplicationStatus.FAILED.value,
                                error="No application URL on job.")

        allow_submit, why = self.may_autosubmit(job, materials, autosubmit_count_today)
        if approved:
            # You pressed Approve: submit, still never past a CAPTCHA or an empty required field.
            allow_submit, why = True, "Approved by you."
        return self._run_playwright(db, job, url, resume_pdf, cover_path,
                                    materials, allow_submit, why)

    def _run_playwright(self, db, job, url, resume_pdf, cover_path,
                        materials, allow_submit, why) -> ApplyOutcome:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        s = self.settings
        shot_dir = Path(s.log_dir) / "screenshots"
        shot_dir.mkdir(parents=True, exist_ok=True)
        shot = str(shot_dir / f"{getattr(job,'job_id','x')}_{int(datetime.now().timestamp())}.png")
        cover_text = ""
        if cover_path and Path(cover_path).exists():
            cover_text = Path(cover_path).read_text(encoding="utf-8", errors="ignore")

        with sync_playwright() as pw:
            try:
                ctx = pw.chromium.launch_persistent_context(
                    user_data_dir=str(s.browser_profile_dir),
                    headless=s.browser_headless,
                    args=["--disable-blink-features=AutomationControlled",
                          "--no-sandbox", "--disable-dev-shm-usage"],
                )
            except Exception as e:
                msg = f"Browser failed to launch ({e})."
                log_event(db, level="error", agent=self.name, message=msg,
                          job_id=getattr(job, "job_id", ""))
                return ApplyOutcome(status=ApplicationStatus.ACTION_REQUIRED.value,
                                    error=msg, detail={"reason": "browser_launch_failed"})
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=s.http_timeout * 1000)
                page.wait_for_timeout(2500)

                wall = self._wall(page)
                if wall:
                    page.screenshot(path=shot)
                    return ApplyOutcome(
                        status=ApplicationStatus.ACTION_REQUIRED.value, screenshot=shot,
                        detail={"challenge": wall, "url": page.url},
                        error=f"Human verification required ({wall}). Paused — not bypassed.")

                detail = {"filled": [], "skipped": [], "url": page.url}
                # Upload first: Lever parses the resume and overwrites fields.
                self._upload(page, resume_pdf, cover_path, detail)
                self._fill(page, self.answer_rules(materials, cover_text), detail)

                missing = page.evaluate(_MISSING_JS)
                detail["missing"] = missing
                page.screenshot(path=shot, full_page=True)

                if missing:
                    return ApplyOutcome(
                        status=ApplicationStatus.ACTION_REQUIRED.value, screenshot=shot,
                        detail=detail,
                        error="Needs your answer: " + "; ".join(missing[:5]))
                if not allow_submit:
                    return ApplyOutcome(status=ApplicationStatus.READY_FOR_REVIEW.value,
                                        screenshot=shot, detail={**detail, "policy": why})

                # Mode B: a single submit, then VERIFY.
                if not self._attempt_submit(page):
                    return ApplyOutcome(status=ApplicationStatus.FAILED.value,
                                        screenshot=shot, detail=detail,
                                        error="Could not locate a submit control.")
                confirmed, ref = self._wait_confirmation(page)
                page.screenshot(path=shot)
                if confirmed:
                    return ApplyOutcome(status=ApplicationStatus.SUBMITTED_CONFIRMED.value,
                                        screenshot=shot, detail={**detail, "reference": ref})
                if self._captcha_visible(page):
                    return ApplyOutcome(status=ApplicationStatus.ACTION_REQUIRED.value,
                                        screenshot=shot, detail=detail,
                                        error="CAPTCHA appeared on submit. Paused — not bypassed.")
                still_missing = page.evaluate(_MISSING_JS)
                if still_missing:
                    return ApplyOutcome(status=ApplicationStatus.ACTION_REQUIRED.value,
                                        screenshot=shot, detail={**detail, "missing": still_missing},
                                        error="Form rejected: " + "; ".join(still_missing[:5]))
                return ApplyOutcome(status=ApplicationStatus.SUBMISSION_UNVERIFIED.value,
                                    screenshot=shot, detail=detail,
                                    error="Clicked submit but no confirmation observed.")
            except PWTimeout as e:
                return ApplyOutcome(status=ApplicationStatus.FAILED.value,
                                    error=f"Timeout: {e}", screenshot=shot)
            except Exception as e:
                return ApplyOutcome(status=ApplicationStatus.FAILED.value, error=str(e))
            finally:
                ctx.close()

    # ---- helpers (best-effort, defensive) -------------------------------- #
    def _visible_text(self, page) -> str:
        try:
            return (page.inner_text("body", timeout=5000) or "").lower()
        except Exception:
            return ""

    def _wall(self, page) -> str:
        text = self._visible_text(page)
        return next((sig for sig in _WALL_SIGNALS if sig in text), "")

    def _captcha_visible(self, page) -> bool:
        for fr in page.frames:
            u = (fr.url or "").lower()
            if ("hcaptcha" in u and "challenge" in u) or ("recaptcha" in u and "bframe" in u):
                try:
                    el = fr.frame_element()
                    if el.is_visible():
                        return True
                except Exception:
                    continue
        return False

    def _fill(self, page, rules, detail):
        ack = bool(self.profile.raw().get("auto_acknowledge_privacy"))
        for f in page.evaluate(_SCAN_JS):
            keys = [f["label"], f["name"], f["id"]]
            # Required "I acknowledge / I have read the privacy notice" questions,
            # when you've allowed it. Optional ones (newsletters) are left alone.
            if ack and f["required"] and any(k and _ACK_RE.search(k) for k in keys):
                if self._acknowledge(page, f):
                    detail["filled"].append(f["label"] or f["name"] or f["id"])
                    continue
            if f["value"] or f["type"] in ("checkbox", "radio"):
                continue
            value = next((v for rx, v in rules if any(k and rx.search(k) for k in keys)), "")
            if not value:
                continue
            loc = page.locator(f"[data-jp='{f['jp']}']").first
            try:
                if f["tag"] == "select":
                    self._select_native(loc, value)
                elif f["combo"]:
                    self._pick_option(page, loc, value)
                else:
                    loc.fill(value, timeout=3000)
                detail["filled"].append(f["label"] or f["name"] or f["id"])
            except Exception:
                detail["skipped"].append(f["label"] or f["name"] or f["id"])

    def _acknowledge(self, page, f) -> bool:
        """Tick the box, or pick the "I acknowledge"/"Yes" option."""
        loc = page.locator(f"[data-jp='{f['jp']}']").first
        try:
            if f["type"] == "checkbox":
                loc.check(timeout=3000)
                return True
            if f["value"] or f["type"] == "radio":
                return False
            if f["tag"] == "select":
                opts = loc.evaluate("s => [...s.options].map(o => o.label)")
                pick = next((o for o in opts if _ACK_OPTION_RE.search(o)), None)
                if pick:
                    loc.select_option(label=pick, timeout=3000)
                    return True
                return False
            if f["combo"]:
                loc.click(timeout=3000)
                page.wait_for_timeout(800)
                opts = page.locator("[role='option']:visible")
                texts = opts.all_inner_texts()
                idx = next((i for i, t in enumerate(texts) if _ACK_OPTION_RE.search(t)), None)
                if idx is None:
                    loc.press("Escape")
                    return False
                opts.nth(idx).click(timeout=3000)
                return True
        except Exception:
            return False
        return False

    @staticmethod
    def _pick_option(page, loc, value):
        """React-select / autocomplete: options only filter on real keystrokes,
        and Enter doesn't commit — click the option that matches the answer."""
        loc.click(timeout=3000)
        loc.press_sequentially(value, delay=40, timeout=8000)
        page.wait_for_timeout(1500)
        opts = page.locator("[role='option']:visible, .dropdown-results > div:visible")
        texts = [x.strip() for x in opts.all_inner_texts()]
        v = value.lower()
        idx = next((i for i, x in enumerate(texts) if x.lower() == v), None)
        if idx is None:
            idx = next((i for i, x in enumerate(texts) if x.lower().startswith(v)), None)
        if idx is None:
            loc.press("Escape")
            raise ValueError(f"no option matching {value!r}")
        opts.nth(idx).click(timeout=3000)

    @staticmethod
    def _select_native(loc, value):
        opts = loc.evaluate("s => [...s.options].map(o => o.label)")
        pick = next((o for o in opts if o.strip().lower() == value.lower()), None) or \
            next((o for o in opts if value.lower() in o.lower()), None)
        if pick is None:
            raise ValueError(f"no option matching {value!r}")
        loc.select_option(label=pick, timeout=3000)

    def _upload(self, page, resume_pdf, cover_path, detail):
        if resume_pdf and Path(resume_pdf).exists():
            for sel in ["input[type='file']#resume", "input[type='file'][name='resume']",
                        "input[type='file'][id*='resume' i]", "input[type='file']"]:
                try:
                    fi = page.locator(sel).first
                    if fi.count():
                        fi.set_input_files(resume_pdf, timeout=5000)
                        detail["filled"].append("resume_upload")
                        page.wait_for_timeout(3000)
                        break
                except Exception:
                    continue
            else:
                detail["skipped"].append("resume_upload")
        if cover_path and Path(cover_path).exists():
            try:
                fi = page.locator("input[type='file']#cover_letter").first
                if fi.count():
                    fi.set_input_files(cover_path, timeout=5000)
                    detail["filled"].append("cover_letter_upload")
            except Exception:
                detail["skipped"].append("cover_letter_upload")

    def _attempt_submit(self, page) -> bool:
        # No bare "Apply" selector — it would click "Apply with LinkedIn".
        for sel in ["#btn-submit", "button:has-text('Submit application')",
                    "button:has-text('Submit Application')", "button[type='submit']",
                    "input[type='submit']", "button:has-text('Submit')"]:
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible():
                    loc.click(timeout=5000)
                    return True
            except Exception:
                continue
        return False

    def _wait_confirmation(self, page) -> tuple[bool, str]:
        for _ in range(10):
            page.wait_for_timeout(1500)
            url = page.url.lower()
            text = self._visible_text(page)
            if "/thanks" in url or "confirmation" in url or any(s in text for s in _CONFIRM_TEXT):
                m = re.search(r"(ref(?:erence)?[:\s#]+)([a-z0-9\-]{4,})", text)
                return True, (m.group(2) if m else "")
        return False, ""
