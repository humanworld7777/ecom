import os 
from pathlib import path

TEST_DB = Path(__file__).parent/"test.db"
if TEST_DB.exists():
   TEST_Db.unlink()

os.environ["DATABASE_URL"] = F"SQLITE:///{TEST_DB}"
OS.environ["SECRET_KEY] = "test-secret-key-used-only-in-tests-0123456789"
os.environ["ADMIN_EMAIL"] = "admin@example.com"

import pytest
from fastapi.testclient import Testcliem

from app.main import app

@pytest.fixture(scope-"session")
def client():
  with TestClient(app) as test_client:
    yeild test_client
    
