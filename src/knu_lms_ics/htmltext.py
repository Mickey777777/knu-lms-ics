import re
import unicodedata
from html.parser import HTMLParser

BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "dd", "div", "dl", "dt", "figcaption", "figure",
    "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "ol", "p", "pre",
    "section", "table", "tbody", "thead", "tfoot", "tr", "ul",
}
CELL_TAGS = {"td", "th"}
SKIP_TAGS = {"script", "style", "noscript", "template"}
INLINE_SPACE = re.compile(r"[ \t\f\v ​]+")
ANY_SPACE = re.compile(r"\s+")


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag == "br" or tag in BLOCK_TAGS:
            self.parts.append("\n")
        elif tag in CELL_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    parser = _TextExtractor()
    parser.feed(markup or "")
    parser.close()
    text = unicodedata.normalize("NFC", "".join(parser.parts))
    lines: list[str] = []
    for raw in text.splitlines():
        line = INLINE_SPACE.sub(" ", raw).strip()
        if line or (lines and lines[-1]):
            lines.append(line)
    return "\n".join(lines).strip()


def match_key(text: str) -> str:
    return ANY_SPACE.sub(" ", unicodedata.normalize("NFKC", text)).strip()


def contains_evidence(source: str, evidence: str) -> bool:
    needle = match_key(evidence)
    return bool(needle) and needle in match_key(source)
