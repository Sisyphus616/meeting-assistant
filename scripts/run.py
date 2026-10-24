"""Entry point used by both WSL and AUP. Configuration is read from project .env."""
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
os.chdir(root)
from dotenv import load_dotenv
load_dotenv(root / '.env', override=False)

import uvicorn

if __name__ == '__main__':
    uvicorn.run('app.main:app', host=os.getenv('HOST', '127.0.0.1'),
                port=int(os.getenv('PORT', '8765')), ws_max_size=700000)
