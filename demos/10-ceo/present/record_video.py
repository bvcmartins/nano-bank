#!/usr/bin/env python3
"""Record the animated C-suite boardroom straight to an MP4 -- no browser
window, no screen-capture pipeline (OBS/window-capture). Drives a real
headless Chromium via Playwright, using its native video capture, then
muxes to H.264 MP4 with ffmpeg. Same approach as
demos/04-external-agent/present/record_video.py, adapted to boardroom.html's
multi-session (meeting/debate/build) shape.

This script only reads demos/10-ceo/present/{boardroom.template.html,
build_boardroom.py, recordings/}; it never modifies them.

One-time setup:
    pip install playwright
    python3 -m playwright install chromium

Usage:
    python3 demos/10-ceo/present/record_video.py                 # debate, default
    python3 demos/10-ceo/present/record_video.py --session meeting
    python3 demos/10-ceo/present/record_video.py --session build --out /tmp/build.mp4
"""
from __future__ import annotations
import argparse
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD_BOARDROOM = os.path.join(HERE, "build_boardroom.py")
BOARDROOM_HTML = os.path.join(HERE, "boardroom.html")


def record(session: str, out_path: str, width: int, height: int,
          speed: float, launch_args: list[str]) -> str:
    from playwright.sync_api import sync_playwright

    # Rebuild from the current on-disk template + recordings -- read-only
    # with respect to those source files, just regenerates the derived HTML.
    subprocess.run([sys.executable, BUILD_BOARDROOM], check=True, cwd=HERE)

    video_dir = tempfile.mkdtemp(prefix="boardroom-video-")

    with sync_playwright() as p:
        browser = p.chromium.launch(args=launch_args)
        context = browser.new_context(
            viewport={"width": width, "height": height},
            record_video_dir=video_dir,
            record_video_size={"width": width, "height": height},
        )
        page = context.new_page()
        page.goto(f"file://{BOARDROOM_HTML}")
        page.wait_for_timeout(300)

        # Session tabs are created dynamically only for sessions that have
        # content; the page already auto-selects "debate" on load if it has
        # beats, but click explicitly so this is correct regardless.
        tab = page.query_selector(f'#sessions button[data-s="{session}"]')
        if tab:
            tab.click()
            page.wait_for_timeout(200)
        elif session != "debate":
            raise SystemExit(f"no '{session}' tab -- that session has no recorded content")

        if speed != 1.0:
            page.evaluate(
                "(v) => { const el = document.getElementById('speed'); "
                "el.value = v; el.dispatchEvent(new Event('input')); }",
                speed)

        page.click("#play")
        # The resting label text differs by session ("Convene" vs "Delegate"),
        # so don't match on it -- watch for the fixed "Pause" substring
        # appearing (confirms playback started) then disappearing (stopped).
        page.wait_for_function(
            "document.getElementById('play').innerHTML.includes('Pause')",
            timeout=30_000)
        page.wait_for_function(
            "!document.getElementById('play').innerHTML.includes('Pause')",
            timeout=300_000)
        page.wait_for_timeout(1200)  # settle on the final frame

        context.close()  # finalizes the video file
        video_path = page.video.path()
        browser.close()

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-crf", "18", "-preset", "medium", out_path],
        check=True, capture_output=True)
    return out_path


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--session", choices=["meeting", "debate", "build"], default="debate")
    p.add_argument("--out", default="/tmp/ceo-boardroom.mp4")
    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    # Same reasoning as demos/04-external-agent's tool: the page's own speed
    # slider bottoms out at 0.5 (min="0.5" on #speed), so that's the slowest
    # a recording actually plays -- use it as the default watchable pace.
    p.add_argument("--speed", type=float, default=0.5)
    p.add_argument("--no-sandbox", action="store_true", default=True,
                   help="pass Chromium --no-sandbox (default on; needed in most containers)")
    args = p.parse_args()

    launch_args = ["--disable-gpu", "--disable-dev-shm-usage"]
    if args.no_sandbox:
        launch_args.append("--no-sandbox")

    out = record(args.session, args.out, args.width, args.height, args.speed, launch_args)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
