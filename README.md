# Threshold

**Point your phone at a room. Threshold finds fall hazards and tells you how to fix them.**

Threshold is a phone-camera app that looks for common fall hazards at home: cords on the
floor, loose rugs, clutter, steps and raised thresholds, stairs, and a toilet or tub with
no grab bar. Each finding comes with a simple fix from the CDC STEADI "Check for Safety"
home checklist. It is built for older adults living alone, especially in rural New Hampshire.

Threshold can miss things. It is not a replacement for a professional home safety assessment.

## Team

Congressional App Challenge 2026, New Hampshire's 2nd district (NH-02).

| Name | Role |
|---|---|
| Kurosh Kavoosi | Machine learning, dataset, hazard detector |
| Prithvi Gupta | Rules engine (FastAPI backend) |
| Arya Somasundaram | Frontend design |

## How it works

1. **The phone sends photos.** In live Scan mode the app sends a camera frame every few
   seconds. In photo mode it sends one photo. Both go to the backend (`POST /analyze-image`).
2. **A hybrid detector finds objects** (`backend/detector.py`):
   - **Grounding DINO** (IDEA Research), a zero-shot detector that finds objects from a
     text prompt. It handles most classes because it works in homes it has never seen.
   - **YOLO11** (Ultralytics), fine-tuned on our own hand-labeled photos, for grab bars.
   - Extra checks cut false alarms: "decoy" prompts for look-alikes (a drawer unit is not a
     staircase, a chair is not a toilet), a stair shape check, and stricter rules outside
     bathrooms.
   - Every detection gets a tier: "hazard" (confident) or "possible hazard" (shown as
     "Worth a quick check").
3. **A rules engine writes the report** (`backend/main.py`, FastAPI). It turns detections
   into hazard cards with a fix and the matching STEADI checklist item, a safety score and a
   summary. Grab bars use an inverted rule: a toilet or tub with no grab bar nearby is the
   hazard, and an unsure grab bar becomes a Yes/No question for the user.
4. **The web frontend** (`frontend/`) works like a native iPhone app: live scan with boxes
   on the camera view, spoken results and voice commands, a report with "Fixed it"
   checkmarks, and a list of rooms. It is plain HTML, CSS and JavaScript served by the
   backend, and it can be added to the iPhone home screen.

## Results on held-out photos

We tested on 4 photos from a teammate's home that were never used for training or tuning.
We labeled 16 hazards in them by hand (10 clutter, 4 cords, 1 rug, 1 raised threshold). A
hazard counts as found when the app puts a box of the right class on it (overlap of at
least 30%).

| Model | Hazards found |
|---|---|
| First model (YOLO11 trained on our own dataset only) | 1 of 16 |
| Current system (v1.0.4, YOLO11 + Grounding DINO) | **9 of 16**: 5 confirmed, plus 4 flagged as "worth a quick check" |

The current system also made 3 false alarms among confirmed hazards (10 when the "worth a
quick check" items are included). It still misses most cords and the threshold strip. This
is a small test set, so these numbers are a rough guide, not a benchmark. (Measured on
October 12, 2026 with the code of v1.0.3; v1.0.4 did not change any detection code.)

What we learned: the first model scored well on its own validation set, but those images
came from the same few homes as the training images. Variety of homes mattered more than
the number of images, which is why we added a zero-shot detector.

## Run it

You need Python 3.10 or newer. An NVIDIA GPU makes it fast (about 1 to 2 seconds per photo);
it also runs on a CPU, just slower.

**1. Install (once):**

```
python -m venv venv
venv\Scripts\activate
pip install -r backend\requirements.txt
```

(On Mac or Linux: `source venv/bin/activate`.) For a GPU, install the CUDA build of PyTorch
from https://pytorch.org first.

**2. Add YOLO weights.** Our fine-tuned weights are not included (see below). The app needs
a YOLO11 model file at `models/best.pt`:
- To try the app, download the standard `yolo11s.pt` from Ultralytics and save it as
  `models/best.pt`. Everything works except grab bar detection, so every toilet and tub is
  reported as missing a grab bar.
- To detect grab bars, train YOLO11 on your own labeled photos with a class named
  `grab_bar` and save the result as `models/best.pt`.

**3. Start the server** (it serves the app and the API):

```
cd backend
uvicorn main:app --host 0.0.0.0
```

The first start downloads the Grounding DINO models from Hugging Face (about 1 GB, once).
Then open http://127.0.0.1:8000 (API docs at http://127.0.0.1:8000/docs).

**4. Use it from a phone.** Live scan needs https. On Windows, `start_demo.bat` starts the
server and a free Cloudflare quick tunnel (install `cloudflared` first) and prints a link to
open on the phone. Anyone with that link can use the app while it runs, so close it after.

**5. Run the tests:**

```
python -m pytest backend -q
```

The tests use a fake detector, so they don't need the models and run in about a second.

## What is not included

- **Model weights.** Our fine-tuned YOLO11 weights are not in this repository.
- **The dataset.** Our training images include frames from public home tour videos and
  images from public datasets that we can't redistribute, plus photos of our own homes.
- **Test photos.** The photos we tested on show the inside of real homes, so they stay private.

## Built with

- [Ultralytics YOLO11](https://github.com/ultralytics/ultralytics) (AGPL-3.0)
- [Grounding DINO](https://github.com/IDEA-Research/GroundingDINO) (Apache-2.0), used through
  Hugging Face `transformers`
- [FastAPI](https://fastapi.tiangolo.com/), PyTorch, Pillow
- The [CDC STEADI](https://www.cdc.gov/steadi/) "Check for Safety" home checklist

## AI use disclosure

- YOLO11 (Ultralytics), fine-tuned on our own hand-labeled photos.
- Grounding DINO (IDEA Research), used zero-shot.
- Claude (Anthropic) was used as a coding and labeling assistant.

## Privacy

Photos go only to the server you run yourself, which analyzes them and does not keep them.
(Exception: with `?debug=1` in the link, the server saves frames that show stairs, a toilet
or a tub to a local `eval_out/scan_debug/` folder, for tuning.) The newest report, including
its photos, plus your rooms and "Fixed it" checkmarks, are saved only in the phone's browser.
"Clear saved rooms" in Settings deletes all of it.

## License

GNU Affero General Public License v3.0 (AGPL-3.0), see [LICENSE](LICENSE). We use AGPL-3.0
because Threshold depends on Ultralytics YOLO, which is licensed under AGPL-3.0. The other
libraries above have their own licenses.

Copyright (C) 2026 Kurosh Kavoosi, Prithvi Gupta, Arya Somasundaram.
