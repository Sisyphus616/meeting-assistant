"""Browser-test fixture only: exercises microphone transport without loading a model."""
from app.config import Settings
from app.main import create_app
from tests.test_app import FakeQwen

app = create_app(Settings.from_env(), FakeQwen())
