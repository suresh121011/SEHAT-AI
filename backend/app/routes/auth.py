from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth import (
    Principal,
    Role,
    authenticate_demo_user,
    create_access_token,
    get_current_principal,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    role: Role


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user_id: str
    username: str
    role: Role


class MeResponse(BaseModel):
    user_id: str
    username: str
    role: Role


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest) -> LoginResponse:
    """Hackathon demo login: username + role → JWT. No passwords; demo accounts only."""
    principal = authenticate_demo_user(body.username, body.role)
    token, expires_in = create_access_token(principal.username, principal.role)
    return LoginResponse(
        access_token=token,
        expires_in=expires_in,
        user_id=principal.user_id,
        username=principal.username,
        role=principal.role,
    )


@router.get("/me", response_model=MeResponse)
async def me(principal: Principal = Depends(get_current_principal)) -> MeResponse:
    """Token introspection used by the frontend's server-side route guards."""
    return MeResponse(user_id=principal.user_id, username=principal.username, role=principal.role)
