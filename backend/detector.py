"""
Threshold - Hazard detector
---------------------------
Hybrid detector: Grounding DINO (zero-shot, works in homes it has never seen)
for most classes, our fine-tuned YOLO for grab bars.

Every detection is a dict in the rules engine's Detection format:
    {"class_name": "rug", "confidence": 0.62, "box": [x1, y1, x2, y2], "tier": "hazard"}
box is in pixels. tier is optional for the rules engine and is one of:
    "hazard"           confident
    "possible hazard"  less sure, shown to the user as "check this"
Missing a real hazard is worse than a false alarm, so the "possible hazard"
cutoffs are tuned for recall (see tune_dino.py in the project root).
"""

# ============ SETTINGS - SAFE TO TUNE ============
# Tuned on two houses (test_photos + tune_arya) with tune_dino.py, Oct 3.
#   model:     "dino-base", "dino-tiny" or "yolo"
#   prompts:   phrases Grounding DINO looks for (one DINO pass per class)
#   possible:  lowest confidence kept, as "possible hazard"
#   hazard:    confidence needed for the "hazard" tier
#   max_area:  drop boxes bigger than this fraction of the photo
#   floor:     apply the floor-line rules below (things lying on the floor)
#   merge:     merge nearby boxes into one (cords get split into pieces)
CLASS_SETTINGS = {
    "clutter_on_floor": dict(model="dino-base", possible=0.25, hazard=0.35, max_area=0.25, floor=True, merge=False,
                             prompts=["shoes", "bag", "backpack", "pile of clothes", "cardboard box",
                                      "plastic bag", "books", "bucket", "trash bag", "box",
                                      "clothes on the floor", "toy", "towel", "ball", "keyboard",
                                      "pile of fabric", "object on the floor"]),
    # base, not tiny: tiny's cord scores can't separate real cords from false alarms
    "cord":             dict(model="dino-base", possible=0.30, hazard=0.35, max_area=0.9, floor=True, merge=False,
                             prompts=["electrical cord", "cable", "wire"]),
    # no grab bars in the tuning photos; YOLO's validation mAP50 is 0.89. Only a
    # "hazard"-tier grab bar counts as present for the missing-grab-bar rule.
    "grab_bar":         dict(model="yolo", possible=0.25, hazard=0.40, max_area=0.9, floor=False, merge=False),
    # only 1 real threshold in the tuning photos, so the hazard cutoff is
    # effectively "never"; thresholds show up as "possible hazard".
    # possible=0.50: lowest cutoff with <= ~5 false alarms over both tuning
    # houses (4); also applies to single steps found by the stair_edge pass.
    "raised_threshold": dict(model="dino-base", possible=0.50, hazard=0.70, max_area=0.25, floor=True, merge=False,
                             prompts=["threshold strip", "floor transition strip"]),
    "rug":              dict(model="dino-base", possible=0.40, hazard=0.40, max_area=0.9, floor=False, merge=False,
                             prompts=["rug", "area rug", "bath mat", "runner rug", "doormat"]),
    # hazard 0.45 -> 0.55 after false "stairs" in live scans. Tuning houses, hazard tier:
    # 4/5 -> 2/5 real stairs (the other 2 become "possible"); with possible: 4/5 both; 0 false alarms both.
    "stair_edge":       dict(model="dino-tiny", possible=0.45, hazard=0.55, max_area=0.9, floor=False, merge=False,
                             prompts=["staircase", "stairs"]),
    "toilet":           dict(model="dino-tiny", possible=0.45, hazard=0.65, max_area=0.9, floor=False, merge=False,
                             prompts=["toilet"]),
    "tub":              dict(model="dino-tiny", possible=0.45, hazard=0.45, max_area=0.9, floor=False, merge=False,
                             prompts=["bathtub"]),
}

