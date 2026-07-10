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
from fastapi import APIRouter, Depends, HTTPException
from core import (
    db, LoginIn, ChangePasswordIn,
    create_token, verify_password, hash_password, public_user, get_current_user, now_utc,
    validate_domain_email,
)

router = APIRouter(prefix="/auth", tags=["auth"])


# ----------------- Email + password login -----------------
@router.post("/login")
async def login(payload: LoginIn):
    email = validate_domain_email(payload.email)
    user = await db.users.find_one({"email": email}, {"_id": 0})
    if not user or not user.get("password_hash") or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(401, "Invalid email or password")
    if user.get("status") == "deactivated":
        raise HTTPException(403, "Account deactivated. Contact your administrator.")
    token = create_token(user["id"], user.get("email") or "", user["role"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": public_user(user),
        "must_change_password": bool(user.get("must_change_password", False)),
    }


# ----------------- Change password (authed) -----------------
@router.post("/password/change")
async def change_password(payload: ChangePasswordIn, user: dict = Depends(get_current_user)):
    if not user.get("password_hash") or not verify_password(payload.current_password, user["password_hash"]):
        raise HTTPException(401, "Current password incorrect")
    if len(payload.new_password) < 6:
        raise HTTPException(400, "New password must be at least 6 characters")
    await db.users.update_one({"id": user["id"]}, {"$set": {
        "password_hash": hash_password(payload.new_password),
        "is_password_set": True,
        "must_change_password": False,
        "password_set_at": now_utc().isoformat(),
    }})
    return {"ok": True}


# ----------------- Me / Logout -----------------
@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return public_user(user)


@router.post("/logout")
async def logout(_user: dict = Depends(get_current_user)):
    return {"ok": True}
