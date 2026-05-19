"""Contract test for the IndieHackers grouppage shape we parse.

IH has no documented API; the contract is on what `__NEXT_DATA__` exposes. If
this test fails after a fixture refresh, Next.js's inline-data shape changed
(or IH rebuilt their page-props) — investigate before touching the parser.

See CLAUDE.md → Project conventions → "Contract tests for external schemas."
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "indiehackers" / "grouppage_main.html"
_NEXT_DATA_RE = re.compile(
    r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.DOTALL,
)


def _load_next_data() -> dict[str, object]:
    html = _FIXTURE_PATH.read_text()
    match = _NEXT_DATA_RE.search(html)
    assert match, "fixture must contain a __NEXT_DATA__ script tag"
    return json.loads(match.group(1))


def test_next_data_envelope_shape() -> None:
    blob = _load_next_data()
    props = blob.get("props")
    assert isinstance(props, dict)
    assert isinstance(props.get("pageProps"), dict)


def test_posts_path_and_required_fields() -> None:
    blob = _load_next_data()
    posts = blob["props"]["pageProps"]["posts"]  # type: ignore[index]
    assert isinstance(posts, list) and len(posts) > 0
    required = {"slug", "title", "rawBody", "author", "createdAt"}
    for idx, post in enumerate(posts):
        assert isinstance(post, dict), f"post {idx} not a dict"
        missing = required - set(post.keys())
        assert not missing, f"post {idx} missing fields: {missing}"


def test_author_subshape() -> None:
    blob = _load_next_data()
    posts = blob["props"]["pageProps"]["posts"]  # type: ignore[index]
    for post in posts:
        author = post.get("author")
        assert isinstance(author, dict)
        assert "username" in author and isinstance(author["username"], str)


def test_rendered_html_post_cards_present() -> None:
    """Smoke check that the HTML-fallback path has something to scrape."""
    html = _FIXTURE_PATH.read_text()
    # Look for at least one rendered post card with the slug attribute the
    # selectolax fallback path relies on.
    assert 'class="post-card"' in html
    assert "data-slug=" in html
