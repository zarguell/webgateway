from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _is_html(content: str) -> bool:
    return bool(_HTML_TAG_RE.search(content))


def markdownify_convert(html: str) -> str:
    from markdownify import markdownify as md
    try:
        return md(
            html,
            heading_style="ATX",
            bullets="-",
            strip=["script", "style", "nav", "footer", "header"],
        )
    except Exception as exc:
        logger.warning("markdownify conversion failed: %s", exc)
        return html


def html2text_convert(html: str) -> str:
    import html2text
    try:
        h = html2text.HTML2Text()
        h.ignore_links = False
        h.ignore_images = True
        return h.handle(html)
    except Exception as exc:
        logger.warning("html2text conversion failed: %s", exc)
        return html


def convert_to_markdown(content: str, converter: str = "markdownify") -> str:
    if converter == "none" or not _is_html(content):
        return content
    if converter == "markdownify":
        return markdownify_convert(content)
    if converter == "html2text":
        return html2text_convert(content)
    return content
