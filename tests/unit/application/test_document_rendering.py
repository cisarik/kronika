"""Safe rendering evidence for completed question/answer documents."""

from __future__ import annotations

from kronika.application.document_rendering import (
    render_inline,
    render_markdown,
    render_record_document,
)


def test_headings_lists_quotes_and_fences_render() -> None:
    html = render_markdown(
        "# Title\n\nIntro paragraph.\n\n- one\n- two\n\n1. first\n2. second\n\n"
        "> quoted\n\n```python\nprint('<x>')\n```\n"
    )
    assert "<h1>Title</h1>" in html
    assert "<p>Intro paragraph.</p>" in html
    assert "<ul><li>one</li><li>two</li></ul>" in html
    assert "<ol><li>first</li><li>second</li></ol>" in html
    assert "<blockquote><p>quoted</p></blockquote>" in html
    assert '<pre><code class="language-python">print(&#x27;&lt;x&gt;&#x27;)</code></pre>' in html


def test_inline_subset_renders_and_escapes() -> None:
    html = render_inline("A **bold** and *italic* and `code` line")
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html
    assert "<code>code</code>" in html


def test_raw_html_is_escaped_not_passed_through() -> None:
    html = render_markdown('<script>alert("x")</script>\n\n<img src=x onerror=alert(1)>')
    assert "<script" not in html
    assert "&lt;script&gt;" in html
    assert "<img" not in html
    assert "&lt;img" in html


def test_unsafe_link_schemes_are_rendered_as_text() -> None:
    html = render_inline("[click](javascript:alert(1)) and [ok](https://example.invalid/a)")
    assert "javascript:" not in html
    assert "[click]" not in html
    assert "click" in html
    assert 'href="https://example.invalid/a"' in html
    assert 'rel="noopener noreferrer"' in html


def test_link_attribute_injection_is_escaped() -> None:
    html = render_inline('[x](https://example.invalid/"onmouseover="alert(1))')
    # The raw quote cannot terminate the href attribute; it is escaped text.
    assert '"onmouseover=' not in html
    assert "&quot;onmouseover=" in html


def test_mailto_links_are_allowed() -> None:
    html = render_inline("[mail](mailto:alice@example.invalid)")
    assert 'href="mailto:alice@example.invalid"' in html


def test_record_document_wraps_question_and_answer() -> None:
    html = render_record_document(
        question_text="What is **it**?",
        answer_text="# Answer\n\nIt is fine.",
    )
    assert 'class="kronika-document"' in html
    assert "<h1>Question</h1>" in html
    assert "<strong>it</strong>" in html
    assert "<h2>Answer</h2>" in html
    assert "<h1>Answer</h1>" in html
    assert html.index("<h1>Question</h1>") < html.index("<h2>Answer</h2>")


def test_large_document_stays_bounded_and_escaped() -> None:
    payload = "<b>" + ("x" * 100_000) + "</b>"
    html = render_markdown(payload)
    assert "<b>" not in html
    assert len(html) < 200_000


def test_deep_quote_nesting_is_bounded_and_escaped() -> None:
    from kronika.application.document_rendering import MAX_QUOTE_DEPTH

    html = render_markdown(("> " * 1200) + "leaf")
    assert "leaf" in html
    assert html.count("<blockquote>") <= MAX_QUOTE_DEPTH + 1
    assert html.count("<blockquote>") == html.count("</blockquote>")
    document = render_record_document(
        question_text="Deep?",
        answer_text=("> " * 1200) + "leaf",
    )
    assert "leaf" in document
