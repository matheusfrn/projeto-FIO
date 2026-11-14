"""
src/api/deps.py — Dependências compartilhadas FastAPI.

Fornece a sessão SQLAlchemy como dependency injection para os routers.
"""

from collections.abc import Generator
from sqlalchemy.orm import Session
from src.models.db_models import engine


def get_db() -> Generator[Session, None, None]:
    """Dependency: abre uma sessão de banco e garante fechamento ao fim da request."""
    with Session(engine) as session:
        yield session
