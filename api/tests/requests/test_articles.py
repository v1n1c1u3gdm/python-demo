from tests.factories import ArticleFactory, AuthorFactory
from tests.utils import json_body


def test_list_articles(client):
    ArticleFactory.create_batch(2)

    response = client.get("/articles")

    assert response.status_code == 200
    assert len(json_body(response)) == 2


def test_show_article(client):
    article = ArticleFactory()

    response = client.get(f"/articles/{article.id}")

    assert response.status_code == 200
    assert json_body(response)["id"] == article.id


def test_create_article(client):
    author = AuthorFactory()
    payload = {
        "article": {
            "title": "New Article",
            "slug": "new-article",
            "published_label": "Hoje",
            "post_entry": "Conteúdo",
            "tags": ["python"],
            "author_id": author.id,
        }
    }

    response = client.post("/articles", json=payload)

    assert response.status_code == 201
    body = json_body(response)
    assert body["title"] == "New Article"


def test_create_article_validation_errors(client):
    response = client.post("/articles", json={"article": {"title": ""}})

    assert response.status_code == 422
    assert "errors" in json_body(response)


def test_update_article(client):
    article = ArticleFactory()

    response = client.patch(
        f"/articles/{article.id}",
        json={"article": {"title": "Atualizado"}},
    )

    assert response.status_code == 200
    assert json_body(response)["title"] == "Atualizado"


def test_delete_article(client):
    article = ArticleFactory()

    response = client.delete(f"/articles/{article.id}")

    assert response.status_code == 204


def test_count_by_author(client):
    author_with_articles = AuthorFactory(name="Alice")
    ArticleFactory.create_batch(2, author=author_with_articles)
    author_without_articles = AuthorFactory(name="Bob")

    response = client.get("/articles/count_by_author")

    assert response.status_code == 200
    payload = json_body(response)
    alice = next(item for item in payload if item["author_id"] == author_with_articles.id)
    bob = next(item for item in payload if item["author_id"] == author_without_articles.id)
    assert alice["articles_count"] == 2
    assert bob["articles_count"] == 0

