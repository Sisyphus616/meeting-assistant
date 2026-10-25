"""Isolated real-model evaluation server. The legacy splitter is experiment-only."""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def add_observer(app):
    from fastapi import Request

    @app.get('/__evaluation/status/{mid}')
    async def evaluation_status(mid: str, request: Request):
        runtime = request.app.state.runtime
        return {'pending': len(runtime.pending[mid]), 'summary_running': mid in runtime.summary_running}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8870)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--splitter', choices=('pause', 'fixed10'), default='pause')
    parser.add_argument('--cpu', action='store_true')
    args = parser.parse_args()
    os.environ.update(PROVIDER='qwen', DATA_DIR=str(args.data_dir.resolve()), ROOT_PATH='')
    if args.cpu:
        os.environ.update(DEVICE='cpu', MODEL_DTYPE='bfloat16',
                          ASR_MODEL=str(ROOT / 'models/Qwen3-ASR-0.6B-hf'),
                          LLM_MODEL=str(ROOT / 'models/Qwen3-0.6B'))
    from app.config import Settings
    settings = Settings.from_env()
    if os.path.isdir('/home/jovyan'):
        for path in (settings.data_dir, Path(settings.asr_model), Path(settings.llm_model),
                     Path(settings.translation_model)):
            if path.is_absolute() and not path.resolve().is_relative_to(Path('/home/jovyan')):
                raise SystemExit(f'AUP experiment paths must be in /home/jovyan: {path}')
    if settings.device.startswith('cuda'):
        # The parent enforces a timeout before the server is started.
        import torch
        assert torch.cuda.is_available(), 'GPU unavailable'
    import app.main as application
    add_observer(application.app)
    if args.splitter == 'fixed10':
        from app.audio import AudioSpan, SAMPLE_RATE

        def fixed10(pcm):
            count = len(pcm) // 2
            return [AudioSpan(start, min(start + SAMPLE_RATE * 10, count), 'fixed10')
                    for start in range(0, count, SAMPLE_RATE * 10)]

        application.split_upload = fixed10
    import uvicorn
    uvicorn.run(application.app, host='127.0.0.1', port=args.port, ws_max_size=700000)


if __name__ == '__main__':
    main()
