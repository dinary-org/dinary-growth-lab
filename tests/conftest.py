import shutil
import subprocess
from pathlib import Path

import pytest

from dgl.store import Store


@pytest.fixture
def store():
    return Store(":memory:")


@pytest.fixture(scope="session")
def tiny_video(tmp_path_factory) -> Path:
    """3 s 1080x1920 clip: 1.5 s colour A, 1.5 s colour B, with a sine tone."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    d = tmp_path_factory.mktemp("media")
    out = d / "tiny.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "color=c=0x3366cc:s=1080x1920:d=1.5:r=30",
        "-f", "lavfi", "-i", "color=c=0xeeaa22:s=1080x1920:d=1.5:r=30",
        "-f", "lavfi", "-i", "sine=f=440:d=3",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)], check=True)
    return out
