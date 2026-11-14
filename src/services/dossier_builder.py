"""
dossier_builder.py — Orquestra a construção e persistência do dossiê investigativo.

Responsabilidades:
  1. Receber alert_id e carregar a transação associada.
  2. Atualizar status do dossiê para GERANDO (máquina de estados).
  3. Chamar ScoringService e EvidenceAggregator.
  4. Calcular investigation_priority (RN-17, D3).
  5. Construir investigation_trace com rastro das etapas (RN-17, D6).
  6. Persistir dossiê com status PRONTO (RN-14).

Regras de negócio implementadas:
  RN-01  um alerta tem no máximo um dossiê ativo (UNIQUE alert_id na tabela)
  RN-08  disclaimer obrigatório e imutável
  RN-09  analyst_decision=null na criação
  RN-14  cache: se dossiê já existe, retorna sem regenerar
  RN-15  model_version preservado no dossiê
  RN-17  investigation_priority e investigation_trace
  RN-18  justification_text=null na criação
  RN-19  cluster_comparison_status="nao_entregue_neste_ciclo"
"""

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.api.schemas.dossier import DeviationReport, PendingCase, PriorDecision, RelatedCases
from src.models.db_models import AnalystDecision, Alert, Acceptor, Card, Customer, Dossier, Transaction
from src.services.evidence_aggregator import EvidenceAggregator
from src.services.scoring_service import ScoringService
from src.api.schemas.score import ScoreInput, ScoreResult

_DISCLAIMER = (
    "Este dossiê é um apoio à investigação. "
    "A decisão de fraude ou legitimidade é de responsabilidade exclusiva do analista."
)


def _calc_investigation_priority(dev: DeviationReport) -> str:
    """Lógica de prioridade do indício dominante (D3 — RN-17).

    Ordem de precedência:
      1. sem_historico_suficiente
      2. valor_atipico
      3. mcc_incomum
      4. hora_incomum
      5. estabelecimento_historico
      6. valor_atipico (fallback)
    """
    if dev.insufficient_history:
        return "sem_historico_suficiente"
    if dev.amount_deviation_relevant:
        return "valor_atipico"
    if dev.mcc_unusual:
        return "mcc_incomum"
    if dev.hour_unusual:
        return "hora_incomum"
    if dev.related_alerts_same_acceptor_30d and dev.related_alerts_same_acceptor_30d > 0:
        return "estabelecimento_historico"
    return "valor_atipico"  # fallback


def _build_trace(score: ScoreResult, dev: DeviationReport, priority: str) -> list[dict]:
    """Constrói o rastro de investigação (D6 — RN-17)."""
    trace = []
    step = 1

    # Passo 1: scoring
    trace.append({
        "step": step,
        "source": "scoring_service",
        "reason": "transação recebida — score calculado",
        "result": f"fraud_score={score.fraud_score_model:.2f} risk_level={score.risk_level}"
                  + (" [SIMULADO]" if score.mock_mode else ""),
        "gaps": ["score em modo mock — modelo real indisponível"] if score.mock_mode else [],
    })
    step += 1

    # Passo 2: histórico
    if dev.insufficient_history:
        trace.append({
            "step": step,
            "source": "evidence_aggregator",
            "reason": "verificação de histórico do associado",
            "result": "histórico insuficiente (< 3 transações nos últimos 90 dias)",
            "gaps": ["desvios não calculados por falta de histórico"],
        })
        step += 1
    else:
        # Passo 2a: desvio de valor
        if dev.amount_deviation_pct is not None:
            label = f"desvio de {dev.amount_deviation_pct:.1f}% em relação à média dos últimos 30 dias"
            trace.append({
                "step": step,
                "source": "evidence_aggregator",
                "reason": "indício dominante: valor da transação",
                "result": label + (" — RELEVANTE" if dev.amount_deviation_relevant else " — dentro do padrão"),
                "gaps": [],
            })
            step += 1

        # Passo 2b: MCC
        if dev.mcc_unusual is not None:
            trace.append({
                "step": step,
                "source": "evidence_aggregator",
                "reason": "verificação de MCC (categoria do estabelecimento)",
                "result": f"MCC atual {dev.current_mcc!r} {'diferente do habitual' if dev.mcc_unusual else 'dentro do padrão'} (usual: {dev.usual_mcc!r})",
                "gaps": [],
            })
            step += 1

        # Passo 2c: horário
        if dev.hour_unusual is not None:
            trace.append({
                "step": step,
                "source": "evidence_aggregator",
                "reason": "verificação de faixa horária habitual",
                "result": f"hora {dev.current_hour!r} {'fora da faixa habitual' if dev.hour_unusual else 'dentro da faixa habitual'} ({dev.usual_hour_range})",
                "gaps": [],
            })
            step += 1

    # Passo: alertas relacionados
    total_related = (dev.related_alerts_same_card_30d or 0) + (dev.related_alerts_same_acceptor_30d or 0)
    trace.append({
        "step": step,
        "source": "evidence_aggregator",
        "reason": "verificação de alertas relacionados (últimos 30 dias)",
        "result": f"{dev.related_alerts_same_card_30d} alertas no mesmo cartão; "
                  f"{dev.related_alerts_same_acceptor_30d} no mesmo estabelecimento",
        "gaps": [] if total_related > 0 else ["nenhum alerta anterior identificado"],
    })
    step += 1

    # Passo final: cluster (C1 — não entregue neste ciclo, RN-19)
    trace.append({
        "step": step,
        "source": "cluster_service",
        "reason": "comparação com perfis similares (C1)",
        "result": "não disponível neste ciclo (decisão D4, 29/10/2026)",
        "gaps": ["C1 declarado indisponível — cluster_comparison_status=nao_entregue_neste_ciclo"],
    })

    return trace


