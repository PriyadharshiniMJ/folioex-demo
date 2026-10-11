"""The return-guess pop-up shown after every upload: pick a range, then see how the money really did."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

import dash_bootstrap_components as dbc
from dash import Dash, Input, Output, State, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate

from folioex.analytics.quiz import Quiz, build_quiz, next_line, seed_for
from folioex.config import Settings
from folioex.dataset import Dataset
from folioex.domain.models import EXTERNAL_FLOW_TYPES
from folioex.errors import MessageCatalog, UserMessage, default_catalog
from folioex.formatting import percent
from folioex.ui.components.common import aria
from folioex.ui.safety import safe_callback
from folioex.ui.state import REGISTRY

# Used only if messages.toml is missing an entry, so the pop-up never shows a blank or a broken sentence.
_FALLBACK = {
    "quiz_title": "Quick guess!",
    "quiz_question": "Before you peek: how much do you think your money has grown?",
    "quiz_definition": "Growth = (value today − money you put in) ÷ money you put in",
    "quiz_right": "Spot on! Your money has grown {gain}.",
    "quiz_right_down": "Spot on! Your money is down {gain}.",
    "quiz_actual_up": "Your money actually grew {gain}.",
    "quiz_actual_down": "Your money is actually down {gain}.",
    "quiz_skip_note": "Pick a range, then lock it in. Or skip, nothing is lost.",
}
_FALLBACK_LINE = "Not quite! The market loves a plot twist."

_CONFETTI_JS = """
function(count, still) {
    if (!count) { return window.dash_clientside.no_update; }
    document.body.setAttribute('data-confetti', String(count));
    if (!still && window.confetti) {
        var fire = function(extra) {
            window.confetti(Object.assign({particleCount: 80, spread: 110, zIndex: 3000}, extra));
        };
        fire({particleCount: 140, spread: 80, origin: {y: 0.6}});
        window.setTimeout(function() {
            fire({origin: {x: 0.15, y: 0.7}});
            fire({origin: {x: 0.85, y: 0.7}});
        }, 250);
    }
    return count;
}
"""


@dataclass(frozen=True, slots=True)
class QuizResult:
    content: html.Div
    right: bool
    line: int | None  # which fun line was shown, so the next wrong answer picks a different one


def _say(catalog: MessageCatalog, key: str) -> str:
    return catalog.text(key) or _FALLBACK[key]


def quiz_for(data: Dataset, settings: Settings) -> Quiz | None:
    """The quiz for this data, or None when it is switched off or there is no percentage to guess."""
    gain = data.holdings.total_gain_pct
    if not settings.quiz.enabled or gain is None:
        return None
    txns = data.ingest.transactions
    deposits = sum(t.net for t in txns if t.type in EXTERNAL_FLOW_TYPES)
    seed = seed_for(len(txns), round(deposits), min(t.date for t in txns), max(t.date for t in txns))
    return build_quiz(gain, settings.quiz.bucket_width_pct, seed=seed)


def quiz_state(quiz: Quiz, gain: float | None) -> dict[str, Any]:
    """What the browser needs to hold between steps: the choices, the right one and the gain as shown."""
    return {
        "labels": [o.label for o in quiz.options],
        "correct": quiz.correct,
        "gain": percent(gain, decimals=1, sign=True),
        "up": quiz.shown >= 0,
    }


def option_list(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"label": label, "value": i} for i, label in enumerate(state["labels"])]


def result_view(
    state: dict[str, Any], choice: int, previous_line: int | None, catalog: MessageCatalog, rng: random.Random
) -> QuizResult:
    gain = str(state["gain"])
    up = bool(state["up"])
    right = choice == state["correct"]
    actual = _say(catalog, "quiz_actual_up" if up else "quiz_actual_down").format(gain=gain)
    if right:
        headline = _say(catalog, "quiz_right" if up else "quiz_right_down").format(gain=gain)
        body: list[Any] = [html.P("🎉", className="fx-quiz-emoji", **aria(hidden="true"))]
        body.append(html.P(headline, className="fx-quiz-headline"))
        return QuizResult(html.Div(body, className="fx-quiz-result fx-quiz-right"), True, None)
    lines = catalog.lines("quiz_wrong")
    picked = next_line(lines, previous_line, rng)
    line = lines[picked] if picked >= 0 else _FALLBACK_LINE
    body = [
        html.P(line, className="fx-quiz-headline"),
        html.P(actual, className="fx-quiz-actual"),
    ]
    return QuizResult(
        html.Div(body, className="fx-quiz-result fx-quiz-wrong"), False, picked if picked >= 0 else None
    )


def quiz_modal(catalog: MessageCatalog) -> dbc.Modal:
    return dbc.Modal(
        [
            dbc.ModalHeader(dbc.ModalTitle(_say(catalog, "quiz_title")), close_button=False),
            dbc.ModalBody(
                [
                    html.Div(
                        [
                            html.P(_say(catalog, "quiz_question"), className="fx-quiz-question"),
                            html.P(_say(catalog, "quiz_definition"), className="fx-muted fx-quiz-def"),
                            html.Div(
                                dbc.RadioItems(
                                    id="quiz-choice",
                                    options=[],
                                    value=None,
                                    className="fx-quiz-options",
                                    inputClassName="btn-check",
                                    labelClassName="fx-quiz-option",
                                    labelCheckedClassName="active",
                                ),
                                role="radiogroup",
                                **aria(label="Pick the range you think your growth falls in"),
                            ),
                            html.Div(
                                [
                                    dbc.Button(
                                        "Lock it in",
                                        id="quiz-lock",
                                        className="fx-btn-primary",
                                        disabled=True,
                                        n_clicks=0,
                                    ),
                                    html.Button("Skip ›", id="quiz-skip", className="fx-link", n_clicks=0),
                                ],
                                className="fx-quiz-actions",
                            ),
                        ],
                        id="quiz-question-view",
                    ),
                    html.Div(
                        [
                            html.Div(id="quiz-result", **aria(live="polite")),
                            dbc.Button(
                                "See my dashboard", id="quiz-done", className="fx-btn-primary", n_clicks=0
                            ),
                        ],
                        id="quiz-result-view",
                        className="fx-quiz-final",
                        hidden=True,
                    ),
                ]
            ),
        ],
        id="quiz-modal",
        is_open=False,
        centered=True,
        backdrop="static",
        keyboard=False,
        className="fx-quiz",
    )


def stores() -> list[Any]:
    """Browser-side memory the pop-up needs between steps (nothing is saved to disk)."""
    return [
        dcc.Store(id="quiz-state", storage_type="memory"),
        dcc.Store(id="quiz-last-line", storage_type="memory"),
        dcc.Store(id="quiz-confetti", storage_type="memory"),
        dcc.Store(id="quiz-sink", storage_type="memory"),
    ]


def _quiet(message: UserMessage) -> Any:
    raise PreventUpdate


def _closed(message: UserMessage) -> tuple[Any, ...]:
    # If anything goes wrong the pop-up simply stays out of the way.
    return False, no_update, [], None, False, True


def register(app: Dash, settings: Settings) -> None:
    rng = random.Random()

    @app.callback(
        Output("quiz-modal", "is_open"),
        Output("quiz-state", "data"),
        Output("quiz-choice", "options"),
        Output("quiz-choice", "value"),
        Output("quiz-question-view", "hidden"),
        Output("quiz-result-view", "hidden"),
        Input("dataset-id", "data"),
        prevent_initial_call=True,
    )
    @safe_callback(_closed)
    def open_after_upload(dataset_id: str | None) -> tuple[Any, ...]:
        data = REGISTRY.get(dataset_id)
        quiz = quiz_for(data, settings) if data is not None else None
        if data is None or quiz is None:
            return False, None, [], None, False, True
        state = quiz_state(quiz, data.holdings.total_gain_pct)
        return True, state, option_list(state), None, False, True

    app.clientside_callback(
        "function(value) { return value === null || value === undefined; }",
        Output("quiz-lock", "disabled"),
        Input("quiz-choice", "value"),
    )

    @app.callback(
        Output("quiz-question-view", "hidden", allow_duplicate=True),
        Output("quiz-result-view", "hidden", allow_duplicate=True),
        Output("quiz-result", "children"),
        Output("quiz-last-line", "data"),
        Output("quiz-confetti", "data"),
        Input("quiz-lock", "n_clicks"),
        State("quiz-choice", "value"),
        State("quiz-state", "data"),
        State("quiz-last-line", "data"),
        State("quiz-confetti", "data"),
        prevent_initial_call=True,
    )
    @safe_callback(_quiet)
    def lock_in(
        clicks: int | None,
        choice: int | None,
        state: dict[str, Any] | None,
        last: int | None,
        shots: int | None,
    ) -> tuple[Any, ...]:
        if not clicks or choice is None or not state:
            raise PreventUpdate
        result = result_view(state, int(choice), last, default_catalog(), rng)
        confetti = (shots or 0) + 1 if result.right else no_update
        return True, False, result.content, result.line if result.line is not None else last, confetti

    app.clientside_callback(
        _CONFETTI_JS,
        Output("quiz-sink", "data"),
        Input("quiz-confetti", "data"),
        State("motion", "data"),
    )

    @app.callback(
        Output("quiz-modal", "is_open", allow_duplicate=True),
        Output("tabs", "active_tab", allow_duplicate=True),
        Input("quiz-skip", "n_clicks"),
        Input("quiz-done", "n_clicks"),
        prevent_initial_call=True,
    )
    @safe_callback(_quiet)
    def close(skip: int | None, done: int | None) -> tuple[Any, ...]:
        if ctx.triggered_id == "quiz-done" and done:
            return False, "holdings"
        if ctx.triggered_id == "quiz-skip" and skip:
            return False, no_update
        raise PreventUpdate
