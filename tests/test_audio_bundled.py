"""Tests for the desktop-bundle binary resolution in lib/audio.py.

Desktop builds ship `resources/bin/ffmpeg` (and vgmstream-cli, ffprobe).
Electron's child PATH on macOS / Linux puts user-installed binaries
(Homebrew `/opt/homebrew/bin`, `/usr/local`) before our `resources/bin`,
so a plain `shutil.which` would pick up the user's binary — which may
have been built without the features we rely on (e.g. Homebrew ffmpeg
formulas that omit libvorbis). `_bundled_bin_dir()` + `_bundled_or_path()`
detect the desktop layout via the vgmstream-cli marker and prefer the
bundled binary; outside the bundle, `shutil.which` is used unchanged.
"""

import os
import shutil

import audio


def _set_audio_file(monkeypatch, fake_lib_dir):
    """Point lib/audio.py's __file__ at a fake location so parents[2] resolves
    to the test's fake resources root."""
    monkeypatch.setattr(audio, "__file__", str(fake_lib_dir / "audio.py"))


def _touch_exec(path):
    """Create `path` and mark it executable so _is_executable() accepts it."""
    path.write_text("")
    path.chmod(0o755)


def test_bundled_bin_dir_returns_path_when_vgmstream_marker_present(tmp_path, monkeypatch):
    """Desktop-bundle layout: resources/slopsmith/lib/audio.py with a
    vgmstream-cli marker file in resources/bin/."""
    fake_resources = tmp_path / "resources"
    fake_lib = fake_resources / "slopsmith" / "lib"
    fake_lib.mkdir(parents=True)
    fake_bin = fake_resources / "bin"
    fake_bin.mkdir()
    _touch_exec(fake_bin / "vgmstream-cli")  # marker

    _set_audio_file(monkeypatch, fake_lib)

    assert audio._bundled_bin_dir() == fake_bin


def test_bundled_bin_dir_returns_none_without_marker(tmp_path, monkeypatch):
    """When parents[2]/bin/ exists but lacks the vgmstream-cli marker
    (e.g. Docker's /bin, dev layouts where parents[2] is the repo root),
    we must NOT treat it as a desktop bundle — otherwise we'd shell out
    to whatever ffmpeg/etc. lives there."""
    fake_resources = tmp_path / "resources"
    fake_lib = fake_resources / "slopsmith" / "lib"
    fake_lib.mkdir(parents=True)
    fake_bin = fake_resources / "bin"
    fake_bin.mkdir()
    # No vgmstream-cli marker. Drop an ffmpeg there to make sure absence
    # of the marker still wins over presence of the target binary.
    _touch_exec(fake_bin / "ffmpeg")

    _set_audio_file(monkeypatch, fake_lib)

    assert audio._bundled_bin_dir() is None


def test_bundled_bin_dir_returns_none_when_bin_dir_missing(tmp_path, monkeypatch):
    fake_resources = tmp_path / "resources"
    fake_lib = fake_resources / "slopsmith" / "lib"
    fake_lib.mkdir(parents=True)
    # no bin/ dir at all

    _set_audio_file(monkeypatch, fake_lib)

    assert audio._bundled_bin_dir() is None


def test_bundled_or_path_prefers_bundled_when_marker_present(tmp_path, monkeypatch):
    """When the desktop layout is detected, the bundled binary wins over
    whatever shutil.which would find on PATH."""
    fake_resources = tmp_path / "resources"
    fake_lib = fake_resources / "slopsmith" / "lib"
    fake_lib.mkdir(parents=True)
    fake_bin = fake_resources / "bin"
    fake_bin.mkdir()
    _touch_exec(fake_bin / "vgmstream-cli")
    _touch_exec(fake_bin / "ffmpeg")

    _set_audio_file(monkeypatch, fake_lib)

    # Pretend shutil.which would have returned a Homebrew ffmpeg — bundled
    # must still win.
    monkeypatch.setattr(shutil, "which", lambda name: "/opt/homebrew/bin/ffmpeg")

    assert audio._bundled_or_path("ffmpeg") == str(fake_bin / "ffmpeg")


