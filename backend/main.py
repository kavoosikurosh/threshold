"""
Threshold - Rules Engine
------------------------
Takes a list of object detections and turns them into a fall-hazard report
mapped to the CDC STEADI home safety checklist. Also serves the app pages
and runs the hazard detector on photos (POST /analyze-image).

Run it:
    uvicorn main:app --reload

Then open http://127.0.0.1:8000 (the app) or http://127.0.0.1:8000/docs (the API).
"""

import io
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

try:   # iPhone photos are HEIC
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

app = FastAPI(title="Threshold Rules Engine")

# Lets the frontend (running on a different port/site) call this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============ SETTINGS - SAFE TO TUNE ============

# Detections below this confidence are ignored (the model is unsure).
# [detector integration] Only for detections WITHOUT a tier: tiered detections
# were already filtered by detector.py.
MIN_CONFIDENCE = 0.40

# A grab bar the model is only somewhat sure about (between this and
# MIN_CONFIDENCE) near a toilet/tub becomes a question for the user instead
# of a "missing grab bar" hazard. Below this it's ignored like anything else.
# [merge-v2] A grab bar with tier "possible hazard" is treated the same way
# (detector.py's possible tier for grab bars starts at 0.25, inside this range).
UNSURE_GRAB_BAR_MIN_CONFIDENCE = 0.20

# How far around a toilet/tub we look for a grab bar, as a fraction of the
# toilet/tub box size. 0.5 = grow the box by 50% of its width/height on
# every side before checking for an overlapping grab bar.
GRAB_BAR_SEARCH_MARGIN = 0.5
# Known limitation: the margin scales with the toilet/tub box, so a narrow
# toilet with bars mounted on the side walls far out (e.g. an ambulant stall,
# bars ~160 px from a 185 px wide toilet) needs ~0.86 to be caught. Raising it
# that far makes a towel bar above a toilet count. Kept at 0.5; re-tune with
# real model detections via tune_margin.py once the model is trained.

# [detector integration] At most this many "possible hazard" cards per photo
# (most confident first). "hazard" cards are never capped.
MAX_POSSIBLE_CARDS = 3

# [final] Stricter missing grab bar rule. A toilet/tub only gives a confident ("hazard")
# "Missing grab bar" card if it is "hazard" tier AND either
#   - its confidence is >= MISSING_GRAB_BAR_SURE, or
#   - there is a second bathroom cue near it: the other fixture (toilet <-> tub), a rug
#     (bath mat) or a sink (detector.py sends sinks as tier "context").
# Otherwise it's only a "possible hazard" card ("worth a quick check").
# 0.75 (not 0.6): detector.py's toilet "hazard" cutoff is already 0.65. Tuning houses: real
# toilets 0.66-0.89 (the ones under 0.75 all have a tub next to them); a staircase read as
# a toilet scored 0.63.
MISSING_GRAB_BAR_SURE = 0.75
# "near" = the cue box touches the fixture box grown by this much of its size on every side
BATHROOM_CUE_MARGIN = 1.0
BATHROOM_CUE_CLASSES = ("toilet", "tub", "rug", "sink")

# [final] Room context. If the user picked a room that isn't a bathroom (Bedroom, Kitchen,
# Living room, Hallway, Stairs...), toilet/tub/grab bar detections below this confidence
# are ignored. A room name containing one of BATHROOM_WORDS counts as a bathroom; no room
# (or plain "Other") = no room rule.
NON_BATHROOM_MIN_CONFIDENCE = 0.85
BATHROOM_WORDS = ("bath", "restroom", "toilet", "washroom", "powder", "wc")
BATHROOM_ONLY_CLASSES = ("toilet", "tub", "grab_bar")

# Safety score: start at 100 and subtract points per hazard TYPE found.
# The first instance of a type costs the full penalty; each extra instance
# of the same type costs a little more, up to a cap. (See SCORING NOTES below.)
# [merge-v2] Only confident ("hazard" tier) cards count toward the score.
SCORE_PENALTY = {"high": 25, "medium": 10, "info": 0}
SCORE_EXTRA_PER_INSTANCE = {"high": 5, "medium": 3, "info": 0}
SCORE_EXTRA_CAP = {"high": 15, "medium": 6, "info": 0}

# =================================================

