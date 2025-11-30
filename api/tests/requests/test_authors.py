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


def test_create_author(client):
    payload = {
        "author": {
            "name": "New Author",
            "birthdate": "1990-01-01",
            "photo_url": "https://example.com/photo.jpg",
            "public_key": "ssh-ed25519 AAAA",
            "bio": "Developer",
        }
    }

    response = client.post("/authors", json=payload)

    assert response.status_code == 201
    assert json_body(response)["name"] == "New Author"


def test_author_validation_errors(client):
    response = client.post("/authors", json={"author": {"name": ""}})

    assert response.status_code == 422
    assert "errors" in json_body(response)


def test_update_author(client):
    author = AuthorFactory()

    response = client.patch(
        f"/authors/{author.id}",
        json={"author": {"bio": "Updated"}},
    )

    assert response.status_code == 200
    assert json_body(response)["bio"] == "Updated"


def test_delete_author(client):
    author = AuthorFactory()

    response = client.delete(f"/authors/{author.id}")

    assert response.status_code == 204

