import pytest

from services.html_sanitizer import sanitize_html


def test_sanitize_html_keeps_allowed_formatting_and_safe_links():
    # Arrange
    html = (
        '<p>Hello <strong>world</strong>, <b>all</b>, <em>friends</em>, '
        '<i>neighbors</i>.<br>Welcome.</p>'
        '<ul><li>First</li></ul><ol><li>Second</li></ol>'
        '<blockquote>Quoted</blockquote>'
        '<h1>One</h1><h2>Two</h2><h3>Three</h3><h4>Four</h4><h5>Five</h5><h6>Six</h6>'
        '<pre><code>sample()</code></pre>'
        '<a href="https://example.test/path" target="_blank">Read more</a>'
        '<a href="http://example.test">HTTP link</a>'
        '<a href="/articles/example">Relative link</a>'
        '<a href="mailto:hello@example.test">Email</a>'
    )

    # Act
    sanitized = sanitize_html(html)

    # Assert
    assert sanitized == (
        '<p>Hello <strong>world</strong>, <b>all</b>, <em>friends</em>, '
        '<i>neighbors</i>.<br>Welcome.</p>'
        '<ul><li>First</li></ul><ol><li>Second</li></ol>'
        '<blockquote>Quoted</blockquote>'
        '<h1>One</h1><h2>Two</h2><h3>Three</h3><h4>Four</h4><h5>Five</h5><h6>Six</h6>'
        '<pre><code>sample()</code></pre>'
        '<a href="https://example.test/path" rel="noopener noreferrer">Read more</a>'
        '<a href="http://example.test" rel="noopener noreferrer">HTTP link</a>'
        '<a href="/articles/example" rel="noopener noreferrer">Relative link</a>'
        '<a href="mailto:hello@example.test" rel="noopener noreferrer">Email</a>'
    )


def test_sanitize_html_removes_active_content_and_unsafe_urls():
    # Arrange
    html = (
        '<p onclick="run()">Safe text</p>'
        '<a href="javascript:run()">Unsafe link</a>'
        '<a href="data:text/html,unsafe">Data link</a>'
        '<iframe src="https://example.test"></iframe>'
        '<form><input value="unsafe"></form>'
        '<svg><circle /></svg>'
    )

    # Act
    sanitized = sanitize_html(html)

    # Assert
    assert sanitized == (
        '<p>Safe text</p><a rel="noopener noreferrer">Unsafe link</a>'
        '<a rel="noopener noreferrer">Data link</a>'
    )


def test_sanitize_html_removes_script_contents_and_inline_styles():
    # Arrange
    html = '<p style="color:red">Visible</p><script>alert("secret")</script>'

    # Act
    sanitized = sanitize_html(html)

    # Assert
    assert sanitized == '<p>Visible</p>'


@pytest.mark.parametrize(
    "unsafe_url",
    (
        "JaVaScRiPt:run()",
        "java\nscript:run()",
        "java\tscript:run()",
        "java&#x0a;script:run()",
    ),
)
def test_sanitize_html_removes_obfuscated_javascript_urls(unsafe_url):
    # Arrange
    html = f'<a href="{unsafe_url}">Unsafe link</a>'

    # Act
    sanitized = sanitize_html(html)

    # Assert
    assert sanitized == '<a rel="noopener noreferrer">Unsafe link</a>'
