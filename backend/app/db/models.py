"""SQLAlchemy ORM models. Tables are added from F02 on."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all tables."""
