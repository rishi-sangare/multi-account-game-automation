"""Put your recorded voice-over on the demo video.

    python tools/add_voiceover.py recordings/<stamp>/demo_captioned.mp4 my_voice.m4a [--offset 0.4]

--offset  seconds to delay (positive) or advance (negative) the voice relative to the video.
The voice is cleaned up lightly (rumble filter + loudness normalised to -16 LUFS, the usual level for
online video). The video length is kept; silence pads the end if the voice is shorter.
Writes <video>_voice.mp4 next to the video.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"


def video_duration(video: Path) -> float:
    """Read the duration from ffmpeg's banner (no ffprobe in the bundled build)."""
    import re
    r = subprocess.run([ffmpeg_exe(), "-nostdin", "-hide_banner", "-i", str(video)], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    if not m:
        raise SystemExit("could not read the video duration")
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("voice")
    ap.add_argument("--offset", type=float, default=0.0)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    video, voice = Path(a.video), Path(a.voice)
    out = Path(a.out) if a.out else video.with_name(video.stem + "_voice.mp4")
    filters = ["highpass=f=80", "loudnorm=I=-16:TP=-1.5:LRA=11"]
    if a.offset > 0:
        filters.insert(0, f"adelay={int(a.offset * 1000)}:all=1")
    elif a.offset < 0:
        filters.insert(0, f"atrim=start={-a.offset},asetpts=PTS-STARTPTS")
    dur = video_duration(video)
    filters.append(f"apad=whole_dur={dur:.3f}")
    cmd = [ffmpeg_exe(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video), "-i", str(voice),
           "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-af", ",".join(filters),
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{dur:.3f}", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    print("written", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
