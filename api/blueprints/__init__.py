def register_blueprints(app):
    from .articles import bp as articles_bp
    from .authors import bp as authors_bp
    from .health import bp as health_bp
    from .metrics import bp as metrics_bp
    from .socials import bp as socials_bp
    from .tech import bp as tech_bp

    app.register_blueprint(articles_bp)
    app.register_blueprint(authors_bp)
    app.register_blueprint(metrics_bp)
    app.register_blueprint(socials_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(tech_bp)