# [decoys] Things that look like a hazard class but aren't. One extra batched DINO pass
# looks for them, with the SAME model that produced that class's boxes (so the scores are
# comparable); they are NEVER shown to the user. A box of the class is dropped when a decoy
# box overlaps it (IoU >= iou, or >= inside of it lies inside the decoy) and the decoy
# scores at least as high. An empty prompts list turns that class's decoys off.
# Why a separate pass: putting the decoys in the stair prompt itself lowered real stair
# scores below the cutoff (tuning houses: real stairs 4/5 found -> 0/5).
DECOY_SETTINGS = dict(
    min_score=0.30,
    iou=0.4,
    inside=0.8,
    prompts={
        # an IKEA Alex drawer unit's stacked drawers read as stair edges
        "stair_edge": ["dresser", "chest of drawers", "drawer unit", "cabinet", "bookshelf",
                       "window blinds", "radiator"],
        # [final] white or round things that read as a toilet outside bathrooms
        "toilet": ["chair", "stool", "white trash can", "laundry basket", "bucket", "sink",
                   "office chair"],
    },
    # [final] per class: also drop when the decoy scores within `margin` of the box, but
    # only for boxes scoring below `up_to` (sure finds are never dropped by a decoy).
    # Toilet, tuned on SUN RGB-D photos (eval only): margin 0.10 below 0.75 removes half of
    # the false toilets in non-bathrooms (armchairs, bins, suitcases) and keeps 97% of real ones.
    # No margin for stairs: at scan-frame size, real staircases beat their best decoy by only
    # 0.07 (tuning houses), so any useful margin would drop real stairs.
    margin={"toilet": (0.10, 0.75), "tub": (0.10, 0.75)},
    # [final] classes whose decoys also include the sink boxes of the context pass (a sink
    # or vanity read as a "bathtub" is the most common false tub on SUN RGB-D photos)
    sink_is_decoy_for=["tub"],
)
DECOY_KEY = "__decoy__"    # raw[DECOY_KEY] = {class: decoy boxes}, passed to postprocess()

# [final] Bathroom cue: when a toilet or tub is found, the decoy pass also looks for a sink.
# A sink that isn't one of the toilets/tubs is passed to the rules engine as class "sink",
# tier "context" (never shown, never scored). It helps decide if a toilet/tub is real
# (see the missing grab bar rule in main.py).
CONTEXT_SETTINGS = dict(model="dino-tiny", prompts=["sink", "bathroom sink"], min_score=0.35,
                        for_classes=["toilet", "tub"])
CONTEXT_KEY = "__context__"
CONTEXT = "context"

# [final] Stair geometry. Real staircases in the tuning photos are big boxes that reach
# the bottom of the photo (all 5: >= 40% of the photo, bottom edge >= 96% down). A drawer
# unit is a compact box. A tall stair box (see STEP_MAX_HEIGHT) is only "hazard" tier if
# it covers >= STAIR_HAZARD_MIN_AREA of the photo AND its bottom edge is below
# STAIR_MIN_BOTTOM; else it's at most "possible hazard". Below STAIR_MIN_AREA it's dropped.
STAIR_HAZARD_MIN_AREA = 0.30
STAIR_MIN_BOTTOM = 0.85
STAIR_MIN_AREA = 0.12

# Floor rules (only for classes with floor=True), as fractions of photo height:
FLOOR_LINE_MIN = 0.25     # bottom edge above this line -> dropped (it's on a wall/shelf)
FLOOR_LINE_HAZARD = 0.45  # bottom edge above this line -> at most "possible hazard"

# A stair_edge box shorter than this fraction of the photo is a single step,
# not a staircase. It's reported as "possible hazard" raised_threshold
# ("step or raised threshold") so a raised doorway edge isn't missed.
STEP_MAX_HEIGHT = 0.5

NMS_IOU = 0.5              # overlapping boxes of the same class -> keep the best
MERGE_DISTANCE = 0.03      # merge cord pieces closer than this fraction of the photo diagonal
TEXT_THRESHOLD = 0.25      # Grounding DINO: how sure it must be about which phrase matched

# A "possible hazard" box that duplicates a "hazard" box of ANOTHER class is
# dropped (e.g. a bath mat found as rug AND as possible clutter). Duplicate =
#   IoU >= DUPLICATE_IOU, or
#   >= DUPLICATE_INSIDE of it lies inside the hazard box AND it is at least
#   DUPLICATE_MIN_SIZE of that box's area (so a small bag ON a big rug is kept).
DUPLICATE_IOU = 0.5
DUPLICATE_INSIDE = 0.8
DUPLICATE_MIN_SIZE = 0.5

# [ui-v2] Same class: a box with >= SAME_CLASS_INSIDE of its area inside another
# box of that class is the same thing (one pile = one box). Keep the "hazard"-tier
# box if the tiers differ, otherwise the bigger box (with the higher confidence).
SAME_CLASS_INSIDE = 0.8

