import click
from email_validator import EmailNotValidError, validate_email
from flask import Flask
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Municipality, Role, Talent, User


def init_cli(app: Flask) -> None:
    @app.cli.command("create-user")
    @click.option("--email", prompt="メールアドレス")
    @click.option("--display-name", prompt="表示名")
    @click.option(
        "--role", type=click.Choice([role.value for role in Role]), prompt="ロール"
    )
    @click.option(
        "--municipality-code", help="つなぎ手のプロフィールを作る市町村のコード"
    )
    def create_user(
        email: str, display_name: str, role: str, municipality_code: str | None
    ) -> None:
        municipality = None
        if municipality_code is not None:
            if role != Role.TSUNAGITE:
                raise click.ClickException("市町村コードはつなぎ手に指定してください。")
            municipality = db.session.scalar(
                select(Municipality).where(Municipality.code == municipality_code)
            )
            if municipality is None:
                raise click.ClickException("登録済みの市町村コードを指定してください。")
        try:
            address = validate_email(
                email.strip(), check_deliverability=False
            ).normalized.lower()
        except EmailNotValidError:
            raise click.ClickException("メールアドレスを確認してください。") from None
        if len(address) > 255 or not 1 <= len(display_name.strip()) <= 100:
            raise click.ClickException(
                "メールアドレスと表示名の長さを確認してください。"
            )
        password = click.prompt("パスワード", hide_input=True, confirmation_prompt=True)
        if not 12 <= len(password) <= 1024:
            raise click.ClickException("パスワードは12〜1024文字で入力してください。")
        user = User(email=address, display_name=display_name.strip(), role=Role(role))
        user.set_password(password)
        db.session.add(user)
        try:
            if municipality is not None:
                db.session.flush()
                db.session.add(Talent(user_id=user.id, municipality_id=municipality.id))
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            raise click.ClickException("ユーザーを作成できませんでした。") from None
        click.echo("ユーザーを作成しました。")
