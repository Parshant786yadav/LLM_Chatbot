# database.py

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Absolute path so startup and request handlers use the same DB file
_db_dir = os.path.dirname(os.path.abspath(__file__))
DATABASE_URL = "sqlite:///" + os.path.join(_db_dir, "chatbot.db").replace("\\", "/")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 20}
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()