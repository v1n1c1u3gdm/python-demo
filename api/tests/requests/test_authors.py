from tests.factories import ArticleFactory, AuthorFactory, SocialFactory
from tests.utils import json_body


def test_list_authors_includes_socials(client):
    SocialFactory()
    response = client.get("/authors")

    assert response.status_code == 200
    payload = json_body(response)
    assert len(payload) == 1
    assert "socials" in payload[0]
    assert "articles" not in payload[0]


def test_show_author_includes_articles_and_socials(client):
    author = AuthorFactory()
    ArticleFactory(author=author)
    SocialFactory(author=author)

    response = client.get(f"/authors/{author.id}")

    assert response.status_code == 200
    body = json_body(response)
    assert len(body["articles"]) == 1
    assert len(body["socials"]) == 1


def test_create_author(client, admin_headers):
    payload = {
        "author": {
            "name": "New Author",
            "birthdate": "1990-01-01",
            "photo_url": "https://example.com/photo.jpg",
            "public_key": "ssh-ed25519 AAAA",
            "bio": "Developer",
        }
    }

    response = client.post("/authors", json=payload, headers=admin_headers)

    assert response.status_code == 201
    assert json_body(response)["name"] == "New Author"


def test_author_validation_errors(client, admin_headers):
    response = client.post("/authors", json={"author": {"name": ""}}, headers=admin_headers)

    assert response.status_code == 422
    assert "errors" in json_body(response)


def test_update_author(client, admin_headers):
    author = AuthorFactory()

    response = client.patch(
        f"/authors/{author.id}",
        json={"author": {"bio": "Updated"}},
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert json_body(response)["bio"] == "Updated"


def test_delete_author(client, admin_headers):
    author = AuthorFactory()

    response = client.delete(f"/authors/{author.id}", headers=admin_headers)

    assert response.status_code == 204


def test_each_request_uses_its_own_bearer_claims(client, admin_headers, author_headers):
    # Arrange
    author = AuthorFactory()

    # Act
    admin_response = client.patch(
        f"/authors/{author.id}", headers=admin_headers, json={"author": {"bio": "Admin edit"}}
    )
    author_response = client.patch(
        f"/authors/{author.id}", headers=author_headers, json={"author": {"bio": "Author edit"}}
    )

    # Assert
    assert admin_response.status_code == 200
    assert author_response.status_code == 403


def test_admin_can_store_and_render_unsanitized_author_bio(client, admin_headers):
    # Arrange
    payload = {"author": {"name": "Raw Bio", "birthdate": "1990-01-01", "photo_url": "https://example.com/photo.jpg", "public_key": "key", "bio": "<script>trusted()</script><p>bio</p>", "bypass_sanitization": True}}

    # Act
    response = client.post("/authors", headers=admin_headers, json=payload)

    # Assert
    assert response.status_code == 201
    author = AuthorFactory._meta.model.query.filter_by(name="Raw Bio").one()
    assert author.bypass_sanitization is True
    assert author.bio == "<script>trusted()</script><p>bio</p>"
    assert json_body(response)["bio"] == author.bio
    assert json_body(response)["bypass_sanitization"] is True


def test_admin_revoking_bio_bypass_sanitizes_stored_bio(client, admin_headers):
    # Arrange
    author = AuthorFactory(bio="<script>alert(1)</script><p>kept</p>", bypass_sanitization=True)

    # Act
    response = client.patch(f"/authors/{author.id}", headers=admin_headers, json={"author": {"bypass_sanitization": False}})

    # Assert
    assert response.status_code == 200
    assert author.bypass_sanitization is False
    assert author.bio == "<p>kept</p>"
    assert json_body(response)["bio"] == "<p>kept</p>"


def test_non_admin_cannot_change_author_bio_bypass(client, author_headers):
    # Arrange
    author = AuthorFactory()

    # Act
    response = client.patch(f"/authors/{author.id}", headers=author_headers, json={"author": {"bypass_sanitization": False}})

    # Assert
    assert response.status_code == 403


def test_bypass_flags_require_json_booleans(client, admin_headers):
    # Arrange
    author = AuthorFactory()
    invalid_values = ["true", 1, None]

    # Act
    article_responses = [
        client.post("/articles", headers=admin_headers, json={"article": {"title": f"Invalid {index}", "slug": f"invalid-{index}", "published_label": "Today", "post_entry": "Body", "tags": [], "author_id": author.id, "bypass_sanitization": value}})
        for index, value in enumerate(invalid_values)
    ]
    author_responses = [
        client.patch(f"/authors/{author.id}", headers=admin_headers, json={"author": {"bypass_sanitization": value}})
        for value in invalid_values
    ]

    # Assert
    assert [response.status_code for response in article_responses] == [422, 422, 422]
    assert [response.status_code for response in author_responses] == [422, 422, 422]
