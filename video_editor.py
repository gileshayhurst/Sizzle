import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def _title_alpha_expr(duration: float) -> str:
    """drawtext `alpha` expression for a traditional title: fade in 0.3s, hold,
    fade out 0.3s. The title shows for min(3s, clip) so it appears then leaves.

    Commas are backslash-escaped: ffmpeg treats a raw comma inside a filter
    option value as a filter separator (verified: raw commas crash, `\\,` works
    on ffmpeg 8.x / Windows)."""
    show = min(3.0, max(0.6, duration))
    fade = 0.3
    out_start = max(fade, show - fade)
    return (
        f"if(lt(t\\,{fade})\\,t/{fade}\\,"
        f"if(lt(t\\,{out_start:.3f})\\,1\\,max(0\\,({show:.3f}-t)/{fade})))"
    )


def _fmt_mmss(seconds: int) -> str:
    """Whole seconds → 'M:SS'."""
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def _timer_drawtext_filters(duration, out_dir, prefix, fontfile_arg, fontsize):
    """Per-second countdown of the clip's remaining time, pinned top-right.

    ffmpeg can't run a dynamic time expression here — this build treats ':' in a
    filter value as an option separator even when escaped, so `%{eif:…}` and any
    'M:SS' literal break the filter. Instead each whole second is a static
    drawtext gated to its 1-second window with `enable=between(t\\,a\\,b)`
    (commas escaped — verified working), and the 'M:SS' text lives in a side-car
    file so its colon never reaches the filter string.

    Returns the list of drawtext filter strings (side-car files already written).
    """
    n = max(1, round(duration))
    margin = max(12, int(fontsize * 0.6))
    filters = []
    for k in range(n):
        remaining = n - k                       # counts n, n-1, …, 1
        tf = out_dir / f"{prefix}_timer{k}.txt"
        tf.write_text(_fmt_mmss(remaining), encoding="utf-8")
        lo = k
        # Last window runs to the end so a fractional tail still shows a value.
        hi = (k + 1) if k < n - 1 else max(duration, n) + 1
        filters.append(
            f"drawtext={fontfile_arg}textfile={tf.name}"
            f":fontcolor=white:fontsize={fontsize}"
            f":box=1:boxcolor=black@0.5:boxborderw=8"
            f":x=w-text_w-{margin}:y={margin}"
            f":enable=between(t\\,{lo}\\,{hi})"
        )
    return filters


def check_ffmpeg() -> None:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        raise RuntimeError(
            "ffmpeg not found. Install it with:\n"
            "  Windows: winget install ffmpeg\n"
            "  Mac: brew install ffmpeg"
        )


def parse_timestamp_to_seconds(ts: str) -> float:
    parts = ts.split(":")
    return float(int(parts[0]) * 60 + int(parts[1]))


