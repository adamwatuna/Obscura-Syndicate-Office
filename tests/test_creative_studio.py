import base64
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend import creative


class CreativeStudioTests(unittest.TestCase):
    @patch.dict("os.environ", {"AI_ENGINE_ENABLED": "true", "OPENAI_API_KEY": "mock-key"})
    @patch("openai.OpenAI")
    def test_image_generation_and_edit_use_the_configured_client(self, client):
        api = client.return_value.__enter__.return_value.images
        image = b"\x89PNG\r\n\x1a\nsynthetic"
        response = SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(image).decode())])
        api.generate.return_value = api.edit.return_value = response
        brief = creative.ImageBrief(prompt="Draw a burgundy book cover")
        self.assertEqual(creative.generate_image(brief), image)
        reference = ("input.png", image, "image/png")
        self.assertEqual(creative.generate_image(brief, reference), image)
        self.assertEqual(api.generate.call_args.kwargs["n"], 1)
        self.assertEqual(api.edit.call_args.kwargs["image"], reference)

    def test_video_trims_a_real_fixture_to_mp4(self):
        import imageio_ffmpeg

        with TemporaryDirectory() as folder:
            source = Path(folder) / "sample.mp4"
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-v", "error",
                            "-f", "lavfi", "-i", "color=c=purple:s=160x96:r=24",
                            "-t", "2", "-c:v", "libx264", str(source)], check=True, timeout=20)
            clip = creative.trim_video(source.name, source.read_bytes(), 0.5, 1.5)
            rendered = Path(folder) / "trimmed.mp4"
            rendered.write_bytes(clip)
            reader = imageio_ffmpeg.read_frames(str(rendered))
            try:
                self.assertAlmostEqual(next(reader)["duration"], 1.0, places=1)
            finally:
                reader.close()

    def test_invalid_trim_is_rejected(self):
        with self.assertRaises(HTTPException) as error:
            creative.trim_video("sample.mp4", b"", 2, 1)
        self.assertEqual(error.exception.status_code, 400)
