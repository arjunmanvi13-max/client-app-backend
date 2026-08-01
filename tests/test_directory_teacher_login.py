"""Directory teacher creation with optional system login."""
import os

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017/test")
os.environ.setdefault("DB_NAME", "test")
os.environ.setdefault("JWT_SECRET", "test-secret")

import pytest
from pydantic import ValidationError

from core import DirectoryTeacherCreate


def _base_payload(**overrides):
    data = {
        "name": "Test Teacher",
        "date_of_birth": "1990-08-15",
        "address": "Sample address",
        "mobile": "9876543210",
        "personal_email": "personal@example.com",
        "aadhaar_number": "ABCD1234EFGH",
        "qualification": "B.Ed",
        "last_job": "Sample School",
        "guardian_name": "Guardian Name",
        "guardian_mobile": "9876501234",
        "reference_name": "Reference Name",
        "reference_mobile": "9876512345",
    }
    data.update(overrides)
    return data


def test_directory_teacher_without_login():
    doc = DirectoryTeacherCreate(**_base_payload())
    assert doc.enable_login is False
    assert doc.login_email is None


def test_directory_teacher_login_requires_credentials():
    with pytest.raises(ValidationError):
        DirectoryTeacherCreate(**_base_payload(enable_login=True))


def test_directory_teacher_login_accepts_credentials():
    doc = DirectoryTeacherCreate(**_base_payload(
        enable_login=True,
        login_email="teacher@prarambhika.com",
        password="Secret1",
    ))
    assert doc.enable_login is True
    assert doc.login_email == "teacher@prarambhika.com"
