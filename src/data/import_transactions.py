"""
import_transactions.py — Carga dos dados do case Sicredi para o banco SQLite.

Normaliza as 297 colunas do CSV em 5 tabelas:
  customers, cards, acceptors, transactions, alerts

Uso:
    python -m src.data.import_transactions
    python -m src.data.import_transactions --csv data/raw/meu_arquivo.csv
    python -m src.data.import_transactions --dry-run   # valida sem inserir

Regras implementadas (ST-2 / docs/fio-architecture-plan.md):
  RN-16  campos de resultado posterior nunca alimentam o modelo:
         feedback_label, dispute_*, fraud, decision=deny,
         transaction_status=declined — são armazenados mas marcados.
  D1     fraud_score, visa_fraud_score, mc_fraud_score incluídos como
         features (scores de bandeira); premissa: disponíveis no momento
         da autorização (declarada no model card de Tim).
  D7     os 6 índices de demonstração reservados por Igor/Isa são excluídos
         do conjunto que Tim usa para treino — mas são INCLUÍDOS aqui no
         banco para que o MVP os exiba. Exclusão é responsabilidade do
         script de modelagem de Tim.

Limiar de alerta (calibrável via --alert-threshold):
  Padrão: percentil 95 de fraud_score sobre todas as transações carregadas.
  Conforme EDA (eda-e-modelagem.ipynb Etapa 3): fraud_score tem forte poder
  discriminante (~4.4× razão de separação). decision=deny e
  transaction_status=declined são leakage pós-evento e NÃO são usados como
  critério de alerta nem no cálculo do limiar.

Idempotência: reexecutar o script não duplica clientes, cartões, estabelecimentos,
transações nem alertas (eventos que já têm alerta são ignorados).
"""