# SCORING NOTES
# - High = 25: the CDC treats these (cords, clutter, no grab bars) as direct
#   trip/fall causes. Two of them drops a room to 50, which should read as "fix
#   this now". Four drops it to 0.
# - Medium = 10: rugs and thresholds are real risks but have cheap, partial fixes
#   (non-slip backing, contrasting tape), so they shouldn't tank the score alone.
# - Info = 0: "stairs detected" is a reminder, not a finding. A house shouldn't
#   lose points for having stairs.
# - Extra instances: 3 rugs are worse than 1, but not 3x worse, since it's the
#   same fix. Penalizing every instance fully would let one messy floor
#   (8 clutter boxes) zero out a room on its own.

# Exact class names from the model. Must match Roboflow spelling.
HAZARD_RULES = {
    "cord": {
        "severity": "high",
        "steadi_section": "Floors",
        "steadi_item": "Do you have to walk over or around wires or cords?",
        "fix": "Coil or tape cords and wires next to the wall so you can't trip over them.",
    },
    "rug": {
        "severity": "medium",
        "steadi_section": "Floors",
        "steadi_item": "Do you have throw rugs on the floor?",
        "fix": "Remove the rug, or use double-sided tape or a non-slip backing so it won't slip.",
    },
    "clutter_on_floor": {
        "severity": "high",
        "steadi_section": "Floors",
        "steadi_item": "Are there papers, books, towels, shoes, magazines, boxes, blankets, or other objects on the floor?",
        "fix": "Pick up things that are on the floor. Always keep objects off the floor.",
    },
    "raised_threshold": {
        "severity": "medium",
        "steadi_section": "Floors (tripping hazards)",
        "steadi_item": "Step or raised doorway threshold (general trip hazard; not a named STEADI item)",
        "fix": "Mark the edge with contrasting tape, or replace it with a low-profile or beveled threshold.",
    },
    "stair_edge": {
        "severity": "info",
        "steadi_section": "Stairs and Steps",
        "steadi_item": "Are handrails loose or broken? Is there a handrail on only one side? Is there a light over the stairway?",
        "fix": "Stairs detected. Check that there are sturdy handrails on both sides and a light at the top and bottom.",
    },
}

MISSING_GRAB_BAR_RULE = {
    "severity": "high",
    "steadi_section": "Bathrooms",
    "steadi_item": "Do you need some support when you get in and out of the tub, or up from the toilet?",
    "fix": "Have grab bars put in next to and inside the tub, and next to the toilet.",
}

# CDC fix text, trimmed to only the fixture(s) actually found in the photo.
MISSING_GRAB_BAR_FIX = {
    ("toilet",): "Have grab bars put in next to the toilet.",
    ("tub",): "Have grab bars put in next to and inside the tub.",
    ("toilet", "tub"): "Have grab bars put in next to and inside the tub, and next to the toilet.",
}

UNSURE_GRAB_BAR_QUESTION = "We may have spotted a grab bar near your {fixture}. Is it a sturdy, wall-mounted bar?"

# Classes that need a grab bar nearby. A tuple (not a set) so the output order
# is the same on every run. Python randomizes set order for strings.
NEEDS_GRAB_BAR = ("toilet", "tub")

# Every class name the model can send.
KNOWN_CLASSES = set(HAZARD_RULES) | set(NEEDS_GRAB_BAR) | {"grab_bar"}
# [final] Never shown or scored; only used as clues (see BATHROOM_CUE_CLASSES).
CONTEXT_CLASSES = {"sink"}

SEVERITY_ORDER = {"high": 0, "medium": 1, "info": 2}

# [detector integration] Optional confidence tier set by detector.py.
# "hazard" = confident, "possible hazard" = shown to the user as "check this".
HAZARD_TIER = "hazard"
POSSIBLE_TIER = "possible hazard"
TIER_ORDER = {HAZARD_TIER: 0, POSSIBLE_TIER: 1}


# ---------- Input / output formats ----------

class Detection(BaseModel):
    class_name: str = Field(examples=["rug"])
    confidence: float = Field(ge=0, le=1, examples=[0.82])
    box: list[Annotated[float, Field(allow_inf_nan=False)]] = Field(
        min_length=4, max_length=4,
        description="[x_min, y_min, x_max, y_max] in pixels",
        examples=[[120, 340, 480, 600]],
    )
    # [detector integration] Optional. Detections WITH a tier were already
    # filtered by detector.py, so MIN_CONFIDENCE doesn't apply to them.
    # Detections without a tier work exactly as before.
    tier: str | None = Field(default=None, examples=["hazard"],
                             description='"hazard" or "possible hazard"')


