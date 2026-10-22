"""
Tests for the rules engine. Run with:
    pytest
"""

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def analyze(detections, w=1000, h=800):
    r = client.post("/analyze", json={"image_width": w, "image_height": h, "detections": detections})
    assert r.status_code == 200, r.text
    return r.json()


def names(report):
    return [h["hazard"] for h in report["hazards"]]


def test_empty_room_has_no_hazards():
    report = analyze([])
    assert report["hazard_count"] == 0


def test_cord_is_flagged():
    report = analyze([{"class_name": "cord", "confidence": 0.9, "box": [10, 10, 100, 50]}])
    assert names(report) == ["cord"]


def test_low_confidence_is_ignored():
    report = analyze([{"class_name": "cord", "confidence": 0.2, "box": [10, 10, 100, 50]}])
    assert report["hazard_count"] == 0


def test_two_rugs_counted_once():
    report = analyze([
        {"class_name": "rug", "confidence": 0.8, "box": [0, 0, 100, 100]},
        {"class_name": "rug", "confidence": 0.7, "box": [300, 300, 400, 400]},
    ])
    assert names(report) == ["rug"]
    assert report["hazards"][0]["count"] == 2


def test_toilet_without_grab_bar_is_flagged():
    report = analyze([{"class_name": "toilet", "confidence": 0.9, "box": [400, 300, 600, 700]}])
    assert names(report) == ["missing_grab_bar"]


def test_toilet_with_grab_bar_nearby_is_safe():
    report = analyze([
        {"class_name": "toilet", "confidence": 0.9, "box": [400, 300, 600, 700]},
        {"class_name": "grab_bar", "confidence": 0.8, "box": [620, 350, 700, 380]},
    ])
    assert report["hazard_count"] == 0
    assert report["safe_items"] == ["Grab bar found near toilet"]


def test_grab_bar_far_away_does_not_count():
    report = analyze([
        {"class_name": "tub", "confidence": 0.9, "box": [0, 400, 300, 700]},
        {"class_name": "grab_bar", "confidence": 0.8, "box": [900, 50, 990, 80]},
    ])
    assert names(report) == ["missing_grab_bar"]


def test_high_severity_listed_first():
    report = analyze([
        {"class_name": "stair_edge", "confidence": 0.9, "box": [0, 0, 100, 100]},
        {"class_name": "rug", "confidence": 0.9, "box": [0, 0, 100, 100]},
        {"class_name": "cord", "confidence": 0.9, "box": [0, 0, 100, 100]},
    ])
    assert names(report) == ["cord", "rug", "stair_edge"]


# ================================================================
# Edge cases (Task 1) - thinking like a judge trying to break it
# ================================================================

TOILET = {"class_name": "toilet", "confidence": 0.9, "box": [400, 300, 600, 700]}
# With the 0.5 margin, that toilet's search area is [300, 100, 700, 900].


def test_toilet_and_tub_but_only_one_grab_bar_by_the_toilet():
    report = analyze([
        {"class_name": "tub", "confidence": 0.9, "box": [0, 400, 200, 700]},
        {"class_name": "toilet", "confidence": 0.9, "box": [600, 300, 800, 700]},
        {"class_name": "grab_bar", "confidence": 0.8, "box": [820, 350, 900, 380]},
    ])
    assert names(report) == ["missing_grab_bar"]
    assert report["hazards"][0]["count"] == 1
    assert report["hazards"][0]["boxes"] == [[0, 400, 200, 700]]  # the tub, not the toilet
    assert report["safe_items"] == ["Grab bar found near toilet"]


def test_one_grab_bar_between_toilet_and_tub_covers_both():
    report = analyze([
        {"class_name": "toilet", "confidence": 0.9, "box": [100, 300, 300, 700]},
        {"class_name": "tub", "confidence": 0.9, "box": [500, 400, 900, 700]},
        {"class_name": "grab_bar", "confidence": 0.8, "box": [350, 400, 450, 430]},
    ])
    assert report["hazard_count"] == 0
    assert report["safe_items"] == ["Grab bar found near toilet", "Grab bar found near tub"]


def test_grab_bar_exactly_on_edge_of_search_area_counts():
    report = analyze([TOILET, {"class_name": "grab_bar", "confidence": 0.8, "box": [700, 350, 780, 380]}])
    assert report["hazard_count"] == 0


def test_grab_bar_one_pixel_past_search_area_does_not_count():
    report = analyze([TOILET, {"class_name": "grab_bar", "confidence": 0.8, "box": [701, 350, 780, 380]}])
    assert names(report) == ["missing_grab_bar"]


def test_wrong_capitalization_still_works():
    report = analyze([{"class_name": "Rug", "confidence": 0.9, "box": [0, 0, 100, 100]}])
    assert names(report) == ["rug"]


def test_spaces_or_hyphens_in_class_name_still_work():
    report = analyze([TOILET, {"class_name": "Grab Bar", "confidence": 0.8, "box": [620, 350, 700, 380]}])
    assert report["hazard_count"] == 0


