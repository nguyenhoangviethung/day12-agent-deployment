"""Mock LLM for local testing."""
import time
import random

MOCK_RESPONSES = {
    "default": [
        "This is a mock response from the AI agent.",
        "The agent is running and ready to answer questions.",
        "Your question was received by the deployed agent.",
    ],
    "docker": ["Containers package apps so they run anywhere: build once, run anywhere."],
    "deploy": ["Deployment moves code from your machine to a server for real users."],
    "health": ["The agent is healthy and operational."],
}


def ask(question: str, delay: float = 0.1) -> str:
    """Return a deterministic mock response with a small delay."""
    time.sleep(delay + random.uniform(0, 0.05))
    question_lower = question.lower()
    for keyword, responses in MOCK_RESPONSES.items():
        if keyword in question_lower:
            return random.choice(responses)
    return random.choice(MOCK_RESPONSES["default"])


def ask_stream(question: str):
    """Yield a mock streaming response token by token."""
    response = ask(question)
    for word in response.split():
        time.sleep(0.05)
        yield word + " "
