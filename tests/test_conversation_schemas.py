"""Stored-conversation request rules and titles (P2-14) - no database needed."""

import uuid

import pytest
from pydantic import ValidationError

from app.schemas import WorkspaceAskRequest
from core.conversations import TITLE_CHARS, title_from

HIST = [{"question": "What is RAG?", "answer": "Retrieval-augmented generation [1]."}]


def test_conversation_id_excludes_history_and_save():
    cid = uuid.uuid4()
    assert WorkspaceAskRequest(question="And its limits?", conversation_id=cid).conversation_id == cid
    with pytest.raises(ValidationError, match="not both"):
        WorkspaceAskRequest(question="And its limits?", conversation_id=cid, history=HIST)
    with pytest.raises(ValidationError, match="starts a new conversation"):
        WorkspaceAskRequest(question="And its limits?", conversation_id=cid, save=True)
    with pytest.raises(ValidationError):
        WorkspaceAskRequest(question="And its limits?", conversation_id="not-a-uuid")


def test_save_may_start_from_client_history():
    r = WorkspaceAskRequest(question="And its limits?", history=HIST, save=True)
    assert r.save and len(r.history) == 1


def test_title_is_the_first_question_clipped():
    assert title_from("  What   is RAG? ") == "What is RAG?"
    long = title_from("why " * 50)
    assert len(long) == TITLE_CHARS and long.endswith("…")
