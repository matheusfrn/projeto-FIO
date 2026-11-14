"""
evidence_aggregator.py — Agrega evidências históricas e calcula desvios comportamentais.

Implementa a lógica de:
  1. Buscar histórico de transações do associado (últimos 90 dias).
  2. Calcular desvios: valor, MCC, horário.
  3. Contar alertas relacionados ao mesmo cartão e estabelecimento.
  4. Montar timeline das últimas 10 transações (RN-12).
  5. Sinalizar histórico insuficiente (< 3 registros) via insufficient_history (RN-13).

Regras de negócio implementadas:
  RN-05  desvio de valor: (atual - média_30d) / média_30d * 100; relevante se > 50%
  RN-06  MCC incomum se não está entre os 3 mais frequentes no histórico 90d
  RN-07  alertas relacionados nos últimos 30 dias para card_id e acceptor_id
  RN-12  timeline: últimas 10 transações, ordem decrescente
  RN-13  insufficient_history=True quando < 3 transações no histórico 90d
"""

from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.api.schemas.dossier import AcceptorHistory, DeviationReport, TimelineEvent
from src.models.db_models import Alert, Acceptor, Transaction

_MIN_HISTORY = 3       # RN-13
_HISTORY_DAYS = 90     # janela principal
_AMOUNT_DAYS = 30      # janela para avg_amount (RN-05)
_ALERT_DAYS = 30       # janela para contagem de alertas (RN-07)
_TIMELINE_LIMIT = 10   # RN-12
_MCC_TOP_N = 3         # RN-06
_ACCEPTOR_HISTORY_DAYS = 90  # janela do histórico do estabelecimento (N4)


