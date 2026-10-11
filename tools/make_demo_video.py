"""Turn a demo recording into the deliverables.

    python tools/make_demo_video.py recordings/<stamp>

Reads raw.mp4 + scenes.json written by `run.py --demo` and produces, in the same folder:
    demo_captioned.mp4   1920x1080 video, caption band at the bottom (no audio yet)
    captions.srt         the same captions as a subtitle file (Loom / YouTube upload)
    transcript.md        the voice-over script with exact timestamps per scene
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"


def ts(t: float, sep: str = ",") -> str:
    h, rem = divmod(max(t, 0.0), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d}{sep}{int(round((s % 1) * 1000)) % 1000:03d}"


def ass_ts(t: float) -> str:
    h, rem = divmod(max(t, 0.0), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def mmss(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02d}"


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    folder = Path(sys.argv[1]).resolve()
    data = json.loads((folder / "scenes.json").read_text(encoding="utf-8"))
    scenes, total = data["scenes"], data["total"]
    spans = []
    for i, sc in enumerate(scenes):
        end = scenes[i + 1]["t"] if i + 1 < len(scenes) else total
        spans.append((sc["t"], end, sc))

    # ---- SRT
    srt = []
    for i, (a, b, sc) in enumerate(spans, 1):
        srt += [str(i), f"{ts(a)} --> {ts(b - 0.05)}", sc["caption"], ""]
    (folder / "captions.srt").write_text("\n".join(srt), encoding="utf-8")

    # ---- ASS (burned into the band under the screen)
    ass = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1920", "PlayResY: 1080", "WrapStyle: 2", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        "Style: Cap,Segoe UI Semibold,30,&H00EBE8E6,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0.3,0,1,0,0,2,40,40,14,1",
        "Style: Brand,Segoe UI Semibold,19,&H0041A4D9,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,2,0,1,0,0,1,30,30,21,1",
        "Style: Time,Consolas,19,&H007B7169,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,3,30,30,21,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        f"Dialogue: 0,{ass_ts(0)},{ass_ts(total)},Brand,,0,0,0,,PROTANKI COMMANDER  ·  PROTOTYPE",
    ]
    for i, (a, b, sc) in enumerate(spans, 1):
        ass.append(f"Dialogue: 1,{ass_ts(a)},{ass_ts(b - 0.05)},Cap,,0,0,0,,{{\\fad(180,120)}}{sc['caption']}")
        ass.append(f"Dialogue: 0,{ass_ts(a)},{ass_ts(b - 0.05)},Time,,0,0,0,,{i:02d} / {len(spans):02d}")
    (folder / "captions.ass").write_text("\n".join(ass), encoding="utf-8-sig")

    # ---- transcript
    words = sum(len(sc["narration"].split()) for sc in scenes)
    md = [
        "# Voice-over script",
        "",
        f"Video length **{mmss(total)}** · {len(scenes)} scenes · {words} words (~{words / max(total, 1) * 60:.0f} "
        "words per minute, a relaxed pace).",
        "",
        "**How to record**",
        "",
        "1. Open `demo_captioned.mp4` muted and play it on one screen; keep this script on the other "
        "(or print it).",
        "2. Start your recorder (Windows Voice Recorder, Audacity or your phone) and say the first line as "
        "the video starts. Each line starts at the timestamp shown; the video holds every scene long enough.",
        "3. If you fall behind, pause briefly at the next timestamp; you don't need to be perfect to the second.",
        "4. Save as .m4a, .mp3 or .wav and send it to me (or run `tools/add_voiceover.py`).",
        "",
        "---",
        "",
    ]
    for i, (a, b, sc) in enumerate(spans, 1):
        md += [f"### {i:02d} · {mmss(a)} – {mmss(b)}  ·  *{sc['caption']}*", "", sc["narration"], ""]
    (folder / "transcript.md").write_text("\n".join(md), encoding="utf-8")

    # ---- burn captions
    raw = folder / "raw.mp4"
    if raw.exists():
        out = folder / "demo_captioned.mp4"
        vf = "scale=1920:-2,pad=1920:1080:0:0:color=0x08090B,ass=captions.ass"
        cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-i", "raw.mp4", "-vf", vf,
               "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
               "-movflags", "+faststart", out.name]
        subprocess.run(cmd, cwd=folder, check=True)
        print("video     ", out)
    print("captions  ", folder / "captions.srt")
    print("transcript", folder / "transcript.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