# [speed] Half precision (fp16) on the GPU for these models: faster, about the same results.
# Leave a model out to run it in full precision. USE_FP16 = False turns fp16 off everywhere.
USE_FP16 = True
# dino-base stays in full precision: in fp16 its batched clutter scores drift by up to
# ~0.005 and add 1-2 false alarms on the tuning houses (dino-tiny: no change at all).
FP16_MODELS = {"dino-tiny"}
# [speed] DINO looks at images with this short side (px). Smaller frames are enlarged to it
# unless ENLARGE_SMALL_FRAMES = False (faster, but the cutoffs were tuned at 800).
DINO_SHORT_SIDE = 800
# [speed] Run one dummy frame through every model when the server starts, so the GPU has
# its kernels ready and the first real photo isn't slow (~2.2 s cold vs ~1.1 s warm).
WARM_UP = True
# [speed] One batched DINO pass per model (False = one pass per class, the old way), and
# decoys only for classes with a box in the photo (False = always). Both give the same results.
BATCH_DINO_PASSES = True
DECOYS_ONLY_WITH_STAIRS = True
ENLARGE_SMALL_FRAMES = True

DINO_MODELS = {"dino-base": "IDEA-Research/grounding-dino-base",
               "dino-tiny": "IDEA-Research/grounding-dino-tiny"}
# YOLO weights: first file that exists (project layout, then the repo's models/ folder)
YOLO_WEIGHTS = ["../runs/threshold_v5/weights/best.pt", "../models/best.pt", "models/best.pt"]

# =================================================

CLASS_NAMES = ["clutter_on_floor", "cord", "grab_bar", "raised_threshold",
               "rug", "stair_edge", "toilet", "tub"]
HAZARD, POSSIBLE = "hazard", "possible hazard"


# ---------- box helpers ----------

def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0


def nms(dets):
    """Keep the most confident of overlapping boxes. dets all one class."""
    keep = []
    for d in sorted(dets, key=lambda d: -d["confidence"]):
        if all(iou(d["box"], k["box"]) < NMS_IOU for k in keep):
            keep.append(d)
    return keep


def merge_nearby(dets, dist):
    """Merge boxes whose gap is smaller than dist. Merged box keeps the best score."""
    dets = [dict(d) for d in dets]
    changed = True
    while changed and len(dets) > 1:
        changed = False
        out, used = [], [False] * len(dets)
        for i, d in enumerate(dets):
            if used[i]:
                continue
            x1, y1, x2, y2 = d["box"]
            conf = d["confidence"]
            for j in range(i + 1, len(dets)):
                if used[j]:
                    continue
                a = dets[j]["box"]
                gx = max(0, max(x1, a[0]) - min(x2, a[2]))
                gy = max(0, max(y1, a[1]) - min(y2, a[3]))
                if (gx ** 2 + gy ** 2) ** 0.5 < dist:
                    x1, y1, x2, y2 = min(x1, a[0]), min(y1, a[1]), max(x2, a[2]), max(y2, a[3])
                    conf = max(conf, dets[j]["confidence"])
                    used[j] = changed = True
            out.append(dict(d, box=[x1, y1, x2, y2], confidence=conf))
            used[i] = True
        dets = out
    return dets


# ---------- raw model output -> tiered detections ----------

