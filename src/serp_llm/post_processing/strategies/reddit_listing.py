"""Reddit listing page extraction strategy.

Parses old.reddit.com listing HTML (subreddit feeds, search results) into
structured markdown with post titles, scores, comment counts, authors,
and URLs.  Individual post pages are handled by the default readability
extractor and don't reach this strategy.

The parser works on the old.reddit.com desktop HTML structure:
  <div class="entry unvoted">
    <div class="top-matter">
      <p class="title"><a class="title may-blank" href="...">Title</a> ...</p>
      <p class="tagline">submitted <time ...>...</time> by <a class="author">...</a></p>
      <ul class="flat-list buttons">
        <li class="first"><a class="comments may-blank">N comments</a></li>
      </ul>
    </div>
  </div>

Score comes from the preceding <div class="score likes" title="N"> div.
"""

from __future__ import annotations

import contextlib
import logging
import re

from serp_llm.post_processing.strategies import StrategyResult

logger = logging.getLogger(__name__)

# Split the page into post-row chunks at each "midcol unvoted" boundary.
# Each chunk contains the vote arrows + score + entry for one post.
# Note: old.reddit.com may insert optional elements between midcol and entry
# (e.g. <a class="thumbnail"> for link posts), so we allow extra content
# between the closing </div> of midcol and the opening of entry.
_CHUNK_RE = re.compile(
    r'<div\s+class="midcol\s+unvoted">'
    r'.*?</div>\s*</div>\s*'
    r'(?:<(?:a|div)[^>]*>.*?</(?:a|div)>\s*)*?'
    r'<div\s+class="entry\s+unvoted">'
    r'.*?</div>\s*</div>\s*'
    r'(?:<div\s+class="child">.*?</div>\s*)?'
    r'<div\s+class="clearleft">',
    re.DOTALL,
)

# Extract score from a chunk: <div class="score likes" title="N">
_SCORE_RE = re.compile(r'<div\s+class="score\s+likes"\s+title="(\d+)"', re.DOTALL)

# Extract title, href, domain from a chunk.
# The title link may be preceded by optional flair/spans (e.g. linkflairlabel),
# and the domain may be after the title link or after additional post-title spans.
# Matches: <p class="title"> ... <a class="title may-blank" href="...">Title</a>
#          ... <span class="domain">(domain)</span>
_TITLE_RE = re.compile(
    r'<p\s+class="title">'
    r'(?:<[^>]+>\s*)*?'  # optional spans before the title link (flair labels, etc.)
    r'<a\s+class="title\s+may-blank[^"]*"\s+[^>]*href="([^"]+)"[^>]*>'
    r'(.*?)</a>'
    r'(?:<[^>]+>[^<]*</[^>]+>\s*)*?'  # optional spans after the title link
    r'<span\s+class="domain">\(<a[^>]*>([^<]*)</a>\)</span>',
    re.DOTALL,
)

# Extract author and comments from a chunk
_AUTHOR_RE = re.compile(
    r'<a[^>]*class="author[^"]*"[^>]*>([^<]+)</a>', re.DOTALL
)
_COMMENTS_RE = re.compile(
    r'<a[^>]*class="[^"]*\bcomments\s+may-blank[^"]*"[^>]*>([^<]*)</a>', re.DOTALL
)

# Subreddit name
_SUBREDDIT_RE = re.compile(
    r'data-subreddit-prefixed="([^"]+)"',
)

# Next page link
_NEXT_RE = re.compile(
    r'<span\s+class="next-button">.*?<a\s+href="([^"]+)"',
    re.DOTALL,
)


def _clean_title(title: str) -> str:
    """Strip HTML tags and decode entities from a title."""
    title = re.sub(r"<[^>]+>", "", title)
    title = title.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    title = title.replace("&quot;", '"').replace("&#x27;", "'").strip()
    return title


class RedditListingStrategy:
    """Extract post listings from old.reddit.com subreddit feeds."""

    async def extract(self, html: str, url: str) -> StrategyResult | None:
        """Parse old.reddit.com listing HTML into structured markdown.

        Returns ``None`` if the page doesn't look like a Reddit listing
        (no post rows found), letting the next strategy in the priority
        chain handle it.
        """
        chunks = _CHUNK_RE.findall(html)
        if not chunks:
            return None

        sr_match = _SUBREDDIT_RE.search(html)
        subreddit = sr_match.group(1) if sr_match else "r/reddit"
        lines: list[str] = []
        lines.append(f"## {subreddit}")
        lines.append("")

        for i, chunk in enumerate(chunks):
            score_m = _SCORE_RE.search(chunk)
            title_m = _TITLE_RE.search(chunk)
            author_m = _AUTHOR_RE.search(chunk)
            comments_m = _COMMENTS_RE.search(chunk)

            if not (score_m and title_m and author_m):
                continue

            score = score_m.group(1)
            href = title_m.group(1)
            raw_title = title_m.group(2)
            domain = title_m.group(3)
            author = author_m.group(1)
            comments_text = comments_m.group(1) if comments_m else ""

            title = _clean_title(raw_title)
            score_int = int(score)

            comments_count = 0
            comments_text_clean = comments_text.strip().lower()
            if comments_text_clean and comments_text_clean != "comment":
                with contextlib.suppress(ValueError, IndexError):
                    comments_count = int(comments_text_clean.split()[0])

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
