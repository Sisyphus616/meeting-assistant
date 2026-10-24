from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env', override=False)


def local_path(value: str) -> Path:
    path = Path(value).expanduser()
    return (ROOT / path).resolve() if not path.is_absolute() else path


@dataclass
class Settings:
    provider: str = 'demo'
    data_dir: Path = ROOT / 'data'
    device: str = 'cpu'
    asr_model: str = 'Qwen/Qwen3-ASR-0.6B-hf'
    llm_model: str = 'Qwen/Qwen3-0.6B'
    dtype: str = 'auto'
    local_only: bool = False
    llm_backend: str = 'transformers'
    llm_base_url: str = 'http://127.0.0.1:8080/v1'
    llm_api_key: str = ''
    threads: int = 6
    summary_every: int = 4
    max_pending: int = 24
    root_path: str = ''
    summary_style: str = 'extractive'
    translation_backend: str = 'llama_cpp'
    translation_model: str = 'tencent/Hy-MT2-1.8B'
    translation_gguf: Path = ROOT / 'models/Hy-MT2-1.8B-GGUF/Hy-MT2-1.8B-Q4_K_M.gguf'
    translation_binary: Path = ROOT / 'tools/llama.cpp/llama-server'
    translation_base_url: str = 'http://127.0.0.1:8081/v1'
    translation_port: int = 8081
    translation_api_key: str = ''

    @classmethod
    def from_env(cls):
        # Set before importing Hugging Face libraries.
        os.environ['HF_HOME'] = str(local_path(os.getenv('HF_HOME', './models')))
        return cls(
            provider=os.getenv('PROVIDER', 'demo'),
            data_dir=local_path(os.getenv('DATA_DIR', './data')),
            device=os.getenv('DEVICE', 'cpu'),
            asr_model=os.getenv('ASR_MODEL', cls.asr_model),
            llm_model=os.getenv('LLM_MODEL', cls.llm_model),
            dtype=os.getenv('MODEL_DTYPE', 'auto'),
            local_only=os.getenv('LOCAL_FILES_ONLY', 'false').lower() == 'true',
            llm_backend=os.getenv('LLM_BACKEND', 'transformers'),
            llm_base_url=os.getenv('LLM_BASE_URL', cls.llm_base_url).rstrip('/'),
            llm_api_key=os.getenv('LLM_API_KEY', ''),
            threads=max(1, int(os.getenv('TORCH_THREADS', '6'))),
            summary_every=max(1, int(os.getenv('SUMMARY_EVERY', '4'))),
            max_pending=max(1, int(os.getenv('MAX_PENDING', '24'))),
            root_path=os.getenv('ROOT_PATH', '').rstrip('/'),
            summary_style=os.getenv('SUMMARY_STYLE', 'extractive'),
            translation_backend=os.getenv('TRANSLATION_BACKEND', 'llama_cpp'),
            translation_model=os.getenv('TRANSLATION_MODEL', cls.translation_model),
            translation_gguf=local_path(os.getenv('TRANSLATION_GGUF', str(cls.translation_gguf))),
            translation_binary=local_path(os.getenv('TRANSLATION_BINARY', str(cls.translation_binary))),
            translation_base_url=os.getenv('TRANSLATION_BASE_URL', cls.translation_base_url).rstrip('/'),
            translation_port=int(os.getenv('TRANSLATION_PORT', '8081')),
            translation_api_key=os.getenv('TRANSLATION_API_KEY', ''),
        )
