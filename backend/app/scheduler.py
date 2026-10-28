"""APScheduler wrapper for recurring discovery cycles.

Reads a cron expression from config. Pause/resume is honored by the
orchestrator itself (it checks the 'paused' KV flag), so pausing doesn't
require tearing down the schedule.
"""
from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import get_settings
from .logging_conf import get_logger

log = get_logger("scheduler")


class JobScheduler:
    def __init__(self, orchestrator):
        self.orchestrator = orchestrator
        self.settings = get_settings()
        self._sched = BackgroundScheduler(timezone="UTC")
        self._job = None

    def start(self):
        if self._sched.running:
            return
        self._add_discovery_job()
        self._add_side_jobs()
        self._sched.start()
        log.info("Scheduler started", extra={"cron": self.settings.discovery_cron})

    def _add_side_jobs(self):
        """Inbox check (hourly) and the weekly review email — independent of the
        discovery/apply cycle, so a long cycle never delays a recruiter reply."""
        s = self.settings
        try:
            self._sched.add_job(self.orchestrator.check_inbox,
                                CronTrigger.from_crontab(s.inbox_cron, timezone="UTC"),
                                id="inbox_check", replace_existing=True, max_instances=1,
                                coalesce=True)
        except Exception as e:
            log.warning(f"Bad inbox cron '{s.inbox_cron}': {e}")
        self._sched.add_job(self.orchestrator.send_weekly_review,
                            CronTrigger(day_of_week=s.weekly_review_day, hour=3, minute=30,
                                        timezone="UTC"),        # 09:00 IST
                            id="weekly_review", replace_existing=True, coalesce=True)

    def _add_discovery_job(self):
        try:
            trigger = CronTrigger.from_crontab(self.settings.discovery_cron, timezone="UTC")
        except Exception as e:
            log.warning(f"Bad cron '{self.settings.discovery_cron}': {e}; defaulting to 6h")
            trigger = CronTrigger(hour="*/6")
        self._job = self._sched.add_job(self._run, trigger, id="discovery_cycle",
                                        replace_existing=True, max_instances=1,
                                        coalesce=True)

    def _run(self):
        log.info("Scheduled discovery cycle starting")
        try:
            stats = self.orchestrator.run_full_cycle()
            log.info("Scheduled cycle done", extra={"stats": stats})
        except Exception as e:
            log.exception(f"scheduled cycle error: {e}")

    def reschedule(self, cron: str):
        self.settings.discovery_cron = cron
        if self._job:
            self._job.reschedule(CronTrigger.from_crontab(cron, timezone="UTC"))

    def next_run(self):
        return str(self._job.next_run_time) if self._job else None

    def shutdown(self):
        if self._sched.running:
            self._sched.shutdown(wait=False)