def postprocess(raw, width, height, settings=None):
    """raw: {class_name: [(box, score), ...]} straight from the models, boxes in
    pixels. Returns tiered detections in the rules engine's format."""
    settings = settings or CLASS_SETTINGS
    all_decoys = raw.get(DECOY_KEY, {})
    if isinstance(all_decoys, list):          # older callers: a plain list = stair decoys
        all_decoys = {"stair_edge": all_decoys}
    out = []
    for cls, pairs in raw.items():
        if cls in (DECOY_KEY, CONTEXT_KEY):
            continue
        decoys = [(b, sc) for b, sc in all_decoys.get(cls, []) if sc >= DECOY_SETTINGS["min_score"]]
        if cls in DECOY_SETTINGS.get("sink_is_decoy_for", []):
            decoys += [(b, sc) for b, sc in raw.get(CONTEXT_KEY, []) if sc >= CONTEXT_SETTINGS["min_score"]]
        if decoys:   # [decoys] before tiers and the step rule
            margin, up_to = DECOY_SETTINGS.get("margin", {}).get(cls, (0, 0))
            pairs = [(b, sc) for b, sc in pairs if not is_decoy(b, sc, decoys, margin if sc < up_to else 0)]
        s = settings[cls]
        dets = []
        for box, score in pairs:
            x1, y1, x2, y2 = box
            if score < s["possible"] or (x2 - x1) * (y2 - y1) > s["max_area"] * width * height:
                continue
            tier = HAZARD if score >= s["hazard"] else POSSIBLE
            if s["floor"]:
                if y2 < FLOOR_LINE_MIN * height:
                    continue
                if y2 < FLOOR_LINE_HAZARD * height:
                    tier = POSSIBLE
            name = cls
            if cls == "stair_edge" and (y2 - y1) < STEP_MAX_HEIGHT * height:
                # single step, not a staircase. It must also pass the
                # raised_threshold cutoff, so one setting controls both sources.
                if score < settings["raised_threshold"]["possible"]:
                    continue
                name, tier = "raised_threshold", POSSIBLE
            elif cls == "stair_edge":
                # [final] stair geometry (see STAIR_HAZARD_MIN_AREA)
                frac = (x2 - x1) * (y2 - y1) / (width * height)
                if frac < STAIR_MIN_AREA:
                    continue
                if frac < STAIR_HAZARD_MIN_AREA or y2 < STAIR_MIN_BOTTOM * height:
                    tier = POSSIBLE
            dets.append({"class_name": name, "confidence": float(score),
                         "box": [float(v) for v in box], "tier": tier})
        out += dets

    # per-class (and per-tier) NMS, then cord merging
    final = []
    diag = (width ** 2 + height ** 2) ** 0.5
    for name in CLASS_NAMES:
        for tier in (HAZARD, POSSIBLE):
            group = nms([d for d in out if d["class_name"] == name and d["tier"] == tier])
            if settings[name]["merge"]:
                group = merge_nearby(group, MERGE_DISTANCE * diag)
            final += group
    # drop "possible" boxes that duplicate a "hazard" box (same class or another class)
    hazards = [d for d in final if d["tier"] == HAZARD]
    final = [d for d in final if d["tier"] == HAZARD or
             not any(is_duplicate(d, h) for h in hazards)]
    # [ui-v2] then one pile = one box. (After the step above, so a small item ON a
    # rug isn't merged into a duplicate box that then gets dropped.)
    final = absorb_inside(final)
    # [final] bathroom cue: a sink that isn't one of the toilets found (see CONTEXT_SETTINGS)
    toilets = [d["box"] for d in final if d["class_name"] in ("toilet", "tub")]
    sinks = nms([{"class_name": "sink", "confidence": float(sc), "box": [float(v) for v in b], "tier": CONTEXT}
                 for b, sc in raw.get(CONTEXT_KEY, []) if sc >= CONTEXT_SETTINGS["min_score"]])
    return final + [d for d in sinks if not any(iou(d["box"], t) >= DECOY_SETTINGS["iou"] for t in toilets)]


def area(b):
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def is_decoy(box, score, decoys, margin=0):
    """[decoys] True if a decoy box covers this box and scores at least as high (minus margin)."""
    return any(dscore >= score - margin and (iou(box, dbox) >= DECOY_SETTINGS["iou"]
                                    or inside_fraction(box, dbox) >= DECOY_SETTINGS["inside"])
               for dbox, dscore in decoys)


def inside_fraction(small, big):
    """How much of box `small` lies inside box `big` (0..1)."""
    inter = area([max(small[0], big[0]), max(small[1], big[1]), min(small[2], big[2]), min(small[3], big[3])])
    return inter / area(small) if area(small) > 0 else 0


def absorb_inside(dets):
    """[ui-v2] Same class, one box mostly inside another -> keep one (see SAME_CLASS_INSIDE)."""
    keep = [dict(d) for d in sorted(dets, key=lambda d: -area(d["box"]))]   # big first
    dropped = set()
    for i, big in enumerate(keep):
        for j in range(i + 1, len(keep)):
            small = keep[j]
            if (i in dropped or j in dropped or small["class_name"] != big["class_name"]
                    or inside_fraction(small["box"], big["box"]) < SAME_CLASS_INSIDE):
                continue
            if small["tier"] == HAZARD and big["tier"] == POSSIBLE:
                dropped.add(i)          # keep the confident (smaller) box
            else:
                big["confidence"] = max(big["confidence"], small["confidence"])
                dropped.add(j)          # keep the bigger box
    return [d for k, d in enumerate(keep) if k not in dropped]


def is_duplicate(possible, hazard):
    p, h = possible["box"], hazard["box"]
    if possible["class_name"] == hazard["class_name"]:
        return iou(p, h) >= NMS_IOU
    if iou(p, h) >= DUPLICATE_IOU:
        return True
    inter = area([max(p[0], h[0]), max(p[1], h[1]), min(p[2], h[2]), min(p[3], h[3])])
    return (area(p) > 0 and inter / area(p) >= DUPLICATE_INSIDE
            and area(p) >= DUPLICATE_MIN_SIZE * area(h))


