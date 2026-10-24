"""Download official public Qwen checkpoints to explicit, portable local folders."""
import argparse
from pathlib import Path
from huggingface_hub import snapshot_download

parser = argparse.ArgumentParser()
parser.add_argument('--directory', type=Path, default=Path(__file__).resolve().parent.parent / 'models')
parser.add_argument('--llm', default='Qwen/Qwen3-0.6B')
parser.add_argument('--asr', default='Qwen/Qwen3-ASR-0.6B-hf')
args = parser.parse_args()
for repo in [args.llm, args.asr]:
    target = args.directory.resolve() / repo.split('/')[-1]
    snapshot_download(repo, local_dir=target,
                      allow_patterns=['*.json','*.safetensors','*.txt','*.jinja','LICENSE','README.md'])
    print(f'{repo}: {target}')
