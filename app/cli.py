import click
from email_validator import EmailNotValidError, validate_email
from flask import Flask
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Municipality, Role, Talent, User
from app.services.municipalities import allowed


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
            # このコマンドが作るのは通常のアカウントなので、実在の町だけ。
            if not allowed(municipality, demo=False):
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

    @app.cli.command("verify-ledger")
    def verify_ledger() -> None:
        from app.services.ledger import ledger_failures

        failures = [
            f"困りごと {need_id} / 行 {seq}" for need_id, seq in ledger_failures()
        ]
        if failures:
            raise click.ClickException("台帳の不一致: " + "、".join(failures))
        click.echo("台帳の検証が完了しました。")

    @app.cli.command("anchor-ledger")
    def anchor_ledger_command() -> None:
        """全部の鎖を検証し、鎖の最後のハッシュを DB の外に控える（毎日）。"""
        from pathlib import Path

        from sqlalchemy.exc import SQLAlchemyError

        from app.models import today_in_japan
        from app.services.anchor import anchor_ledger

        directory = Path(
            app.config["LEDGER_ANCHOR_DIR"]
            or Path(app.instance_path) / "ledger-anchors"
        )
        try:
            result = anchor_ledger(directory, today_in_japan())
        except OSError:
            db.session.rollback()
            raise click.ClickException(
                "控えのフォルダに書けませんでした。LEDGER_ANCHOR_DIR をご確認ください。"
            ) from None
        except SQLAlchemyError:
            db.session.rollback()
            raise click.ClickException(
                "控えを保存できませんでした。マイグレーションをご確認ください。"
            ) from None
        click.echo(
            f"鎖{result.chains}本・{result.entries}行を確かめました。"
            + (
                "控えを保存しました。"
                if result.created
                else "今日の控えは保存済みです。"
            )
        )
        click.echo(f"まとめた印: {result.digest}")
        if result.problems:
            raise click.ClickException(
                "台帳の不一致（運営に知らせました）: " + "、".join(result.problems)
            )

    def run_demo(*, reset: bool) -> None:
        from sqlalchemy.exc import SQLAlchemyError

        from app.services.demo import (
            DemoError,
            clear_demo,
            demo_allowed,
            demo_password,
            seed_demo,
        )

        if not demo_allowed():
            raise click.ClickException(
                "デモ操作は demo・development 環境で実行してください。"
            )
        try:
            password = demo_password()
            if reset:
                clear_demo()
            created = seed_demo(password)
            db.session.commit()
        except DemoError as exc:
            db.session.rollback()
            raise click.ClickException(str(exc)) from None
        except SQLAlchemyError:
            db.session.rollback()
            raise click.ClickException(
                "デモを保存できませんでした。既存のデータとマイグレーションをご確認ください。"
            ) from None
        click.echo(
            "デモを用意しました。" if created else "デモはすでに用意されています。"
        )

    @app.cli.command("seed-demo")
    def seed_demo_command() -> None:
        run_demo(reset=False)

    @app.cli.command("reset-demo")
    @click.option("--yes", is_flag=True, help="既存のデモデータを初期化します。")
    def reset_demo_command(yes: bool) -> None:
        if not yes:
            raise click.ClickException("デモの初期化には --yes を指定してください。")
        run_demo(reset=True)

    @app.cli.command("sync-municipalities")
    def sync_municipalities() -> None:
        """市町村の一覧（総務省のコード）から、無い行だけを足す。"""
        from sqlalchemy.exc import SQLAlchemyError

        from app.services.municipalities import load_source, sync

        try:
            result = sync(load_source())
        except (OSError, KeyError, ValueError) as exc:
            raise click.ClickException(
                f"市町村の一覧を読めませんでした。{exc}"
            ) from None
        except SQLAlchemyError:
            db.session.rollback()
            raise click.ClickException(
                "市町村を保存できませんでした。マイグレーションをご確認ください。"
            ) from None
        click.echo(
            f"市町村を{result.added}件足しました。"
            f"すでにある{result.kept}件は、そのままです。"
        )
        if result.different:
            click.echo(
                f"名前か都道府県が一覧と違う行が{result.different}件あります。"
                "直していません。"
            )

    @app.cli.command("send-mail")
    def send_mail() -> None:
        from app.services.mail import MailError, send_pending

        try:
            sent = send_pending()
        except MailError as exc:
            raise click.ClickException(str(exc)) from None
        click.echo(f"{sent}件のメールを送信しました。")

    @app.cli.command("prune-audit")
    def prune_audit() -> None:
        """監査記録の IP の印を、180日たったら消す（行は残す）。"""
        from datetime import timedelta

        from app.models import AuditLog, utc_now

        limit = utc_now() - timedelta(days=180)
        rows = db.session.scalars(
            select(AuditLog).where(
                AuditLog.ip_hash.is_not(None), AuditLog.created_at < limit
            )
        ).all()
        for row in rows:
            row.ip_hash = None
        db.session.commit()
        click.echo(f"監査記録{len(rows)}件から、IP の印を消しました。")
