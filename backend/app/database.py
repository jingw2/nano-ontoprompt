from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.config import settings

_is_sqlite = settings.database_url.startswith("sqlite")

_kwargs: dict = {"pool_pre_ping": True}
if _is_sqlite:
    _kwargs["connect_args"] = {"check_same_thread": False}
else:
    _kwargs["pool_size"] = 5
    _kwargs["max_overflow"] = 10

engine = create_engine(settings.database_url, **_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    pass
