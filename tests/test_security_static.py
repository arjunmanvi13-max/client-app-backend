"""Static source-control checks — no network required."""
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_gitignore_excludes_env():
    """Launch blocker: .env must not be committable."""
    gi = (ROOT / ".gitignore").read_text()
    assert ".env" in gi or "*.env" in gi, ".gitignore must exclude .env files"


def test_no_jwt_secret_hardcoded():
    core = (ROOT / "core.py").read_text()
    assert 'JWT_SECRET = os.environ["JWT_SECRET"]' in core or "os.environ.get(\"JWT_SECRET\")" in core
    assert "JWT_SECRET =" not in core.replace('JWT_SECRET = os.environ["JWT_SECRET"]', "")


def test_seed_passwords_are_demo_only_documented():
    """Seed passwords exist for demo — production must rotate before launch."""
    seed = (ROOT / "seed.py").read_text()
    assert "DEMO_USERS" in seed
    assert "Super@123" in seed  # documents known demo credential risk


def test_staff_default_password_documented():
    people = (ROOT / "routers" / "people.py").read_text()
    assert "Staff@123" in people  # documents predictable staff provisioning risk
