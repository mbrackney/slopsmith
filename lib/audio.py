"""Audio extraction and conversion for Rocksmith CDLC."""

import os
import shutil
import subprocess
from pathlib import Path


def _bundled_bin_dir() -> Path | None:
    """Resolve the desktop bundle's resources/bin/ directory if we're
    running inside one. Layout: resources/slopsmith/lib/audio.py →
    resources/bin/. Gate on vgmstream-cli's presence so we don't
    misidentify random parent dirs (e.g. Docker's `/bin`, dev
    layouts where parents[2] resolves to the repo root) — vgmstream-cli
    is bundled on every desktop platform and isn't a typical system
    binary, so it's a precise signature for the desktop layout."""
    bundled = Path(__file__).resolve().parents[2] / "bin"
    if any((bundled / n).is_file() for n in ("vgmstream-cli", "vgmstream-cli.exe")):
        return bundled
    return None


def _bundled_or_path(name: str) -> str | None:
    """Prefer the bundled binary on desktop, fall back to PATH lookup.

    Necessary because Electron's child PATH on macOS / Linux puts
    user-installed binaries (Homebrew `/opt/homebrew/bin`, /usr/local)
    before our `resources/bin`, so `shutil.which` alone picks up the
    user's binary — which may have been built without the features
    we rely on (e.g. Homebrew ffmpeg formulas that omit libvorbis)."""
    bundled = _bundled_bin_dir()
    if bundled is not None:
        for fname in (name, f"{name}.exe"):
            cand = bundled / fname
            if cand.is_file():
                return str(cand)
    return shutil.which(name)


def _vgmstream_cmd() -> str | None:
    """Return the path to vgmstream-cli, preferring the bundled binary."""
    return _bundled_or_path("vgmstream-cli")


def _ffmpeg_cmd() -> str | None:
    """Return the path to ffmpeg, preferring the bundled binary."""
    return _bundled_or_path("ffmpeg")


def _ffmpeg_wav_to_ogg(ffmpeg: str, wav: Path, out_ogg: Path) -> subprocess.CompletedProcess:
    """Encode WAV → Ogg Vorbis. Prefers libvorbis (external, full quality);
    if the ffmpeg build lacks it (some Homebrew formulas no longer set
    --enable-libvorbis), retries with ffmpeg's built-in `vorbis` encoder
    under `-strict experimental`. Same .ogg container either way; the
    built-in path produces a lower-quality file but always works."""
    r = subprocess.run(
        [ffmpeg, "-y", "-i", str(wav), "-c:a", "libvorbis", "-q:a", "5", str(out_ogg)],
        capture_output=True,
    )
    if r.returncode == 0 and out_ogg.exists() and out_ogg.stat().st_size >= 100:
        return r
    if b"Unknown encoder 'libvorbis'" not in (r.stderr or b""):
        return r
    return subprocess.run(
        [ffmpeg, "-y", "-i", str(wav),
         "-c:a", "vorbis", "-strict", "experimental", "-q:a", "5", str(out_ogg)],
        capture_output=True,
    )


def find_wem_files(extracted_dir: str) -> list[str]:
    """Find WEM audio files, sorted largest first (full song before preview)."""
    wem_files = list(Path(extracted_dir).rglob("*.wem"))
    wem_files.sort(key=lambda p: p.stat().st_size, reverse=True)
    return [str(f) for f in wem_files]


def convert_wem(wem_path: str, output_base: str) -> str:
    """
    Convert a WEM file to a playable format.
    Returns path to the converted audio file.
    """
    # Try vgmstream-cli → WAV → MP3 (best browser compatibility)
    vgmstream = _vgmstream_cmd()
    if vgmstream:
        wav = output_base + ".wav"
        r = subprocess.run(
            [vgmstream, "-o", wav, wem_path], capture_output=True
        )
        if r.returncode == 0 and os.path.exists(wav) and os.path.getsize(wav) > 0:
            ffmpeg = _ffmpeg_cmd()
            if ffmpeg:
                mp3 = output_base + ".mp3"
                r2 = subprocess.run(
                    [ffmpeg, "-y", "-i", wav, "-b:a", "192k", mp3],
                    capture_output=True,
                )
                if r2.returncode == 0 and os.path.exists(mp3):
                    os.remove(wav)
                    return mp3
            return wav

    # Try ffmpeg directly (some builds handle Wwise)
    ffmpeg = _ffmpeg_cmd()
    if ffmpeg:
        mp3 = output_base + ".mp3"
        r = subprocess.run(
            [ffmpeg, "-y", "-i", wem_path, "-b:a", "192k", mp3],
            capture_output=True,
        )
        if r.returncode == 0 and os.path.exists(mp3) and os.path.getsize(mp3) > 0:
            return mp3

        # Try WAV output as fallback
        wav = output_base + ".wav"
        r = subprocess.run(
            [ffmpeg, "-y", "-i", wem_path, wav],
            capture_output=True,
        )
        if r.returncode == 0 and os.path.exists(wav) and os.path.getsize(wav) > 0:
            return wav

    # Try ww2ogg
    if shutil.which("ww2ogg"):
        ogg = output_base + ".ogg"
        r = subprocess.run(
            ["ww2ogg", wem_path, "-o", ogg], capture_output=True
        )
        if r.returncode == 0 and os.path.exists(ogg) and os.path.getsize(ogg) > 0:
            return ogg

    raise RuntimeError(
        "No WEM audio decoder found. Install vgmstream-cli:\n"
        "  Manjaro/Arch:  yay -S vgmstream-cli-bin\n"
        "  Or build from: github.com/vgmstream/vgmstream"
    )
