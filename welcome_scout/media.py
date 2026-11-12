"""Подготовка визуального материала для анализа: кадры из видео (через ffmpeg) или изображения поста."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import requests

from .models import Post

log = logging.getLogger(__name__)

# Плотно берём первые секунды (хук), дальше — равномерно по ролику.
HOOK_TIMESTAMPS = [0.0, 0.8, 1.6, 2.5, 3.5]
BODY_FRAMES = 5
MAX_VIDEO_BYTES = 80 * 1024 * 1024
FRAME_HEIGHT = 640


@dataclass
class Visuals:
    images: list[tuple[str, bytes]] = field(default_factory=list)  # (подпись, jpeg)
    basis: str = ""  # что именно увидела модель — показываем пользователю
    duration: float | None = None


def _download(url: str, dest: Path, max_bytes: int) -> bool:
    try:
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            size = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    size += len(chunk)
                    if size > max_bytes:
                        return False
                    f.write(chunk)
        return True
    except requests.RequestException as e:
        log.warning("download failed %s: %s", url[:80], e)
        return False


def _duration(path: Path) -> float | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(out.stdout.strip())
    except (ValueError, subprocess.SubprocessError, OSError):
        return None


def _frame(path: Path, t: float, out: Path) -> bytes | None:
    try:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
             "-vf", f"scale=-2:{FRAME_HEIGHT}", "-q:v", "5", str(out)],
            capture_output=True, timeout=60, check=True,
        )
        return out.read_bytes() if out.exists() else None
    except (subprocess.SubprocessError, OSError):
        return None


def _image(url: str) -> bytes | None:
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        return r.content
    except requests.RequestException:
        return None


def collect_visuals(post: Post) -> Visuals:
    v = Visuals()
    if post.is_video and post.video_url and shutil.which("ffmpeg") and shutil.which("ffprobe"):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "v.mp4"
            if _download(post.video_url, video, MAX_VIDEO_BYTES):
                dur = _duration(video) or 0
                v.duration = dur or None
                times = [t for t in HOOK_TIMESTAMPS if not dur or t < dur]
                if dur > 5:
                    step = (dur - 4) / (BODY_FRAMES + 1)
                    times += [4 + step * (i + 1) for i in range(BODY_FRAMES)]
                for i, t in enumerate(times):
                    data = _frame(video, t, Path(tmp) / f"f{i}.jpg")
                    if data:
                        v.images.append((f"Кадр на {t:.1f} с", data))
        if v.images:
            v.basis = f"{len(v.images)} кадров из видео (длительность {v.duration:.0f} с) + подпись. Звук и речь не анализировались."
            return v

    urls = post.image_urls[:6] or ([post.thumbnail_url] if post.thumbnail_url else [])
    for i, url in enumerate(urls):
        data = _image(url)
        if data:
            label = "Обложка ролика" if post.is_video else f"Слайд {i + 1}"
            v.images.append((label, data))
    if post.is_video:
        v.basis = (
            "Видео скачать не удалось (часто из-за лицензионной музыки или отсутствия ссылки в API) — "
            "разбор только по обложке и подписи, начало ролика не просмотрено."
            if v.images else "Нет ни видео, ни обложки — разбор только по подписи."
        )
    else:
        v.basis = f"{len(v.images)} изображений + подпись." if v.images else "Изображения недоступны — разбор только по подписи."
    return v