# ---------------------------------------------------------------------------
# Singleton do ScoringService — carregado uma vez, reutilizado em todos os dossiês
# ---------------------------------------------------------------------------
_scoring_singleton: ScoringService | None = None


def _get_scoring_service() -> ScoringService:
    global _scoring_singleton
    if _scoring_singleton is None:
        _scoring_singleton = ScoringService()
        _scoring_singleton.load_artifacts()
    return _scoring_singleton


class DossierBuilder:
    """Orquestra a construção e persistência do dossiê investigativo."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._scoring = _get_scoring_service()
        self._evidence = EvidenceAggregator(db)

    def build(self, alert_id: str) -> Dossier:
        """Constrói e persiste o dossiê para o alerta.

        Se o dossiê já existir, retorna o persistido sem regenerar (RN-14).
        Respeita a máquina de estados: pendente → gerando → pronto.

        Args:
            alert_id: ID do alerta para o qual gerar o dossiê.

        Returns:
            Instância ORM Dossier persistida.

        Raises:
            ValueError: se o alerta não for encontrado.
        """
        # RN-14: cache — retornar dossiê existente sem regenerar
        existing = self._db.query(Dossier).filter(Dossier.alert_id == alert_id).first()
        if existing is not None:
            return existing

        alert = self._db.get(Alert, alert_id)
        if alert is None:
            raise ValueError(f"Alerta '{alert_id}' não encontrado.")

        tx = self._db.get(Transaction, alert.event_external_id)
        if tx is None:
            raise ValueError(f"Transação '{alert.event_external_id}' não encontrada.")

        # Criar registro com status GERANDO (máquina de estados)
        dossier_id = f"DOSS_{uuid.uuid4().hex[:12].upper()}"
        now = datetime.now(timezone.utc).isoformat()

        dossier = Dossier(
            dossier_id=dossier_id,
            alert_id=alert_id,
            generated_at=now,
            status="gerando",
            disclaimer=_DISCLAIMER,
            cluster_comparison_status="nao_entregue_neste_ciclo",
            analyst_decision=None,
            justification_text=None,
        )
        self._db.add(dossier)
        self._db.flush()  # persiste o ID sem commitar para garantir unicidade (RN-01)

        try:
            # Scoring (ST-3)
            score_input = ScoreInput(
                event_external_id=tx.event_external_id,
                amount=tx.amount,
                tipo_transacao=tx.tipo_transacao,
                modalidade_transacao=tx.modalidade_transacao,
                customer_id=tx.customer_id,
                card_id=tx.card_id,
                acceptor_id=tx.acceptor_id,
                pos_environment=tx.pos_environment,
                mcc=tx.mcc,
                currency_code=tx.currency_code,
                event_occurred_at=tx.event_occurred_at,
                fraud_score=tx.fraud_score,
                visa_fraud_score=tx.visa_fraud_score,
                mc_fraud_score=tx.mc_fraud_score,
            )
            score = self._scoring.predict(score_input)

            # Evidências
            dev = self._evidence.aggregate(
                customer_id=tx.customer_id,
                card_id=tx.card_id,
                acceptor_id=tx.acceptor_id,
                event_occurred_at=tx.event_occurred_at,
                current_amount=tx.amount,
                current_mcc=tx.mcc,
            )

            # investigation_priority (D3 — RN-17)
            priority = _calc_investigation_priority(dev)

            # investigation_trace (D6 — RN-17)
            trace = _build_trace(score, dev, priority)

            # Panorama de outros casos do mesmo associado
            related = self._build_related_cases(tx.customer_id, alert_id)

            # Seções JSON
            customer_section = self._build_customer_section(tx)
            transaction_section = self._build_transaction_section(tx)

            dossier.transaction_json = json.dumps(transaction_section, ensure_ascii=False)
            dossier.customer_json = json.dumps(customer_section, ensure_ascii=False)
            dossier.scoring_json = json.dumps(score.model_dump(), ensure_ascii=False)
            dossier.deviations_json = json.dumps(dev.model_dump(), ensure_ascii=False)
            dossier.timeline_json = json.dumps(
                [t.model_dump() for t in dev.timeline], ensure_ascii=False
            )
            dossier.investigation_priority = priority
            dossier.investigation_trace = json.dumps(trace, ensure_ascii=False)
            dossier.related_cases_json = json.dumps(related.model_dump(), ensure_ascii=False)
            dossier.model_version = score.model_version
            dossier.status = "pronto"

        except Exception:
            dossier.status = "erro"
            self._db.commit()
            raise

        self._db.commit()
        self._db.refresh(dossier)
        return dossier

    # ------------------------------------------------------------------
    # Helpers de montagem das seções
    # ------------------------------------------------------------------

    def _build_related_cases(self, customer_id: str | None, current_alert_id: str) -> RelatedCases:
        """Monta o panorama de outros casos do mesmo associado.

        Um alerta isolado, sem visão de que a mesma pessoa já teve decisões
        anteriores ou outros casos abertos ao mesmo tempo, força o analista a
        reconstruir esse contexto manualmente — exatamente o retrabalho que o
        Fio existe para eliminar (RN-05/N5: histórico e rastreabilidade entre
        investigações do mesmo associado).
        """
        if not customer_id:
            return RelatedCases()

        decision_rows = self._db.execute(
            select(AnalystDecision, Alert, Transaction)
            .join(Alert, AnalystDecision.alert_id == Alert.alert_id)
            .join(Transaction, Alert.event_external_id == Transaction.event_external_id)
            .where(Transaction.customer_id == customer_id)
            .where(Alert.alert_id != current_alert_id)
            .order_by(AnalystDecision.decided_at.desc())
            .limit(5)
        ).all()
        prior_decisions = [
            PriorDecision(
                alert_id=alert.alert_id,
                decision=decision.decision,
                decided_at=decision.decided_at,
                amount=tx.amount,
                event_occurred_at=tx.event_occurred_at,
            )
            for decision, alert, tx in decision_rows
        ]

        pending_stmt = (
            select(Alert, Transaction)
            .join(Transaction, Alert.event_external_id == Transaction.event_external_id)
            .where(Transaction.customer_id == customer_id)
            .where(Alert.alert_id != current_alert_id)
            .where(Alert.status == "NOVO")
        )
        other_pending_count = self._db.scalar(
            select(func.count()).select_from(pending_stmt.subquery())
        ) or 0
        pending_rows = self._db.execute(
            pending_stmt.order_by(Transaction.fraud_score.desc().nulls_last()).limit(5)
        ).all()
        other_pending = [
            PendingCase(
                alert_id=alert.alert_id,
                amount=tx.amount,
                event_occurred_at=tx.event_occurred_at,
                fraud_score=tx.fraud_score,
            )
            for alert, tx in pending_rows
        ]

        return RelatedCases(
            prior_decisions=prior_decisions,
            other_pending=other_pending,
            other_pending_count=other_pending_count,
        )

    def _build_transaction_section(self, tx: Transaction) -> dict:
        return {
            "event_external_id": tx.event_external_id,
            "amount": tx.amount,
            "amount_fees": tx.amount_fees,
            "tipo_transacao": tx.tipo_transacao,
            "modalidade_transacao": tx.modalidade_transacao,
            "event_occurred_at": tx.event_occurred_at,
            "pos_environment": tx.pos_environment,
            "mcc": tx.mcc,
            "currency_code": tx.currency_code,
            "acceptor_id": tx.acceptor_id,
            **(self._build_acceptor_fields(tx.acceptor_id)),
        }

    def _build_customer_section(self, tx: Transaction) -> dict:
        section: dict = {"customer_id": tx.customer_id}
        if tx.customer_id:
            customer = self._db.get(Customer, tx.customer_id)
            if customer:
                section.update({
                    "customer_name": customer.customer_name,
                    "customer_segment": customer.customer_segment,
                    "customer_risk_rating": customer.customer_risk_rating,
                    "customer_region": customer.customer_region,
                    "customer_registration_date": customer.customer_registration_date,
                    "customer_kyc_lvl": customer.customer_kyc_lvl,
                    "customer_total_balance": customer.customer_total_balance,
                })
        if tx.card_id:
            card = self._db.get(Card, tx.card_id)
            if card:
                section.update({
                    "card_id": card.card_id,
                    "card_type": card.card_type,
                    "card_brand": card.card_brand,
                    "card_status": card.card_status,
                    "card_credit_limit": card.card_credit_limit,
                })
        return section

    def _build_acceptor_fields(self, acceptor_id: str | None) -> dict:
        if not acceptor_id:
            return {}
        acc = self._db.get(Acceptor, acceptor_id)
        if not acc:
            return {}
        return {
            "acceptor_name": acc.acceptor_name,
            "acceptor_city": acc.acceptor_city,
            "acceptor_region": acc.acceptor_region,
            "acceptor_country_code": acc.acceptor_country_code,
        }
