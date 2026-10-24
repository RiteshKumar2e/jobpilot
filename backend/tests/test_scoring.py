from app.agents.matching import MatchingAgent
from app.schemas import RawJob


def mk(title, desc, loc="India"):
    return RawJob(source="greenhouse", company="x", title=title, description=desc, location=loc)


def test_score_bounds(profile):
    m = MatchingAgent(profile)
    r = m.score(mk("ML Engineer", "Python PyTorch computer vision"), "VERIFIED")
    assert 0 <= r.total <= 100


def test_weights_sum_to_100(profile):
    m = MatchingAgent(profile)
    assert sum(m.WEIGHTS.values()) == 100


def test_relevant_job_scores_higher_than_irrelevant(profile):
    m = MatchingAgent(profile)
    good = m.score(mk("Computer Vision Engineer",
                      "steel defect MobileNetV2 NEU-DET OpenCV PyTorch FastAPI python entry level"),
                   "VERIFIED")
    bad = m.score(mk("Accountant", "Tally GST audit bookkeeping"), "NEEDS_REVIEW")
    assert good.total > bad.total


def test_rejected_zeroes_experience_component(profile):
    m = MatchingAgent(profile)
    r = m.score(mk("ML Engineer", "python"), "REJECTED")
    assert r.breakdown["experience"] == 0.0


def test_resume_version_recommended(profile):
    m = MatchingAgent(profile)
    r = m.score(mk("Computer Vision Engineer", "defect detection opencv"), "VERIFIED")
    assert r.recommended_resume_version == "Computer Vision / Research"
    r2 = m.score(mk("Backend Engineer", "fastapi rest api microservices python developer"), "VERIFIED")
    assert r2.recommended_resume_version == "Software Development / Backend"
    cases = {
        "Generative AI Engineer": "Generative AI / LLM",
        "LLM Engineer": "Generative AI / LLM",
        "Machine Learning Engineer": "Machine Learning",
        "AI Engineer": "AI/ML",
        "Full Stack Developer": "Full-Stack / Python",
        "Software Engineer I": "Software Development / Backend",
    }
    for title, want in cases.items():
        # 'fraud detection' / 'image' in the JD must not drag it to CV anymore
        got = m.score(mk(title, "fraud detection, image uploads, python"), "VERIFIED")
        assert got.recommended_resume_version == want, title


def test_resume_labels_from_filename():
    from app.resume.parsing import _label_from_filename
    assert _label_from_filename("Resume_GENAI.pdf") == "Generative AI / LLM"
    assert _label_from_filename("Resume_AI_ML.pdf") == "AI/ML"
    assert _label_from_filename("Resume_ML.pdf") == "Machine Learning"
    assert _label_from_filename("Resume_SD.pdf") == "Software Development / Backend"
    assert _label_from_filename("Resume_FullStack.pdf") == "Full-Stack / Python"
    assert _label_from_filename("Resume.pdf") == ""


def test_select_base_falls_back_to_nearest_variant(profile, tmp_path):
    from app.resume.parsing import ParsedResume
    from app.resume.tailor import ResumeTailor
    rs = [ParsedResume("a", "Resume_ML.pdf", ".pdf", "", "Machine Learning"),
          ParsedResume("b", "Resume_SD.pdf", ".pdf", "", "Software Development / Backend")]
    t = ResumeTailor(profile, tmp_path)
    assert t.select_base(rs, "Computer Vision / Research").filename == "Resume_ML.pdf"
    assert t.select_base(rs, "Full-Stack / Python").filename == "Resume_SD.pdf"


def test_matching_skills_detected(profile):
    m = MatchingAgent(profile)
    r = m.score(mk("ML Engineer", "python pytorch opencv fastapi"), "VERIFIED")
    assert "python" in r.matching_skills
    assert "pytorch" in r.matching_skills
