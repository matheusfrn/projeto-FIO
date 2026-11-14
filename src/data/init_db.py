"""
init_db.py — Inicializa o banco de dados SQLite criando todas as tabelas.

Uso:
    python -m src.data.init_db

Cria o arquivo data/fio.db (ou o path definido em DATABASE_URL) com as
7 tabelas do schema: customers, cards, acceptors, transactions, alerts,
dossiers, analyst_decisions.
"""

import os
import pathlib
from src.models.db_models import Base, engine


_log = __import__("logging").getLogger(__name__)


def init_db() -> None:
    # Garantir que o diretório data/ existe
    db_url = os.getenv("DATABASE_URL", "sqlite:///./data/fio.db")
    if db_url.startswith("sqlite:///"):
        db_path = pathlib.Path(db_url.replace("sqlite:///", ""))
        db_path.parent.mkdir(parents=True, exist_ok=True)

    Base.metadata.create_all(bind=engine)

    if __name__ == "__main__":
        # Só imprime quando executado diretamente (python -m src.data.init_db)
        from sqlalchemy import inspect
        print("✅ Banco inicializado. Tabelas criadas:")
        for table in inspect(engine).get_table_names():
            print(f"   - {table}")
    else:
        _log.info("init_db: banco pronto.")


if __name__ == "__main__":
    init_db()
