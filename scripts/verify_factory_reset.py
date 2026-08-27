"""Exercise a REAL factory reset against a throwaway database.

Never point this at a database you care about. It expects a backend started with
a scratch DB_NAME, wipes it, and asserts what survived.

    DB_NAME=pws_alpha_resettest SEED_DEMO_DATA=1 \
        .venv/Scripts/python.exe -m uvicorn server:app --port 8001
    EXPO_PUBLIC_BACKEND_URL=http://127.0.0.1:8001 \
        .venv/Scripts/python.exe scripts/verify_factory_reset.py
"""
import os
import sys

import requests

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or "http://127.0.0.1:8001").rstrip("/") + "/api"
EMAIL, PASSWORD = "superadmin@prarambhika.com", "Super@123"
PHRASE = "DELETE ALL DATA"

if "8000" in BASE:
    sys.exit("Refusing to run against :8000 — that is the working dev backend.")


def hdr():
    r = requests.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}", "Content-Type": "application/json"}


def post(headers, dry_run):
    return requests.post(
        f"{BASE}/admin/factory-reset",
        headers=headers,
        params={"dry_run": str(dry_run).lower()},
        json={"password": PASSWORD, "confirmation": PHRASE, "reason": "scripted verification"},
        timeout=120,
    )


def main():
    headers = hdr()

    preview = post(headers, True).json()
    print(f"preview: {preview['total_documents']} documents across {len(preview['counts'])} collections")
    assert preview["total_documents"] > 0, "scratch DB looks empty — seed it first"

    result = post(headers, False)
    assert result.status_code == 200, result.text
    body = result.json()
    print(f"reset:   status={body['status']} deleted={body['total_deleted']} "
          f"super_admins_kept={body['super_admins_kept']}")
    assert not body["failures"], body["failures"]
    assert body["super_admins_kept"] >= 1

    again = hdr()
    print("re-login after reset: OK")

    after = post(again, True).json()
    print(f"post-reset preview: {after['total_documents']} documents remain")
    assert after["total_documents"] == 0, after["counts"]

    people = requests.get(f"{BASE}/people?kind=student", headers=again, timeout=30).json()
    people = people if isinstance(people, list) else people.get("data", [])
    assert people == [], people
    print("people cleared: OK")

    history = requests.get(f"{BASE}/admin/factory-reset/history", headers=again, timeout=30).json()
    assert history["resets"], "reset was not recorded in the audit trail"
    print(f"audit trail: {len(history['resets'])} entry(s), latest by {history['resets'][0]['actor_email']}")

    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()
