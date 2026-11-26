"""Any source as passages: what a reading needs, whatever was read (PLAN.md, Learning from sources).

    read(url) -> {"title", "url", "sections": [(heading, text), ...]} or raises Unreadable

A source is anything with an address, a date it was read and passages. MediaWiki pages go
through the site's API, so its furniture is left out; arXiv abstracts and PDFs are read as their
HTML version; other HTML is read as sections under its headings; PDFs through pdftotext; plain
text by paragraphs. A section too long for one turn is split at paragraphs, so nothing is cut.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
from html import unescape

from bench import learn
from tapestry import consolidate

AGENT = "tapestry-bench-study/0 (prototype; reads a few dozen pages once)"
PASSAGE = consolidate.TURN_CHARS - 500


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
    u = urllib.parse.urlparse(url)
    if "/wiki/" not in u.path:
        return None
    title = urllib.parse.unquote(u.path.split("/wiki/", 1)[1]).replace("_", " ")
    for api in (f"{u.scheme}://{u.netloc}/w/api.php", f"{u.scheme}://{u.netloc}/api.php"):
        try:
            page = learn.fetch(api, title)
        except Exception:
            continue
        if page and page["sections"]:
            return {"title": page["title"], "url": page["url"], "sections": page["sections"]}
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
            page = {"title": _title(html, final), "url": final, "sections": learn.sections(html)}
        else:
            text = body.decode("utf-8", "replace")
            page = {"title": final.rsplit("/", 1)[-1], "url": final,
                    "sections": [("", "\n".join(" ".join(p.split()) for p in re.split(r"\n\s*\n", text)))]}
    page["sections"] = _split([(h, t) for h, t in page["sections"] if t.strip()])
    if not page["sections"]:
        raise Unreadable(f"nothing readable at {url}")
    return page
