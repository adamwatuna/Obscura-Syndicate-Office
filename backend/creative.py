"""CEO-triggered Creative Studio generation and local rendering."""
import base64
import math
import os
from pathlib import Path
import subprocess
import tempfile

from fastapi import HTTPException
from pydantic import BaseModel, Field

from backend import deliverables


class ImageBrief(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)
    size: str = Field(default="1024x1024", pattern=r"^(1024x1024|1024x1536|1536x1024)$")


def generate_image(brief, reference=None):
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if os.getenv("AI_ENGINE_ENABLED", "false").lower() != "true" or not key:
        raise HTTPException(503, "Image generation needs the enabled OpenAI connection")
    from openai import OpenAI, OpenAIError
    try:
        with OpenAI(api_key=key, timeout=120, max_retries=0) as client:
            options = {"prompt": brief.prompt, "size": brief.size, "n": 1, "output_format": "png"}
            if reference:
                result = client.images.edit(
                    model=os.getenv("OPENAI_IMAGE_EDIT_MODEL", "gpt-image-2.5-sunburst"),
                    image=reference, **options,
                )
            else:
                result = client.images.generate(
                    model=os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2.5-flare"), **options,
                )
        encoded = result.data[0].b64_json if result.data else None
        if not encoded:
            raise ValueError("No image returned")
        data = base64.b64decode(encoded, validate=True)
        deliverables.check_file("creative-image.png", data)
        return data
    except (OpenAIError, ValueError):
        raise HTTPException(502, "Image generation did not return a usable image") from None


def trim_video(filename, data, start, end):
    if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end or end-start > 90:
        raise HTTPException(400, "Choose a valid start and end time, up to 90 seconds apart")
    ext = deliverables.check_file(filename, data)
    if ext not in ("mp4", "mov", "webm"):
        raise HTTPException(400, "Upload an MP4, MOV or WebM video")
    import imageio_ffmpeg

    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / ("source." + ext)
        output = Path(folder) / "preview.mp4"
        source.write_bytes(data)
        demuxer = "matroska" if ext == "webm" else "mov"
        try:
            subprocess.run([
                imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-v", "error",
                "-protocol_whitelist", "file", "-f", demuxer, "-ss", str(start), "-i", str(source),
                "-t", str(end-start), "-map", "0:v:0", "-map", "0:a:0?",
                "-vf", "scale=1280:1280:force_original_aspect_ratio=decrease:force_divisible_by=2",
                "-threads", "2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "25",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart",
                "-fs", str(deliverables.MAX_ASSET_BYTES + 1), str(output),
            ], check=True, timeout=100, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (subprocess.SubprocessError, OSError):
            raise HTTPException(422, "Video could not be rendered. Check its format and trim range.") from None
        if not output.is_file() or output.stat().st_size > deliverables.MAX_ASSET_BYTES:
            raise HTTPException(422, "Rendered video exceeds the review size limit. Try a shorter edit.")
        result = output.read_bytes()
        deliverables.check_file("creative-video.mp4", result)
        return result