def test_unknown_class_is_reported_not_silently_dropped():
    report = analyze([{"class_name": "chair", "confidence": 0.9, "box": [0, 0, 100, 100]}])
    assert report["hazard_count"] == 0
    assert report["ignored"] == [{"class_name": "chair", "reason": "unknown class"}]


def test_empty_detections_is_a_clean_report():
    report = analyze([])
    assert report["hazards"] == []
    assert report["safe_items"] == []
    assert report["safety_score"] == 100
    assert report["summary"] == "We didn't spot any fall hazards in this photo."


def test_flipped_box_is_fixed_not_misread():
    # Same toilet as above, corners given backwards. The grab bar right next
    # to it must still be found. (The starter flagged a missing grab bar here.)
    report = analyze([
        {"class_name": "toilet", "confidence": 0.9, "box": [600, 700, 400, 300]},
        {"class_name": "grab_bar", "confidence": 0.8, "box": [620, 350, 700, 380]},
    ])
    assert report["hazard_count"] == 0


def test_flipped_box_is_returned_in_correct_order():
    report = analyze([{"class_name": "cord", "confidence": 0.9, "box": [100, 50, 10, 10]}])
    assert report["hazards"][0]["boxes"] == [[10, 10, 100, 50]]


def test_zero_size_box_is_ignored():
    report = analyze([{"class_name": "cord", "confidence": 0.9, "box": [50, 50, 50, 80]}])
    assert report["hazard_count"] == 0
    assert report["ignored"][0]["reason"] == "box has zero width or height"


def test_confidence_exactly_at_cutoff_is_kept():
    report = analyze([{"class_name": "cord", "confidence": 0.40, "box": [10, 10, 100, 50]}])
    assert names(report) == ["cord"]


def test_very_low_confidence_grab_bar_does_not_count():
    report = analyze([TOILET, {"class_name": "grab_bar", "confidence": 0.15, "box": [620, 350, 700, 380]}])
    assert names(report) == ["missing_grab_bar"]
    assert report["needs_check"] == []


def test_output_order_is_the_same_every_time():
    # The starter used a set for toilet/tub, so this order changed between runs.
    report = analyze([
        {"class_name": "tub", "confidence": 0.9, "box": [0, 400, 300, 700]},
        {"class_name": "toilet", "confidence": 0.9, "box": [600, 300, 800, 700]},
    ])
    assert report["hazards"][0]["boxes"] == [[600, 300, 800, 700], [0, 400, 300, 700]]


def test_bad_inputs_are_rejected_with_422():
    bad = [
        {"image_width": 0, "image_height": 800, "detections": []},
        {"image_width": 1000, "image_height": -5, "detections": []},
        {"image_width": 1000, "image_height": 800,
         "detections": [{"class_name": "rug", "confidence": 1.5, "box": [0, 0, 1, 1]}]},
        {"image_width": 1000, "image_height": 800,
         "detections": [{"class_name": "rug", "confidence": 0.9, "box": [0, 0, 1]}]},
    ]
    for body in bad:
        assert client.post("/analyze", json=body).status_code == 422, body


# ================================================================
# Safety score (Task 3) and summary (Task 4)
# ================================================================

def test_score_doc_example():
    report = analyze([TOILET, {"class_name": "rug", "confidence": 0.8, "box": [100, 600, 350, 780]}])
    assert report["safety_score"] == 100 - 25 - 10


def test_stairs_alone_do_not_lower_score():
    report = analyze([{"class_name": "stair_edge", "confidence": 0.9, "box": [0, 0, 100, 100]}])
    assert report["safety_score"] == 100
    assert report["summary"].startswith("We didn't spot any fall hazards")
    assert "handrails" in report["summary"]


def test_extra_instances_cost_less_and_are_capped():
    rugs = [{"class_name": "rug", "confidence": 0.9, "box": [i * 10, 0, i * 10 + 5, 5]} for i in range(3)]
    assert analyze(rugs)["safety_score"] == 100 - 10 - 3 - 3
    many = [{"class_name": "clutter_on_floor", "confidence": 0.9, "box": [i * 10, 0, i * 10 + 5, 5]} for i in range(20)]
    assert analyze(many)["safety_score"] == 100 - 25 - 15


def test_score_never_goes_below_zero():
    report = analyze([
        {"class_name": "cord", "confidence": 0.9, "box": [0, 0, 10, 10]},
        {"class_name": "clutter_on_floor", "confidence": 0.9, "box": [0, 0, 10, 10]},
        TOILET,
        *[{"class_name": "cord", "confidence": 0.9, "box": [i, 0, i + 5, 5]} for i in range(10, 60, 10)],
        *[{"class_name": "clutter_on_floor", "confidence": 0.9, "box": [i, 0, i + 5, 5]} for i in range(10, 60, 10)],
        {"class_name": "rug", "confidence": 0.9, "box": [0, 0, 10, 10]},
    ])
    assert report["safety_score"] == 0


