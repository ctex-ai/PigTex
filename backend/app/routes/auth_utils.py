from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
import jwt
from jwt.exceptions import PyJWTError
import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import User

settings = get_settings()
security = HTTPBearer()
ROLE_USER = "user"
ROLE_ADMIN = "admin"
ROLE_SUPER_ADMIN = "super_admin"
ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    ROLE_USER: (),
    ROLE_ADMIN: (
        "admin.console:view",
        "skill.audit:read",
        "skill.registry:read",
        "skill.resolve:read",
        "skill.compile:write",
        "skill.publish:write",
        "skill.rollback:write",
    ),
    ROLE_SUPER_ADMIN: (
        "admin.console:view",
        "skill.audit:read",
        "skill.registry:read",
        "skill.resolve:read",
        "skill.compile:write",
        "skill.publish:write",
        "skill.rollback:write",
        "admin.access:write",
    ),
}

# Try to import argon2 for argon2 hash support
try:
    import argon2
    from argon2 import PasswordHasher
    _argon2_hasher = PasswordHasher()
    _has_argon2 = True
except ImportError:
    _has_argon2 = False


def _truncate_for_bcrypt(password: str) -> bytes:
    """Truncate password to 72 bytes for bcrypt compatibility."""
    return password.encode("utf-8")[:72]


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a hash (supports both bcrypt and argon2)."""
    try:
        # Detect hash type
        if hashed_password.startswith("$2b$") or hashed_password.startswith("$2a$"):
            # bcrypt hash - truncate to 72 bytes
            return bcrypt.checkpw(
                _truncate_for_bcrypt(plain_password),
                hashed_password.encode("utf-8")
            )
        elif hashed_password.startswith("$argon2") and _has_argon2:
            # argon2 hash
            return _argon2_hasher.verify(hashed_password, plain_password)
        else:
            # Fallback: try bcrypt
            return bcrypt.checkpw(
                _truncate_for_bcrypt(plain_password),
                hashed_password.encode("utf-8")
            )
    except Exception:
        return False


def get_password_hash(password: str) -> str:
    """Hash a password using argon2 (preferred) or bcrypt as fallback."""
    if _has_argon2:
        return _argon2_hasher.hash(password)
    # Fallback to bcrypt
    return bcrypt.hashpw(
        _truncate_for_bcrypt(password),
        bcrypt.gensalt()
    ).decode("utf-8")


def get_website_password_hash(password: str) -> str:
    """Hash password using bcrypt for website NextAuth compatibility."""
    return bcrypt.hashpw(
        _truncate_for_bcrypt(password),
        bcrypt.gensalt()
    ).decode("utf-8")


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return encoded_jwt


def decode_token(token: str) -> Optional[str]:
    """Decode a JWT token and return user_id"""
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        user_id: str = payload.get("sub")
        return user_id
    except PyJWTError:
        return None


def resolve_role_permissions(role: str) -> list[str]:
    normalized_role = (role or ROLE_USER).strip().lower() or ROLE_USER
    permissions = ROLE_PERMISSIONS.get(normalized_role, ROLE_PERMISSIONS[ROLE_USER])
    return list(permissions)


def ensure_bootstrap_admin(user: User, db: Session) -> User:
    bootstrap_emails = settings.get_admin_bootstrap_emails()
    if not bootstrap_emails:
        return user
    if (user.email or "").strip().lower() not in bootstrap_emails:
        return user
    current_role = (user.role or ROLE_USER).strip().lower() or ROLE_USER
    if current_role in {ROLE_ADMIN, ROLE_SUPER_ADMIN}:
        return user
    user.role = ROLE_ADMIN
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_user_permissions(user: User) -> list[str]:
    return resolve_role_permissions(getattr(user, "role", ROLE_USER))


def user_has_permissions(user: User, required_permissions: tuple[str, ...]) -> bool:
    if not required_permissions:
        return True
    granted = set(get_user_permissions(user))
    return all(permission in granted for permission in required_permissions)


def require_permissions(*required_permissions: str) -> Callable[[User], User]:
    async def _dependency(current_user: User = Depends(get_current_user)) -> User:
        if not user_has_permissions(current_user, required_permissions):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    return _dependency


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """Get current authenticated user from JWT token"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    token = credentials.credentials
    user_id = decode_token(token)
    
    if user_id is None:
        raise credentials_exception
    
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise credentials_exception
    
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")

    return ensure_bootstrap_admin(user, db)
