"""Guards for the single-file front end (static/index.html)."""
import re
import unittest
from pathlib import Path

HTML = Path(__file__).resolve().parent.parent / "static" / "index.html"


def _css():
    css = "\n".join(re.findall(r"<style>(.*?)</style>", HTML.read_text(encoding="utf-8"), re.S))
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return css


def _strip_at_blocks(css):
    """Remove @media/@supports blocks (their rules legitimately repeat a selector)."""
    out, i = [], 0
    while i < len(css):
        if css.startswith("@media", i) or css.startswith("@supports", i):
            depth, j = 0, css.index("{", i)
            while True:
                depth += 1 if css[j] == "{" else -1 if css[j] == "}" else 0
                j += 1
                if depth == 0:
                    break
            i = j
        else:
            out.append(css[i])
            i += 1
    return "".join(out)


class StylesheetTests(unittest.TestCase):
    def test_no_class_has_two_base_rules(self):
        """A second `.name{...}` rule silently overrides the first and breaks whichever page used it
        (this once squashed the Journal buttons and blew up the Habits day rings)."""
        base = re.findall(r"(?:^|\})\s*(\.[A-Za-z_][\w-]*)\s*\{", _strip_at_blocks(_css()))
        dupes = sorted({name for name in base if base.count(name) > 1})
        self.assertEqual(dupes, [], f"declared more than once: {dupes}")

    def test_no_gradients_or_purple(self):
        css = _css().lower()
        self.assertNotIn("gradient(", css)
        for purple in ("#b08cf6", "#6d3fd1", "#120c1e"):
            self.assertNotIn(purple, css)


if __name__ == "__main__":
    unittest.main()
