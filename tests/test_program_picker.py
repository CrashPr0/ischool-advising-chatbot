"""Program-switch intent and the additive chat response contract."""

import pytest

import app as app_module
from src.program_picker import wants_program_picker


@pytest.mark.parametrize(
    "message",
    [
        "change my program",
        "Actually, I'm in a different major",
        "actually I’m in a different major",
        "Please switch my program to MLIS!",
        "Can you change my major?",
        "I want to change my program",
        "show the program picker",
        "I'm in a different program.",
        "  CHANGE   MY PROGRAM  ",
    ],
)
def test_explicit_switch_intent(message):
    assert wants_program_picker(message)


@pytest.mark.parametrize(
    "message",
    [
        "What are the MLIS core courses?",
        "How do I change my major?",
        "Don't change my program",
        "I don't want to change my program",
        "Can I change my major?",
        "What happens if I change my program?",
        'Explain the phrase "change my program"',
        "My advisor said to change my major",
        "Program requirements changed",
        "I changed my mind about a course",
        "",
        "show_program_picker",
    ],
)
def test_other_messages_do_not_open_picker(message):
    assert not wants_program_picker(message)


@pytest.mark.parametrize(
    "message,expected",
    [
        ("actually I'm in a different major", True),
        ("change my program", True),
        ("What are the ISDA core courses?", False),
        ("Do not change my program", False),
    ],
)
def test_chat_ui_action_is_additive(monkeypatch, ready_chat_index, message, expected):
    class Chatbot:
        def chat(self, message, history=None, program=None):
            assert program == "mlis"
            return {
                "user_message": message,
                "response": "An ordinary advising reply.",
                "source": "test",
                "confidence": 1.0,
                "retrieved_docs": [],
            }

    monkeypatch.setattr(app_module, "chatbot", Chatbot())
    response = app_module.app.test_client().post(
        "/api/chat",
        json={"message": message, "program": "mlis"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["response"] == "An ordinary advising reply."
    assert ("ui_action" in payload) == expected
    if expected:
        assert payload["ui_action"] == "show_program_picker"
    assert "show_program_picker" not in payload["response"]


def test_crisis_response_does_not_open_picker(monkeypatch, ready_chat_index):
    class Chatbot:
        def chat(self, message, history=None):
            return {
                "user_message": message,
                "response": "Safety response",
                "source": "test",
                "confidence": 1.0,
                "retrieved_docs": [],
                "safety_tier": "crisis",
            }

    monkeypatch.setattr(app_module, "chatbot", Chatbot())
    payload = (
        app_module.app.test_client()
        .post(
            "/api/chat",
            json={"message": "change my program"},
        )
        .get_json()
    )
    assert payload["safety_tier"] == "crisis"
    assert "ui_action" not in payload