def test_summary_counts_hazards_and_leads_with_most_serious_fix():
    report = analyze([TOILET, {"class_name": "rug", "confidence": 0.8, "box": [100, 600, 350, 780]}])
    assert report["summary"] == (
        "We found 2 hazards. The most important fix: "
        "Have grab bars put in next to the toilet."
    )


def test_summary_singular():
    report = analyze([{"class_name": "rug", "confidence": 0.8, "box": [0, 0, 10, 10]}])
    assert report["summary"].startswith("We found 1 hazard.")


# ================================================================
# Kurosh's review: unsure grab bars, hazard_count, per-fixture fix text
# ================================================================

UNSURE_BAR = {"class_name": "grab_bar", "confidence": 0.30, "box": [620, 350, 700, 380]}
TUB = {"class_name": "tub", "confidence": 0.9, "box": [0, 400, 200, 700]}


def test_unsure_grab_bar_near_toilet_becomes_a_question():
    report = analyze([TOILET, UNSURE_BAR])
    assert report["hazards"] == []
    assert report["hazard_count"] == 0
    assert report["safety_score"] == 100
    check = report["needs_check"][0]
    assert len(report["needs_check"]) == 1
    assert check["fixture"] == "toilet"
    assert check["question"] == "We may have spotted a grab bar near your toilet. Is it a sturdy, wall-mounted bar?"
    assert check["fixture_box"] == [400, 300, 600, 700]
    assert check["grab_bar_boxes"] == [[620, 350, 700, 380]]
    assert report["summary"] == (
        "We didn't spot any fall hazards in this photo. Please answer 1 question below."
    )


def test_unsure_grab_bar_at_exact_lower_cutoff_is_a_question():
    report = analyze([TOILET, {**UNSURE_BAR, "confidence": 0.20}])
    assert len(report["needs_check"]) == 1


def test_unsure_grab_bar_far_away_is_still_missing():
    report = analyze([TOILET, {**UNSURE_BAR, "box": [900, 50, 990, 80]}])
    assert names(report) == ["missing_grab_bar"]
    assert report["needs_check"] == []


def test_sure_grab_bar_wins_over_unsure_one():
    report = analyze([TOILET, UNSURE_BAR, {"class_name": "grab_bar", "confidence": 0.9, "box": [300, 350, 380, 380]}])
    assert report["safe_items"] == ["Grab bar found near toilet"]
    assert report["needs_check"] == []


def test_unsure_bar_by_toilet_does_not_cover_the_tub():
    report = analyze([TOILET, TUB, {**UNSURE_BAR, "box": [620, 350, 700, 380]}])
    assert [n["fixture"] for n in report["needs_check"]] == ["toilet"]
    assert names(report) == ["missing_grab_bar"]
    assert report["hazards"][0]["boxes"] == [[0, 400, 200, 700]]
    assert report["hazards"][0]["fix"] == "Have grab bars put in next to and inside the tub."
    assert report["summary"].endswith("Please answer 1 question below.")


def test_unsure_grab_bar_with_no_toilet_or_tub_does_nothing():
    report = analyze([UNSURE_BAR])
    assert report["hazards"] == [] and report["needs_check"] == []


def test_hazard_count_counts_every_instance():
    rugs = [{"class_name": "rug", "confidence": 0.9, "box": [i * 10, 0, i * 10 + 5, 5]} for i in range(3)]
    report = analyze(rugs)
    assert report["hazard_count"] == 3
    assert report["summary"].startswith("We found 3 hazards.")


def test_hazard_count_skips_stairs_reminder():
    stairs = {"class_name": "stair_edge", "confidence": 0.9, "box": [0, 0, 100, 100]}
    assert analyze([stairs])["hazard_count"] == 0
    report = analyze([stairs, {"class_name": "cord", "confidence": 0.9, "box": [0, 0, 100, 100]}])
    assert report["hazard_count"] == 1
    assert report["summary"].startswith("We found 1 hazard.")


def test_missing_grab_bar_fix_mentions_only_toilet():
    report = analyze([TOILET])
    assert report["hazards"][0]["fix"] == "Have grab bars put in next to the toilet."


def test_missing_grab_bar_fix_mentions_only_tub():
    report = analyze([TUB])
    assert report["hazards"][0]["fix"] == "Have grab bars put in next to and inside the tub."


def test_missing_grab_bar_fix_mentions_both():
    report = analyze([TOILET, TUB])
    assert report["hazards"][0]["count"] == 2
    assert report["hazards"][0]["fix"] == (
        "Have grab bars put in next to and inside the tub, and next to the toilet."
    )


def test_needs_check_if_no_is_the_missing_grab_bar_hazard():
    report = analyze([TOILET, UNSURE_BAR])
    assert report["needs_check"][0]["if_no"] == {
        "hazard": "missing_grab_bar",
        "count": 1,
        "severity": "high",
        "steadi_section": "Bathrooms",
        "steadi_item": "Do you need some support when you get in and out of the tub, or up from the toilet?",
        "fix": "Have grab bars put in next to the toilet.",
        "boxes": [[400, 300, 600, 700]],
    }