class AnalyzeRequest(BaseModel):
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    detections: list[Detection]
    # [final] Optional: the room the user picked ("Bathroom", "Bedroom", ...). See
    # NON_BATHROOM_MIN_CONFIDENCE. Leave it out if unknown.
    room: str | None = Field(default=None, examples=["Bathroom"])
    # [final] Scan mode decides the bathroom cue over the whole scan (toilet/tub in 2 of 3
    # frames AND a sink / tub+toilet / bath mat seen, or room = Bathroom) and sends it here:
    # true = cue found, false = no cue (then a toilet/tub only gives a "possible" Missing
    # grab bar, however confident). Leave it out for photos: the cue is looked for in the photo.
    bathroom_cue: bool | None = None


class Hazard(BaseModel):
    hazard: str
    count: int
    severity: str
    steadi_section: str
    steadi_item: str
    fix: str
    boxes: list[list[float]]
    # [detector integration] "hazard" or "possible hazard" (one card per tier), and
    # the highest confidence of the card's boxes. Left out of the JSON when not set
    # (e.g. in needs_check.if_no).
    tier: str | None = None
    confidence: float | None = None
    # [v1.0.1] missing_grab_bar only: which fixtures it is about, e.g. ["toilet"]
    fixtures: list[str] | None = None


class IgnoredDetection(BaseModel):
    class_name: str
    reason: str


class NeedsCheck(BaseModel):
    fixture: str
    question: str
    fixture_box: list[float]
    grab_bar_boxes: list[list[float]]
    # The hazard to add to the report if the user answers "no" (it's not a
    # sturdy, wall-mounted bar). Same shape as an entry in `hazards`.
    if_no: "Hazard"


class AnalyzeResponse(BaseModel):
    hazard_count: int
    hazards: list[Hazard]
    safe_items: list[str]
    needs_check: list[NeedsCheck] = []
    safety_score: int
    summary: str
    ignored: list[IgnoredDetection] = []
    # [detector integration] size of the photo the boxes refer to (upright pixels)
    image_width: int | None = None
    image_height: int | None = None
    # [merge-v2] /analyze-image only: the detections it used, so the frontend can
    # re-send them to /analyze after a needs_check answer.
    detections: list[Detection] | None = None


# ---------- Helper functions ----------

def normalize_class_name(name):
    """'Rug', ' grab bar ', 'Grab-Bar' -> 'rug', 'grab_bar', 'grab_bar'."""
    return name.strip().lower().replace(" ", "_").replace("-", "_")


def normalize_box(box):
    """Put corners in order, so [600, 700, 400, 300] -> [400, 300, 600, 700]."""
    x1, y1, x2, y2 = box
    return [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]


def safety_score(hazards):
    score = 100
    for h in hazards:
        extra = SCORE_EXTRA_PER_INSTANCE[h.severity] * (h.count - 1)
        score -= SCORE_PENALTY[h.severity] + min(extra, SCORE_EXTRA_CAP[h.severity])
    return max(score, 0)


def count_hazards(hazards):
    """Number of individual hazards found. Info reminders (stairs) don't count."""
    return sum(h.count for h in hazards if h.severity != "info")


def build_summary(hazards, needs_check):
    real = [h for h in hazards if h.severity != "info"]
    reminders = [h for h in hazards if h.severity == "info"]
    if not real:
        text = "We didn't spot any fall hazards in this photo."
        if reminders:
            text += " " + reminders[0].fix
    else:
        n = count_hazards(hazards)
        word = "hazard" if n == 1 else "hazards"
        # hazards are already sorted most serious first
        text = f"We found {n} {word}. The most important fix: {real[0].fix}"
    if needs_check:
        q = "question" if len(needs_check) == 1 else "questions"
        text += f" Please answer {len(needs_check)} {q} below."
    return text

def expand_box(box, margin):
    """Grow a box by `margin` (fraction of its width/height) on every side."""
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    return [x1 - w * margin, y1 - h * margin, x2 + w * margin, y2 + h * margin]


def boxes_overlap(a, b):
    """True if two [x1, y1, x2, y2] boxes touch or overlap."""
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def grab_bars_nearby(fixture_box, grab_bar_boxes):
    search_area = expand_box(fixture_box, GRAB_BAR_SEARCH_MARGIN)
    return [gb for gb in grab_bar_boxes if boxes_overlap(search_area, gb)]


