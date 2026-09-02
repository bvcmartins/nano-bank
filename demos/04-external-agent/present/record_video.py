#!/usr/bin/env python3
"""Record the animated gateway cinematic straight to an MP4 -- no browser
window, no screen-capture pipeline (OBS/window-capture), no re-encode-loss
from a compositor. Drives a real headless Chromium via Playwright, using its
native video capture, then muxes to H.264 MP4 with ffmpeg.

One-time setup:
    pip install playwright
    python3 -m playwright install chromium

Usage:
    python3 demos/04-external-agent/present/record_video.py
    python3 demos/04-external-agent/present/record_video.py \
        --recording present/recordings/canonical.json --speed 1.5 \
        --out /tmp/demo4.mp4
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_gateway  # noqa: E402


def _load_events(recording_path: str) -> list:
    with open(recording_path, encoding="utf-8") as f:
        return json.load(f).get("events", [])


def record(recording_path: str, out_path: str, width: int, height: int,
          speed: float, launch_args: list[str]) -> str:
    from playwright.sync_api import sync_playwright

    events = _load_events(recording_path)
    html = build_gateway.render(events)

    fd, html_path = tempfile.mkstemp(suffix=".html", prefix="gateway-record-")
    os.close(fd)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    video_dir = tempfile.mkdtemp(prefix="gateway-video-")

    with sync_playwright() as p:
        browser = p.chromium.launch(args=launch_args)
        context = browser.new_context(
            viewport={"width": width, "height": height},
            record_video_dir=video_dir,
            record_video_size={"width": width, "height": height},
        )
        page = context.new_page()
        page.goto(f"file://{html_path}")
        page.wait_for_timeout(300)

        if speed != 1.0:
            page.evaluate(
                "(v) => { const el = document.getElementById('speed'); "
                "el.value = v; el.dispatchEvent(new Event('input')); }",
                speed)

        page.click("#play")
        # The page itself reverts the button label when playback finishes
        # (stop()) -- wait on that instead of pre-computing total duration,
        # so this stays correct regardless of recording length or speed.
        page.wait_for_function(
            "document.getElementById('play').innerHTML.includes('Run')",
            timeout=180_000)
        page.wait_for_timeout(1200)  # settle on the final frame

        context.close()  # finalizes the video file
        video_path = page.video.path()
        browser.close()

    os.remove(html_path)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-crf", "18", "-preset", "medium", out_path],
        check=True, capture_output=True)
    return out_path


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--recording", default=os.path.join(HERE, "recordings", "canonical.json"))
    p.add_argument("--out", default="/tmp/demo4-gateway.mp4")
    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    # The page's own speed slider only goes down to 0.5 (min="0.5" on
    # #speed) -- anything lower gets silently clamped by the browser, so
    # 0.5 is the slowest a recording actually plays. Default to that for a
    # watchable pace on dense text; --speed 1.0 restores the page default.
    p.add_argument("--speed", type=float, default=0.5)
    p.add_argument("--no-sandbox", action="store_true", default=True,
                   help="pass Chromium --no-sandbox (default on; needed in most containers)")
    args = p.parse_args()

    launch_args = ["--disable-gpu", "--disable-dev-shm-usage"]
    if args.no_sandbox:
        launch_args.append("--no-sandbox")

    out = record(args.recording, args.out, args.width, args.height, args.speed, launch_args)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
