import shutil
import sys
import uuid
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def workspace_tmp_path() -> Path:
    base_dir = PROJECT_ROOT / ".tmp_testdata"
    base_dir.mkdir(exist_ok=True)
    tmp_dir = base_dir / f"tmp_{uuid.uuid4().hex}"
    tmp_dir.mkdir()
    try:
        yield tmp_dir
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
