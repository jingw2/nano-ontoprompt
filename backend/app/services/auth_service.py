from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from app.models.user import User
from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt_sha256", "bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def create_access_token(data: dict) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode({**data, "exp": expire}, settings.secret_key, algorithm="HS256")

def create_oauth_access_token(user_id: str, client_id: str, scope: str) -> str:
    """A short-lived, stateless JWT for an OAuth client — see the
    oauth-pkce-authorization-server plan's Global Constraints for why the
    `token_use` claim is required in both directions."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.oauth_access_token_expire_minutes)
    return jwt.encode(
        {"sub": user_id, "client_id": client_id, "scope": scope, "token_use": "oauth_access", "exp": expire},
        settings.secret_key, algorithm="HS256",
    )

_UNCHECKED = object()


def decode_token(token: str, expected_token_use: str | None = _UNCHECKED) -> dict:
    payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    if expected_token_use is not _UNCHECKED and payload.get("token_use") != expected_token_use:
        raise JWTError("token_use claim mismatch")
    return payload

def authenticate_user(db: Session, username: str, password: str) -> User | None:
    user = db.query(User).filter(User.username == username, User.is_active == True).first()
    if user and verify_password(password, user.password_hash):
        return user
    return None

def get_user_by_id(db: Session, user_id: str) -> User | None:
    return db.query(User).filter(User.id == user_id).first()

def seed_admin(db: Session):
    if db.query(User).filter(User.role == "admin").count() == 0:
        import uuid
        admin = User(
            id=str(uuid.uuid4()),
            username=settings.first_admin_user,
            email=f"{settings.first_admin_user}@ontoprompt.local",
            password_hash=hash_password(settings.first_admin_password),
            role="admin",
        )
        db.add(admin)
        db.commit()
