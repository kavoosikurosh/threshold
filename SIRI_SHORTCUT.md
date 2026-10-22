# "Hey Siri, Threshold"

An iPhone Shortcut named **Threshold** that opens the app straight into Scan mode.
After this, saying **"Hey Siri, Threshold"** opens the camera scan with spoken results.

## Before you start

1. On the PC, double-click `start_demo.bat`.
2. It prints a green link like
   `https://something-random.trycloudflare.com/?mode=scan`
   and copies it to the clipboard.
3. Get that link onto the iPhone: paste it into a text or email to yourself, then on the
   iPhone press and hold the link and tap **Copy**.

The `?mode=scan` at the end is what makes the app start scanning right away.

## Make the Shortcut (once, about 2 minutes)

1. Open the **Shortcuts** app on the iPhone (it's built in; search for it if you can't find it).
2. Tap **+** (top right) to make a new shortcut.
3. Tap **Add Action**, type `URL` in the search box, and tap the action called **URL**.
4. Tap the light-blue `apple.com` text inside the action, delete it, and **paste** your link.
5. Tap the search bar at the bottom, type `Open URLs`, and tap **Open URLs**.
   It automatically uses the URL from step 4.
6. Tap the name at the top (it says "New Shortcut"), tap **Rename**, type `Threshold`, and tap **Done**.
7. Tap **Done** (top right) to save.

## Use it

- Say **"Hey Siri, Threshold"**, or tap the Threshold shortcut.
- The first time, Siri may ask to allow opening the link: tap **Always Allow**.
- Safari opens and asks to use the camera: tap **Allow**. Scanning starts.
- Tap **Tap here to hear me talk** (or the big Start button) once, so the phone lets the app
  speak and listen. Then you can say **"what's here"**, **"repeat"**, or **"done"**.

Tip: you can also put the shortcut on the home screen: in Shortcuts, press and hold
**Threshold**, tap **Share**, then **Add to Home Screen**.

## When the link changes (every time you run start_demo.bat)

The free Cloudflare quick tunnel gives a **new random link each time**, so the shortcut
needs the new one:

1. Run `start_demo.bat` and get the new green link onto the iPhone (see "Before you start").
2. Open **Shortcuts**, press and hold **Threshold**, and tap **Edit**.
3. Tap the link inside the **URL** action, select all of it, and **paste** the new link.
4. Tap **Done**.

The same applies to "Add to Home Screen" from Safari (the full-screen app icon): it's tied
to one link, so after the link changes, delete the old icon and add it again from the new link.

**To stop the link changing:** a *named* Cloudflare tunnel with your own domain gives a
permanent address (needs a domain, about $10 a year). Ask Claude Code to set it up if you
get a domain.

## If something doesn't work

| Problem | Fix |
|---|---|
| Siri says it can't find "Threshold" | Check the shortcut is named exactly `Threshold`. Try "Hey Siri, run Threshold". |
| The page doesn't load | `start_demo.bat` isn't running on the PC, or the link is old. Update the link (above). |
| No camera, it says "Tap Start" | Tap **Start**. Safari sometimes needs one tap before it allows the camera. |
| It doesn't talk | Tap **Tap here to hear me talk**, and check **Voice on** is green. Check the phone's silent switch and volume. |
| It doesn't hear me | Tap **Talk to me**. Safari asks once for the microphone: tap **Allow**. |
