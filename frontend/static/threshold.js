/* Threshold shared code [merge-v2]
 * Loaded by every page (welcome, home, camera, report). One copy of:
 *   [final] native-style UI pieces (tab bar, sheets, room picker, icons, haptics),
 *   settings + API calls, hazard labels and tiers, box drawing (scan view),
 *   voice: spoken results, voice commands, iPhone fixes, ?debug=1 log.
 * Everything is on window.Threshold (short name T inside the pages).
 */
(function () {
  "use strict";
  const T = window.Threshold = {};

  // ============ SETTINGS ============
  // [phone] "" = the same server that served the page (works on a phone, over Wi-Fi
  // or the https tunnel). Old two-server setup (port 5500, or a file) uses port 8000.
  T.API_URL = (location.protocol === "file:" || location.port === "5500")
    ? `http://${location.hostname || "127.0.0.1"}:8000` : "";
  T.MAX_SIDE = 2000;          // [phone] photos are shrunk to this before upload
  T.SCAN_EVERY_MS = 2500;     // [ui-v2] live scan: one frame every 2.5 s (if the last one finished)
  T.SCAN_MAX_SIDE = 1280;
  T.MAX_POSSIBLE_CARDS = 3;
  T.CONFIRM_FRAMES = 2;       // [scan-confirm] a hazard type counts once it's in 2 of the last 3 frames
  T.CONFIRM_WINDOW = 3;
  T.BOX_HOLD_MS = 2500;       // [speed] live boxes stay fully visible this long...
  T.BOX_FADE_MS = 1500;       // ...then fade out over this long (unless new boxes arrive first)
  // [voice-ios] ?debug=1 turns on the on-screen log; it stays on while you move between pages
  // (?debug=0 turns it off).
  try {
    const q = new URLSearchParams(location.search).get("debug");
    if (q === "1") sessionStorage.setItem("threshold.debug", "1");
    if (q === "0") sessionStorage.removeItem("threshold.debug");
    T.DEBUG = sessionStorage.getItem("threshold.debug") === "1";
  } catch (e) { T.DEBUG = false; }
  // [v1.0.4] ?nomic=1 = "filming mode": the microphone never turns on, so iPhone Safari keeps the audio
  // session in playback only. Spoken hazard announcements then play reliably and get captured by iOS
  // screen recording. It sticks until you open the app with ?nomic=0.
  try {
    const nm = new URLSearchParams(location.search).get("nomic");
    if (nm === "1") localStorage.setItem("threshold.nomic", "1");
    if (nm === "0") localStorage.removeItem("threshold.nomic");
    T.NOMIC = localStorage.getItem("threshold.nomic") === "1";
  } catch (e) { T.NOMIC = false; }
  // ==================================

  T.LABELS = {
    cord: "Cord or wire on the floor", rug: "Loose rug", clutter_on_floor: "Clutter on the floor",
    raised_threshold: "Step or raised threshold", stair_edge: "Stairs", missing_grab_bar: "Missing grab bar",
  };
  T.REMINDERS = new Set(["stair_edge"]);   // shown as a check, never as a hazard
  T.SPOKEN = {                              // [voice] [what to call it, the short fix]
    cord: ["a cord on the floor", "coil it next to the wall"],
    rug: ["a loose rug", "tape it down, or put it away"],
    clutter_on_floor: ["clutter on the floor", "pick it up off the floor"],
    raised_threshold: ["a step or raised threshold", "watch your step, or mark the edge with bright tape"],
    stair_edge: ["stairs", "use the handrail, and keep them well lit"],
    missing_grab_bar: ["a toilet or tub with no grab bar", "a grab bar there would help"],
  };
  T.esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  // [v1.0.1] "Missing grab bar by the toilet" (the card says which fixture)
  const fixturesOf = hz => (hz.fixtures && hz.fixtures.length ? hz.fixtures.join(" and ") : "");
  T.name = hz => hz.hazard === "missing_grab_bar" && fixturesOf(hz) ? `Missing grab bar by the ${fixturesOf(hz)}` : (T.LABELS[hz.hazard] || hz.hazard);
  T.count = hz => Number.isFinite(hz.count) ? hz.count : (hz.boxes || []).length || 1;

  // ---------- tiers ----------
  T.isPossible = hz => hz.tier === "possible hazard" || hz.tier === "possible" || hz.possible === true;
  // [voice] If a hazard type already has a confident (red) card, its "possible" boxes are
  // folded into that card (drawn dashed) instead of a separate "Worth a quick check" card.
  T.foldPossible = function (hazards) {
    const list = (hazards || []).map(hz => ({ ...hz }));
    const red = new Map(list.filter(hz => !T.isPossible(hz)).map(hz => [hz.hazard, hz]));
    return list.filter(hz => {
      const card = T.isPossible(hz) && red.get(hz.hazard);
      if (!card) return true;
      card.possible_boxes = [...(card.possible_boxes || []), ...(hz.boxes || [])];
      return false;
    });
  };

  // ---------- images ----------
  T.shrinkToCanvas = function (source, w, h, maxSide) {
    const k = Math.min(1, maxSide / Math.max(w, h));
    const c = document.createElement("canvas");
    c.width = Math.round(w * k); c.height = Math.round(h * k);
    c.getContext("2d").drawImage(source, 0, 0, c.width, c.height);
    return c;
  };
  T.canvasToBlob = (c, q = 0.88) => new Promise(r => c.toBlob(r, "image/jpeg", q));
  T.iou = function (a, b) {
    const ix = Math.max(0, Math.min(a[2], b[2]) - Math.max(a[0], b[0]));
    const iy = Math.max(0, Math.min(a[3], b[3]) - Math.max(a[1], b[1]));
    const inter = ix * iy, area = r => Math.max(0, r[2] - r[0]) * Math.max(0, r[3] - r[1]);
    const u = area(a) + area(b) - inter;
    return u > 0 ? inter / u : 0;
  };

  // ---------- API (format unchanged) ----------
  T.analyzeImage = async function (blob, room) {
    const form = new FormData();
    form.append("file", blob, "photo.jpg");
    // [debug] in ?debug=1 mode the server saves frames with stairs/steps/toilets/tubs.
    // [final] room = the room the user picked (stricter toilet/tub/grab bar outside bathrooms)
    const q = new URLSearchParams();
    if (T.DEBUG) q.set("debug", "1");
    if (room) q.set("room", room);
    const res = await fetch(T.API_URL + "/analyze-image" + (q.toString() ? "?" + q : ""), { method: "POST", body: form });
    if (!res.ok) throw new Error("server said " + res.status);
    // [final] ?debug=1: show whether the server saved this frame (no header = old server code)
    if (T.DEBUG) T.dbg("debug frame: " + (res.headers.get("X-Debug-Saved") || "NOT SAVED, server is running old code?"));
    return res.json();
  };
  // [final] bathroomCue: Scan mode's own bathroom cue (true/false), undefined for photos
  T.analyzeDetections = async function (w, h, detections, room, bathroomCue) {
    const res = await fetch(T.API_URL + "/analyze", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ image_width: w, image_height: h, detections, room: room || undefined,
                             bathroom_cue: typeof bathroomCue === "boolean" ? bathroomCue : undefined }),
    });
    if (!res.ok) throw new Error("server said " + res.status);
    return res.json();
  };
  // [merge-v2] Agreed grab bar rule. Answer to a needs_check question -> detections to
  // re-send: "yes" = that grab bar as tier "hazard" (cancels the warning),
  // "no" = without that grab bar (becomes "Missing grab bar"). null if nothing matched.
  T.answerDetections = function (detections, question, answer) {
    if (!detections) return null;
    const isThat = d => d.class_name === "grab_bar" &&
      (question.grab_bar_boxes || []).some(b => T.iou(b, d.box) >= 0.5);
    if (!detections.some(isThat)) return null;
    if (answer === "no") return detections.filter(d => !isThat(d));
    return detections.map(d => isThat(d) ? { ...d, tier: "hazard", confidence: Math.max(d.confidence || 0, 0.4) } : d);
  };

  // ---------- drawing boxes on a canvas (live scan view, thumbnails) ----------
  // [ui-v2] Confident: solid box, ONE label per hazard type (biggest box, with count).
  // Possible: thin dashed yellow box, no label. Folded "possible" boxes: dashed in the
  // card's color. Labels never overlap. k scales the server's box pixels to the canvas.
  // [final] red = real hazard, gray = stairs reminder, amber dashed = worth a check
  const SEVERITY_COLORS = { high: "#d70015", medium: "#d70015", info: "#636366" };
  const LABEL_FILL = { high: "#d70015", medium: "#d70015", info: "#636366" };
  const POSSIBLE_COLOR = "#ff9f0a";
  T.drawHazards = function (c, W, H, hazards, k) {
    const line = Math.max(3, W / 150), fontSize = Math.max(13, Math.round(line * 5.5));
    const labelH = Math.round(fontSize * 1.45), pad = line * 1.5;
    c.font = `600 ${fontSize}px -apple-system, system-ui, sans-serif`;
    const sc = b => b.map(v => v * k);
    const box = ([x1, y1, x2, y2], color, dash, width = line) => {
      c.setLineDash([]); c.lineWidth = width + 3; c.strokeStyle = "rgba(255,255,255,0.75)"; c.strokeRect(x1, y1, x2 - x1, y2 - y1);
      c.setLineDash(dash); c.lineWidth = width; c.strokeStyle = color; c.strokeRect(x1, y1, x2 - x1, y2 - y1);
    };
    const labels = [];
    for (const hz of [...hazards].sort((a, b) => T.isPossible(b) - T.isPossible(a))) {
      const boxes = (hz.boxes || []).map(sc); if (!boxes.length) continue;
      if (T.isPossible(hz)) { for (const b of boxes) box(b, POSSIBLE_COLOR, [line * 2, line * 1.5], Math.max(2, line / 2)); continue; }
      const color = SEVERITY_COLORS[hz.severity] || "#1f5f8b";
      for (const b of boxes) box(b, color, []);
      for (const b of (hz.possible_boxes || []).map(sc)) box(b, color, [line * 2, line * 1.5], Math.max(2, line / 2));
      const big = boxes.reduce((a, b) => (b[2] - b[0]) * (b[3] - b[1]) > (a[2] - a[0]) * (a[3] - a[1]) ? b : a);
      const n = T.count(hz);
      labels.push({ text: T.name(hz) + (n > 1 ? ` (${n})` : ""), fill: LABEL_FILL[hz.severity] || "#1f5f8b", x1: big[0], y1: big[1] });
    }
    c.setLineDash([]);
    const placed = [], overlaps = (a, b) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
    for (const lb of labels) {
      let text = lb.text;
      while (c.measureText(text).width + pad * 2 > W && text.length > 4) text = text.slice(0, -2) + "…";
      const w = Math.min(W, c.measureText(text).width + pad * 2);
      const rect = { x: Math.max(0, Math.min(lb.x1, W - w)), y: lb.y1 - labelH < 0 ? Math.max(0, lb.y1) : lb.y1 - labelH, w, h: labelH };
      for (let t = 0; t < 50; t++) { const hit = placed.find(p => overlaps(rect, p)); if (!hit) break; rect.y = hit.y + hit.h + 2; }
      rect.y = Math.min(rect.y, H - labelH); placed.push(rect);
      c.fillStyle = lb.fill; c.fillRect(rect.x, rect.y, rect.w, rect.h);
      c.fillStyle = "#fff"; c.textBaseline = "middle"; c.fillText(text, rect.x + pad, rect.y + labelH / 2 + 1);
    }
    c.textBaseline = "alphabetic";
  };

  // ---------- what to say ----------
  const capFirst = s => s.charAt(0).toUpperCase() + s.slice(1);
  T.capFirst = capFirst;
  T.speakList = a => a.length < 2 ? (a[0] || "") : `${a.slice(0, -1).join(", ")}, and ${a[a.length - 1]}`;
  T.spokenParts = hz => hz.hazard === "missing_grab_bar" && fixturesOf(hz)
    ? [`no grab bar by the ${fixturesOf(hz)}`, "a grab bar there would help"]
    : T.SPOKEN[hz.hazard] || [T.name(hz).toLowerCase(), "take a look"];
  T.hazardSentence = function (hz) {
    const [name, fix] = T.spokenParts(hz), n = T.count(hz);
    return `${capFirst(name)}${n > 1 ? `, in ${n} places` : ""}: ${fix}.`;
  };
  // "I found 2 things. A cord on the floor: coil it next to the wall. ..."
  T.reportSpeech = function (report) {
    const hz = T.foldPossible(report.hazards || []);
    const sure = hz.filter(h => !T.isPossible(h));
    const maybe = hz.filter(T.isPossible).slice(0, T.MAX_POSSIBLE_CARDS);
    const more = maybe.length ? ` ${maybe.length === 1 ? "One more thing is" : `${maybe.length} more things are`} worth a quick check.` : "";
    if (!sure.length) return `I didn't find any clear hazards here.${more}`;
    return `I found ${sure.length === 1 ? "one thing" : `${sure.length} things`}. ${sure.map(T.hazardSentence).join(" ")}${more}`;
  };
  T.questionSpeech = q => `${q.question || `Is there a grab bar next to the ${q.fixture}?`} Say yes or no.`;

  // =====================================================================
  // [final] Native-style UI pieces shared by every page: line icons, the bottom tab
  // bar, the nav bar's scroll border, sheets, the room picker, toasts, haptics.
  // =====================================================================
  const P = {   // simple line icons, 24x24
    home: '<path d="M3 11.2 12 4l9 7.2"/><path d="M5.5 9.5V20h4.5v-6h4v6h4.5V9.5"/>',
    rooms: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
    scan: '<path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2"/><circle cx="12" cy="12" r="3.2"/>',
    report: '<path d="M7 3h7l4 4v14H7z"/><path d="M14 3v4h4M10 12h5M10 16h5"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M12 2.8v2.4M12 18.8v2.4M4.2 7.5l2.1 1.2M17.7 15.3l2.1 1.2M4.2 16.5l2.1-1.2M17.7 8.7l2.1-1.2"/><circle cx="12" cy="12" r="7"/>',
    back: '<path d="M15 5l-7 7 7 7"/>',
    chevron: '<path d="M9 5l7 7-7 7"/>',
    speaker: '<path d="M4 9.5h3.5L12 6v12l-4.5-3.5H4z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/>',
    stop: '<rect x="7" y="7" width="10" height="10" rx="2"/>',
    mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    camera: '<path d="M4 8h3l1.6-2.4h6.8L17 8h3v11H4z"/><circle cx="12" cy="13.3" r="3.4"/>',
    share: '<path d="M12 3v12M8 7l4-4 4 4M6 11H5v10h14V11h-1"/>',
    trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
    question: '<circle cx="12" cy="12" r="9"/><path d="M9.6 9.4a2.5 2.5 0 1 1 3.4 2.3c-.7.3-1 .8-1 1.5v.4M12 16.8v.2"/>',
    bath: '<path d="M3 12h18v2a5 5 0 0 1-5 5H8a5 5 0 0 1-5-5z"/><path d="M6 12V6a2 2 0 0 1 4 0M7 19l-1 2M17 19l1 2"/>',
    bed: '<path d="M3 18V7M3 14h18v4M21 14v-2a3 3 0 0 0-3-3h-7v5"/><circle cx="7" cy="11" r="2"/>',
    pot: '<path d="M4 10h16v5a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5z"/><path d="M2 10h20M9 6c0-1 1-1 1-2M14 6c0-1 1-1 1-2"/>',
    sofa: '<path d="M4 11V8a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v3"/><path d="M2 13a2 2 0 0 1 4 0v1h12v-1a2 2 0 0 1 4 0v4H2zM4 17v2M20 17v2"/>',
    hall: '<path d="M7 21V5l10-2v18M4 21h16"/><circle cx="14" cy="12" r=".8"/>',
    stairs: '<path d="M3 20h5v-4h4v-4h4V8h5M3 20h18"/>',
    other: '<circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/>',
    cord: '<path d="M4 18c3 0 3-4 6-4s3 4 6 4 3-6 4-9"/><path d="M18 4v4M21 6h-3"/>',
    rug: '<rect x="4" y="6" width="16" height="12" rx="1"/><path d="M4 9h16M4 15h16"/>',
    clutter: '<path d="M4 20l2-8h6l2 8z"/><path d="M14 20l1.5-6h5L19 20zM8 12V8h3"/>',
    step: '<path d="M3 18h7v-4h11"/><path d="M3 21h18"/>',
    grabbar: '<path d="M5 10h14"/><path d="M5 8v4M19 8v4"/><path d="M6 15c0 3 2 5 6 5s6-2 6-5"/>',
    warn: '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17v.3"/>',
  };
  T.ICON_PATHS = P;
  T.icon = (name, cls = "i") => `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true">${P[name] || P.other}</svg>`;
  T.HAZARD_ICON = { cord: "cord", rug: "rug", clutter_on_floor: "clutter", raised_threshold: "step", stair_edge: "stairs",
                    missing_grab_bar: "grabbar", q: "question" };

  // The rooms people pick from (same order everywhere).
  T.ROOMS = [
    { name: "Bathroom", icon: "bath" }, { name: "Bedroom", icon: "bed" }, { name: "Kitchen", icon: "pot" },
    { name: "Living room", icon: "sofa" }, { name: "Hallway", icon: "hall" }, { name: "Stairs", icon: "stairs" },
    { name: "Other", icon: "other" },
  ];
  T.roomIcon = name => (T.ROOMS.find(r => r.name === name) || { icon: "other" }).icon;
  // [final] report title: the room the user picked, or "Room report"
  T.reportTitle = room => room && room !== "Other" ? `${room} report` : "Room report";

  // Haptic tap: Android vibrates; iPhone (iOS 18+) ticks when a hidden switch is toggled.
  let hapticEl = null;
  T.haptic = function () {
    try {
      if (navigator.vibrate) { navigator.vibrate(10); return; }
      if (!hapticEl) {
        hapticEl = document.createElement("label");
        hapticEl.setAttribute("aria-hidden", "true");
        hapticEl.style.cssText = "position:fixed;width:1px;height:1px;overflow:hidden;opacity:0;pointer-events:none;left:-9px";
        hapticEl.innerHTML = '<input type="checkbox" switch tabindex="-1">';
        document.body.appendChild(hapticEl);
      }
      hapticEl.click();
    } catch (e) {}
  };

  // Bottom tab bar. active = "home" | "rooms" | "report" | "settings". On home.html the
  // tabs switch views in place; elsewhere they are links back to home.html.
  T.tabBar = function (active, onTab) {
    const tabs = [["home", "Home", "home"], ["rooms", "Rooms", "rooms"], ["scan", "Scan", "scan"],
                  ["report", "Report", "report"], ["settings", "Settings", "settings"]];
    const nav = document.createElement("nav");
    nav.className = "tabbar"; nav.setAttribute("aria-label", "Tabs");
    nav.innerHTML = `<div class="tabbar-in">${tabs.map(([id, label, icon]) => {
      const href = id === "report" ? "report.html" : `home.html#${id}`;
      const inner = id === "scan" ? `<span class="disc">${T.icon(icon)}</span>${label}` : `${T.icon(icon)}${label}`;
      return `<a class="tab tab-${id}" href="${href}" data-tab="${id}" ${id === active ? 'aria-current="page"' : ""}>${inner}</a>`;
    }).join("")}</div>`;
    document.body.appendChild(nav);
    nav.addEventListener("click", e => {
      const a = e.target.closest("[data-tab]"); if (!a) return;
      T.haptic();
      if (onTab && onTab(a.dataset.tab) !== false) e.preventDefault();
    });
    T.setTab = id => nav.querySelectorAll("[data-tab]").forEach(a => a.dataset.tab === id ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current"));
    return nav;
  };

  // Nav bar: show the small title + border once the large title scrolls away.
  let navEl = null, navTitleEl = null;
  const updateNav = () => navEl && navEl.classList.toggle("scrolled",
    !navTitleEl || navTitleEl.getBoundingClientRect().bottom < navEl.getBoundingClientRect().bottom);
  addEventListener("scroll", updateNav, { passive: true });
  T.watchNav = function (nav, largeTitle) { navEl = nav; navTitleEl = largeTitle; updateNav(); };
  // Back links slide the other way (page transition).
  document.addEventListener("click", e => { if (e.target.closest(".back")) document.documentElement.classList.add("back"); });
  addEventListener("pageshow", () => document.documentElement.classList.remove("back"));

  // Sheets: T.openSheet(el), T.closeSheet(el). One shared scrim.
  let scrim = null, openSheetEl = null, lastFocus = null;
  T.openSheet = function (el) {
    if (!scrim) {
      scrim = document.createElement("div"); scrim.className = "scrim";
      scrim.addEventListener("click", () => openSheetEl && T.closeSheet(openSheetEl));
      document.body.appendChild(scrim);
      document.addEventListener("keydown", e => { if (e.key === "Escape" && openSheetEl) T.closeSheet(openSheetEl); });
    }
    lastFocus = document.activeElement;
    openSheetEl = el;
    el.setAttribute("role", "dialog"); el.setAttribute("aria-modal", "true");
    scrim.classList.add("open"); el.classList.add("open");
    el.tabIndex = -1;   // focus the sheet itself (screen readers start there, no focus ring on a button)
    setTimeout(() => el.focus({ preventScroll: true }), 50);
  };
  T.closeSheet = function (el) {
    if (scrim) scrim.classList.remove("open");
    el.classList.remove("open");
    if (openSheetEl === el) openSheetEl = null;
    if (el.onclose) el.onclose();
    if (lastFocus && lastFocus.focus) lastFocus.focus({ preventScroll: true });
  };

  // Room picker sheet. onPick(roomName) runs INSIDE the tap (so a scan can start the
  // camera and the voice from it). opts.title, opts.lead.
  // [v1.0.2] opts.quick: a "Quick scan (skip)" button on top that calls onPick(null).
  T.roomSheet = function (onPick, opts = {}) {
    let el = document.getElementById("roomSheet");
    if (!el) {
      el = document.createElement("section");
      el.className = "sheet"; el.id = "roomSheet"; el.setAttribute("aria-labelledby", "roomSheetTitle");
      document.body.appendChild(el);
    }
    el.innerHTML = `<div class="grabber" aria-hidden="true"></div>
      <div class="sheet-head"><button class="nav-btn" type="button" data-close>Cancel</button>
        <h2 id="roomSheetTitle">${T.esc(opts.title || "Which room?")}</h2><span></span></div>
      ${opts.lead ? `<p class="lead">${T.esc(opts.lead)}</p>` : ""}
      ${opts.quick ? `<button class="btn primary" type="button" data-quick>${T.icon("scan")}Quick scan (skip)</button>
        <p class="group-foot" style="margin:8px 16px 16px">Start right away. You can name the room on the report.</p>` : ""}
      <div class="group">${T.ROOMS.map(r => `<button class="row" type="button" data-room="${r.name}">
        <span class="ico">${T.icon(r.icon)}</span><span class="row-main"><span class="row-title">${r.name}</span></span>
        <svg class="chev" viewBox="0 0 24 24" aria-hidden="true">${P.chevron}</svg></button>`).join("")}</div>`;
    el.onclick = e => {
      if (e.target.closest("[data-close]")) { T.closeSheet(el); return; }
      if (e.target.closest("[data-quick]")) { T.haptic(); T.closeSheet(el); onPick(null); return; }
      const b = e.target.closest("[data-room]"); if (!b) return;
      T.haptic();
      T.closeSheet(el);
      onPick(b.dataset.room);
    };
    T.openSheet(el);
    return el;
  };

  // Short message at the bottom ("Saved rooms cleared").
  T.toast = function (text) {
    let el = document.getElementById("toast");
    if (!el) { el = document.createElement("div"); el.id = "toast"; el.className = "toast"; el.setAttribute("role", "status"); document.body.appendChild(el); }
    el.textContent = text; el.classList.add("show");
    clearTimeout(el._t); el._t = setTimeout(() => el.classList.remove("show"), 2600);
  };

  // The newest report, kept so the Report tab can open it later (if it fits in storage).
  T.saveLastReport = function (data) {
    try { sessionStorage.setItem("threshold.report", JSON.stringify(data)); } catch (e) {}
    try { localStorage.setItem("threshold.lastReport", JSON.stringify(data)); } catch (e) { try { localStorage.removeItem("threshold.lastReport"); } catch (e2) {} }
  };
  T.loadReport = function () {
    for (const [store, key] of [[sessionStorage, "threshold.report"], [localStorage, "threshold.lastReport"]]) {
      try { const d = JSON.parse(store.getItem(key) || "null"); if (d && d.report) return d; } catch (e) {}
    }
    return null;
  };
  T.savedRooms = function () { try { return JSON.parse(localStorage.getItem("threshold.rooms") || "[]"); } catch (e) { return []; } };

  // =====================================================================
  // [voice] [voice-ios] Spoken results + voice commands
  // iPhone Safari rules (they caused "little or no audio"):
  //  1. The first speech must start inside a tap, synchronously (before any await).
  //     This is per PAGE: each page needs its own first tap.
  //  2. While speech recognition (the mic) is on, iOS turns speech down or sends it
  //     to the earpiece. So the mic pauses while we talk and comes back afterwards.
  //  3. Long utterances can stall: short sentences + a watchdog that calls resume().
  //  4. [final] A phone call / FaceTime / Siri can break speech for the rest of the visit
  //     (speak() silently does nothing). If speech doesn't START within SPEECH_START_MS
  //     or fails with an audio error, we retry SPEECH_RETRIES times, then show a
  //     "Tap to turn voice back on" button (a tap re-unlocks speech).
  // =====================================================================
  const canSpeak = "speechSynthesis" in window;
  const Recognizer = window.SpeechRecognition || window.webkitSpeechRecognition;
  T.canSpeak = canSpeak; T.canListen = !!Recognizer;
  const voice = T.voice = { on: true, unlocked: false, last: "", rec: null, wantListen: false, listening: false,
                            log: [], busy: false, token: 0, watch: null, lastProgress: 0, queue: [], broken: false };
  const SPEECH_START_MS = 3000, SPEECH_RETRIES = 2;   // [final]
  const AUDIO_ERRORS = ["audio-busy", "audio-hardware", "synthesis-failed", "synthesis-unavailable", "network", "interrupted"];
  try { voice.on = localStorage.getItem("threshold.voice") !== "off"; } catch (e) {}

  // ?debug=1 log panel
  const dbgLines = [];
  T.dbg = function (msg) {
    if (!T.DEBUG) return;
    dbgLines.push(`${new Date().toTimeString().slice(0, 8)} ${msg}`);
    if (dbgLines.length > 40) dbgLines.shift();
    renderDebug();
  };
  function renderDebug() {
    let el = document.getElementById("debugPanel");
    if (!el) {
      el = document.createElement("div"); el.id = "debugPanel";
      el.style.cssText = "position:fixed;left:0;right:0;bottom:0;z-index:9999;max-height:40vh;overflow:auto;margin:0;padding:8px 10px;" +
        "background:rgba(15,20,16,.92);color:#b8f5c4;font:12px/1.35 ui-monospace,Menlo,Consolas,monospace;white-space:pre-wrap";
      document.body.appendChild(el);
      document.body.style.paddingBottom = "44vh";
    }
    const synth = canSpeak ? `speaking=${speechSynthesis.speaking} pending=${speechSynthesis.pending} paused=${speechSynthesis.paused}` : "no speechSynthesis";
    const status = `MIC: ${voice.listening ? "ON" : "off"} (wanted ${voice.wantListen ? "yes" : "no"}) | TALKING: ${voice.busy ? "yes" : "no"} (${synth}) | recognition: ${Recognizer ? "yes" : "none"} | unlocked: ${voice.unlocked} | voice: ${voice.on ? "on" : "off"}`;
    el.innerHTML = `<b style="color:#fff">${T.esc(status)}</b>\n${dbgLines.slice().reverse().map(T.esc).join("\n")}`;
  }
  if (T.DEBUG) document.addEventListener("DOMContentLoaded", () => { setInterval(renderDebug, 500); T.dbg(`debug on (${location.pathname}). ${navigator.userAgent}`); });

  let chosenVoice = null;
  function pickVoice() {   // an installed US English voice (Samantha on iPhone), else the default
    const vs = canSpeak ? speechSynthesis.getVoices() : [];
    const en = vs.filter(v => /^en[-_]US/i.test(v.lang));
    chosenVoice = en.find(v => v.localService && /samantha/i.test(v.name)) || en.find(v => v.localService && v.default) || en.find(v => v.localService) || null;
    T.dbg(`voices loaded: ${vs.length}, using ${chosenVoice ? chosenVoice.name : "the default voice"}`);
  }
  if (canSpeak) { speechSynthesis.onvoiceschanged = pickVoice; pickVoice(); }

  function chunks(text) {   // short pieces: one sentence each, long ones cut at a comma
    const out = [];
    for (let p of (text.match(/[^.!?]+[.!?]*/g) || [text]).map(s => s.trim()).filter(Boolean)) {
      while (p.length > 140) { const c = p.lastIndexOf(",", 140); const i = c > 40 ? c + 1 : 140; out.push(p.slice(0, i).trim()); p = p.slice(i).trim(); }
      if (p) out.push(p);
    }
    return out;
  }
  function el(id) { return document.getElementById(id); }
  function show(node, yes) {   // works with both the hidden attribute and a .hidden class
    if (!node) return;
    node.hidden = !yes;
    node.classList.toggle("hidden", !yes);
  }

  // opts.queue: wait for the current speech instead of cutting it off (scan announcements).
  T.say = function (text, opts = {}) {
    if (!text) return;
    voice.last = text;
    voice.log.push(text); if (voice.log.length > 50) voice.log.shift();
    const cap = el("caption");
    if (cap && cap.dataset.oneLine !== undefined) showLine(chunks(text)[0] || text);   // [v1.0.1] scan view
    else if (cap) { cap.textContent = text; show(cap, true); }
    if (!voice.on || !canSpeak) return;
    if (opts.queue && voice.busy) { voice.queue.push(text); T.dbg("queued (waiting for current speech)"); return; }
    voice.queue = [];
    // [v1.0.3] Also when the mic is still starting (voice.listening is only true after onstart).
    const micWasOn = voice.listening || voice.micStarting;
    pauseListening();
    if (micWasOn) {   // let iOS switch back to the loud speaker: speak once the mic has really stopped
      voice.busy = true;
      let spoken = false;
      const go = why => { if (spoken) return; spoken = true; voice.afterMicOff = null; T.dbg(`speaking after ${why}`); speakNow(text); };
      voice.afterMicOff = () => go("mic onend");
      setTimeout(() => go("350 ms backup"), 350);   // in case onend never fires
    }
    else speakNow(text);                                                         // keeps a first tap synchronous
  };
  function speakNow(text, attempt = 0) {
    if (speechSynthesis.speaking || speechSynthesis.pending) speechSynthesis.cancel();
    const parts = chunks(text), token = ++voice.token;
    voice.busy = true; voice.lastProgress = Date.now();
    let started = false, retried = false;
    // [final] speech that never starts or dies with an audio error -> retry, then ask for a tap
    const retry = why => {
      if (retried || token !== voice.token) return;
      retried = true;
      T.dbg(`speech failed (${why}), attempt ${attempt + 1} of ${SPEECH_RETRIES + 1}`);
      speechSynthesis.cancel();
      if (attempt < SPEECH_RETRIES) setTimeout(() => { if (token === voice.token) speakNow(text, attempt + 1); }, 400 + attempt * 600);
      else voiceBroken(text);
    };
    setTimeout(() => { if (!started && voice.on) retry("did not start"); }, SPEECH_START_MS);
    parts.forEach((p, i) => {
      const u = new SpeechSynthesisUtterance(p);
      if (chosenVoice) u.voice = chosenVoice;
      u.lang = chosenVoice ? chosenVoice.lang : "en-US"; u.rate = 0.95; u.pitch = 1; u.volume = 1;
      const last = i === parts.length - 1;
      u.onstart = () => { showLine(p); started = true; voiceWorks(); voice.lastProgress = Date.now(); T.dbg(`speech start: "${p.slice(0, 48)}"`); };
      u.onend = () => { voice.lastProgress = Date.now(); T.dbg("speech end"); if (last && token === voice.token) speechDone(); };
      u.onerror = e => {
        T.dbg(`speech error: ${e.error}`);
        if (e.error === "not-allowed" && T.onSpeechBlocked) T.onSpeechBlocked();   // no tap on this page yet
        else if (token === voice.token && AUDIO_ERRORS.includes(e.error)) return retry(e.error);   // [final]
        if (last && token === voice.token) speechDone();
      };
      speechSynthesis.speak(u);
    });
    clearInterval(voice.watch);
    voice.watch = setInterval(() => {   // [voice-ios] stall watchdog
      if (!voice.busy || token !== voice.token) { clearInterval(voice.watch); return; }
      if (speechSynthesis.paused) { T.dbg("speech paused -> resume()"); speechSynthesis.resume(); }
      else if (Date.now() - voice.lastProgress > 5000) { T.dbg("speech stalled -> resume()"); speechSynthesis.resume(); voice.lastProgress = Date.now(); }
      if (!speechSynthesis.speaking && !speechSynthesis.pending && Date.now() - voice.lastProgress > 1500) { T.dbg("speech queue empty"); speechDone(); }
    }, 1000);
  }
  T.speakNow = t => canSpeak && speakNow(t);
  // [v1.0.1] One-line caption (scan view): the sentence being spoken, fades after 3 s.
  function showLine(text) {
    const cap = el("caption");
    if (!cap || cap.dataset.oneLine === undefined) return;
    cap.textContent = text; cap.title = text;
    show(cap, true); cap.classList.remove("fade");
    clearTimeout(cap._fade); cap._fade = setTimeout(() => cap.classList.add("fade"), 3000);
  }

  // [final] speech is broken (phone call, FaceTime...): show a button; a tap fixes it
  function voiceBroken(text) {
    voice.broken = true;
    voice.pendingText = text;
    T.dbg("speech still not working -> showing 'Tap to turn voice back on'");
    speechDone();
    if (!voice.unlocked && T.onSpeechBlocked) { T.onSpeechBlocked(); return; }   // just needs a first tap on this page
    let b = el("voiceRevive");
    if (!b) {
      b = document.createElement("button");
      b.id = "voiceRevive"; b.type = "button";
      b.textContent = "Tap to turn voice back on";
      b.style.cssText = "position:fixed;left:50%;transform:translateX(-50%);top:calc(env(safe-area-inset-top,0px) + 56px);z-index:9998;" +
        "min-height:48px;padding:0 20px;border:0;border-radius:24px;background:#1c1c1e;color:#fff;" +
        "font:600 17px -apple-system,BlinkMacSystemFont,'SF Pro Text',system-ui,Roboto,sans-serif;box-shadow:0 4px 16px rgba(0,0,0,.25)";
      b.addEventListener("click", T.reviveVoice);
      document.body.appendChild(b);
    }
    b.hidden = false;
  }
  function voiceWorks() {
    if (!voice.broken) return;
    voice.broken = false;
    const b = el("voiceRevive"); if (b) b.hidden = true;
    T.dbg("speech works again");
  }
  // Must run inside a tap: resets the speech engine and speaks right away (iPhone rule).
  T.reviveVoice = function () {
    T.dbg("voice revive tapped");
    voice.unlocked = true;
    const b = el("voiceRevive"); if (b) b.hidden = true;
    try { speechSynthesis.cancel(); speechSynthesis.resume(); } catch (e) {}
    pickVoice();
    if (T.haptic) T.haptic();
    speakNow("Voice is back on. " + (voice.pendingText || ""));
    voice.pendingText = "";
  };
  // [final] coming back to the page after a call: clear speech that was cut off
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible" || !canSpeak) return;
    T.dbg("page visible again");
    if (voice.busy && !speechSynthesis.speaking) { speechSynthesis.cancel(); speechDone(); }
  });
  function speechDone() {
    if (!voice.busy) return;
    voice.busy = false; clearInterval(voice.watch);
    if (voice.queue.length && voice.on) { const next = voice.queue.join(" "); voice.queue = []; speakNow(next); return; }
    resumeListening();
    (T._idleWaiters || []).splice(0).forEach(f => f());
  }
  T.stopSpeaking = function () { if (canSpeak) speechSynthesis.cancel(); voice.queue = []; speechDone(); };
  // resolves when speech is done (or after maxMs)
  T.speechIdle = maxMs => new Promise(res => {
    if (!voice.busy) return res();
    (T._idleWaiters = T._idleWaiters || []).push(res);
    setTimeout(res, maxMs || 6000);
  });
  // Must run inside a tap (iPhone rule). Speaks right away and remembers that speech is allowed.
  T.unlockVoice = function (text) {
    voice.unlocked = true;
    show(el("voiceUnlock"), false);
    T.dbg("voice unlocked by a tap");
    T.say(text);
  };
  // [voice-ios] "Test voice": speaks straight from the tap, even if Voice is off.
  T.testVoice = function () {
    T.dbg("Test voice tapped");
    if (!canSpeak) return false;
    voice.unlocked = true;
    speakNow("Hello, I'm Threshold.");
    return true;
  };

  // ---------- listening ----------
  // The mic is never on while we speak: startListening() marks that we want it; it
  // actually starts when speech is done.
  T.startListening = function () {
    if (!Recognizer || T.NOMIC) return;   // [v1.0.4] filming mode: no mic
    voice.wantListen = true;
    if (voice.busy) { T.dbg("mic waits until speech ends"); updateButtons(); return; }
    startRecognizer();
  };
  function startRecognizer() {
    if (!Recognizer || T.NOMIC || voice.listening || voice.busy || !voice.wantListen) return;   // [v1.0.4]
    if (!voice.rec) {
      const rec = voice.rec = new Recognizer();
      rec.lang = "en-US"; rec.continuous = true; rec.interimResults = false;
      rec.onstart = () => { voice.micStarting = false; voice.listening = true; T.dbg("mic on"); updateButtons(); };
      rec.onresult = e => {
        const r = e.results[e.results.length - 1]; if (!r || !r.isFinal) return;
        T.dbg(`mic heard: "${r[0].transcript}"`);
        if (voice.busy) return;   // never react to our own voice
        T.handleCommand(r[0].transcript);
      };
      rec.onerror = e => {
        voice.micStarting = false;
        T.dbg(`mic error: ${e.error}`);
        if (e.error === "not-allowed" || e.error === "service-not-allowed") { voice.wantListen = false; heard("To talk to me, tap “Talk to me”."); updateButtons(); }
      };
      rec.onend = () => {
        voice.micStarting = false;
        voice.listening = false; T.dbg("mic off"); updateButtons();
        if (voice.afterMicOff) voice.afterMicOff();   // [v1.0.3] T.say was waiting for the mic to stop
        if (voice.wantListen && !voice.busy) setTimeout(startRecognizer, 300);
      };
    }
    voice.micStarting = true;   // [v1.0.3]
    try { voice.rec.start(); T.dbg("mic starting"); } catch (e) { voice.micStarting = false; T.dbg(`mic start failed: ${e.message}`); }
  }
  function pauseListening() { if (voice.rec && (voice.listening || voice.micStarting)) { T.dbg("mic paused for speech"); try { voice.rec.abort(); } catch (e) {} } }
  function resumeListening() { if (voice.wantListen && !voice.listening) setTimeout(startRecognizer, 300); }
  T.stopListening = function () { voice.wantListen = false; try { if (voice.rec) voice.rec.abort(); } catch (e) {} updateButtons(); };
  // Tap on the mic button: inside a tap, so Safari allows the mic.
  T.micTap = function () {
    if (!voice.unlocked) { voice.unlocked = true; show(el("voiceUnlock"), false); }
    if (voice.wantListen) { T.stopListening(); return; }
    if (voice.busy && canSpeak) { speechSynthesis.cancel(); voice.busy = false; clearInterval(voice.watch); voice.queue = []; }
    voice.wantListen = true;
    startRecognizer();
  };

  // ---------- commands ----------
  // Pages add handlers with T.onCommand(fn); fn(text) returns true if it handled it.
  const handlers = [];
  T.onCommand = fn => handlers.push(fn);
  function heard(text) { const h = el("heard"); if (h) { h.textContent = text; show(h, !!text); } }
  T.heard = heard;
  T.handleCommand = function (raw) {
    const t = String(raw).toLowerCase().trim();
    heard(`I heard: “${String(raw).trim()}”`);
    for (const fn of handlers) if (fn(t, raw)) return;
    if (/\b(repeat|again)\b/.test(t)) return T.say(voice.last || "I haven't said anything yet.");
    if (/\b(stop|quiet)\b/.test(t)) return T.stopSpeaking();
    heard(`I heard: “${String(raw).trim()}”. Try “what's here”, “done”, or “repeat”.`);
  };

  // ---------- voice buttons (any page that has them) ----------
  function updateButtons() {
    const t = el("speakToggle");
    if (t) { t.setAttribute("aria-pressed", String(voice.on)); const l = el("speakLabel"); if (l) l.textContent = voice.on ? "Voice on" : "Voice off"; }
    const m = el("micBtn");
    if (m) {
      m.setAttribute("aria-pressed", String(voice.listening || voice.wantListen));
      const l = el("micLabel"); if (l) l.textContent = voice.listening ? "Listening…" : voice.wantListen ? "Mic on" : "Talk to me";
    }
  }
  T.updateVoiceButtons = updateButtons;
  T.bindVoiceUI = function (onUnlock) {
    const t = el("speakToggle");
    if (t) t.addEventListener("click", () => {
      voice.on = !voice.on;
      try { localStorage.setItem("threshold.voice", voice.on ? "on" : "off"); } catch (e) {}
      if (voice.on) T.unlockVoice("Voice is on."); else T.stopSpeaking();
      updateButtons();
    });
    const m = el("micBtn");
    if (m) { show(m, !!Recognizer && !T.NOMIC); m.addEventListener("click", T.micTap); }   // [v1.0.4]
    const u = el("voiceUnlock");
    if (u) u.addEventListener("click", () => { voice.unlocked = true; show(u, false); if (onUnlock) onUnlock(); });
    updateButtons();
  };

  // [final] ONE small voice control for a nav bar (replaces "Read it to me" + "Talk to me").
  //  mode "toggle" (Scan): tap turns voice (speaking + listening) on or off.
  //  mode "read" (Report): tap reads the report (onRead), tap again stops; while the mic
  //  listens, tap stops listening.
  // The icon shows the state: speaker, stop (talking), mic with a dot (listening), muted.
  T.voiceControl = function (btn, mode, onRead) {
    if (!btn) return;
    const P = T.ICON_PATHS;
    const muted = '<path d="M4 9.5h3.5L12 6v12l-4.5-3.5H4z"/><path d="M16 9.5l5 5M21 9.5l-5 5"/>';
    let shown = "";
    function paint() {
      const st = !voice.on && mode === "toggle" ? "off" : voice.busy ? "talking" : voice.listening ? "listening" : "idle";
      if (st === shown) return;
      shown = st;
      const path = st === "off" ? muted : st === "listening" ? P.mic : P.speaker;   // [v1.0.1] no square "stop" icon
      btn.innerHTML = `<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${path}</svg>${st === "listening" ? '<span class="live-dot" aria-hidden="true"></span>' : ""}`;
      btn.dataset.state = st;
      btn.setAttribute("aria-label", mode === "toggle"
        ? (st === "off" ? "Voice is off. Turn voice on" : "Voice is on. Turn voice off")
        : (st === "talking" ? "Stop reading" : st === "listening" ? "Listening. Tap to stop" : "Read the report to me"));
    }
    btn.addEventListener("click", () => {
      T.haptic();
      if (mode === "toggle") {
        voice.on = !voice.on;
        try { localStorage.setItem("threshold.voice", voice.on ? "on" : "off"); } catch (e) {}
        if (voice.on) { T.unlockVoice("Voice is on."); T.startListening(); } else { T.stopSpeaking(); T.stopListening(); }
      } else if (voice.busy) T.stopSpeaking();
      else if (voice.wantListen) T.stopListening();
      else if (onRead) onRead();
      paint();
    });
    setInterval(paint, 300);
    paint();
  };
  T.setVoiceOn = function (on) {
    voice.on = on;
    try { localStorage.setItem("threshold.voice", on ? "on" : "off"); } catch (e) {}
    if (!on) { T.stopSpeaking(); T.stopListening(); }
  };

  window.thresholdVoice = { handleCommand: t => T.handleCommand(t), say: t => T.say(t), get log() { return [...voice.log]; } };   // testing / debugging
})();
