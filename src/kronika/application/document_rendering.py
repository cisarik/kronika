"""Safe bounded rendering for completed question/answer documents.

The renderer converts a small Markdown subset to escaped HTML. Raw HTML is
escaped, never passed through. Links are emitted only for safe URL schemes.
There is no template engine, no trusted HTML field and no new dependency.
"""

from __future__ import annotations

import re
from html import escape

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_UNORDERED_ITEM = re.compile(r"^[-*]\s+(.*)$")
_ORDERED_ITEM = re.compile(r"^\d+[.)]\s+(.*)$")
_FENCE = re.compile(r"^```([A-Za-z0-9_+.-]{0,32})\s*$")
_INLINE = re.compile(
    r"`(?P<code>[^`]+)`"
    r"|\[(?P<link_text>[^\]]+)\]\((?P<link_url>[^)\s]+)\)"
    r"|\*\*(?P<bold>[^*]+)\*\*"
    r"|\*(?P<italic>[^*]+)\*"
)
_SAFE_SCHEMES = ("http://", "https://", "mailto:")

# Bounded quote nesting: deeper prefixes are rendered as escaped text instead
# of recursing further, so hostile input cannot exhaust the call stack.
MAX_QUOTE_DEPTH = 32


def _safe_url(url: str) -> str | None:
    candidate = url.strip()
    if not candidate or len(candidate) > 2048:
        return None
    lowered = candidate.lower()
    if lowered.startswith(_SAFE_SCHEMES):
        return candidate
    return None


def render_inline(text: str) -> str:
    """Escape one text line and apply the inline Markdown subset."""
    result: list[str] = []
    position = 0
    for match in _INLINE.finditer(text):
        result.append(escape(text[position : match.start()]))
        if match.group("code") is not None:
            result.append(f"<code>{escape(match.group('code'))}</code>")
        elif match.group("link_text") is not None:
            url = _safe_url(match.group("link_url"))
            label = escape(match.group("link_text"))
            if url is None:
                result.append(label)
            else:
                result.append(
                    f'<a href="{escape(url, quote=True)}" rel="noopener noreferrer">'
                    f"{label}</a>"
                )
        elif match.group("bold") is not None:
            result.append(f"<strong>{escape(match.group('bold'))}</strong>")
        else:
            result.append(f"<em>{escape(match.group('italic'))}</em>")
        position = match.end()
    result.append(escape(text[position:]))
    return "".join(result)


def render_markdown(text: str, *, _depth: int = 0) -> str:
    """Render the bounded Markdown subset of ``text`` as escaped HTML."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    list_tag = "ul"
    quote_lines: list[str] = []
    code_lines: list[str] = []
    in_code = False
    code_language = ""

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(
                "<p>" + "<br />".join(render_inline(line) for line in paragraph) + "</p>"
            )
            paragraph.clear()

    def flush_list() -> None:
        nonlocal list_tag
        if list_items:
            items = "".join(f"<li>{render_inline(item)}</li>" for item in list_items)
            blocks.append(f"<{list_tag}>{items}</{list_tag}>")
            list_items.clear()
            list_tag = "ul"

    def flush_quote() -> None:
        if quote_lines:
            if _depth >= MAX_QUOTE_DEPTH:
                escaped = "<br />".join(render_inline(line) for line in quote_lines)
                blocks.append(f"<blockquote><p>{escaped}</p></blockquote>")
            else:
                inner = render_markdown("\n".join(quote_lines), _depth=_depth + 1)
                blocks.append(f"<blockquote>{inner}</blockquote>")
            quote_lines.clear()

    def flush_code() -> None:
        if in_code or code_lines:
            language_class = (
                f' class="language-{escape(code_language, quote=True)}"'
                if code_language
                else ""
            )
            blocks.append(
                f"<pre><code{language_class}>"
                + escape("\n".join(code_lines))
                + "</code></pre>"
            )
            code_lines.clear()

    for line in lines:
        fence = _FENCE.match(line)
        if fence:
            if in_code:
                flush_code()
                in_code = False
                code_language = ""
            else:
                flush_paragraph()
                flush_list()
                flush_quote()
                in_code = True
                code_language = fence.group(1)
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not line.strip():
            flush_paragraph()
            flush_list()
            flush_quote()
            continue
        heading = _HEADING.match(line)
        if heading is not None:
            flush_paragraph()
            flush_list()
            flush_quote()
            level = len(heading.group(1))
            blocks.append(f"<h{level}>{render_inline(heading.group(2))}</h{level}>")
            continue
        quote = line.lstrip()
        if quote.startswith("> "):
            flush_paragraph()
            flush_list()
            quote_lines.append(quote[2:])
            continue
        unordered = _UNORDERED_ITEM.match(line)
        ordered = _ORDERED_ITEM.match(line)
        if unordered is not None or ordered is not None:
            flush_paragraph()
            flush_quote()
            desired_tag = "ul" if unordered is not None else "ol"
            if list_items and desired_tag != list_tag:
                flush_list()
            list_tag = desired_tag
            list_items.append((unordered or ordered).group(1))
            continue
        flush_quote()
        paragraph.append(line)
    flush_code()
    flush_paragraph()
    flush_list()
    flush_quote()
    return "".join(blocks)


def render_record_document(*, question_text: str, answer_text: str) -> str:
    """Render one completed question/answer document as safe HTML."""
    question_html = render_markdown(question_text)
    answer_html = render_markdown(answer_text)
    return (
        '<article class="kronika-document">'
        '<section class="kronika-question">'
        "<h1>Question</h1>"
        f"{question_html}"
        "</section>"
        '<section class="kronika-answer">'
        "<h2>Answer</h2>"
        f"{answer_html}"
        "</section>"
        "</article>"
    )
