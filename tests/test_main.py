"""Exercise the retrieval CLI without loading models or writing an index."""

from unittest.mock import Mock, call

import pytest

from src import main as cli


@pytest.fixture
def chatbot(monkeypatch):
    bot = Mock(
        spec=[
            "chat",
            "clear_history",
            "process_documents",
            "get_knowledge_base_questions",
        ]
    )
    factory = Mock(return_value=bot)
    factory.get_available_providers.return_value = ["openai", "anthropic"]
    monkeypatch.setattr(cli, "RAGAdvisingChatbot", factory)
    return bot, factory


def enter_commands(monkeypatch, commands):
    inputs = iter(commands)

    def read_input(prompt):
        # Exhaustion fails the test if the CLI fails to honor exit.
        try:
            return next(inputs)
        except StopIteration:
            pytest.fail("CLI requested more input after the scripted session")

    monkeypatch.setattr("builtins.input", read_input)


def test_missing_providers_exits_before_constructing_chatbot(chatbot, capsys):
    bot, factory = chatbot
    factory.get_available_providers.return_value = []

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 1
    factory.assert_not_called()
    assert bot.mock_calls == []
    assert "ERROR: No LLM providers configured." in capsys.readouterr().out


def test_initialization_failure_exits_before_reading_input(
    chatbot, monkeypatch, capsys
):
    _, factory = chatbot
    factory.side_effect = RuntimeError("index unavailable")
    read_input = Mock(side_effect=AssertionError("must not start a session"))
    monkeypatch.setattr("builtins.input", read_input)

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 1
    factory.assert_called_once_with()
    read_input.assert_not_called()
    assert (
        "ERROR: Failed to initialize chatbot: index unavailable"
        in capsys.readouterr().out
    )


def test_commands_dispatch_in_order_without_sending_them_to_chat(
    chatbot, monkeypatch, capsys
):
    bot, factory = chatbot
    bot.get_knowledge_base_questions.return_value = [
        "Which courses are required?",
        "When can I register?",
    ]
    actions = Mock()
    actions.attach_mock(bot, "bot")
    setup = Mock()
    actions.attach_mock(setup, "setup")
    monkeypatch.setattr(cli, "setup_sample_docs", setup)
    enter_commands(
        monkeypatch, ["   ", " HELP ", "Docs", "QUESTIONS", " Clear ", "EXIT"]
    )

    cli.main()

    factory.assert_called_once_with()
    assert actions.mock_calls == [
        call.setup(),
        call.bot.process_documents(),
        call.bot.get_knowledge_base_questions(),
        call.bot.clear_history(),
    ]
    output = capsys.readouterr().out
    assert "Available LLM Providers: openai, anthropic" in output
    assert "'docs'          - Load and process advising documents" in output
    assert "Processing documents..." in output
    assert "Document processing finished." in output
    assert "Available hardcoded questions (2):" in output
    assert "  - Which courses are required?" in output
    assert "  - When can I register?" in output
    assert "Conversation history cleared." in output
    assert output.rstrip().endswith("Goodbye!")


@pytest.mark.parametrize(
    ("source", "source_label"),
    [
        ("knowledge_base", "[Source: Knowledge Base]"),
        ("rag", "[Source: Document Retrieval + LLM] (2 documents)"),
        ("llm", "[Source: LLM]"),
    ],
)
def test_answers_show_their_actual_source(
    chatbot, monkeypatch, capsys, source, source_label
):
    bot, _ = chatbot
    bot.chat.return_value = {
        "response": "Registration opens next term.",
        "source": source,
        "retrieved_docs": [{"content": "registration"}, {"content": "dates"}],
    }
    enter_commands(monkeypatch, ["  When can I register?  ", "exit"])

    cli.main()

    bot.chat.assert_called_once_with("When can I register?")
    output = capsys.readouterr().out
    assert "Assistant: Registration opens next term." in output
    assert source_label in output
    assert output.count("[Source:") == 1


@pytest.mark.parametrize("operation", ["chat", "process_documents"])
def test_operation_failure_reports_error_and_accepts_the_next_question(
    chatbot, monkeypatch, capsys, operation
):
    bot, _ = chatbot
    answer = {"response": "Try the course catalog.", "source": "knowledge_base"}
    if operation == "chat":
        bot.chat.side_effect = [RuntimeError("temporary failure"), answer]
        commands = ["First question", "Next question", "exit"]
        expected_chats = [call("First question"), call("Next question")]
    else:
        monkeypatch.setattr(cli, "setup_sample_docs", Mock())
        bot.process_documents.side_effect = RuntimeError("temporary failure")
        bot.chat.return_value = answer
        commands = ["docs", "Next question", "exit"]
        expected_chats = [call("Next question")]
    enter_commands(monkeypatch, commands)

    cli.main()

    assert bot.chat.call_args_list == expected_chats
    output = capsys.readouterr().out
    assert "ERROR: temporary failure" in output
    assert "Assistant: Try the course catalog." in output
    assert "Document processing finished." not in output
    assert output.rstrip().endswith("Goodbye!")


def test_keyboard_interrupt_ends_session_without_chatting(chatbot, monkeypatch, capsys):
    bot, _ = chatbot
    monkeypatch.setattr("builtins.input", Mock(side_effect=KeyboardInterrupt))

    cli.main()

    bot.chat.assert_not_called()
    output = capsys.readouterr().out
    assert output.rstrip().endswith("Goodbye!")
    assert "ERROR:" not in output