def test_if_no_for_tub_uses_tub_fix():
    report = analyze([TUB, {**UNSURE_BAR, "box": [210, 450, 290, 480]}])
    assert report["needs_check"][0]["if_no"]["fix"] == "Have grab bars put in next to and inside the tub."
    assert report["needs_check"][0]["if_no"]["boxes"] == [[0, 400, 200, 700]]


def test_two_questions_summary():
    report = analyze([
        TOILET, UNSURE_BAR,
        TUB, {**UNSURE_BAR, "box": [210, 450, 290, 480]},
    ])
    assert [n["fixture"] for n in report["needs_check"]] == ["toilet", "tub"]
    assert report["hazards"] == []
    assert report["summary"] == (
        "We didn't spot any fall hazards in this photo. Please answer 2 questions below."
    )


def test_two_questions_summary_with_a_hazard():
    report = analyze([
        TOILET, UNSURE_BAR,
        TUB, {**UNSURE_BAR, "box": [210, 450, 290, 480]},
        {"class_name": "rug", "confidence": 0.9, "box": [0, 750, 100, 790]},
    ])
    assert report["summary"] == (
        "We found 1 hazard. The most important fix: Remove the rug, or use double-sided tape "
        "or a non-slip backing so it won't slip. Please answer 2 questions below."
    )


# ================================================================
# [merge-v2] Detector integration, tiers, scan, voice, pages (Kurosh + Claude Code)
# ================================================================

# ---------- [detector integration] tier tests ----------

def tiers(report):
    return [(h["hazard"], h["tier"]) for h in report["hazards"]]


def test_no_tier_reports_as_hazard():
    report = analyze([{"class_name": "rug", "confidence": 0.9, "box": [0, 0, 100, 100]}])
    assert tiers(report) == [("rug", "hazard")]


def test_low_confidence_possible_hazard_is_kept():
    # tiered detections were already filtered by detector.py, so 0.3 is kept
    report = analyze([{"class_name": "cord", "confidence": 0.3, "box": [10, 10, 100, 50],
                       "tier": "possible hazard"}])
    assert tiers(report) == [("cord", "possible hazard")]


def test_hazard_and_possible_get_separate_cards():
    report = analyze([
        {"class_name": "clutter_on_floor", "confidence": 0.6, "box": [0, 0, 50, 50], "tier": "hazard"},
        {"class_name": "clutter_on_floor", "confidence": 0.3, "box": [200, 200, 250, 250],
         "tier": "possible hazard"},
    ])
    assert tiers(report) == [("clutter_on_floor", "hazard"), ("clutter_on_floor", "possible hazard")]


def test_possible_toilet_still_gets_missing_grab_bar():
    report = analyze([{"class_name": "toilet", "confidence": 0.5, "box": [400, 300, 600, 700],
                       "tier": "possible hazard"}])
    assert tiers(report) == [("missing_grab_bar", "possible hazard")]


def test_possible_grab_bar_near_fixture_is_a_question_not_a_warning():
    """[merge-v2] Agreed rule: a possible-tier grab bar near a toilet/tub = a needs_check
    question, not a "Missing grab bar" warning and not a "Worth a quick check" card."""
    report = analyze([
        {"class_name": "tub", "confidence": 0.8, "box": [0, 400, 300, 700], "tier": "hazard"},
        {"class_name": "grab_bar", "confidence": 0.3, "box": [310, 450, 400, 480], "tier": "possible hazard"},
    ])
    assert report["hazards"] == []
    assert [q["fixture"] for q in report["needs_check"]] == ["tub"]
    assert report["safety_score"] == 100


def test_answer_yes_resends_grab_bar_as_hazard_and_cancels_warning():
    """[merge-v2] "Yes": the frontend re-sends that grab bar with tier "hazard"."""
    report = analyze([
        {"class_name": "tub", "confidence": 0.8, "box": [0, 400, 300, 700], "tier": "hazard"},
        {"class_name": "grab_bar", "confidence": 0.4, "box": [310, 450, 400, 480], "tier": "hazard"},
    ])
    assert report["hazards"] == [] and report["needs_check"] == []
    assert report["safe_items"] == ["Grab bar found near tub"]


def test_answer_no_resend_without_the_bar_gives_missing_grab_bar():
    """[merge-v2] "No": the frontend re-sends without that grab bar -> missing grab bar hazard."""
    report = analyze([{"class_name": "tub", "confidence": 0.8, "box": [0, 400, 300, 700], "tier": "hazard"}])
    assert tiers(report) == [("missing_grab_bar", "hazard")]
    assert report["safety_score"] == 75


def test_possible_cards_dont_lower_the_score_but_are_mentioned():
    """[merge-v2] Score/count use confident cards; "possible" ones get a sentence."""
    report = analyze([{"class_name": "cord", "confidence": 0.3, "box": [0, 0, 10, 10], "tier": "possible hazard"}])
    assert report["safety_score"] == 100 and report["hazard_count"] == 0
    assert report["summary"].endswith("1 more thing is worth a quick check.")


