from functools import cache

from flask import Blueprint, jsonify, render_template, request
from flask.typing import ResponseReturnValue

from app.guide import SCRIPTS
from app.guide_faq import BY_KEY, QUESTION_LIMIT, Faq, answer_key, answer_text
from app.guide_faq import suggestions as pick
from tsunagu_rules.translation import load_dictionary

bp = Blueprint("guide", __name__)


@cache
def _taxwork() -> tuple[str, ...]:
    return load_dictionary().taxwork


def suggestions(screen: str | None, exclude: str | None = None) -> tuple[Faq, ...]:
    """テンプレートからも使う。知らない画面の名前は、基本の質問に落とす。"""
    return pick(screen if screen in SCRIPTS else None, exclude)


@bp.post("/guide/ask")
def ask() -> ResponseReturnValue:
    """案内役への質問。用意した答えだけを返し、状態は変えず、質問は残さない。"""
    screen = request.form.get("screen", "")
    key = request.form.get("faq", "")
    question = request.form.get("question", "").strip()[:QUESTION_LIMIT]
    if key:
        reply = answer_key(key)
        asked = BY_KEY[key].question if key in BY_KEY else ""
    else:
        reply = answer_text(question, _taxwork())
        asked = question
    following = suggestions(screen, reply.key)
    if request.accept_mimetypes.best == "application/json":
        return jsonify(
            answer=reply.answer,
            kind=reply.kind,
            next=[{"key": faq.key, "question": faq.question} for faq in following],
        )
    return render_template(
        "guide/answer.html",
        asked=asked,
        reply=reply,
        ask_screen=screen if screen in SCRIPTS else "",
        ask_exclude=reply.key,
    )
