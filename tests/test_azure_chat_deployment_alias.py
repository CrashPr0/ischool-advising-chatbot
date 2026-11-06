from config import resolve_azure_chat_deployment as r


def test_chat_deployment_preferred():
    assert r({"AZURE_OPENAI_CHAT_DEPLOYMENT": "new", "AZURE_OPENAI_DEPLOYMENT": "old"}) == "new"


def test_legacy_alias():
    assert r({"AZURE_OPENAI_DEPLOYMENT": "old"}) == "old"
    assert r({"AZURE_OPENAI_CHAT_DEPLOYMENT": "  ", "AZURE_OPENAI_DEPLOYMENT": "old"}) == "old"


def test_no_default_deployment():
    assert r({}) is None
