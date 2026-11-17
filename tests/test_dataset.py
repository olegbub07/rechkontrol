from app.dataset import status


def test_dataset_status_idle_or_ready():
    info = status()
    assert "ready" in info
    assert info["source"] == "PolyAI/minds14"
    assert info["config"] == "ru-RU"
