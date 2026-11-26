"""Any source as passages: what a reading is made of, whatever was read (docs/primitives/EPISODE.md).

    read(url) -> {"title", "url", "sections": [(heading, text), ...]} or raises Unreadable

A source is anything with an address: a wiki page goes through its site's API, so its furniture
is left out; an arXiv abstract or PDF is read as its HTML version; other HTML as sections under
its headings; a PDF through pdftotext; plain text by paragraphs. A section too long for one
passage is split at paragraphs, so nothing is cut. A source that can't be read raises
Unreadable, with why, and is never returned empty.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
from html import unescape
from html.parser import HTMLParser

AGENT = "tapestry/0 (reads a source an agent asked to learn from, once)"
PASSAGE = 7_500   # the most one passage holds: under what a drawer is shown of one turn
MIN_SECTION_CHARS = 80

SKIPPED_CLASSES = {"navbox", "reference", "references", "mw-editsection", "toc", "gallery", "noprint",
                   "mw-references-wrap", "reflist", "hatnote", "thumbcaption", "wikia-gallery", "metadata",
                   "mw-empty-elt", "sistersitebox", "navigation-not-searchable"}
SKIPPED_SECTIONS = {"references", "gallery", "external links", "see also", "navigation", "notes", "further reading",
                    "videos", "trivia", "citations", "bibliography", "sources"}
VOID = {"br", "img", "hr", "input", "meta", "link", "wbr", "source", "col", "area"}


class _Sections(HTMLParser):
    """A rendered page as its sections of plain text, without its furniture."""

    def __init__(self) -> None:
        super().__init__()
        self.sections: list[list[str]] = [["", ""]]   # [heading, text]
        self._open: list[bool] = []                   # per open element: is it skipped
        self._heading: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            return
        classes = set((dict(attrs).get("class") or "").split())
        self._open.append(bool(self._open and self._open[-1]) or tag in ("script", "style", "sup")
                          or bool(classes & SKIPPED_CLASSES))
        if tag in ("h2", "h3") and not self._open[-1]:
            self._heading = []
        elif tag in ("p", "li", "tr", "div", "h4", "dd", "dt") and not self._open[-1]:
            self.sections[-1][1] += "\n"

    def handle_endtag(self, tag):
        if tag in VOID or not self._open:
            return
        self._open.pop()
        if tag in ("h2", "h3") and self._heading is not None:
            self.sections.append(["".join(self._heading).strip(), ""])
            self._heading = None
        elif tag in ("td", "th"):
            self.sections[-1][1] += " "

    def handle_data(self, data):
        if self._open and self._open[-1]:
            return
        if self._heading is not None:
            self._heading.append(data)
        else:
            self.sections[-1][1] += data


def sections(html: str) -> list[tuple[str, str]]:
    p = _Sections()
    p.feed(html)
    out = []
    for heading, text in p.sections:
        text = "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())
        if heading.lower() not in SKIPPED_SECTIONS and len(text) >= MIN_SECTION_CHARS:
            out.append((heading, text))
    return out



class Unreadable(Exception):
    pass


def _get(url: str) -> tuple[bytes, str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.read(), r.headers.get("Content-Type", ""), r.geturl()
    except Exception as e:
        raise Unreadable(f"couldn't fetch {url}: {type(e).__name__}: {e}") from None


def _split(sections: list[tuple[str, str]]) -> list[tuple[str, str]]:
    out = []
    for heading, text in sections:
        if len(text) <= PASSAGE:
            out.append((heading, text))
            continue
        part, n = "", 1
        for para in text.split("\n"):
            while len(para) > PASSAGE:          # one paragraph longer than a passage
                if part:
                    out.append((f"{heading} (part {n})".strip(), part)); n += 1; part = ""
                out.append((f"{heading} (part {n})".strip(), para[:PASSAGE])); n += 1
                para = para[PASSAGE:]
            if part and len(part) + len(para) + 1 > PASSAGE:
                out.append((f"{heading} (part {n})".strip(), part)); n += 1; part = ""
            part = f"{part}\n{para}" if part else para
        if part:
            out.append((f"{heading} (part {n})".strip() if n > 1 else heading, part))
    return out


def _mediawiki(url: str) -> dict | None:
    """A wiki page through its site's own API, so the site's furniture is left out."""
    u = urllib.parse.urlparse(url)
    if "/wiki/" not in u.path:
        return None
    title = urllib.parse.unquote(u.path.split("/wiki/", 1)[1]).replace("_", " ")
    for api in (f"{u.scheme}://{u.netloc}/w/api.php", f"{u.scheme}://{u.netloc}/api.php"):
        q = urllib.parse.urlencode({"action": "parse", "page": title, "prop": "text", "format": "json",
                                    "redirects": 1, "formatversion": 2})
        try:
            body, _, _ = _get(f"{api}?{q}")
            parsed = json.loads(body).get("parse")
        except (Unreadable, ValueError):
            continue
        if parsed and (found := sections(parsed["text"])):
            base = api.rsplit("/", 1)[0].removesuffix("/w")
            return {"title": parsed["title"], "sections": found,
                    "url": f"{base}/wiki/{urllib.parse.quote(parsed['title'].replace(' ', '_'))}"}
    return None


def _title(html: str, fallback: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    return " ".join(unescape(m.group(1)).split()) if m else fallback


def read(url: str) -> dict:
    url = url.strip()
    if not re.match(r"https?://", url):
        raise Unreadable(f"{url!r} is not a web address")
    page = _mediawiki(url)
    if page is None:
        m = re.match(r"https?://(?:www\.)?arxiv\.org/(?:abs|pdf|html)/([^?#]+?)(?:\.pdf)?/?$", url)
        if m:
            url = f"https://arxiv.org/html/{m.group(1)}"
        body, kind, final = _get(url)
        if "pdf" in kind or body[:5] == b"%PDF-":
            if not shutil.which("pdftotext"):
                raise Unreadable("this is a PDF and pdftotext isn't installed")
            with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
                f.write(body); f.flush()
                text = subprocess.run(["pdftotext", "-layout", f.name, "-"], capture_output=True,
                                      text=True, timeout=120).stdout
            paras = [" ".join(p.split()) for p in re.split(r"\n\s*\n", text) if p.strip()]
            page = {"title": final.rsplit("/", 1)[-1], "url": final, "sections": [("", "\n".join(paras))]}
        elif "html" in kind or body.lstrip()[:1] == b"<":
            html = body.decode("utf-8", "replace")
            page = {"title": _title(html, final), "url": final, "sections": sections(html)}
        else:
            text = body.decode("utf-8", "replace")
            page = {"title": final.rsplit("/", 1)[-1], "url": final,
                    "sections": [("", "\n".join(" ".join(p.split()) for p in re.split(r"\n\s*\n", text)))]}
    page["sections"] = _split([(h, t) for h, t in page["sections"] if t.strip()])
    if not page["sections"]:
        raise Unreadable(f"nothing readable at {url}")
    return page
