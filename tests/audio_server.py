"""Browser-test fixture only: exercises microphone transport without loading a model."""
from app.config import Settings
from app.main import create_app
from tests.test_app import FakeQwen

class BrowserASR(FakeQwen):
    def transcribe(self, path, source, glossary):
        super().transcribe(path, source, glossary)
        return '第一句话已经识别。第二句话也应该单独显示！'


app = create_app(Settings.from_env(), BrowserASR())
