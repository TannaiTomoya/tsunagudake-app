from flask import Flask, Response, render_template, request
from werkzeug.exceptions import HTTPException, SecurityError
from werkzeug.middleware.proxy_fix import ProxyFix


def init_security(app: Flask) -> None:
    if app.config["PROXY_FIX"]:
        # nginx が上書きする二つのヘッダーだけを、一段分信頼します。
        app.wsgi_app = ProxyFix(  # type: ignore[method-assign]
            app.wsgi_app, x_for=1, x_proto=1, x_host=0
        )

    @app.after_request
    def add_headers(response: Response) -> Response:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.is_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.errorhandler(HTTPException)
    def http_error(error: HTTPException) -> Response:
        if isinstance(error, SecurityError):
            return Response(
                "リクエストを確認できませんでした。", status=400, mimetype="text/plain"
            )
        messages = {
            400: (
                "入力や操作を確認できませんでした。前の画面に戻ってやり直してください。"
            ),
            403: (
                "この操作は現在の立場では行えません。"
                "担当の画面に戻るか、事務所の代表にご確認ください。"
            ),
            404: (
                "ページが見つかりませんでした。"
                "アドレスを確かめるか、ホームから開き直してください。"
            ),
            405: "この操作は受け付けていません。画面のボタンからやり直してください。",
            413: (
                "送信する内容が大きすぎます。文字数を減らしてもう一度お試しください。"
            ),
            500: (
                "一時的な問題で処理を完了できませんでした。"
                "少し時間をおいてもう一度お試しください。"
            ),
        }
        response = Response(status=error.code, headers=error.get_headers())
        response.data = render_template(
            "error.html",
            message=messages.get(error.code or 500, "処理できませんでした。"),
        )
        response.content_type = "text/html; charset=utf-8"
        return response