def test_bundled_or_path_falls_back_to_which_outside_bundle(tmp_path, monkeypatch):
    """No marker → no bundle → shutil.which result is returned verbatim."""
    fake_resources = tmp_path / "resources"
    fake_lib = fake_resources / "slopsmith" / "lib"
    fake_lib.mkdir(parents=True)
    # No bin/ dir → no marker → not a bundle.

    _set_audio_file(monkeypatch, fake_lib)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/ffmpeg")

    assert audio._bundled_or_path("ffmpeg") == "/usr/bin/ffmpeg"


def test_bundled_or_path_returns_none_when_neither_available(tmp_path, monkeypatch):
    fake_resources = tmp_path / "resources"
    fake_lib = fake_resources / "slopsmith" / "lib"
    fake_lib.mkdir(parents=True)

    _set_audio_file(monkeypatch, fake_lib)
    monkeypatch.setattr(shutil, "which", lambda name: None)

    assert audio._bundled_or_path("ffmpeg") is None


def test_bundled_or_path_falls_through_to_which_when_bundle_has_marker_but_not_target(tmp_path, monkeypatch):
    """If the bundle is detected (vgmstream-cli marker present) but the
    *requested* binary isn't actually in resources/bin/, we should still
    fall through to PATH rather than returning None — otherwise a partial
    desktop bundle would silently disable any binary it forgot to ship."""
    fake_resources = tmp_path / "resources"
    fake_lib = fake_resources / "slopsmith" / "lib"
    fake_lib.mkdir(parents=True)
    fake_bin = fake_resources / "bin"
    fake_bin.mkdir()
    _touch_exec(fake_bin / "vgmstream-cli")  # marker present
    # ffmpeg deliberately NOT in fake_bin

    _set_audio_file(monkeypatch, fake_lib)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/ffmpeg")

    assert audio._bundled_or_path("ffmpeg") == "/usr/bin/ffmpeg"


def test_bundled_or_path_falls_through_to_which_when_bundled_lacks_exec_bit(tmp_path, monkeypatch):
    """If the bundle is detected (executable vgmstream-cli marker present)
    but the *target* binary exists without the exec bit (broken bundle,
    or a malformed copy step), we must not return that non-executable
    path — subprocess.run() would PermissionError. Fall through to PATH
    instead so the user gets a working binary (and the diagnostic comes
    from a downstream "encoder X not found" rather than an obscure
    PermissionError on the bundled file)."""
    fake_resources = tmp_path / "resources"
    fake_lib = fake_resources / "slopsmith" / "lib"
    fake_lib.mkdir(parents=True)
    fake_bin = fake_resources / "bin"
    fake_bin.mkdir()
    _touch_exec(fake_bin / "vgmstream-cli")  # executable marker
    # Drop a non-executable ffmpeg shim.
    (fake_bin / "ffmpeg").write_text("")
    (fake_bin / "ffmpeg").chmod(0o644)

    _set_audio_file(monkeypatch, fake_lib)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/ffmpeg")

    assert audio._bundled_or_path("ffmpeg") == "/usr/bin/ffmpeg"


def test_bundled_bin_dir_rejects_non_executable_marker(tmp_path, monkeypatch):
    """A vgmstream-cli that exists but isn't executable is treated as an
    invalid marker — the bundle is broken, so detection should fail and
    every helper should fall through to PATH."""
    fake_resources = tmp_path / "resources"
    fake_lib = fake_resources / "slopsmith" / "lib"
    fake_lib.mkdir(parents=True)
    fake_bin = fake_resources / "bin"
    fake_bin.mkdir()
    (fake_bin / "vgmstream-cli").write_text("")
    (fake_bin / "vgmstream-cli").chmod(0o644)  # not executable

    _set_audio_file(monkeypatch, fake_lib)

    assert audio._bundled_bin_dir() is None