def extract_clip(video_path: str, start_sec: float, end_sec: float, output_path: str,
                 fade_out_secs: float = 0.0, title_lines: list | None = None,
                 font_path: str | None = None, height: int | None = None,
                 fade_in_secs: float = 0.0, show_timer: bool = False) -> None:
    # Re-encode (never stream-copy) so every clip starts on an I-frame.
    # -ss before -i: fast input seek. -t duration (not -to) is relative to the
    # seek point. -avoid_negative_ts make_zero zeroes each clip's timestamps so
    # the concat demuxer sees clean zero-based PTS on every clip — prevents AV drift.
    duration = end_sec - start_sec
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_sec),
        "-i", video_path,
        "-t", str(duration),
        "-avoid_negative_ts", "make_zero",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-r", "30",       # normalise to 30 fps — a single consistent video
        "-c:a", "aac",    # timebase so the concat demuxer sees uniform clips
        "-ar", "48000",
        "-ac", "2",
    ]

    vf = []
    af = []
    run_cwd = None

    # Text overlays (identification title + countdown timer) both need a relative
    # font and side-car text files resolved from the output dir. textfile= and a
    # relative fontfile= keep every path out of the filter string, so the ffmpeg
    # 8.x/Windows drive-letter-colon quirk never bites. Requires cwd=out_dir.
    if title_lines or show_timer:
        out_dir = Path(output_path).parent
        run_cwd = str(out_dir)
        prefix = Path(output_path).stem
        h = height or 1080
        fontsize = max(20, h // 22)

        fontfile_arg = ""
        if font_path and Path(font_path).exists():
            font_dest = out_dir / Path(font_path).name
            if not font_dest.exists():
                shutil.copy(font_path, font_dest)
            fontfile_arg = f"fontfile={Path(font_path).name}:"

        # ── Identification overlay: title_lines top-anchored, fading in/out
        #    like a traditional title (0-second title-card cost). ──
        if title_lines:
            line_height = int(fontsize * 1.35)
            top = max(fontsize, h // 14)
            alpha = _title_alpha_expr(duration)
            for i, line in enumerate(title_lines):
                tf = out_dir / f"{prefix}_t{i}.txt"
                # drawtext expands % format specifiers even from a textfile.
                tf.write_text(line.replace("%", "%%"), encoding="utf-8")
                y = top + i * line_height
                vf.append(
                    f"drawtext={fontfile_arg}textfile={tf.name}"
                    f":fontcolor=white:fontsize={fontsize}"
                    f":shadowcolor=black@0.8:shadowx=2:shadowy=2"
                    f":x=w/2-text_w/2:y={y}:alpha={alpha}"
                )

        # ── Countdown timer: remaining clip time, top-right. ──
        if show_timer:
            vf.extend(_timer_drawtext_filters(duration, out_dir, prefix, fontfile_arg, fontsize))

    # Fades come after the overlays so a boundary dip also dims the text.
    if fade_in_secs > 0.0:
        vf.append(f"fade=t=in:st=0:d={fade_in_secs}")
        af.append(f"afade=t=in:st=0:d={fade_in_secs}")
    if fade_out_secs > 0.0:
        fade_start = max(0.0, duration - fade_out_secs)
        vf.append(f"fade=t=out:st={fade_start}:d={fade_out_secs}")
        af.append(f"afade=t=out:st={fade_start}:d={fade_out_secs}")

    if vf:
        cmd += ["-vf", ",".join(vf)]
    if af:
        cmd += ["-af", ",".join(af)]
    cmd.append(output_path)
    subprocess.run(
        cmd,
        check=True,
        capture_output=True,
        cwd=run_cwd,
    )


def stitch_clips(clip_paths: list[str], output_path: str) -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        concat_list_path = f.name
        for path in clip_paths:
            f.write(f"file '{Path(path).as_posix()}'\n")
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_list_path,
                "-c", "copy",
                output_path,
            ],
            capture_output=True,
        )
        if result.returncode != 0:
            print(result.stderr.decode(errors="replace"), file=__import__("sys").stderr)
            result.check_returncode()
    finally:
        os.unlink(concat_list_path)


def stitch_clips_to_pipe(clip_paths: list[str]) -> subprocess.Popen:
    """Like stitch_clips but streams fragmented MP4 to stdout instead of writing a file.

    Returns a Popen object. Caller must:
    - Read proc.stdout (to consume the stream and avoid pipe buffer deadlock)
    - Drain proc.stderr in a separate thread (to prevent ffmpeg blocking on a full pipe)
    - Call proc.wait() after stdout is exhausted
    - Delete proc._concat_list_path (the temp concat list file) after proc.wait()
    """
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    concat_list_path = f.name
    for path in clip_paths:
        f.write(f"file '{Path(path).as_posix()}'\n")
    f.close()

    try:
        proc = subprocess.Popen(
            [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_list_path,
                "-c", "copy",
                "-movflags", "frag_keyframe+empty_moov",
                "-f", "mp4",
                "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except Exception:
        os.unlink(concat_list_path)
        raise
    proc._concat_list_path = concat_list_path
    return proc
