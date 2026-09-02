# External mandated agent — presentation extras

`app.py` (one level up) is the live demo — unchanged in behavior, now
restyled into a two-tone nav+centre stepper and saving each run here as a
recording. This directory adds a standalone **animated replay**.

## 🎬 Animated gateway cinematic (standalone)

A split-screen view of a recorded run: the external agent on the left (an
"outsider" palette — it never touches the bank directly), the personal
manager on the right (nano-bank's own brand palette), and a gateway rail
between them that lights 🟢/🔴/🟡 for each mandate-gated act and animates
the A2A hand-off for each message. Zero model delay — it replays a captured
recording.

    demos/04-external-agent/present/gateway_server.py    # -> http://localhost:8521/gateway.html

Or build once and open the file directly (uses whatever
`recordings/canonical.json` is already checked in / captured):

    python3 demos/04-external-agent/present/build_gateway.py

Controls: ▶ Run / Pause (Space), ⏮ ⏭ step (arrows), a speed slider, a
progress scrubber. The **⦿ Capture live** button (only works when served via
`gateway_server.py`, needs `DEMO_BRANCH_BASE` + `AGENT_GATEWAY_TOKEN` +
`OLLAMA_API_KEY` in the environment, and a port-forward to `svc/agent-api`)
re-runs `capture.py` against the deployed stack, saves the result as the
canonical recording, and rebuilds `gateway.html`.

## 🎥 Recording an MP4 directly (no OBS / no browser window)

`record_video.py` drives a real headless Chromium (Playwright) and uses its
native video capture — no screen-capture pipeline, no window compositing,
no bitrate/codec settings to tune. One-time setup:

    pip install playwright
    python3 -m playwright install chromium

Then:

    python3 demos/04-external-agent/present/record_video.py
    # or: --recording PATH --out /tmp/x.mp4 --speed 1.5 --width 1920 --height 1080

Plays the recording end to end at 1920×1080, captures it natively, and
muxes to H.264 MP4 with ffmpeg. Takes as long as the recording's own
playback duration (no faster — it's driven by the real page timing).
