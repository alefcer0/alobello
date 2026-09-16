import base64
import hashlib
import hmac
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.db.connection import get_connection
from app.models.auth import AuthenticatedUser

logger = logging.getLogger(__name__)

_auth_schema_ready = False
_password_iterations = 390000
_bearer = HTTPBearer(auto_error=False)


def _normalize_username(username: str) -> str:
    return re.sub(r"\s+", " ", username.strip().lower())


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _derive_password_hash(password: str, salt_b64: str, iterations: int) -> str:
    salt = base64.b64decode(salt_b64.encode("ascii"))
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return base64.b64encode(digest).decode("ascii")


def _build_password_material(password: str, iterations: int = _password_iterations) -> tuple[str, str, int]:
    salt = os.urandom(16)
    salt_b64 = base64.b64encode(salt).decode("ascii")
    password_hash = _derive_password_hash(password, salt_b64, iterations)
    return salt_b64, password_hash, iterations


def _get_required_secret(name: str, value: str) -> str:
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _build_token_payload(user: AuthenticatedUser, expires_at: datetime) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "sub": str(user.user_id),
        "username": user.username,
        "is_superuser": user.is_superuser,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "typ": "access",
    }


def _create_hs256_signature(message: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).digest()
    return _b64url_encode(digest)


def _create_jwt(payload: dict, secret: str, algorithm: str) -> str:
    if algorithm != "HS256":
        raise RuntimeError(f"Unsupported JWT_ALGORITHM: {algorithm}")

    header = {"alg": algorithm, "typ": "JWT"}
    encoded_header = _b64url_encode(
        json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    encoded_payload = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    signature = _create_hs256_signature(signing_input, secret)
    return f"{encoded_header}.{encoded_payload}.{signature}"


def _decode_jwt(token: str, secret: str, algorithm: str) -> dict:
    if algorithm != "HS256":
        raise ValueError(f"Unsupported JWT_ALGORITHM: {algorithm}")

    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid token format")

    encoded_header, encoded_payload, encoded_signature = parts
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    expected_signature = _create_hs256_signature(signing_input, secret)
    if not hmac.compare_digest(expected_signature, encoded_signature):
        raise ValueError("Invalid token signature")

    try:
        header = json.loads(_b64url_decode(encoded_header).decode("utf-8"))
        payload = json.loads(_b64url_decode(encoded_payload).decode("utf-8"))
    except Exception as exc:  # pragma: no cover - defensive parse guard
        raise ValueError("Invalid token encoding") from exc

    if header.get("alg") != algorithm:
        raise ValueError("Invalid token algorithm")

    exp = payload.get("exp")
    if not isinstance(exp, int):
        raise ValueError("Invalid token expiration")
    if exp <= int(datetime.now(timezone.utc).timestamp()):
        raise ValueError("Token expired")

    return payload


async def ensure_auth_schema_and_superuser() -> None:
    global _auth_schema_ready

    if _auth_schema_ready:
        return

    username = _normalize_username(settings.superuser_username)
    password = settings.superuser_password.strip()

    if not username:
        raise RuntimeError("Missing required environment variable: SUPERUSER_USERNAME")
    if not password:
        raise RuntimeError("Missing required environment variable: SUPERUSER_PASSWORD")
    if len(password) < 8:
        raise RuntimeError("SUPERUSER_PASSWORD must be at least 8 characters")

    _get_required_secret("JWT_SECRET_KEY", settings.jwt_secret_key)

    async with get_connection() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_users (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                username VARCHAR(120) NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                password_iterations INTEGER NOT NULL,
                is_superuser BOOLEAN NOT NULL DEFAULT FALSE,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
            """
        )

        salt_b64, password_hash, iterations = _build_password_material(password)

        await conn.execute(
            """
            INSERT INTO auth_users (
                username,
                password_hash,
                password_salt,
                password_iterations,
                is_superuser,
                is_active,
                created_at,
                updated_at
            )
            VALUES ($1, $2, $3, $4, TRUE, TRUE, NOW(), NOW())
            ON CONFLICT (username)
            DO UPDATE
            SET password_hash = EXCLUDED.password_hash,
                password_salt = EXCLUDED.password_salt,
                password_iterations = EXCLUDED.password_iterations,
                is_superuser = TRUE,
                is_active = TRUE,
                updated_at = NOW()
            """,
            username,
            password_hash,
            salt_b64,
            iterations,
        )

    _auth_schema_ready = True
    logger.info("Auth schema ready and superuser ensured: %s", username)


async def authenticate_user(username: str, password: str) -> Optional[AuthenticatedUser]:
    normalized_username = _normalize_username(username)

    async with get_connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, username, password_hash, password_salt, password_iterations, is_superuser, is_active
            FROM auth_users
            WHERE username = $1
            LIMIT 1
            """,
            normalized_username,
        )

    if not row or not row["is_active"]:
        return None

    candidate_hash = _derive_password_hash(password, row["password_salt"], row["password_iterations"])
    if not hmac.compare_digest(candidate_hash, row["password_hash"]):
        return None

    return AuthenticatedUser(
        user_id=row["id"],
        username=row["username"],
        is_superuser=row["is_superuser"],
    )


def create_access_token(user: AuthenticatedUser) -> tuple[str, datetime, int]:
    secret = _get_required_secret("JWT_SECRET_KEY", settings.jwt_secret_key)
    expires_in_seconds = max(60, settings.jwt_access_token_expire_minutes * 60)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)
    payload = _build_token_payload(user, expires_at)
    token = _create_jwt(payload, secret, settings.jwt_algorithm)
    return token, expires_at, expires_in_seconds


async def validate_access_token(token: str) -> Optional[AuthenticatedUser]:
    if not token:
        return None

    try:
        payload = _decode_jwt(
            token,
            _get_required_secret("JWT_SECRET_KEY", settings.jwt_secret_key),
            settings.jwt_algorithm,
        )
    except ValueError:
        return None

    subject = payload.get("sub")
    is_superuser = bool(payload.get("is_superuser", False))

    if not subject:
        return None

    try:
        user_id = UUID(subject)
    except ValueError:
        return None

    async with get_connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, username, is_superuser, is_active
            FROM auth_users
            WHERE id = $1
            LIMIT 1
            """,
            user_id,
        )

    if not row or not row["is_active"]:
        return None

    return AuthenticatedUser(
        user_id=row["id"],
        username=row["username"],
        is_superuser=is_superuser and bool(row["is_superuser"]),
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> AuthenticatedUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )

    user = await validate_access_token(credentials.credentials)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    return user
