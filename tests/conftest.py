import ast
from pathlib import Path
import sys

import pytest

repo_root = Path(__file__).resolve().parents[1]
src_path = repo_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))


@pytest.fixture
def documented_response():
    readme = repo_root / "README.md"
    sample = next(
        line for line in readme.read_text().splitlines() if line.startswith(">> ")
    )
    return {"data": ast.literal_eval(sample[3:])}
