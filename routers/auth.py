"""Authentication routes — email + password (domain-restricted).

All users sign in with an @prarambhika.com email. Accounts and initial
passwords are assigned centrally by the Super Admin. Users whose password was
admin-assigned carry must_change_password=True and are prompted to set their
own password after first successful login.

Endpoints:
- POST /auth/login             — email + password (domain-restricted).
- POST /auth/password/change   — change own password (authed, clears must_change_password).
- GET  /auth/me                — current user.
- POST /auth/logout            — no-op (JWT is stateless).
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from core import (
    db, LoginIn, ChangePasswordIn, DUMMY_PASSWORD_HASH,
    create_token, verify_password_async, hash_password_async, public_user, get_current_user, now_utc,
    validate_domain_email, MIN_PASSWORD_LENGTH, validate_password_strength,
)
from login_throttle import check_login_allowed, record_login_failure, reset_login_failures

router = APIRouter(prefix="/auth", tags=["auth"])


# ----------------- Email + password login -----------------
@router.post("/login")
async def login(payload: LoginIn, request: Request):
    email = validate_domain_email(payload.email)
    client_ip = (request.client.host if request.client else "") or "unknown"
    check_login_allowed(email, client_ip)

    user = await db.users.find_one({"email": email}, {"_id": 0})
    stored_hash = (user or {}).get("password_hash") or DUMMY_PASSWORD_HASH
    password_ok = await verify_password_async(payload.password, stored_hash)
    if not user or not user.get("password_hash") or not password_ok:
        record_login_failure(email, client_ip)
        raise HTTPException(401, "Invalid email or password")
    reset_login_failures(email, client_ip)
    if user.get("status") == "deactivated":
        raise HTTPException(403, "Account deactivated. Contact your administrator.")
    if user.get("requires_user_type_review"):
        raise HTTPException(
            403,
            "Your account requires an approved user type assignment. Please contact the Super Admin.",
        )
    token = create_token(user["id"], user.get("email") or "", user["role"], user.get("password_set_at"))
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": public_user(user),
        "must_change_password": bool(user.get("must_change_password", False)),
    }


# ----------------- Change password (authed) -----------------
@router.post("/password/change")
async def change_password(payload: ChangePasswordIn, user: dict = Depends(get_current_user)):
    current_ok = await verify_password_async(
        payload.current_password, user.get("password_hash") or DUMMY_PASSWORD_HASH
    )
    if not user.get("password_hash") or not current_ok:
        raise HTTPException(401, "Current password incorrect")
    validate_password_strength(payload.new_password)
    password_set_at = now_utc().isoformat()
    await db.users.update_one({"id": user["id"]}, {"$set": {
        "password_hash": await hash_password_async(payload.new_password),
        "is_password_set": True,
        "must_change_password": False,
        "password_set_at": password_set_at,
    }})
    token = create_token(user["id"], user.get("email") or "", user["role"], password_set_at)
    return {"ok": True, "access_token": token, "token_type": "bearer"}


# ----------------- Me / Logout -----------------
@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return public_user(user)


@router.post("/logout")
async def logout(_user: dict = Depends(get_current_user)):
    return {"ok": True}
