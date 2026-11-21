from functools import cache

from flask import Blueprint, jsonify, render_template, request
from flask.typing import ResponseReturnValue
from flask_login import current_user

from app.guide import SCRIPTS
from app.guide_faq import (
    BY_KEY,
    QUESTION_LIMIT,
    TOPIC_BY_KEY,
    Faq,
    Reply,
    Topic,
    answer_key,
    answer_text,
    may_send_out,
    nearest,
)
from app.guide_faq import suggestions as pick
from app.guide_faq import topics as all_topics
from app.services import ai
from tsunagu_rules.translation import load_dictionary

bp = Blueprint("guide", __name__)


@cache
def _taxwork() -> tuple[str, ...]:
    return load_dictionary().taxwork


def suggestions(screen: str | None, exclude: str | None = None) -> tuple[Faq, ...]:
    """テンプレートからも使う。知らない画面の名前は、基本の質問に落とす。"""
    return pick(screen if screen in SCRIPTS else None, exclude)


def topics() -> tuple[Topic, ...]:
    """テンプレートからも使う話題の一覧。"""
    return all_topics()


@bp.post("/guide/ask")
def ask() -> ResponseReturnValue:
    """案内役への質問。用意した答えを返し、質問は残さない。

    自分の言葉で聞くときは、選んだ話題の答えだけを探す。
    外の AI が有効のときだけ、ログインした人の、用意した答えに無い質問を、
    伏せてから外に聞く（税務の言葉がある質問は送らない。材料もその話題だけ）。
    残すのは `ai_runs` の1行だけ。
    """
    screen = request.form.get("screen", "")
    key = request.form.get("faq", "")
    topic = request.form.get("topic", "").strip()
    if topic not in TOPIC_BY_KEY:
        topic = ""
    question = request.form.get("question", "").strip()[:QUESTION_LIMIT]
    if key:
        reply = answer_key(key)
        asked = BY_KEY[key].question if key in BY_KEY else ""
    else:
        reply = answer_text(question, _taxwork(), topic or None)
        asked = question
        if (
            reply.kind == "none"
            and topic
            and current_user.is_authenticated
            and may_send_out(question, _taxwork())
        ):
            written = ai.answer_question(
                current_user.id,
                question,
                [(faq.question, faq.answer) for faq in nearest(question, topic=topic)],
            )
            if written:
                reply = Reply("auto", written)
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
        ask_topic=topic,
    )
