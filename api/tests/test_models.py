from factories import ArticleFactory, AuthorFactory


def test_new_content_records_default_to_sanitization(app):
    # Arrange
    with app.app_context():
        author = AuthorFactory()
        article = ArticleFactory(author=author)

        # Act
        from extensions import db

        db.session.flush()

        # Assert
        assert author.bypass_sanitization is False
        assert article.bypass_sanitization is False
