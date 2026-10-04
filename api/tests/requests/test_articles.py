from extensions import db
from models import AuthorIdentity
from tests.factories import ArticleFactory, AuthorFactory, SocialFactory
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


def test_create_article(client, admin_headers):
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

    response = client.post("/articles", json=payload, headers=admin_headers)

    assert response.status_code == 201
    body = json_body(response)
    assert body["title"] == "New Article"


def test_create_article_validation_errors(client, admin_headers):
    response = client.post("/articles", json={"article": {"title": ""}}, headers=admin_headers)

    assert response.status_code == 422
    assert "errors" in json_body(response)


def test_update_article(client, admin_headers):
    article = ArticleFactory()

    response = client.patch(
        f"/articles/{article.id}",
        json={"article": {"title": "Atualizado"}},
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert json_body(response)["title"] == "Atualizado"


def test_delete_article(client, admin_headers):
    article = ArticleFactory()

    response = client.delete(f"/articles/{article.id}", headers=admin_headers)

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


def test_public_reads_remain_anonymous(client):
    # Arrange
    author = AuthorFactory()
    ArticleFactory(author=author)
    SocialFactory(author=author)

    # Act
    responses = [
        client.get("/articles"),
        client.get("/articles/count_by_author"),
        client.get("/authors"),
        client.get("/socials"),
    ]

    # Assert
    assert [response.status_code for response in responses] == [200, 200, 200, 200]


def test_anonymous_writes_return_401(client):
    # Arrange
    author = AuthorFactory()
    payloads = [
        ("/articles", {"article": {"title": "New", "slug": "new", "published_label": "Today", "post_entry": "Body", "tags": []}}),
        ("/authors", {"author": {"name": "New", "birthdate": "1990-01-01", "photo_url": "https://example.com/a.jpg", "public_key": "key", "bio": "Bio"}}),
        ("/socials", {"social": {"slug": "new", "profile_link": "https://example.com", "description": "Profile", "author_id": author.id}}),
    ]

    # Act
    responses = [client.post(path, json=payload) for path, payload in payloads]

    # Assert
    assert [response.status_code for response in responses] == [401, 401, 401]


def test_admin_can_write_all_domain_resources(client, admin_headers):
    # Arrange
    author = AuthorFactory()

    # Act
    article_response = client.post(
        "/articles",
        headers=admin_headers,
        json={"article": {"title": "Admin article", "slug": "admin-article", "published_label": "Today", "post_entry": "Body", "tags": [], "author_id": author.id}},
    )
    author_response = client.patch(
        f"/authors/{author.id}", headers=admin_headers, json={"author": {"bio": "Managed"}}
    )
    social_response = client.post(
        "/socials",
        headers=admin_headers,
        json={"social": {"slug": "admin-social", "profile_link": "https://example.com", "description": "Managed", "author_id": author.id}},
    )

    # Assert
    assert article_response.status_code == 201
    assert author_response.status_code == 200
    assert social_response.status_code == 201


def test_author_can_create_and_mutate_only_linked_articles(app, client, author_headers):
    # Arrange
    owner = AuthorFactory(name="Linked author")
    other = AuthorFactory(name="Another author")
    db.session.add(
        AuthorIdentity(
            author_id=owner.id,
            issuer=app.config["KEYCLOAK_ISSUER"].encode(),
            subject=b"linked-author-subject",
        )
    )
    foreign_article = ArticleFactory(author=other)

    # Act
    created = client.post(
        "/articles",
        headers=author_headers,
        json={"article": {"title": "Mine", "slug": "mine", "published_label": "Today", "post_entry": "Body", "tags": []}},
    )
    own_article_id = json_body(created).get("id")
    updated = client.patch(
        f"/articles/{own_article_id}",
        headers=author_headers,
        json={"article": {"title": "Updated mine"}},
    )
    delete_own = client.delete(f"/articles/{own_article_id}", headers=author_headers)
    update_foreign = client.patch(
        f"/articles/{foreign_article.id}", headers=author_headers, json={"article": {"title": "Steal"}}
    )
    delete_foreign = client.delete(f"/articles/{foreign_article.id}", headers=author_headers)
    missing_before_payload_validation = client.patch(
        "/articles/99999", headers=author_headers, json={"article": {}}
    )

    # Assert
    assert created.status_code == 201
    assert json_body(created)["author_id"] == owner.id
    assert updated.status_code == 200
    assert delete_own.status_code == 204
    assert update_foreign.status_code == 403
    assert delete_foreign.status_code == 403
    assert missing_before_payload_validation.status_code == 404


def test_author_cannot_supply_or_transfer_author_id(app, client, author_headers):
    # Arrange
    owner = AuthorFactory(name="Linked author")
    other = AuthorFactory(name="Other author")
    db.session.add(
        AuthorIdentity(
            author_id=owner.id,
            issuer=app.config["KEYCLOAK_ISSUER"].encode(),
            subject=b"linked-author-subject",
        )
    )
    article = ArticleFactory(author=owner)

    # Act
    created_with_author_id = client.post(
        "/articles",
        headers=author_headers,
        json={"article": {"title": "Spoof", "slug": "spoof", "published_label": "Today", "post_entry": "Body", "tags": [], "author_id": owner.id}},
    )
    update_with_other_author = client.patch(
        f"/articles/{article.id}",
        headers=author_headers,
        json={"article": {"author_id": other.id}},
    )

    # Assert
    assert created_with_author_id.status_code == 422
    assert update_with_other_author.status_code == 422
    assert article.author_id == owner.id


def test_unlinked_author_is_forbidden(client, unlinked_author_headers):
    # Arrange
    payload = {"article": {"title": "Mine", "slug": "unlinked", "published_label": "Today", "post_entry": "Body", "tags": []}}

    # Act
    response = client.post("/articles", headers=unlinked_author_headers, json=payload)

    # Assert
    assert response.status_code == 403
