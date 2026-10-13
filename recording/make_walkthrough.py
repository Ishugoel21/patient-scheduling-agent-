"""Record the desk walkthrough and mix a voiceover. Not part of the graded agent."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path

import edge_tts
import imageio_ffmpeg
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
WORK = ROOT / "work"
URL = "http://127.0.0.1:8765"
VOICE = "en-US-JennyNeural"
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

SCENES = [
    (
        "intro",
        "This is the Riverside scheduling desk, still on the policy from before the fix. "
        "Chest pain is handled like a normal appointment.",
    ),
    (
        "chest",
        "This is the full conversation. The patient has had chest pain since this morning. "
        "The agent asks for a name and date of birth, offers Thursday, October fifteenth, at three PM, "
        "with Doctor Shah, and books it when the patient says yes. "
        "The visit fails, because an emergency was put on the schedule.",
    ),
    (
        "eval_open",
        "The same thirteen visits now go through the improvement loop.",
    ),
    (
        "scores",
        "Version zero scores ten out of thirteen. The three emergencies fail. "
        "After the accepted rule, those same visits score thirteen out of thirteen.",
    ),
    (
        "rules",
        "Two rules are compiled from that failure. The broad rule is rejected. "
        "It still mishandles self-harm, and it breaks ordinary booking. "
        "The precise rule is accepted. It escalates only while the red-flag signal is active, "
        "and the other ten visits still pass.",
    ),
    (
        "after_chest",
        "Replay the same chest-pain conversation after the rule. "
        "The policy blocks the booking, alerts the on-call clinician, and tells the patient to call nine one one. "
        "Nothing is booked. This visit now passes.",
    ),
    (
        "headache",
        "A routine headache still books Friday, October sixteenth, at nine AM, with Doctor Shah. "
        "The improvement did not break a normal visit. "
        "The loop is closed. The failure was found, the narrower rule was kept, "
        "and the score moved from ten to thirteen.",
    ),
]


def duration_seconds(path: Path) -> float:
    probe = subprocess.run(
        [FFMPEG, "-i", str(path)],
        capture_output=True,
        text=True,
    )
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", probe.stderr)
    if not match:
        raise RuntimeError(probe.stderr[-500:])
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


async def synthesize() -> dict[str, float]:
    WORK.mkdir(parents=True, exist_ok=True)
    durations = {}
    for name, text in SCENES:
        target = WORK / f"{name}.mp3"
        await edge_tts.Communicate(text, VOICE, rate="-6%").save(str(target))
        durations[name] = duration_seconds(target)
        print(f"voice {name} {durations[name]:.1f}s")
    return durations


def glide(page, locator) -> None:
    locator.scroll_into_view_if_needed()
    box = locator.bounding_box()
    if not box:
        locator.click()
        return
    page.evaluate(
        """async ({ x, y }) => {
          const cursor = document.getElementById('rec-cursor');
          const startX = parseFloat(cursor.style.left) || 90;
          const startY = parseFloat(cursor.style.top) || 90;
          const steps = 16;
          for (let i = 1; i <= steps; i++) {
            const t = i / steps;
            cursor.style.left = (startX + (x - startX) * t) + 'px';
            cursor.style.top = (startY + (y - startY) * t) + 'px';
            await new Promise((resolve) => setTimeout(resolve, 16));
          }
        }""",
        {"x": box["x"] + min(box["width"] / 2, 220), "y": box["y"] + box["height"] / 2},
    )
    locator.click()


def hold(page, marks, durations, name: str) -> None:
    marks.append({"name": name, "start": time.perf_counter()})
    page.wait_for_timeout(int((durations[name] + 0.85) * 1000))


def walk(page, marks, durations) -> None:
    page.goto(URL, wait_until="networkidle")
    page.evaluate(
        """() => {
          const cursor = document.createElement('div');
          cursor.id = 'rec-cursor';
          cursor.style.cssText = [
            'position:fixed', 'z-index:99999', 'width:16px', 'height:16px',
            'border:2px solid #142e2a', 'border-radius:50%', 'background:#fff',
            'pointer-events:none', 'left:70px', 'top:48px',
            'box-shadow:0 1px 4px rgba(0,0,0,.35)'
          ].join(';');
          document.body.appendChild(cursor);
        }"""
    )
    chest = page.get_by_role("button", name="Chest pain is not a scheduling request")
    chest.wait_for()
    hold(page, marks, durations, "intro")

    glide(page, chest)
    page.locator("#grade.fail").wait_for()
    page.get_by_text("You're booked for Thursday").wait_for()
    page.evaluate("() => { document.getElementById('log').scrollTop = 0; }")
    page.wait_for_timeout(400)
    hold(page, marks, durations, "chest")

    glide(page, page.get_by_role("button", name="Evaluation"))
    page.get_by_role("button", name="Run the loop").wait_for()
    page.wait_for_timeout(300)
    hold(page, marks, durations, "eval_open")

    glide(page, page.get_by_role("button", name="Run the loop"))
    page.get_by_text("13/13").wait_for()
    page.get_by_text("Rejected").wait_for()
    page.get_by_text("Accepted").wait_for()
    page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(400)
    hold(page, marks, durations, "scores")

    page.locator("article.candidate.accept").evaluate(
        "element => element.scrollIntoView({ block: 'center' })"
    )
    page.wait_for_timeout(400)
    hold(page, marks, durations, "rules")

    glide(page, page.get_by_role("button", name="After the rule"))
    glide(page, page.get_by_role("button", name="Desk"))
    glide(page, page.get_by_role("button", name="Chest pain is not a scheduling request"))
    page.locator("#grade.pass").wait_for()
    page.get_by_text("call 911").first.wait_for()
    page.evaluate("() => { document.getElementById('log').scrollTop = 0; }")
    page.wait_for_timeout(400)
    hold(page, marks, durations, "after_chest")

    glide(page, page.get_by_role("button", name="A routine headache is not an emergency"))
    page.get_by_text("Friday, October 16, 2026 at 9:00 AM").first.wait_for()
    page.locator("#grade.pass").wait_for()
    page.evaluate("() => { document.getElementById('log').scrollTop = 0; }")
    page.wait_for_timeout(400)
    hold(page, marks, durations, "headache")
    page.wait_for_timeout(700)


def record(durations: dict[str, float]) -> tuple[Path, list[dict], float]:
    marks: list[dict] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            record_video_dir=str(WORK),
            record_video_size={"width": 1440, "height": 900},
        )
        page = context.new_page()
        started = time.perf_counter()
        try:
            walk(page, marks, durations)
        except Exception:
            page.screenshot(path=str(WORK / "failure.png"), full_page=True)
            raise
        finally:
            video = page.video
            context.close()
            browser.close()
            video_path = Path(video.path())
    for item in marks:
        item["start"] = round(item["start"] - started, 3)
    return video_path, marks, time.perf_counter() - started


def mix(video_path: Path, marks: list[dict], total: float) -> Path:
    out = ROOT / "riverside-walkthrough.mp4"
    audio = WORK / "narration.m4a"
    inputs = [FFMPEG, "-y", "-f", "lavfi", "-t", f"{total + 0.4:.3f}", "-i", "anullsrc=r=24000:cl=stereo"]
    filters = []
    mix_inputs = ["[0:a]"]
    for index, item in enumerate(marks, start=1):
        inputs.extend(["-i", str(WORK / f"{item['name']}.mp3")])
        delay = int(item["start"] * 1000)
        filters.append(
            f"[{index}:a]aformat=channel_layouts=stereo,adelay={delay}|{delay}[a{index}]"
        )
        mix_inputs.append(f"[a{index}]")
    count = len(marks) + 1
    filters.append(
        "".join(mix_inputs)
        + f"amix=inputs={count}:duration=first:dropout_transition=0:normalize=0[out]"
    )
    subprocess.run(
        inputs
        + [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[out]",
            "-c:a",
            "aac",
            str(audio),
        ],
        check=True,
    )
    subprocess.run(
        [
            FFMPEG,
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            "-movflags",
            "+faststart",
            str(out),
        ],
        check=True,
    )
    return out


def main() -> None:
    urllib.request.urlopen(URL, timeout=5).read(200)
    durations = asyncio.run(synthesize())
    video_path, marks, total = record(durations)
    (WORK / "marks.json").write_text(json.dumps({"total": total, "marks": marks}, indent=2), encoding="utf-8")
    print("recorded", video_path, f"{total:.1f}s")
    out = mix(video_path, marks, total)
    print("wrote", out)


if __name__ == "__main__":
    main()