def test_confident_grab_bar_counts_as_present():
    report = analyze([
        {"class_name": "tub", "confidence": 0.8, "box": [0, 400, 300, 700], "tier": "hazard"},
        {"class_name": "grab_bar", "confidence": 0.7, "box": [310, 450, 400, 480], "tier": "hazard"},
    ])
    assert report["hazard_count"] == 0
    assert report["safe_items"] == ["Grab bar found near tub"]


def test_detector_output_is_accepted():
    """detector.postprocess output goes straight into /analyze."""
    import detector
    dets = detector.postprocess({
        "toilet": [([400, 300, 600, 700], 0.9)],
        "stair_edge": [([0, 600, 1000, 750], 0.6)],   # short box = single step
        "cord": [([100, 700, 300, 780], 0.32)],
    }, 1000, 800)
    report = analyze(dets)
    assert ("missing_grab_bar", "hazard") in tiers(report)
    assert ("raised_threshold", "possible hazard") in tiers(report)
    assert ("cord", "possible hazard") in tiers(report)


def test_possible_duplicate_of_other_class_is_dropped():
    """A bath mat found as rug (hazard) AND as clutter (possible) shows once."""
    import detector
    dets = detector.postprocess({
        "rug": [([500, 600, 900, 780], 0.45)],
        "clutter_on_floor": [([505, 605, 895, 790], 0.29),     # same box: duplicate
                             ([650, 640, 950, 770], 0.30),     # 83% inside, 54% of rug size: duplicate
                             ([800, 700, 860, 760], 0.30)],    # small bag ON the rug: kept
    }, 1000, 800)
    got = sorted((d["class_name"], d["tier"], d["box"][0]) for d in dets)
    assert got == [("clutter_on_floor", "possible hazard", 800), ("rug", "hazard", 500)]


def test_one_pile_one_box():
    """[ui-v2] Same class, one box mostly inside another -> one box (count matches a person)."""
    import detector
    dets = detector.postprocess({
        "clutter_on_floor": [([100, 400, 600, 780], 0.40),      # the whole pile
                             ([150, 450, 300, 600], 0.45),      # a piece of the same pile
                             ([700, 600, 800, 700], 0.38)],     # a separate shoe
    }, 1000, 800)
    boxes = sorted(d["box"][0] for d in dets)
    assert boxes == [100, 700]
    assert max(d["confidence"] for d in dets) == 0.45          # the pile keeps the best confidence


def test_possible_cards_capped_at_three_hazard_cards_never():
    dets = [
        {"class_name": "cord", "confidence": 0.9, "box": [0, 0, 10, 10], "tier": "hazard"},
        {"class_name": "rug", "confidence": 0.8, "box": [0, 0, 10, 10], "tier": "hazard"},
        {"class_name": "clutter_on_floor", "confidence": 0.7, "box": [0, 0, 10, 10], "tier": "hazard"},
        {"class_name": "stair_edge", "confidence": 0.6, "box": [0, 0, 10, 10], "tier": "hazard"},
        {"class_name": "cord", "confidence": 0.30, "box": [50, 50, 60, 60], "tier": "possible hazard"},
        {"class_name": "rug", "confidence": 0.35, "box": [50, 50, 60, 60], "tier": "possible hazard"},
        {"class_name": "clutter_on_floor", "confidence": 0.26, "box": [50, 50, 60, 60], "tier": "possible hazard"},
        {"class_name": "raised_threshold", "confidence": 0.55, "box": [50, 50, 60, 60], "tier": "possible hazard"},
    ]
    report = analyze(dets)
    assert sum(1 for h in report["hazards"] if h["tier"] == "hazard") == 4
    possible = [h["hazard"] for h in report["hazards"] if h["tier"] == "possible hazard"]
    assert sorted(possible) == ["cord", "raised_threshold", "rug"]   # 0.26 clutter dropped


def test_hazard_cards_listed_before_possible():
    report = analyze([
        {"class_name": "cord", "confidence": 0.3, "box": [0, 0, 10, 10], "tier": "possible hazard"},
        {"class_name": "stair_edge", "confidence": 0.9, "box": [0, 0, 10, 10], "tier": "hazard"},
    ])
    assert tiers(report) == [("stair_edge", "hazard"), ("cord", "possible hazard")]


# ---------- /analyze-image (detector replaced by a fake: no models load) ----------

class FakeDetector:
    def __init__(self, dets):
        self.dets = dets
        self.seen_size = None

    def detect(self, image):
        self.seen_size = image.size
        return self.dets


def photo_bytes(w=400, h=300, fmt="JPEG"):
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, fmt)
    return buf.getvalue()


