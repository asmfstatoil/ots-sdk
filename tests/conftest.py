from pathlib import Path
import sys

import pytest

repo_root = Path(__file__).resolve().parents[1]
src_path = repo_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))


@pytest.fixture
def documented_response():
    return {
        "data": {
            "items": [
                {
                    "id": "series-1",
                    "datapoints": [
                        {
                            "time": "2022-01-01T00:00:00.000Z",
                            "value": 1.0,
                            "status": 192,
                        }
                    ],
                }
            ]
        }
    }
