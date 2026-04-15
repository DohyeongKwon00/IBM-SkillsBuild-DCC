"""Shared test fixtures for CommCopilot tests."""

import pytest
from commcopilot.session import SessionState


@pytest.fixture
def base_state() -> SessionState:
    """A minimal SessionState for testing."""
    return SessionState(
        session_id="test-session-123",
        transcript_buffer=[
            "The deadline is Friday.",
            "Submit via the course portal.",
            "Um, what about the rubric?",
        ],
        phrases_used=[],
        hesitation_count=0,
        awaiting_phrases=False,
    )


@pytest.fixture
def mock_orchestrate_response():
    """Factory for mocking httpx responses from Orchestrate."""
    def _make(json_body: dict, status_code: int = 200):
        class MockResponse:
            def __init__(self):
                self.status_code = status_code

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise Exception(f"HTTP {self.status_code}")

            def json(self):
                return json_body

        return MockResponse()
    return _make
