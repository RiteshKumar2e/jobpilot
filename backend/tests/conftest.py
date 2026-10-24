import os
import tempfile
import pytest

# Isolate each test session's DB + dirs so tests never touch real data.
_tmp = tempfile.mkdtemp(prefix="jobpilot_test_")
os.environ["JOBPILOT_DB_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["JOBPILOT_TAILORED_DIR"] = f"{_tmp}/tailored"
os.environ["JOBPILOT_LOG_DIR"] = f"{_tmp}/logs"
os.environ["JOBPILOT_RESUMES_DIR"] = f"{_tmp}/resumes"
os.environ["JOBPILOT_LLM_PROVIDER"] = "none"
os.environ["JOBPILOT_APPROVAL_MODE"] = "SAFE"


@pytest.fixture(scope="session", autouse=True)
def _setup():
    # reset cached settings so the env above takes effect
    import app.config as cfg
    cfg._settings = None
    from app.config import get_settings
    get_settings()
    from app.database import init_db
    init_db()
    yield


@pytest.fixture
def profile():
    from app.profile_loader import load_profile
    return load_profile(force=True)
