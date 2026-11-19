import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from flask import Flask

from app.cli import init_cli
from app.config import load_config, validate_config
from app.extensions import csrf, db, login_manager, migrate
from app.security import init_security


def create_app(config: Mapping[str, Any] | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(load_config(os.environ))
    if config:
        app.config.update(config)
    validate_config(app.config)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app, add_context_processor=False)
    csrf.init_app(app)
    init_security(app)

    from app.blueprints.auth import bp as auth_bp
    from app.blueprints.needs import bp as needs_bp
    from app.blueprints.public import bp

    login_manager.login_view = "auth.login"
    login_manager.login_message = None
    app.register_blueprint(bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(needs_bp)
    init_cli(app)
    return app
