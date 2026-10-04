from tests.factories import AuthorFactory, SocialFactory


def test_admin_can_access_tech(client, admin_headers):
    # Arrange
    expected_page = "/tech &mdash; python-demo diagnostics"

    # Act
    response = client.get("/tech", headers=admin_headers)

    # Assert
    assert response.status_code == 200
    assert response.mimetype == "text/html"
    assert expected_page in response.get_data(as_text=True)


def test_non_admin_cannot_access_tech(client, author_headers):
    # Arrange

    # Act
    response = client.get("/tech", headers=author_headers)

    # Assert
    assert response.status_code == 403


def test_non_admin_cannot_mutate_author(client, author_headers):
    # Arrange
    author = AuthorFactory()

    # Act
    response = client.patch(
        f"/authors/{author.id}", headers=author_headers, json={"author": {"bio": "Changed"}}
    )

    # Assert
    assert response.status_code == 403


def test_non_admin_cannot_mutate_social(client, author_headers):
    # Arrange
    author = AuthorFactory()
    social = SocialFactory(author=author)

    # Act
    response = client.patch(
        f"/socials/{social.id}",
        headers=author_headers,
        json={"social": {"description": "Changed"}},
    )

    # Assert
    assert response.status_code == 403
