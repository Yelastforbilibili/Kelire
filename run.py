"""启动入口：双击 run.py 或 python run.py。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ttdm.app import main

if __name__ == "__main__":
    sys.exit(main())
