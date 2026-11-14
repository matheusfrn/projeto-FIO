"""
db_models.py — Modelos ORM SQLAlchemy para a plataforma Fio.

Schema baseado em docs/arquitetura.md Seção 6 (ER) e Seção 12.7 (schema detalhado).
7 tabelas: customers, cards, acceptors, transactions, alerts, dossiers, analyst_decisions.
"""

import os
from sqlalchemy import (
    Column, Text, Integer, UniqueConstraint,
    ForeignKey, create_engine
)
from sqlalchemy import REAL as Real
from sqlalchemy.orm import DeclarativeBase, relationship

# ---------------------------------------------------------------------------
# Engine — usa DATABASE_URL do ambiente; fallback para SQLite local
# ---------------------------------------------------------------------------
_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/fio.db")
engine = create_engine(
    _DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in _DATABASE_URL else {},
)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# customers
# ---------------------------------------------------------------------------
class Customer(Base):
    __tablename__ = "customers"

    customer_id = Column(Text, primary_key=True)
    customer_name = Column(Text)
    customer_segment = Column(Text)
    customer_risk_rating = Column(Text)
    customer_total_balance = Column(Real)
    customer_total_credit_limit = Column(Real)
    customer_region = Column(Text)
    customer_registration_date = Column(Text)
    customer_kyc_lvl = Column(Text)

    cards = relationship("Card", back_populates="customer")
    transactions = relationship("Transaction", back_populates="customer")


# ---------------------------------------------------------------------------
# cards
# ---------------------------------------------------------------------------
class Card(Base):
    __tablename__ = "cards"

    card_id = Column(Text, primary_key=True)
    customer_id = Column(Text, ForeignKey("customers.customer_id"))
    card_type = Column(Text)
    card_brand = Column(Text)
    card_status = Column(Text)
    card_credit_limit = Column(Real)
    card_available_balance = Column(Real)
    card_expiration_date = Column(Text)

    customer = relationship("Customer", back_populates="cards")
    transactions = relationship("Transaction", back_populates="card")


# ---------------------------------------------------------------------------
# acceptors
# ---------------------------------------------------------------------------
class Acceptor(Base):
    __tablename__ = "acceptors"

    acceptor_id = Column(Text, primary_key=True)
    acceptor_name = Column(Text)
    acceptor_city = Column(Text)
    acceptor_region = Column(Text)
    acceptor_country_code = Column(Text)
    acceptor_risk = Column(Real)
    acceptor_risky_flag = Column(Integer)        # 0 / 1
    acceptor_mastercard_risk = Column(Real)
    acceptor_visa_risk = Column(Real)

    transactions = relationship("Transaction", back_populates="acceptor")


# ---------------------------------------------------------------------------
# transactions
# ---------------------------------------------------------------------------
class Transaction(Base):
    """
    Tabela central de transações.

    IMPORTANTE (RN-16): os campos feedback_label, decision e transaction_status
    são armazenados aqui para fins de carga e consulta histórica, mas NUNCA
    devem ser passados como features ao modelo preditivo.
    """
    __tablename__ = "transactions"

    event_external_id = Column(Text, primary_key=True)
    customer_id = Column(Text, ForeignKey("customers.customer_id"))
    card_id = Column(Text, ForeignKey("cards.card_id"))
    acceptor_id = Column(Text, ForeignKey("acceptors.acceptor_id"))
    alert_id = Column(Text, nullable=True)

    amount = Column(Real, nullable=False)
    amount_fees = Column(Real)
    tipo_transacao = Column(Text)
    modalidade_transacao = Column(Text)
    event_occurred_at = Column(Text, nullable=False)  # ISO 8601
    pos_environment = Column(Text)
    mcc = Column(Text)
    currency_code = Column(Text)

    # Scores externos das bandeiras — usados como FEATURES no modelo (decisão D1)
    fraud_score = Column(Real)
    visa_fraud_score = Column(Real)
    mc_fraud_score = Column(Real)

    # Campos de resultado posterior — armazenados mas EXCLUÍDOS do modelo (RN-16)
    feedback_label = Column(Integer)      # target: 1=fraude, 0=legítima
    transaction_status = Column(Text)     # approved / declined (leakage pós-evento)
    decision = Column(Text)              # deny / approve (leakage pós-evento)
    dt_partition = Column(Text)

    raw_json = Column(Text)              # linha original do CSV serializada

    customer = relationship("Customer", back_populates="transactions")
    card = relationship("Card", back_populates="transactions")
    acceptor = relationship("Acceptor", back_populates="transactions")