def has_grab_bar_nearby(fixture_box, grab_bar_boxes):
    return bool(grab_bars_nearby(fixture_box, grab_bar_boxes))


def is_non_bathroom(room):
    """[final] True if the user picked a room that clearly isn't a bathroom."""
    r = (room or "").strip().lower()
    if r in ("", "other", "other room", "room"):
        return False
    return not any(w in r for w in BATHROOM_WORDS)


def has_bathroom_cue(fixture, fixture_box, cues):
    """[final] cues = [(class name, box)]. True if another bathroom thing is near the fixture."""
    near = expand_box(fixture_box, BATHROOM_CUE_MARGIN)
    return any(name != fixture and boxes_overlap(near, box) for name, box in cues)


def is_unsure_grab_bar(d):
    """[merge-v2] Agreed grab bar rule: a "possible hazard" grab bar, or (no tier)
    a confidence between UNSURE_GRAB_BAR_MIN_CONFIDENCE and MIN_CONFIDENCE,
    becomes a needs_check question near a toilet/tub."""
    if d.tier is not None:
        return d.tier == POSSIBLE_TIER
    return UNSURE_GRAB_BAR_MIN_CONFIDENCE <= d.confidence < MIN_CONFIDENCE


# ---------- Endpoints ----------

@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/analyze", response_model=AnalyzeResponse, response_model_exclude_none=True)
def analyze(request: AnalyzeRequest):
    # 1. Clean up each detection. Drop low-confidence ones, and report
    #    (instead of silently dropping) unknown classes and empty boxes.
    #    Detections are grouped by (class name, tier); no tier = "hazard", as before.
    by_class: dict[tuple[str, str], list[tuple[list[float], float]]] = {}
    unsure_grab_bars: list[list[float]] = []
    ignored: list[IgnoredDetection] = []
    cues: list[tuple[str, list[float]]] = []   # [final] bathroom cues
    strict_room = is_non_bathroom(request.room)
    for d in request.detections:
        name = normalize_class_name(d.class_name)
        if name in CONTEXT_CLASSES:              # [final] a clue only, never a card
            cues.append((name, normalize_box(d.box)))
            continue
        if strict_room and name in BATHROOM_ONLY_CLASSES and d.confidence < NON_BATHROOM_MIN_CONFIDENCE:
            ignored.append(IgnoredDetection(class_name=d.class_name, reason=f"not sure enough for a {request.room.strip()}"))
            continue
        unsure_grab_bar = name == "grab_bar" and is_unsure_grab_bar(d)
        if d.tier is None and d.confidence < MIN_CONFIDENCE and not unsure_grab_bar:
            continue
        if name not in KNOWN_CLASSES:
            ignored.append(IgnoredDetection(class_name=d.class_name, reason="unknown class"))
            continue
        box = normalize_box(d.box)
        if box[2] - box[0] <= 0 or box[3] - box[1] <= 0:
            ignored.append(IgnoredDetection(class_name=d.class_name, reason="box has zero width or height"))
            continue
        # 2. Group boxes by class name (unsure grab bars kept separately)
        if unsure_grab_bar:
            unsure_grab_bars.append(box)
        else:
            by_class.setdefault((name, d.tier or HAZARD_TIER), []).append((box, d.confidence))
        if name in BATHROOM_CUE_CLASSES:
            cues.append((name, box))

    hazards: list[Hazard] = []
    safe_items: list[str] = []

    # 3. Direct hazards: if the object is there, flag it (one card per tier)
    for class_name, rule in HAZARD_RULES.items():
        for tier in TIER_ORDER:
            dets = by_class.get((class_name, tier), [])
            if dets:
                hazards.append(Hazard(hazard=class_name, count=len(dets), boxes=[b for b, _ in dets],
                                      tier=tier, confidence=max(c for _, c in dets), **rule))

    # 4. Inverted rule: toilet/tub WITHOUT a grab bar nearby is the hazard.
    #    Only a confident grab bar counts as present. If the only grab bar nearby
    #    is one the model is unsure about, ask the user instead (no score penalty).
    grab_bars = [b for b, _ in by_class.get(("grab_bar", HAZARD_TIER), [])]
    needs_check: list[NeedsCheck] = []
    missing: dict[str, list] = {}   # tier -> [(box, confidence, fixture)]
    has_bar: set[str] = set()        # [v1.0.1] fixture types with a grab bar found
    for fixture in NEEDS_GRAB_BAR:
        for tier in TIER_ORDER:
            for box, conf in by_class.get((fixture, tier), []):
                if has_grab_bar_nearby(box, grab_bars):
                    has_bar.add(fixture)
                    if f"Grab bar found near {fixture}" not in safe_items:
                        safe_items.append(f"Grab bar found near {fixture}")
                elif unsure := grab_bars_nearby(box, unsure_grab_bars):
                    needs_check.append(NeedsCheck(
                        fixture=fixture,
                        question=UNSURE_GRAB_BAR_QUESTION.format(fixture=fixture),
                        fixture_box=box,
                        grab_bar_boxes=unsure,
                        if_no=Hazard(
                            hazard="missing_grab_bar",
                            count=1,
                            boxes=[box],
                            **{**MISSING_GRAB_BAR_RULE, "fix": MISSING_GRAB_BAR_FIX[(fixture,)]},
                        ),
                    ))
                else:
                    # [final] stricter rule: a lone, not-so-sure toilet/tub is only "worth a check"
                    if tier == HAZARD_TIER:
                        if request.bathroom_cue is None:
                            sure = conf >= MISSING_GRAB_BAR_SURE or has_bathroom_cue(fixture, box, cues)
                        else:
                            sure = request.bathroom_cue
                        if not sure:
                            tier = POSSIBLE_TIER
                    missing.setdefault(tier, []).append((box, conf, fixture))

    # [v1.0.1] Never "missing" for a fixture type that has a grab bar (in a scan the same
    # tub can show up in several frames, with the bar visible in only one of them).
    for tier, items in missing.items():
        items = [it for it in items if it[2] not in has_bar]
        if not items:
            continue
        fixtures = [f for f in NEEDS_GRAB_BAR if any(it[2] == f for it in items)]
        rule = {**MISSING_GRAB_BAR_RULE, "fix": MISSING_GRAB_BAR_FIX[tuple(fixtures)]}
        hazards.append(Hazard(
            hazard="missing_grab_bar",
            count=len(items),
            boxes=[it[0] for it in items],
            tier=tier,
            confidence=max(it[1] for it in items),
            fixtures=fixtures,
            **rule,
        ))

    # 5. [detector integration] Keep only the MAX_POSSIBLE_CARDS most confident
    #    "possible hazard" cards
    possible = sorted((h for h in hazards if h.tier == POSSIBLE_TIER), key=lambda h: -(h.confidence or 0))
    dropped = {id(h) for h in possible[MAX_POSSIBLE_CARDS:]}
    hazards = [h for h in hazards if id(h) not in dropped]

    # 6. Confident hazards first, then most serious first
    hazards.sort(key=lambda h: (TIER_ORDER.get(h.tier, 99), SEVERITY_ORDER.get(h.severity, 99)))

    # 7. [merge-v2] Score, count and summary use confident cards only; "possible"
    #    cards get one extra sentence.
    confident = [h for h in hazards if h.tier != POSSIBLE_TIER]
    summary = build_summary(confident, needs_check)
    n_possible = len(hazards) - len(confident)
    if n_possible:
        summary += f" {n_possible} more {'thing is' if n_possible == 1 else 'things are'} worth a quick check."

    return AnalyzeResponse(
        hazard_count=count_hazards(confident),
        hazards=hazards,
        safe_items=safe_items,
        needs_check=needs_check,
        safety_score=safety_score(confident),
        summary=summary,
        ignored=ignored,
        image_width=request.image_width,
        image_height=request.image_height,
    )


