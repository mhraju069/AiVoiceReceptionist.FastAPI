import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Load environment variables
load_dotenv()

# Database connection selection — toggle using USE_SQLITE (true/false)
USE_SQLITE = os.getenv("USE_SQLITE", "false").lower() in ("true", "1", "t", "yes")

if USE_SQLITE:
    SQLALCHEMY_DATABASE_URL = os.getenv(
        "SQLITE_DATABASE_URL",
        os.getenv("DATABASE_URL", "sqlite:///vocaai.db")
    )
else:
    # Default to PostgreSQL
    SQLALCHEMY_DATABASE_URL = os.getenv(
        "DATABASE_URL",
        os.getenv("POSTGRES_DATABASE_URL", "postgresql://postgres:postgres@db:5432/vocaai")
    )

# SQLite fallback or option:
# If using SQLite, we need connect_args={"check_same_thread": False}
connect_args = {}
if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependency to get a DB session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
