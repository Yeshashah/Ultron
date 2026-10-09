import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import Settings  # noqa: E402


@pytest.fixture
def settings(tmp_path):
    """Settings with no API key and all files inside a temporary directory."""
    return Settings(openai_api_key="", data_dir=tmp_path / "data", log_dir=tmp_path / "logs")