# ---------------------------------------------------------------------------
# alerts
# ---------------------------------------------------------------------------
class Alert(Base):
    __tablename__ = "alerts"

    alert_id = Column(Text, primary_key=True)
    event_external_id = Column(Text, ForeignKey("transactions.event_external_id"), nullable=False)
    generated_at = Column(Text, nullable=False)  # ISO 8601
    status = Column(Text, nullable=False, default="NOVO")
    # Valores válidos: NOVO / EM_ANALISE / ENCERRADO
    source = Column(Text, default="import_script")

    dossier = relationship("Dossier", back_populates="alert", uselist=False)
    decisions = relationship("AnalystDecision", back_populates="alert")


# ---------------------------------------------------------------------------
# dossiers
# ---------------------------------------------------------------------------
class Dossier(Base):
    """
    Dossiê investigativo gerado para um alerta.

    RN-01: um alerta tem no máximo um dossiê ativo (alert_id UNIQUE).
    RN-08: disclaimer é imutável e obrigatório.
    RN-09: analyst_decision aceita somente fraude_confirmada / legitima / em_analise / null.
    RN-14: persistido após a primeira geração; requisições subsequentes retornam do cache.
    RN-15: model_version preservado; dossiês não são regerados ao trocar versão.
    RN-17: investigation_priority e investigation_trace registram o rastro da investigação.
    RN-18: justification_text registra justificativa em texto livre do analista.
    RN-19: cluster_comparison_status sempre "nao_entregue_neste_ciclo".
    """
    __tablename__ = "dossiers"
    __table_args__ = (UniqueConstraint("alert_id", name="uq_dossier_alert"),)

    dossier_id = Column(Text, primary_key=True)
    alert_id = Column(Text, ForeignKey("alerts.alert_id"), nullable=False)
    generated_at = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default="pendente")
    # Valores válidos: pendente / gerando / pronto / pronto_parcial / erro / em_analise /
    #                  concluido_fraude / concluido_legitimo / aguardando_info

    # Seções serializadas como JSON (TEXT no SQLite)
    transaction_json = Column(Text)
    customer_json = Column(Text)
    scoring_json = Column(Text)
    deviations_json = Column(Text)
    timeline_json = Column(Text)
    related_cases_json = Column(Text)   # decisões anteriores e outros alertas pendentes do mesmo associado

    # Rastro de investigação (RN-17)
    investigation_priority = Column(Text)
    # Valores válidos: valor_atipico / estabelecimento_historico / mcc_incomum /
    #                  hora_incomum / sem_historico_suficiente
    investigation_trace = Column(Text)   # JSON serializado: [{step, source, reason, result, gaps}]

    # Comparação com cluster — não entregue neste ciclo (RN-19, decisão D4)
    cluster_comparison_status = Column(Text, default="nao_entregue_neste_ciclo")

    # Disclaimer obrigatório (RN-08)
    disclaimer = Column(Text, nullable=False)

    # Campos de decisão do analista (RN-09, RN-18)
    analyst_decision = Column(Text)          # null até decisão humana
    justification_text = Column(Text)        # texto livre, opcional
    decision_at = Column(Text)

    # Rastreabilidade do modelo (RN-15)
    model_version = Column(Text)

    alert = relationship("Alert", back_populates="dossier")


# ---------------------------------------------------------------------------
# analyst_decisions
# ---------------------------------------------------------------------------
class AnalystDecision(Base):
    __tablename__ = "analyst_decisions"

    decision_id = Column(Text, primary_key=True)
    alert_id = Column(Text, ForeignKey("alerts.alert_id"), nullable=False)
    decision = Column(Text, nullable=False)
    # Valores válidos (RN-09): fraude_confirmada / legitima / em_analise
    notes = Column(Text)
    justification_text = Column(Text)        # texto livre (RN-18, decisão D5)
    decided_at = Column(Text, nullable=False)
    analyst_id = Column(Text)               # reservado para autenticação futura

    alert = relationship("Alert", back_populates="decisions")