import argparse
import json
import logging
import pathlib
import sys
import uuid
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.data.init_db import init_db
from src.models.db_models import (
    Acceptor, Alert, Card, Customer, Transaction, engine,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mapeamento de colunas CSV → tabelas ORM
# ---------------------------------------------------------------------------

# Colunas extraídas para a tabela customers
_CUSTOMER_COLS = [
    "customer_id", "customer_name", "customer_segment", "customer_risk_rating",
    "customer_total_balance", "customer_total_credit_limit", "customer_region",
    "customer_registration_date", "customer_kyc_lvl",
]

# Colunas extraídas para a tabela cards
_CARD_COLS = [
    "card_id", "customer_id", "card_type", "card_brand", "card_status",
    "card_credit_limit", "card_available_balance", "card_expiration_date",
]

# Colunas extraídas para a tabela acceptors
_ACCEPTOR_COLS = [
    "acceptor_id", "acceptor_name", "acceptor_city", "acceptor_region",
    "acceptor_country_code", "acceptor_risk", "acceptor_risky_flag",
    "acceptor_mastercard_risk", "acceptor_visa_risk",
]

# Colunas centrais da transação
_TX_COLS = [
    "event_external_id", "customer_id", "card_id", "acceptor_id",
    "amount", "amount_fees", "tipo_transacao", "modalidade_transacao",
    "event_occurred_at", "pos_environment", "mcc", "currency_code",
    # Features D1 (scores de bandeira)
    "fraud_score", "visa_fraud_score", "mc_fraud_score",
    # Leakage — armazenados, NUNCA usados como features (RN-16)
    "feedback_label", "transaction_status", "decision", "dt_partition",
]


# Colunas sem as quais a carga não faz sentido
_REQUIRED_COLS = [
    "event_external_id", "customer_id", "card_id", "acceptor_id",
    "amount", "event_occurred_at", "fraud_score",
]


def _json_default(obj):
    """Serializa tipos numpy preservando o tipo (int/float/bool) em vez de str."""
    if hasattr(obj, "item"):
        return obj.item()
    return str(obj)


def _existing_ids(db: Session, column) -> set[str]:
    """IDs já presentes no banco (evita uma consulta por linha na carga)."""
    return {r[0] for r in db.execute(select(column))}


def _safe_str(val) -> str | None:
    if pd.isna(val):
        return None
    return str(val).strip() or None


def _safe_float(val) -> float | None:
    if pd.isna(val):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_int(val) -> int | None:
    if pd.isna(val):
        return None
    try:
        v = str(val).strip().lower()
        if v in ("true", "1", "t", "yes"):
            return 1
        if v in ("false", "0", "f", "no"):
            return 0
        return int(float(v))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Normalização de entidades
# ---------------------------------------------------------------------------

def _upsert_customers(df: pd.DataFrame, db: Session) -> int:
    """Insere clientes únicos, ignorando duplicatas."""
    available = [c for c in _CUSTOMER_COLS if c in df.columns]
    uniq = df[available].drop_duplicates(subset=["customer_id"]).dropna(subset=["customer_id"])
    existing = _existing_ids(db, Customer.customer_id)
    inserted = 0
    for row in uniq.itertuples(index=False):
        cid = _safe_str(row.customer_id)
        if not cid or cid in existing:
            continue
        existing.add(cid)
        db.add(Customer(
            customer_id=cid,
            customer_name=_safe_str(getattr(row, "customer_name", None)),
            customer_segment=_safe_str(getattr(row, "customer_segment", None)),
            customer_risk_rating=_safe_str(getattr(row, "customer_risk_rating", None)),
            customer_total_balance=_safe_float(getattr(row, "customer_total_balance", None)),
            customer_total_credit_limit=_safe_float(getattr(row, "customer_total_credit_limit", None)),
            customer_region=_safe_str(getattr(row, "customer_region", None)),
            customer_registration_date=_safe_str(getattr(row, "customer_registration_date", None)),
            customer_kyc_lvl=_safe_str(getattr(row, "customer_kyc_lvl", None)),
        ))
        inserted += 1
    db.flush()
    return inserted


def _upsert_cards(df: pd.DataFrame, db: Session) -> int:
    """Insere cartões únicos, ignorando duplicatas."""
    available = [c for c in _CARD_COLS if c in df.columns]
    uniq = df[available].drop_duplicates(subset=["card_id"]).dropna(subset=["card_id"])
    existing = _existing_ids(db, Card.card_id)
    inserted = 0
    for row in uniq.itertuples(index=False):
        cid = _safe_str(row.card_id)
        if not cid or cid in existing:
            continue
        existing.add(cid)
        db.add(Card(
            card_id=cid,
            customer_id=_safe_str(getattr(row, "customer_id", None)),
            card_type=_safe_str(getattr(row, "card_type", None)),
            card_brand=_safe_str(getattr(row, "card_brand", None)),
            card_status=_safe_str(getattr(row, "card_status", None)),
            card_credit_limit=_safe_float(getattr(row, "card_credit_limit", None)),
            card_available_balance=_safe_float(getattr(row, "card_available_balance", None)),
            card_expiration_date=_safe_str(getattr(row, "card_expiration_date", None)),
        ))
        inserted += 1
    db.flush()
    return inserted


def _upsert_acceptors(df: pd.DataFrame, db: Session) -> int:
    """Insere estabelecimentos únicos, ignorando duplicatas."""
    available = [c for c in _ACCEPTOR_COLS if c in df.columns]
    uniq = df[available].drop_duplicates(subset=["acceptor_id"]).dropna(subset=["acceptor_id"])
    existing = _existing_ids(db, Acceptor.acceptor_id)
    inserted = 0
    for row in uniq.itertuples(index=False):
        aid = _safe_str(row.acceptor_id)
        if not aid or aid in existing:
            continue
        existing.add(aid)
        db.add(Acceptor(
            acceptor_id=aid,
            acceptor_name=_safe_str(getattr(row, "acceptor_name", None)),
            acceptor_city=_safe_str(getattr(row, "acceptor_city", None)),
            acceptor_region=_safe_str(getattr(row, "acceptor_region", None)),
            acceptor_country_code=_safe_str(getattr(row, "acceptor_country_code", None)),
            acceptor_risk=_safe_float(getattr(row, "acceptor_risk", None)),
            acceptor_risky_flag=_safe_int(getattr(row, "acceptor_risky_flag", None)),
            acceptor_mastercard_risk=_safe_float(getattr(row, "acceptor_mastercard_risk", None)),
            acceptor_visa_risk=_safe_float(getattr(row, "acceptor_visa_risk", None)),
        ))
        inserted += 1
    db.flush()
    return inserted


def _insert_transactions(df: pd.DataFrame, db: Session) -> int:
    """Insere transações, pulando duplicatas de event_external_id."""
    existing = _existing_ids(db, Transaction.event_external_id)
    inserted = 0
    for row in df.itertuples(index=False):
        eid = _safe_str(row.event_external_id)
        if not eid or eid in existing:
            continue
        existing.add(eid)
        # Serializar linha completa como raw_json para rastreabilidade
        raw = {col: (None if pd.isna(getattr(row, col, None)) else getattr(row, col))
               for col in df.columns}
        db.add(Transaction(
            event_external_id=eid,
            customer_id=_safe_str(getattr(row, "customer_id", None)),
            card_id=_safe_str(getattr(row, "card_id", None)),
            acceptor_id=_safe_str(getattr(row, "acceptor_id", None)),
            amount=_safe_float(getattr(row, "amount", None)) or 0.0,
            amount_fees=_safe_float(getattr(row, "amount_fees", None)),
            tipo_transacao=_safe_str(getattr(row, "tipo_transacao", None)),
            modalidade_transacao=_safe_str(getattr(row, "modalidade_transacao", None)),
            event_occurred_at=_safe_str(getattr(row, "event_occurred_at", None)),
            pos_environment=_safe_str(getattr(row, "pos_environment", None)),
            mcc=_safe_str(getattr(row, "mcc", None)),
            currency_code=_safe_str(getattr(row, "currency_code", None)),
            # Features D1
            fraud_score=_safe_float(getattr(row, "fraud_score", None)),
            visa_fraud_score=_safe_float(getattr(row, "visa_fraud_score", None)),
            mc_fraud_score=_safe_float(getattr(row, "mc_fraud_score", None)),
            # Leakage (RN-16) — armazenados, nunca passados ao modelo
            feedback_label=_safe_int(getattr(row, "feedback_label", None)),
            transaction_status=_safe_str(getattr(row, "transaction_status", None)),
            decision=_safe_str(getattr(row, "decision", None)),
            dt_partition=_safe_str(getattr(row, "dt_partition", None)),
            raw_json=json.dumps(raw, default=_json_default, ensure_ascii=False),
        ))
        inserted += 1
        if inserted % 10_000 == 0:
            db.flush()
            log.info("  transações inseridas: %d", inserted)
    db.flush()
    return inserted


def _generate_alerts(df: pd.DataFrame, db: Session, threshold: float) -> int:
    """Gera alertas para transações com fraud_score acima do limiar.

    Limiar padrão: percentil 95 de fraud_score (calculado sobre o dataframe).
    Não usa decision=deny nem transaction_status=declined como critério (RN-16).
    """
    if "fraud_score" not in df.columns:
        log.warning("Coluna fraud_score ausente — nenhum alerta gerado.")
        return 0

    n_null = int(df["fraud_score"].isna().sum())
    if n_null:
        log.warning("%d transações com fraud_score nulo não geram alerta.", n_null)

    import_time = datetime.now(timezone.utc).isoformat()
    mask = df["fraud_score"] >= threshold
    alerta_df = df[mask & df["event_external_id"].notna()]
    already_alerted = _existing_ids(db, Alert.event_external_id)
    inserted = 0
    for row in alerta_df.itertuples(index=False):
        eid = _safe_str(row.event_external_id)
        if not eid or eid in already_alerted:
            continue
        already_alerted.add(eid)
        alert_id = f"ALERT_{uuid.uuid4().hex[:16].upper()}"
        # generated_at = o próprio event_occurred_at da transação (não o horário de
        # importação). Alertas reais surgem perto do momento do evento suspeito;
        # usar um timestamp único de importação para todas as linhas quebrava
        # silenciosamente a janela de "últimos 30 dias" da RN-07 (todo alerta
        # ficava sempre dentro da janela, pois generated_at caía sempre no futuro
        # em relação às transações sintéticas de 2025).
        event_at = _safe_str(getattr(row, "event_occurred_at", None)) or import_time
        db.add(Alert(
            alert_id=alert_id,
            event_external_id=eid,
            generated_at=event_at,
            status="NOVO",
            source="import_script",
        ))
        # Atualizar campo alert_id na transação para rastreabilidade
        tx = db.get(Transaction, eid)
        if tx:
            tx.alert_id = alert_id
        inserted += 1
        if inserted % 1_000 == 0:
            db.flush()
    db.flush()
    return inserted


# ---------------------------------------------------------------------------
# Entrypoint principal
# ---------------------------------------------------------------------------

def run(csv_path: pathlib.Path, alert_threshold: float | None, dry_run: bool) -> None:
    log.info("=== Importação Fio — ST-2 ===")
    log.info("CSV: %s", csv_path)

    if not csv_path.exists():
        log.error("Arquivo não encontrado: %s", csv_path)
        log.error("Coloque o CSV em data/raw/ antes de executar este script.")
        sys.exit(1)

    # Carregar CSV
    log.info("Carregando CSV (pode demorar para arquivos grandes)...")
    # Strings Arrow reduzem a memória durante a carga do CSV de 297 colunas.
    df = pd.read_csv(csv_path, low_memory=False, dtype_backend="pyarrow")
    log.info("CSV carregado: %d linhas × %d colunas", *df.shape)

    missing = [c for c in _REQUIRED_COLS if c not in df.columns]
    if missing:
        log.error("Colunas obrigatórias ausentes no CSV: %s", ", ".join(missing))
        sys.exit(1)

    # Remover registros sem feedback_label (conforme EDA) e sem event_occurred_at (NOT NULL no schema)
    initial = len(df)
    df = df.dropna(subset=["feedback_label"]) if "feedback_label" in df.columns else df
    df = df.dropna(subset=["event_occurred_at"]) if "event_occurred_at" in df.columns else df
    dropped = initial - len(df)
    if dropped:
        log.info("Removidos %d registros sem feedback_label ou event_occurred_at.", dropped)

    # Calcular limiar de alerta
    if alert_threshold is None and "fraud_score" in df.columns:
        # Percentil sobre todas as linhas: decision/transaction_status são
        # leakage pós-evento (RN-16) e não entram no cálculo.
        alert_threshold = float(df["fraud_score"].quantile(0.95))
        log.info("Limiar de alerta (p95 de fraud_score): %.4f", alert_threshold)
    elif alert_threshold is None:
        alert_threshold = 0.5
        log.warning("fraud_score ausente — usando limiar padrão %.2f", alert_threshold)

    if dry_run:
        log.info("=== DRY RUN — nenhum dado será inserido ===")
        log.info("Clientes únicos: %d", df["customer_id"].nunique() if "customer_id" in df.columns else 0)
        log.info("Cartões únicos: %d", df["card_id"].nunique() if "card_id" in df.columns else 0)
        log.info("Estabelecimentos únicos: %d", df["acceptor_id"].nunique() if "acceptor_id" in df.columns else 0)
        log.info("Transações: %d", len(df))
        log.info("Alertas a gerar (fraud_score >= %.4f): %d",
                 alert_threshold,
                 int((df["fraud_score"] >= alert_threshold).sum()) if "fraud_score" in df.columns else 0)
        return

    # Inicializar banco (só na carga real — dry-run não cria arquivo)
    init_db()

    with Session(engine) as db:
        log.info("--- Inserindo clientes...")
        n_cust = _upsert_customers(df, db)
        log.info("  clientes inseridos: %d", n_cust)

        log.info("--- Inserindo cartões...")
        n_cards = _upsert_cards(df, db)
        log.info("  cartões inseridos: %d", n_cards)

        log.info("--- Inserindo estabelecimentos...")
        n_acc = _upsert_acceptors(df, db)
        log.info("  estabelecimentos inseridos: %d", n_acc)

        log.info("--- Inserindo transações...")
        n_tx = _insert_transactions(df, db)
        log.info("  transações inseridas: %d", n_tx)

        log.info("--- Gerando alertas (fraud_score >= %.4f)...", alert_threshold)
        n_alerts = _generate_alerts(df, db, alert_threshold)
        log.info("  alertas gerados: %d", n_alerts)

        log.info("Commitando...")
        db.commit()

    log.info("=== Importação concluída ===")
    log.info("  customers:    %d", n_cust)
    log.info("  cards:        %d", n_cards)
    log.info("  acceptors:    %d", n_acc)
    log.info("  transactions: %d", n_tx)
    log.info("  alerts:       %d", n_alerts)
    log.info("Banco: %s", engine.url)


def main() -> None:
    parser = argparse.ArgumentParser(description="Importa o CSV do case Sicredi para o banco Fio.")
    parser.add_argument(
        "--csv",
        type=pathlib.Path,
        default=pathlib.Path("data/raw/case_transacoes_cartoes_sintetico_900k.csv"),
        help="Caminho para o CSV do case (padrão: data/raw/case_transacoes_cartoes_sintetico_900k.csv)",
    )
    parser.add_argument(
        "--alert-threshold",
        type=float,
        default=None,
        help="Limiar de fraud_score para gerar alertas (padrão: p95 calculado sobre o CSV)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Valida o CSV e exibe contagens sem inserir no banco",
    )
    args = parser.parse_args()
    run(args.csv, args.alert_threshold, args.dry_run)


if __name__ == "__main__":
    main()