def test_analyze_image_runs_detector_and_rules(monkeypatch):
    import main
    fake = FakeDetector([
        {"class_name": "toilet", "confidence": 0.9, "box": [100, 50, 200, 250], "tier": "hazard"},
        {"class_name": "cord", "confidence": 0.31, "box": [10, 200, 90, 220], "tier": "possible hazard"},
    ])
    monkeypatch.setattr(main, "get_detector", lambda: fake)
    r = client.post("/analyze-image", files={"file": ("room.jpg", photo_bytes(), "image/jpeg")})
    assert r.status_code == 200, r.text
    report = r.json()
    assert tiers(report) == [("missing_grab_bar", "hazard"), ("cord", "possible hazard")]
    assert (report["image_width"], report["image_height"]) == (400, 300)
    assert fake.seen_size == (400, 300)
    # [merge-v2] the detections it used come back, for re-sending after an answer
    assert [d["class_name"] for d in report["detections"]] == ["toilet", "cord"]
    assert report["detections"][1]["tier"] == "possible hazard"


def test_analyze_image_rotates_phone_photos_upright(monkeypatch):
    import io
    import main
    from PIL import Image
    img = Image.new("RGB", (400, 300), "white")
    exif = img.getexif()
    exif[0x0112] = 6            # EXIF orientation: "rotate 90 degrees", like a phone held upright
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    fake = FakeDetector([])
    monkeypatch.setattr(main, "get_detector", lambda: fake)
    r = client.post("/analyze-image", files={"file": ("room.jpg", buf.getvalue(), "image/jpeg")})
    assert r.status_code == 200, r.text
    assert fake.seen_size == (300, 400)
    assert r.json()["hazard_count"] == 0


def test_debug_saves_stair_frames_only_when_asked(monkeypatch, tmp_path):
    """[debug] ?debug=1 saves frames with a stair_edge detection; normal calls save nothing."""
    import main
    monkeypatch.setattr(main, "SCAN_DEBUG_DIR", tmp_path)
    stairs = FakeDetector([{"class_name": "stair_edge", "confidence": 0.5, "box": [10, 10, 300, 290], "tier": "possible hazard"}])
    monkeypatch.setattr(main, "get_detector", lambda: stairs)
    files = {"file": ("room.jpg", photo_bytes(), "image/jpeg")}
    assert client.post("/analyze-image", files=files).status_code == 200
    assert list(tmp_path.glob("*.jpg")) == []                          # not in debug mode
    assert client.post("/analyze-image?debug=1", files=files).status_code == 200
    saved = sorted(p.name.rsplit("_", 1)[1] for p in tmp_path.glob("*.jpg"))
    assert saved == ["marked.jpg", "raw.jpg"]                          # debug + stairs: marked + clean copy
    monkeypatch.setattr(main, "get_detector", lambda: FakeDetector([]))
    assert client.post("/analyze-image?debug=1", files=files).status_code == 200
    assert len(list(tmp_path.glob("*.jpg"))) == 2                      # debug, no stairs: nothing new


def test_decoy_drops_look_alike_stairs():
    """[decoys] A drawer unit (decoy) covering a stair box, scoring at least as high, removes it."""
    import detector
    tall = [100, 50, 400, 700]                                         # tall enough to be "stairs", not a step
    raw = {"stair_edge": [(tall, 0.58)], detector.DECOY_KEY: [([90, 40, 410, 720], 0.60)]}
    assert [d for d in detector.postprocess(raw, 1000, 800) if d["class_name"] == "stair_edge"] == []
    raw[detector.DECOY_KEY] = [([90, 40, 410, 720], 0.45)]            # decoy clearly weaker (by > the 0.08 margin): kept
    assert [d["class_name"] for d in detector.postprocess(raw, 1000, 800)] == ["stair_edge"]
    raw[detector.DECOY_KEY] = [([600, 50, 900, 700], 0.90)]           # decoy somewhere else: kept
    assert [d["class_name"] for d in detector.postprocess(raw, 1000, 800)] == ["stair_edge"]


