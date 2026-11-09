from agent.events import redact


def test_real_key_is_masked(monkeypatch):
    # Long enough to be masked, but not shaped like a provider key (A-19 scans for "sk-…").
    key = "test-key-0123456789abcdef"
    monkeypatch.setenv("DEEPCODE_API_KEY", key)

    assert redact({"note": f"auth {key} failed"}) == {"note": "auth *** failed"}


def test_dummy_local_key_does_not_corrupt_text(monkeypatch):
    # Local models (Ollama, LM Studio) accept any key; "local" is a typical one.
    monkeypatch.setenv("DEEPCODE_API_KEY", "local")

    assert redact("http://localhost:11434/v1 local-llama") == (
        "http://localhost:11434/v1 local-llama"
    )
