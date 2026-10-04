from services.tech_report import TechReport


def test_tech_report_redacts_database_credentials(app, monkeypatch):
    # Arrange
    secret_uri = "mysql+pymysql://reporter:db-password-123@db:3306/articles?ssl=true&password=query-secret"
    monkeypatch.setitem(app.config, "SQLALCHEMY_DATABASE_URI", secret_uri)
    report = TechReport(env={"DATABASE_URL": secret_uri})

    # Act
    with app.app_context():
        database_info = report._database_info()
        environment = dict(report._sanitized_env())

    # Assert
    assert "db-password-123" not in database_info["URI"]
    assert "query-secret" not in database_info["URI"]
    assert environment["DATABASE_URL"] == "[FILTERED]"