def test_analyze_image_rejects_non_image(monkeypatch):
    import main
    monkeypatch.setattr(main, "get_detector", lambda: FakeDetector([]))
    r = client.post("/analyze-image", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 400


# ---------- [phone] one server: the backend serves the frontend ----------

def test_root_sends_people_to_welcome_or_home():
    """[merge-v2] GET / = a tiny router: welcome.html the first time, home.html after."""
    r = client.get("/")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "threshold.welcomed" in r.text and "welcome.html" in r.text and "home.html" in r.text


def test_all_pages_are_served():
    """[merge-v2] Arya's pages + the shared script, from the one server."""
    for page in ["welcome.html", "home.html", "camera.html", "report.html", "static/threshold.js"]:
        r = client.get("/" + page)
        assert r.status_code == 200, page
    assert "/analyze-image" in client.get("/static/threshold.js").text
    assert 'id="scanBtn"' in client.get("/home.html").text


def test_home_screen_app_files_are_served():
    """[voice] manifest + icon for "Add to Home Screen"."""
    m = client.get("/static/manifest.json")
    assert m.status_code == 200
    assert m.json()["name"] == "Threshold" and m.json()["display"] == "standalone"
    icon = client.get("/static/icons/apple-touch-icon.png")
    assert icon.status_code == 200 and icon.headers["content-type"] == "image/png"


def test_health_check():
    assert client.get("/health").json() == {"status": "ok"}


# ---------- [final] fewer false alarms: stricter grab bar rule, room, decoys, stairs ----------

def card(report, name):
    return next((h for h in report["hazards"] if h["hazard"] == name), None)


def test_lone_unsure_toilet_is_only_worth_a_check():
    """[final] A hazard-tier toilet under MISSING_GRAB_BAR_SURE with nothing else bathroom-like
    near it: "Missing grab bar" is only a "possible hazard" card (no score penalty)."""
    report = analyze([{"class_name": "toilet", "confidence": 0.68, "box": [400, 300, 600, 700], "tier": "hazard"}])
    assert card(report, "missing_grab_bar")["tier"] == "possible hazard"
    assert report["safety_score"] == 100 and report["hazard_count"] == 0


def test_sure_toilet_still_gets_missing_grab_bar():
    report = analyze([{"class_name": "toilet", "confidence": 0.85, "box": [400, 300, 600, 700], "tier": "hazard"}])
    assert card(report, "missing_grab_bar")["tier"] == "hazard"


def test_bathroom_cue_makes_unsure_toilet_count():
    """[final] A tub, bath mat (rug) or sink near the toilet = it really is a bathroom."""
    toilet = {"class_name": "toilet", "confidence": 0.68, "box": [400, 300, 600, 700], "tier": "hazard"}
    for cue in [{"class_name": "tub", "confidence": 0.5, "box": [650, 300, 990, 700], "tier": "possible hazard"},
                {"class_name": "rug", "confidence": 0.6, "box": [380, 690, 640, 790], "tier": "hazard"},
                {"class_name": "sink", "confidence": 0.5, "box": [150, 250, 380, 450], "tier": "context"}]:
        report = analyze([toilet, cue])
        assert card(report, "missing_grab_bar")["tier"] == "hazard", cue["class_name"]
    far_rug = {"class_name": "rug", "confidence": 0.6, "box": [0, 0, 20, 20], "tier": "hazard"}   # rug far away: no cue
    assert card(analyze([toilet, far_rug]), "missing_grab_bar")["tier"] == "possible hazard"


def test_scan_sends_its_own_bathroom_cue():
    """[final] Scan mode: bathroom_cue=false keeps even a sure toilet at "possible"; true makes an unsure one count."""
    def run(cue, conf):
        r = client.post("/analyze", json={"image_width": 1000, "image_height": 800, "bathroom_cue": cue, "detections": [
            {"class_name": "toilet", "confidence": conf, "box": [400, 300, 600, 700], "tier": "hazard"}]})
        return card(r.json(), "missing_grab_bar")["tier"]
    assert run(False, 0.9) == "possible hazard"
    assert run(True, 0.68) == "hazard"


def test_sink_is_a_clue_not_a_card():
    report = analyze([{"class_name": "sink", "confidence": 0.7, "box": [10, 10, 100, 100], "tier": "context"}])
    assert report["hazards"] == [] and report["ignored"] == []


def test_non_bathroom_room_needs_much_higher_confidence():
    """[final] Room picked = Bedroom: a 0.7 toilet is ignored, a 0.9 one still counts."""
    def run(room, conf):
        r = client.post("/analyze", json={"image_width": 1000, "image_height": 800, "room": room, "detections": [
            {"class_name": "toilet", "confidence": conf, "box": [400, 300, 600, 700], "tier": "hazard"},
            {"class_name": "tub", "confidence": conf, "box": [650, 300, 990, 700], "tier": "hazard"}]})
        return r.json()
    assert card(run("Bedroom", 0.7), "missing_grab_bar") is None
    assert card(run("Kitchen", 0.9), "missing_grab_bar")["tier"] == "hazard"
    for room in ["Bathroom", "Half bath", "Other", None]:   # bathrooms, or room unknown: normal rules
        assert card(run(room, 0.7), "missing_grab_bar")["tier"] == "hazard", room


def test_analyze_image_passes_room(monkeypatch):
    import main
    fake = FakeDetector([{"class_name": "toilet", "confidence": 0.7, "box": [10, 10, 100, 200], "tier": "hazard"}])
    monkeypatch.setattr(main, "get_detector", lambda: fake)
    files = {"file": ("room.jpg", photo_bytes(), "image/jpeg")}
    assert card(client.post("/analyze-image?room=Hallway", files=files).json(), "missing_grab_bar") is None
    assert card(client.post("/analyze-image?room=Bathroom", files=files).json(), "missing_grab_bar") is not None


def test_toilet_decoy_drops_a_chair():
    """[final] Toilet decoys work like the stair decoys."""
    import detector
    raw = {"toilet": [([400, 300, 600, 700], 0.66)], detector.DECOY_KEY: {"toilet": [([390, 280, 610, 720], 0.70)]}}
    assert [d for d in detector.postprocess(raw, 1000, 800) if d["class_name"] == "toilet"] == []
    raw[detector.DECOY_KEY] = {"stair_edge": [([390, 280, 610, 720], 0.90)]}   # stair decoys don't touch toilets
    assert [d["class_name"] for d in detector.postprocess(raw, 1000, 800)] == ["toilet"]


def test_sink_cue_comes_out_of_the_detector():
    import detector
    raw = {"toilet": [([400, 300, 600, 700], 0.7)],
           detector.CONTEXT_KEY: [([100, 250, 350, 450], 0.5), ([405, 305, 600, 700], 0.6)]}   # 2nd = the toilet itself
    sinks = [d for d in detector.postprocess(raw, 1000, 800) if d["class_name"] == "sink"]
    assert [d["box"] for d in sinks] == [[100, 250, 350, 450]] and sinks[0]["tier"] == "context"


def test_stair_geometry():
    """[final] Real staircases are big and reach the bottom of the photo. A compact box
    (drawer unit) is at most "possible"; a tiny one is dropped."""
    import detector
    def tiers(box):
        return [d["tier"] for d in detector.postprocess({"stair_edge": [(box, 0.6)]}, 1000, 800)
                if d["class_name"] == "stair_edge"]
    assert tiers([50, 200, 950, 790]) == ["hazard"]            # big, reaches the floor
    assert tiers([300, 200, 650, 790]) == ["possible hazard"]  # 26% of the photo
    assert tiers([50, 50, 950, 600]) == ["possible hazard"]    # big but ends 75% down
    assert tiers([400, 300, 560, 790]) == []                   # 10% of the photo: dropped


def test_debug_saves_toilet_and_dropped_frames(monkeypatch, tmp_path):
    """[final] Toilets/tubs are saved too, and so are frames where a stair box was dropped."""
    import main
    monkeypatch.setattr(main, "SCAN_DEBUG_DIR", tmp_path)
    files = {"file": ("room.jpg", photo_bytes(), "image/jpeg")}
    toilet = FakeDetector([{"class_name": "toilet", "confidence": 0.7, "box": [10, 10, 100, 200], "tier": "hazard"}])
    monkeypatch.setattr(main, "get_detector", lambda: toilet)
    client.post("/analyze-image?debug=1", files=files)
    assert len(list(tmp_path.glob("*toilet*.jpg"))) == 2
    dropped = FakeDetector([])
    dropped.last_raw = {"stair_edge": [([10, 10, 300, 290], 0.6)]}
    monkeypatch.setattr(main, "get_detector", lambda: dropped)
    client.post("/analyze-image?debug=1", files=files)
    assert len(list(tmp_path.glob("*stair_edge*.jpg"))) == 2


def test_missing_grab_bar_names_its_fixture_and_never_contradicts():
    """[v1.0.1] The card says which fixture; a fixture type with a grab bar is never "missing"."""
    tub_a = {"class_name": "tub", "confidence": 0.9, "box": [100, 300, 400, 600], "tier": "hazard"}
    bar = {"class_name": "grab_bar", "confidence": 0.9, "box": [120, 250, 380, 290], "tier": "hazard"}
    tub_b = {"class_name": "tub", "confidence": 0.9, "box": [100300, 300, 100600, 600], "tier": "hazard"}   # same tub, another scan frame
    toilet = {"class_name": "toilet", "confidence": 0.9, "box": [700, 300, 850, 600], "tier": "hazard"}
    r = analyze([tub_a, bar, tub_b, toilet], w=200000)
    mgb = [h for h in r["hazards"] if h["hazard"] == "missing_grab_bar"]
    assert len(mgb) == 1 and mgb[0]["fixtures"] == ["toilet"] and mgb[0]["count"] == 1
    assert "Grab bar found near tub" in r["safe_items"]
    assert card(analyze([tub_a, bar]), "missing_grab_bar") is None


def test_toilet_decoy_margin_only_below_sure():
    """[final] A decoy within 0.10 drops a not-sure toilet, never a sure one (>= 0.75)."""
    import detector
    decoy = {"toilet": [([390, 280, 610, 720], 0.60)]}
    raw = {"toilet": [([400, 300, 600, 700], 0.66)], detector.DECOY_KEY: decoy}
    assert [d for d in detector.postprocess(raw, 1000, 800) if d["class_name"] == "toilet"] == []
    raw["toilet"] = [([400, 300, 600, 700], 0.80)]
    assert [d["class_name"] for d in detector.postprocess(raw, 1000, 800) if d["class_name"] == "toilet"] == ["toilet"]


def test_sink_read_as_tub_is_dropped():
    """[final] The sink pass doubles as tub decoys: a "tub" that is really the sink goes away."""
    import detector
    raw = {"tub": [([100, 300, 400, 500], 0.48)], detector.CONTEXT_KEY: [([110, 290, 390, 510], 0.55)]}
    assert [d for d in detector.postprocess(raw, 1000, 800) if d["class_name"] == "tub"] == []
    raw[detector.CONTEXT_KEY] = [([600, 100, 800, 300], 0.55)]                   # sink elsewhere: tub kept
    assert [d["class_name"] for d in detector.postprocess(raw, 1000, 800)] == ["tub", "sink"]
