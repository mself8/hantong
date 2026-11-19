import os
import sys
import tempfile
from pathlib import Path

# 시험은 임시 SQLite에 쓰고, 실제 LLM을 부르지 않는다.
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["DEMO_CALLERS"] = "01012345678:R001"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
