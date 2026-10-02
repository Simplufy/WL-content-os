import os
import tempfile
from pathlib import Path

import pytest

_tmp = Path(tempfile.mkdtemp(prefix="studio-test-"))
os.environ["STUDIO_DATA_DIR"] = str(_tmp / "data")
os.environ["STUDIO_MEDIA_DIR"] = str(_tmp / "media")


@pytest.fixture(autouse=True)
def fresh_db():
    from studio import config, db
    db.reset_for_tests()
    config.DB_PATH.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(config.DB_PATH) + suffix).unlink(missing_ok=True)
    db.conn()
    yield
    db.reset_for_tests()
