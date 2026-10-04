from tests.factories import ArticleFactory, AuthorFactory
from tests.utils import json_body


def test_legacy_article_and_bio_are_sanitized_on_public_serialization(client):
    # Arrange
    author = AuthorFactory(bio="<script>bio secret</script><p>public bio</p>", bypass_sanitization=False)
    article = ArticleFactory(author=author, post_entry="<script>article secret</script><p>public article</p>", bypass_sanitization=False)

    # Act
    article_response = client.get(f"/articles/{article.id}")
    author_response = client.get(f"/authors/{author.id}")

    # Assert
    assert article_response.status_code == 200
    assert author_response.status_code == 200
    assert json_body(article_response)["post_entry"] == "<p>public article</p>"
    assert json_body(author_response)["bio"] == "<p>public bio</p>"
    assert "article secret" in article.post_entry
    assert "bio secret" in author.bio


def test_nested_author_articles_apply_each_article_bypass(client):
    # Arrange
    author = AuthorFactory(bio="<script>bio secret</script><p>bio</p>")
    ArticleFactory(author=author, slug="sanitized-nested", post_entry="<script>private</script><p>clean</p>", bypass_sanitization=False)
    ArticleFactory(author=author, slug="raw-nested", post_entry="<script>approved</script><p>raw</p>", bypass_sanitization=True)

    # Act
    response = client.get(f"/authors/{author.id}")

    # Assert
    assert response.status_code == 200
    payload = json_body(response)
    assert payload["bio"] == "<p>bio</p>"
    nested = {article["slug"]: article for article in payload["articles"]}
    assert nested["sanitized-nested"]["post_entry"] == "<p>clean</p>"
    assert nested["sanitized-nested"]["bypass_sanitization"] is False
    assert nested["raw-nested"]["post_entry"] == "<script>approved</script><p>raw</p>"
    assert nested["raw-nested"]["bypass_sanitization"] is True
