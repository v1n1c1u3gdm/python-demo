from tests.factories import AuthorFactory, SocialFactory
from tests.utils import json_body


def test_list_socials(client):
    SocialFactory.create_batch(3)

    response = client.get("/socials")

    assert response.status_code == 200
    assert len(json_body(response)) == 3


def test_show_social(client):
    social = SocialFactory()

    response = client.get(f"/socials/{social.id}")

    assert response.status_code == 200
    assert json_body(response)["id"] == social.id


def test_create_social(client):
    author = AuthorFactory()
    payload = {
        "social": {
            "slug": "github",
            "profile_link": "https://github.com/example",
            "description": "Repo",
            "author_id": author.id,
        }
    }

    response = client.post("/socials", json=payload)

    assert response.status_code == 201
    assert json_body(response)["slug"] == "github"


def test_social_validation_errors(client):
    response = client.post("/socials", json={"social": {"slug": ""}})

    assert response.status_code == 422
    assert "errors" in json_body(response)


def test_update_social(client):
    social = SocialFactory()

    response = client.patch(
        f"/socials/{social.id}",
        json={"social": {"description": "Atualizado"}},
    )

    assert response.status_code == 200
    assert json_body(response)["description"] == "Atualizado"


def test_delete_social(client):
    social = SocialFactory()

    response = client.delete(f"/socials/{social.id}")

    assert response.status_code == 204

