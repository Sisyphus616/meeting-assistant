import importlib.util
import json
import os
import platform
import shutil
from pathlib import Path

root = Path(__file__).resolve().parent.parent
result = {'python': platform.python_version(), 'platform': platform.platform(),
          'cpu_threads': os.cpu_count(), 'project': str(root),
          'disk_free_gib': round(shutil.disk_usage(root).free / 2**30, 1),
          'packages': {name: importlib.util.find_spec(name) is not None for name in
                       ['fastapi','uvicorn','torch','transformers','librosa']}}
if result['packages']['torch']:
    import torch
    result['torch'] = {'version': torch.__version__, 'rocm': torch.version.hip,
                       'gpu_available': torch.cuda.is_available()}
    if torch.cuda.is_available(): result['torch']['gpu_name'] = torch.cuda.get_device_name(0)
print(json.dumps(result, ensure_ascii=False, indent=2))
