"""
Database engine and session factory.
SQLite file-based for local dev — no PostgreSQL needed.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

DATABASE_URL = "sqlite:///./bidsure.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},  # required for SQLite + FastAPI
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency: yields a DB session and guarantees cleanup."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