class EvidenceAggregator:
    """Agrega evidências históricas para um caso de investigação."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def aggregate(
        self,
        customer_id: str,
        card_id: str | None,
        acceptor_id: str | None,
        event_occurred_at: str,
        current_amount: float,
        current_mcc: str | None,
    ) -> DeviationReport:
        """Calcula desvios e monta o relatório de evidências para um caso.

        Args:
            customer_id: ID do associado.
            card_id: ID do cartão utilizado.
            acceptor_id: ID do estabelecimento.
            event_occurred_at: Timestamp ISO 8601 da transação suspeita.
            current_amount: Valor da transação suspeita.
            current_mcc: MCC da transação suspeita.

        Returns:
            DeviationReport com todos os campos calculados ou insufficient_history=True.
        """
        try:
            event_dt = datetime.fromisoformat(event_occurred_at.replace("Z", "+00:00"))
        except Exception:
            event_dt = datetime.now(timezone.utc)

        cutoff_90 = (event_dt - timedelta(days=_HISTORY_DAYS)).isoformat()
        cutoff_30 = (event_dt - timedelta(days=_AMOUNT_DAYS)).isoformat()

        # Histórico 90 dias (excluindo a transação atual)
        history_90 = self._db.scalars(
            select(Transaction)
            .where(Transaction.customer_id == customer_id)
            .where(Transaction.event_occurred_at >= cutoff_90)
            .where(Transaction.event_occurred_at < event_occurred_at)
            .order_by(Transaction.event_occurred_at.desc())
        ).all()

        # Timeline: últimas 10 (RN-12)
        timeline = self._build_timeline(history_90[:_TIMELINE_LIMIT])

        # Alertas relacionados (RN-07) — exclui o próprio caso da contagem
        related_card = self._count_related_alerts(card_id, None, cutoff_30, before=event_occurred_at)
        related_acceptor = self._count_related_alerts(None, acceptor_id, cutoff_30, before=event_occurred_at)

        # Histórico do estabelecimento (N4) — independente do histórico do associado
        cutoff_acceptor = (event_dt - timedelta(days=_ACCEPTOR_HISTORY_DAYS)).isoformat()
        acceptor_history = self._build_acceptor_history(acceptor_id, cutoff_acceptor, event_occurred_at)

        if len(history_90) < _MIN_HISTORY:
            # Histórico insuficiente — campos de desvio ficam null (RN-13)
            return DeviationReport(
                customer_id=customer_id,
                insufficient_history=True,
                related_alerts_same_card_30d=related_card,
                related_alerts_same_acceptor_30d=related_acceptor,
                timeline=timeline,
                acceptor_history=acceptor_history,
            )

        # Calcular avg_amount_30d (RN-05)
        # Preferência: transações nos últimos 30d. Fallback: toda a janela de 90d
        # quando não há dados suficientes nos 30d (evita desvio null com histórico válido).
        history_30 = [tx for tx in history_90 if tx.event_occurred_at >= cutoff_30]
        base_for_avg = history_30 if len(history_30) >= 3 else history_90
        avg_amount_30d: float | None = None
        amount_deviation_pct: float | None = None
        amount_deviation_relevant = False
        if base_for_avg:
            avg_amount_30d = sum(tx.amount for tx in base_for_avg) / len(base_for_avg)
            if avg_amount_30d and avg_amount_30d > 0:
                amount_deviation_pct = (current_amount - avg_amount_30d) / avg_amount_30d * 100
                amount_deviation_relevant = abs(amount_deviation_pct) > 50

        # MCC habitual (RN-06)
        mcc_counts = Counter(tx.mcc for tx in history_90 if tx.mcc)
        top_mccs = [m for m, _ in mcc_counts.most_common(_MCC_TOP_N)]
        usual_mcc = top_mccs[0] if top_mccs else None
        mcc_unusual = bool(current_mcc and top_mccs and current_mcc not in top_mccs)

        # Faixa horária habitual
        usual_hour_range, hour_unusual = self._calc_hour_deviation(history_90, event_dt)

        return DeviationReport(
            customer_id=customer_id,
            insufficient_history=False,
            avg_amount_30d=round(avg_amount_30d, 2) if avg_amount_30d is not None else None,
            amount_deviation_pct=round(amount_deviation_pct, 1) if amount_deviation_pct is not None else None,
            amount_deviation_relevant=amount_deviation_relevant,
            usual_mcc=usual_mcc,
            current_mcc=current_mcc,
            mcc_unusual=mcc_unusual,
            usual_hour_range=usual_hour_range,
            current_hour=event_dt.strftime("%H:%M"),
            hour_unusual=hour_unusual,
            related_alerts_same_card_30d=related_card,
            related_alerts_same_acceptor_30d=related_acceptor,
            timeline=timeline,
            acceptor_history=acceptor_history,
        )

    # ------------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------------

    def _build_acceptor_history(
        self, acceptor_id: str | None, cutoff: str, before: str,
    ) -> AcceptorHistory:
        """Monta o histórico do estabelecimento (N4): janela, suporte e disponibilidade.

        `support` é o volume de transações observadas no estabelecimento (qualquer
        associado) dentro da janela, anteriores ao caso em investigação — usado para
        distinguir ausência de registros (support == 0, available=False) de um
        histórico efetivamente limpo (support > 0, alerts_count == 0).
        """
        if not acceptor_id:
            return AcceptorHistory(
                window_days=_ACCEPTOR_HISTORY_DAYS, support=0, alerts_count=0, available=False,
            )

        support = self._db.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.acceptor_id == acceptor_id)
            .where(Transaction.event_occurred_at >= cutoff)
            .where(Transaction.event_occurred_at < before)
        ) or 0

        alerts_count = self._count_related_alerts(None, acceptor_id, cutoff, before=before)

        return AcceptorHistory(
            window_days=_ACCEPTOR_HISTORY_DAYS,
            support=support,
            alerts_count=alerts_count,
            available=support > 0,
        )

    def _build_timeline(self, transactions: list[Transaction]) -> list[TimelineEvent]:
        """Monta a linha do tempo com no máximo _TIMELINE_LIMIT entradas (RN-12)."""
        events = []
        for tx in transactions:
            acceptor_name: str | None = None
            if tx.acceptor_id:
                acc = self._db.get(Acceptor, tx.acceptor_id)
                acceptor_name = acc.acceptor_name if acc else tx.acceptor_id
            events.append(TimelineEvent(
                event_occurred_at=tx.event_occurred_at,
                amount=tx.amount,
                acceptor_name=acceptor_name,
                mcc=tx.mcc,
                fraud_score=tx.fraud_score,
            ))
        return events

    def _count_related_alerts(
        self,
        card_id: str | None,
        acceptor_id: str | None,
        cutoff: str,
        before: str | None = None,
    ) -> int:
        """Conta alertas relacionados ao cartão ou estabelecimento nos últimos 30 dias (RN-07).

        `before`, quando informado, exclui o próprio alerta/transação em investigação
        da contagem (evita que o caso se conte como "alerta relacionado" a si mesmo).
        """
        if not card_id and not acceptor_id:
            return 0

        stmt = (
            select(Alert)
            .join(Transaction, Alert.event_external_id == Transaction.event_external_id)
            .where(Alert.generated_at >= cutoff)
        )
        if before:
            stmt = stmt.where(Transaction.event_occurred_at < before)
        if card_id:
            stmt = stmt.where(Transaction.card_id == card_id)
        if acceptor_id:
            stmt = stmt.where(Transaction.acceptor_id == acceptor_id)

        return len(self._db.scalars(stmt).all())

    def _calc_hour_deviation(
        self,
        history: list[Transaction],
        event_dt: datetime,
    ) -> tuple[str | None, bool]:
        """Calcula a faixa horária habitual e verifica se a transação está fora dela."""
        hours = []
        for tx in history:
            try:
                h = datetime.fromisoformat(tx.event_occurred_at.replace("Z", "+00:00")).hour
                hours.append(h)
            except Exception:
                continue

        if not hours:
            return None, False

        avg_hour = sum(hours) / len(hours)
        std_hours = (sum((h - avg_hour) ** 2 for h in hours) / len(hours)) ** 0.5
        low = max(0, int(avg_hour - std_hours))
        high = min(23, int(avg_hour + std_hours) + 1)
        usual_range = f"{low:02d}:00-{high:02d}:59"

        current_hour = event_dt.hour
        hour_unusual = not (low <= current_hour <= high)
        return usual_range, hour_unusual
