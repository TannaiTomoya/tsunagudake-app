import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from flask import Flask

from app.cli import init_cli
from app.config import load_config, validate_config
from app.extensions import csrf, db, login_manager, migrate
from app.guide import SCRIPTS
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
    app.jinja_env.globals["guide_scripts"] = SCRIPTS

    from app.blueprints.auth import bp as auth_bp
    from app.blueprints.needs import bp as needs_bp
    from app.blueprints.public import bp

    login_manager.login_view = "auth.login"
    login_manager.login_message = None
    app.register_blueprint(bp)
    app.register_blueprint(auth_bp)
    from app.blueprints.matching import bp as matching_bp

    app.register_blueprint(needs_bp)
    app.register_blueprint(matching_bp)
    from app.blueprints.statements import bp as statements_bp

    app.register_blueprint(statements_bp)
    if app.config["APP_ENV"] in {"demo", "development"}:
        from app.blueprints.demo import bp as demo_bp

        app.register_blueprint(demo_bp)
    from app.blueprints.accounts import bp as accounts_bp
    from app.blueprints.admin import bp as admin_bp
    from app.blueprints.talent import bp as talent_bp

    app.register_blueprint(accounts_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(talent_bp)
    init_cli(app)
    return app