# ---------- [detector integration] photo in, report out ----------

# The models take a few seconds to load, so they load once, in the background,
# when the server starts. Tests replace get_detector() with a fake.
_detector = None
_detector_lock = threading.Lock()


def get_detector():
    global _detector
    with _detector_lock:
        if _detector is None:
            from detector import HazardDetector   # imports torch, so only when needed
            _detector = HazardDetector()
        return _detector


@app.on_event("startup")
def preload_detector():
    threading.Thread(target=get_detector, daemon=True).start()


@app.post("/analyze-image", response_model=AnalyzeResponse, response_model_exclude_none=True)
def analyze_image(response: Response, file: UploadFile = File(...), debug: bool = False, room: str | None = None):
    t0 = time.perf_counter()
    try:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(file.file.read()))).convert("RGB")
    except Exception:
        raise HTTPException(status_code=400, detail="Could not read that file as a photo.")
    t_decode = time.perf_counter() - t0
    detector = get_detector()
    detections = detector.detect(image)
    if debug:
        saved = save_debug_frame(image, detections, getattr(detector, "last_raw", None))   # [debug]
        # [final] tells the page's ?debug=1 log that a frame was saved (or why not)
        response.headers["X-Debug-Saved"] = saved.name if saved else "no stairs/toilet/tub in this frame"
    request = AnalyzeRequest(image_width=image.width, image_height=image.height,
                             detections=[Detection(**d) for d in detections], room=room)
    report = analyze(request)
    report.detections = request.detections   # [merge-v2] for re-sending after an answer
    # [speed] how long each step took on the server (browsers show it in the Network tab)
    timing = {"decode": t_decode, **getattr(detector, "last_timing", {}), "server": time.perf_counter() - t0}
    response.headers["Server-Timing"] = ", ".join(f"{k.replace('-', '_')};dur={v * 1000:.0f}" for k, v in timing.items())
    return report


