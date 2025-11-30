import factory

from extensions import db
from models import Article, Author, Social


class SQLAlchemyFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        abstract = True
        sqlalchemy_session = db.session
        sqlalchemy_session_persistence = "flush"


class AuthorFactory(SQLAlchemyFactory):
    class Meta:
        model = Author

    name = factory.Sequence(lambda n: f"Author {n}")
    birthdate = factory.Faker("date_object")
    photo_url = factory.Faker("image_url")
    public_key = factory.Faker("pystr", max_chars=32)
    bio = factory.Faker("sentence", nb_words=6)


class ArticleFactory(SQLAlchemyFactory):
    class Meta:
        model = Article

    author = factory.SubFactory(AuthorFactory)
    title = factory.Sequence(lambda n: f"Article {n}")
    slug = factory.Sequence(lambda n: f"article-{n}")
    published_label = factory.Faker("sentence", nb_words=3)
    post_entry = factory.Faker("paragraph")
    tags = factory.LazyFunction(lambda: ["dev", "blog"])


class SocialFactory(SQLAlchemyFactory):
    class Meta:
        model = Social

    author = factory.SubFactory(AuthorFactory)
    slug = factory.Sequence(lambda n: f"social-{n}")
    profile_link = factory.LazyAttribute(lambda obj: f"https://example.com/{obj.slug}")
    description = factory.Faker("sentence", nb_words=4)

