"""Reddit listing page extraction strategy.

Parses old.reddit.com listing HTML (subreddit feeds, search results) into
structured markdown with post titles, scores, comment counts, authors,
and URLs.  Individual post pages are handled by the default readability
extractor and don't reach this strategy.

The parser anchors on ``<div class="midcol unvoted">`` which marks the
vote column — the one element that every post in a listing page has.
The HTML between two consecutive midcol positions is one post's block.
"""

from __future__ import annotations

import contextlib
import logging
import re

from serp_llm.post_processing.strategies import StrategyResult

logger = logging.getLogger(__name__)

# Every post row starts with a midcol (vote column).  Use its position
# as the anchor — split the page at midcol boundaries so we never need
# to know the exact closing elements of each post.
_MIDCOL_RE = re.compile(r'<div\s+class="midcol\s+unvoted">', re.DOTALL)

# Individual field matchers — each is applied within a post block
_SCORE_RE = re.compile(
    r'<div\s+class="score\s+likes"(?:\s+title="(\d+)")?',
)
_TITLE_RE = re.compile(
    r'<a\s+class="title\s+may-blank[^"]*"\s+[^>]*href="([^"]+)"[^>]*>'
    r'(?P<title>.*?)</a>'
)
_DOMAIN_RE = re.compile(
    r'<span\s+class="domain">\(<a[^>]*>([^<]*)</a>\)</span>'
)
_AUTHOR_RE = re.compile(
    r'<a[^>]*class="author[^"]*"[^>]*>([^<]+)</a>'
)
_COMMENTS_RE = re.compile(
    r'<a[^>]*class="[^"]*\bcomments\s+may-blank[^"]*"[^>]*>([^<]*)</a>'
)

# Subreddit name from the page header
_SUBREDDIT_RE = re.compile(r'data-subreddit-prefixed="([^"]+)"')

# Next page link
_NEXT_RE = re.compile(
    r'<span\s+class="next-button">.*?<a\s+href="([^"]+)"', re.DOTALL
)


def _clean_title(title: str) -> str:
    """Strip HTML tags and decode entities from a title."""
    title = re.sub(r"<[^>]+>", "", title)
    for old, new in (
        ("&amp;", "&"),
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("&quot;", '"'),
        ("&#x27;", "'"),
        ("&#39;", "'"),
    ):
        title = title.replace(old, new)
    return title.strip()


class RedditListingStrategy:
    """Extract post listings from old.reddit.com subreddit feeds."""

    async def extract(self, html: str, url: str) -> StrategyResult | None:
        """Parse old.reddit.com listing HTML into structured markdown.

        Splits the page at ``midcol unvoted`` boundaries.  Each section
        between two consecutive midcol positions is one post.  This avoids
        fragility from variable HTML structures (thumbnails, flair labels,
        sticky badges, etc.) between the vote column and the entry div.
        """
        # Find all midcol positions — these are the reliable post anchors
        midcols = [m.end() for m in _MIDCOL_RE.finditer(html)]
        if not midcols:
            return None

        # Build a list of sections: each section goes from one midcol's end
        # to the start of the next midcol (or end of string).
        sections: list[str] = []
        for i in range(len(midcols)):
            start = midcols[i]
            end = midcols[i + 1] if i + 1 < len(midcols) else len(html)
            sections.append(html[start:end])

        sr_match = _SUBREDDIT_RE.search(html)
        subreddit = sr_match.group(1) if sr_match else "r/reddit"
        lines: list[str] = [f"## {subreddit}", ""]

        for i, section in enumerate(sections):
            score_m = _SCORE_RE.search(section)
            title_m = _TITLE_RE.search(section)
            domain_m = _DOMAIN_RE.search(section)
            author_m = _AUTHOR_RE.search(section)
            comments_m = _COMMENTS_RE.search(section)

            if not (score_m and title_m and author_m):
                continue

            score_raw = score_m.group(1)
            href = title_m.group(1)
            raw_title = title_m.group("title")
            domain = domain_m.group(1) if domain_m else ""
            author = author_m.group(1)
            comments_text = comments_m.group(1) if comments_m else ""

            title = _clean_title(raw_title)
            score_int = int(score_raw) if score_raw else 0

            comments_count = 0
            comments_clean = comments_text.strip().lower()
            if comments_clean and comments_clean != "comment":
                with contextlib.suppress(ValueError, IndexError):
                    comments_count = int(comments_clean.split()[0])

            post_url = href if href.startswith("http") else "https://old.reddit.com" + href

            line = f"{i + 1}. **{title}**"
            if domain:
                line += f" ({domain})"
            lines.append(line)

            details = [f"Score: {score_int}"]
            if comments_count:
                details.append(f"Comments: {comments_count}")
            details.append(f"by {author}")
            lines.append(f"   {' | '.join(details)}")
            lines.append(f"   {post_url}")
            lines.append("")

        next_match = _NEXT_RE.search(html)
        if next_match:
            next_url = next_match.group(1).replace("&amp;", "&")
            lines.append(f"---\n[Next page]({next_url})")

        return StrategyResult(
            content="\n".join(lines).strip(),
            format="markdown",
        )