# [debug] With ?debug=1 (sent by the page in its ?debug=1 mode), frames that produced a
# stair_edge (or step), toilet or tub detection are saved with their boxes drawn, plus a
# clean copy, to see what is mistaken for them. [final] Also frames where the model saw one
# but a decoy / the stair geometry check dropped it (marked "dropped"). Local only, never
# committed (eval_out/ is in .gitignore), at most 200 files.
SCAN_DEBUG_DIR = Path(__file__).parent.parent / "eval_out" / "scan_debug"
SCAN_DEBUG_CLASSES = {"stair_edge", "raised_threshold", "toilet", "tub"}   # steps come from short stair boxes
SCAN_DEBUG_DROPPED_MIN = 0.45   # dropped boxes are drawn from this confidence up


def box_iou(a, b):
    inter = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0
SCAN_DEBUG_MAX_FILES = 200


def save_debug_frame(image, detections, raw=None):
    hits = [d for d in detections if d["class_name"] in SCAN_DEBUG_CLASSES]
    # [final] boxes the model found that never reached the report (decoys, geometry)
    dropped = [{"class_name": c, "confidence": float(sc), "box": b, "tier": "dropped"}
               for c, pairs in (raw or {}).items() if c in SCAN_DEBUG_CLASSES
               for b, sc in pairs if sc >= SCAN_DEBUG_DROPPED_MIN and not any(box_iou(b, d["box"]) >= 0.5 for d in detections)]
    if not hits and not dropped:
        return None
    SCAN_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    if len(list(SCAN_DEBUG_DIR.glob("*.jpg"))) >= SCAN_DEBUG_MAX_FILES:
        return None
    top = max(hits or dropped, key=lambda d: d["confidence"])
    stem = f'{datetime.now():%Y%m%d_%H%M%S_%f}_{top["class_name"]}_{top["confidence"]:.2f}'
    image.save(SCAN_DEBUG_DIR / f"{stem}_raw.jpg", quality=90)   # clean copy, to re-run detection on later
    from PIL import ImageDraw
    img = image.copy()
    draw = ImageDraw.Draw(img)
    width = max(3, img.width // 250)
    for d in detections + dropped:
        color = ("orange" if d["tier"] == "dropped" else "red") if d["class_name"] in SCAN_DEBUG_CLASSES else "gray"
        draw.rectangle(d["box"], outline=color, width=width if color != "gray" else 1)
        if color != "gray":
            draw.text((d["box"][0] + 6, d["box"][1] + 6),
                      f'{d["class_name"]} {d["confidence"]:.2f} {d.get("tier", "")}', fill=color)
    path = SCAN_DEBUG_DIR / f"{stem}_marked.jpg"
    img.save(path, quality=85)
    return path


# ---------- [merge-v2] the app's pages ----------
# Every page in frontend/ (welcome.html, home.html, camera.html, report.html, the
# static/ files) is served as is. GET / serves index.html, which sends first-time
# visitors to welcome.html and everyone else to home.html.
# Mounted LAST, so the API routes above always win.
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
if FRONTEND_DIR.is_dir():   # [final] so a copy of this file elsewhere (the eval scripts' app/) still imports
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