# ---------- the detector ----------

class HazardDetector:
    """Loads YOLO + Grounding DINO once. detect(pil_image) -> tiered detections."""

    def __init__(self, device=None):
        import time
        from pathlib import Path

        import torch
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor
        from ultralytics import YOLO

        t0 = time.time()
        self.torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.fp16 = USE_FP16 and self.device == "cuda"
        here = Path(__file__).parent
        weights = next((here / p for p in YOLO_WEIGHTS if (here / p).exists()), None)
        if weights is None:
            raise FileNotFoundError(f"YOLO weights not found, looked for {YOLO_WEIGHTS} next to {here}")
        self.yolo = YOLO(str(weights))
        self.dino = {}
        needed = {s["model"] for s in CLASS_SETTINGS.values()} | {CONTEXT_SETTINGS["model"]}
        for name in sorted(n for n in needed if n in DINO_MODELS):
            processor = AutoProcessor.from_pretrained(DINO_MODELS[name])
            model = AutoModelForZeroShotObjectDetection.from_pretrained(DINO_MODELS[name]).to(self.device)
            self.dino[name] = (processor, model.eval())
        if WARM_UP:
            self.warm_up()
        print(f"HazardDetector ready on {self.device} in {time.time() - t0:.1f}s "
              f"(YOLO {weights.name}, {', '.join(self.dino)})")

    def warm_up(self):
        """[speed] One dummy scan-size frame through every model (decoys too)."""
        from PIL import Image
        frame = Image.new("RGB", (960, 1280), (128, 120, 110))
        self.detect(frame)
        self._extras(frame, set(CLASS_SETTINGS))

    def _extras(self, image, found, raw=None):
        """[decoys] Decoy prompts for every found class that has them, plus the sink cue if a
        toilet/tub was found: one batched pass per model. Fills raw[DECOY_KEY], raw[CONTEXT_KEY]."""
        raw = {} if raw is None else raw
        jobs = {}   # model -> [(key, prompts, threshold)]
        for c in sorted(found):
            if DECOY_SETTINGS["prompts"].get(c) and CLASS_SETTINGS[c]["model"] in self.dino:
                jobs.setdefault(CLASS_SETTINGS[c]["model"], []).append((c, DECOY_SETTINGS["prompts"][c], DECOY_SETTINGS["min_score"]))
        if CONTEXT_SETTINGS["prompts"] and found & set(CONTEXT_SETTINGS["for_classes"]):
            jobs.setdefault(CONTEXT_SETTINGS["model"], []).append((CONTEXT_KEY, CONTEXT_SETTINGS["prompts"], CONTEXT_SETTINGS["min_score"]))
        for model, js in jobs.items():
            results = self._dino_many(model, image, [(p, th) for _, p, th in js])
            for (key, _, _), pairs in zip(js, results):
                if key == CONTEXT_KEY:
                    raw[CONTEXT_KEY] = pairs
                else:
                    raw.setdefault(DECOY_KEY, {})[key] = pairs
        return bool(jobs)

    def _fast(self, name):
        """[speed] inference_mode (no gradient bookkeeping) + fp16 autocast on CUDA."""
        import contextlib
        stack = contextlib.ExitStack()
        stack.enter_context(self.torch.inference_mode())
        if self.fp16 and name in FP16_MODELS:
            stack.enter_context(self.torch.autocast("cuda", dtype=self.torch.float16))
        return stack

    def _dino(self, name, image, prompts, threshold):
        processor, model = self.dino[name]
        w, h = image.size
        text = " ".join(f"{p}." for p in prompts)
        inputs = processor(images=image, text=text, return_tensors="pt").to(self.device)
        with self._fast(name):
            outputs = model(**inputs)
        r = processor.post_process_grounded_object_detection(
            outputs, inputs.input_ids, threshold=threshold, text_threshold=TEXT_THRESHOLD,
            target_sizes=[(h, w)])[0]
        return list(zip(r["boxes"].tolist(), r["scores"].tolist()))

    def _dino_many(self, name, image, jobs):
        """[speed] All of one model's prompts in ONE batched pass. jobs = [(prompts, threshold)].
        Each prompt is still scored on its own (padding is masked out), so results match
        separate passes; the image is preprocessed once and shared."""
        processor, model = self.dino[name]
        w, h = image.size
        texts = [" ".join(f"{p}." for p in prompts) for prompts, _ in jobs]
        short = DINO_SHORT_SIDE if ENLARGE_SMALL_FRAMES else min(DINO_SHORT_SIDE, min(w, h))   # [speed]
        pix = processor.image_processor(image, size={"shortest_edge": short, "longest_edge": 1333}, return_tensors="pt")
        tok = processor.tokenizer(texts, padding="longest", return_tensors="pt")
        n = len(jobs)
        inputs = {**{k: v.to(self.device) for k, v in tok.items()},
                  "pixel_values": pix["pixel_values"].repeat(n, 1, 1, 1).to(self.device)}
        if "pixel_mask" in pix:
            inputs["pixel_mask"] = pix["pixel_mask"].repeat(n, 1, 1).to(self.device)
        with self._fast(name):
            outputs = model(**inputs)
        results = processor.post_process_grounded_object_detection(
            outputs, inputs["input_ids"], threshold=min(th for _, th in jobs), text_threshold=TEXT_THRESHOLD,
            target_sizes=[(h, w)] * n)
        return [[(b, sc) for b, sc in zip(r["boxes"].tolist(), r["scores"].tolist()) if sc >= th]
                for r, (_, th) in zip(results, jobs)]

    def detect(self, image):
        """image: upright RGB PIL image (call ImageOps.exif_transpose first).
        Returns [{"class_name", "confidence", "box": [x1, y1, x2, y2] px, "tier"}].
        [speed] Seconds per step are kept in self.last_timing."""
        import time
        timing = {}
        t0 = t = time.perf_counter()
        def lap(name):
            nonlocal t
            now = time.perf_counter(); timing[name] = timing.get(name, 0) + now - t; t = now
        image = image.convert("RGB")
        w, h = image.size
        raw = {}
        yolo_classes = [c for c, s in CLASS_SETTINGS.items() if s["model"] == "yolo"]
        if yolo_classes:
            low = min(CLASS_SETTINGS[c]["possible"] for c in yolo_classes)
            r = self.yolo(image, conf=low, verbose=False, device=self.device)[0]
            for b, s, c in zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist(), r.boxes.cls.tolist()):
                name = r.names[int(c)]
                if name in yolo_classes:
                    raw.setdefault(name, []).append((b, s))
            lap("yolo")
        # [speed] one batched pass per DINO model (instead of one pass per class)
        for name in self.dino:
            classes = [c for c, s in CLASS_SETTINGS.items() if s["model"] == name]
            if classes and BATCH_DINO_PASSES:
                found = self._dino_many(name, image, [(CLASS_SETTINGS[c]["prompts"], CLASS_SETTINGS[c]["possible"]) for c in classes])
                raw.update(zip(classes, found))
            for c in ([] if BATCH_DINO_PASSES else classes):
                raw[c] = self._dino(name, image, CLASS_SETTINGS[c]["prompts"], CLASS_SETTINGS[c]["possible"])
            if classes:
                lap(name)
        # [decoys] look-alikes (and [final] the sink cue), never shown to the user.
        # [speed] Only for classes with a box in this photo: they never change anything else.
        found = {c for c in CLASS_SETTINGS if raw.get(c)} if DECOYS_ONLY_WITH_STAIRS else set(CLASS_SETTINGS)
        if self._extras(image, found, raw):
            lap("decoys")
        self.last_raw = dict(raw)   # [final] raw model boxes (incl. decoys), for the debug capture and tuning
        dets = postprocess(raw, w, h)
        lap("postprocess")
        timing["total"] = time.perf_counter() - t0
        self.last_timing = timing
        print(f"detect: {len(dets)} detections in {timing['total']:.2f}s ({w}x{h})")
        return dets


if __name__ == "__main__":
    # python detector.py <photo>   -> prints the detections for one photo
    import sys

    from PIL import Image, ImageOps
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass

    if len(sys.argv) != 2:
        sys.exit("usage: python detector.py <photo>")
    img = ImageOps.exif_transpose(Image.open(sys.argv[1]))
    detector = HazardDetector()
    detector.detect(img)                       # first run warms up the GPU
    for d in detector.detect(img):
        x1, y1, x2, y2 = (round(v) for v in d["box"])
        print(f'{d["tier"]:16s} {d["class_name"]:18s} {d["confidence"]:.2f}  [{x1}, {y1}, {x2}, {y2}]')
