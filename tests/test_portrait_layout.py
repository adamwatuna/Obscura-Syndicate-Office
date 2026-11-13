"""Guard the portrait CSS cascade from cropping the faces again."""
import re
import unittest
from pathlib import Path

class PortraitStyleTests(unittest.TestCase):
    def test_photo_scale_and_centering_not_overridden(self):
        html=(Path(__file__).resolve().parent.parent/"frontend"/"index.html").read_text()
        # Avatar base style must precede the more specific photo style.
        base=html.find(".avatar{")
        photo=html.find(".avatar.avatar-photo{")
        self.assertGreaterEqual(base,0)
        self.assertGreater(photo,base)
        base_rule=html[base:html.find("}",base)]
        photo_rule=html[photo:html.find("}",photo)]
        self.assertNotRegex(base_rule,r"(?<!-)\bbackground\s*:")
        self.assertIn("background-size:300% 300%",photo_rule)
        self.assertIn("width:76px",photo_rule)
        self.assertIn("height:76px",photo_rule)
        self.assertIn("background-position:",html)
        self.assertIn("EXECUTIVE_ATLAS",html)

if __name__=="__main__":
    unittest.main()
