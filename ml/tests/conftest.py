import pytest


@pytest.fixture(autouse=True)
def no_default_ppe_weights(monkeypatch):
    """Тесты не берут веса СИЗ из домашней папки разработчика."""
    from worker import main

    monkeypatch.setattr(main, "default_ppe_weights", lambda: None)
