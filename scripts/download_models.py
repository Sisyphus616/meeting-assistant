"""Download public ASR, minutes and optional translation checkpoints."""
import argparse
from pathlib import Path
from huggingface_hub import snapshot_download

parser = argparse.ArgumentParser()
parser.add_argument('--directory', type=Path, default=Path(__file__).resolve().parent.parent / 'models')
parser.add_argument('--llm', default='Qwen/Qwen3-0.6B')
parser.add_argument('--asr', default='Qwen/Qwen3-ASR-0.6B-hf')
parser.add_argument('--translation', help='Optional dedicated translation repository, e.g. tencent/Hy-MT2-1.8B')
args = parser.parse_args()
repos = [args.asr, args.llm] + ([args.translation] if args.translation else [])
for repo in dict.fromkeys(repos):
    target = args.directory.resolve() / repo.split('/')[-1]
    snapshot_download(repo, local_dir=target,
                      allow_patterns=['*.json','*.safetensors','*.txt','*.jinja','*.model','LICENSE','README.md'])
    print(f'{repo}: {target}')
