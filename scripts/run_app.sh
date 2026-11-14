#!/usr/bin/env bash
#
# run_app.sh - Sobe a Plataforma Fio completa (backend + frontend) com um comando.
#
# O que este script faz:
#   1. Resolve a raiz do projeto a partir da localizacao do proprio script,
#      para funcionar de qualquer diretorio de trabalho.
#   2. Garante o banco SQLite inicializado (python3 -m src.data.init_db, idempotente).
#   3. Sobe o backend FastAPI (uvicorn) na porta 8000 e aguarda GET /health
#      responder antes de prosseguir, para o frontend nao cair em "API offline".
#   4. Sobe o frontend Streamlit na porta 8501 (ja apontando para a API via
#      FIO_API_BASE_URL).
#   5. Faz teardown limpo: ao receber Ctrl+C ou ao sair, encerra os dois processos.
#
# Uso:
#   ./scripts/run_app.sh
#
# Logs:
#   Backend  -> <raiz>/logs/backend.log
#   Frontend -> <raiz>/logs/frontend.log
#

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------
API_HOST="${FIO_API_HOST:-127.0.0.1}"
API_PORT="${FIO_API_PORT:-8000}"
FRONTEND_PORT="${FIO_FRONTEND_PORT:-8501}"

# Quantas vezes checar /health antes de desistir (tentativa a cada 1s).
HEALTH_MAX_ATTEMPTS="${FIO_HEALTH_MAX_ATTEMPTS:-30}"

# python3 explicitamente (ambiente usa pyenv shims).
PYTHON_BIN="${PYTHON_BIN:-python3}"

# ---------------------------------------------------------------------------
# Resolve a raiz do projeto a partir da localizacao do script
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_ROOT}"

LOG_DIR="${PROJECT_ROOT}/logs"
mkdir -p "${LOG_DIR}"
BACKEND_LOG="${LOG_DIR}/backend.log"
FRONTEND_LOG="${LOG_DIR}/frontend.log"

# O frontend le a URL da API daqui (default do app ja e este valor, mas
# deixamos explicito para refletir host/porta configuraveis).
export FIO_API_BASE_URL="http://${API_HOST}:${API_PORT}"

# PIDs dos servicos (preenchidos abaixo).
BACKEND_PID=""
FRONTEND_PID=""

# ---------------------------------------------------------------------------
# Teardown limpo
# ---------------------------------------------------------------------------
cleanup() {
    # Evita reentrancia do trap durante o proprio cleanup.
    trap - INT TERM EXIT

    echo ""
    echo "[run_app] Encerrando servicos..."

    if [[ -n "${FRONTEND_PID}" ]] && kill -0 "${FRONTEND_PID}" 2>/dev/null; then
        echo "[run_app] Parando frontend (PID ${FRONTEND_PID})"
        kill "${FRONTEND_PID}" 2>/dev/null || true
    fi

    if [[ -n "${BACKEND_PID}" ]] && kill -0 "${BACKEND_PID}" 2>/dev/null; then
        echo "[run_app] Parando backend (PID ${BACKEND_PID})"
        kill "${BACKEND_PID}" 2>/dev/null || true
    fi

    # Aguarda os processos terminarem (sem travar se ja sairam).
    [[ -n "${FRONTEND_PID}" ]] && wait "${FRONTEND_PID}" 2>/dev/null || true
    [[ -n "${BACKEND_PID}" ]] && wait "${BACKEND_PID}" 2>/dev/null || true

    echo "[run_app] Tudo encerrado."
}
trap cleanup INT TERM EXIT

# ---------------------------------------------------------------------------
# 1. Inicializa o banco (idempotente)
# ---------------------------------------------------------------------------
echo "[run_app] Projeto: ${PROJECT_ROOT}"
echo "[run_app] Inicializando banco de dados (idempotente)..."
"${PYTHON_BIN}" -m src.data.init_db

# ---------------------------------------------------------------------------
# 2. Sobe o backend (uvicorn) e aguarda ficar saudavel
# ---------------------------------------------------------------------------
echo "[run_app] Subindo backend (uvicorn) em http://${API_HOST}:${API_PORT} ..."
echo "[run_app] Logs do backend: ${BACKEND_LOG}"
"${PYTHON_BIN}" -m uvicorn src.api.main:app \
    --host "${API_HOST}" \
    --port "${API_PORT}" \
    > "${BACKEND_LOG}" 2>&1 &
BACKEND_PID=$!

HEALTH_URL="http://${API_HOST}:${API_PORT}/health"
echo "[run_app] Aguardando a API ficar de pe em ${HEALTH_URL} ..."

healthy=0
for attempt in $(seq 1 "${HEALTH_MAX_ATTEMPTS}"); do
    # Se o backend morreu enquanto esperavamos, falha cedo com log.
    if ! kill -0 "${BACKEND_PID}" 2>/dev/null; then
        echo "[run_app] ERRO: o processo do backend encerrou antes de ficar saudavel." >&2
        echo "[run_app] Ultimas linhas de ${BACKEND_LOG}:" >&2
        tail -n 20 "${BACKEND_LOG}" >&2 || true
        exit 1
    fi

    if curl -sf -o /dev/null "${HEALTH_URL}" 2>/dev/null; then
        healthy=1
        echo "[run_app] API saudavel (tentativa ${attempt})."
        break
    fi

    sleep 1
done

if [[ "${healthy}" -ne 1 ]]; then
    echo "[run_app] ERRO: a API nao respondeu em ${HEALTH_URL} apos ${HEALTH_MAX_ATTEMPTS}s." >&2
    echo "[run_app] Ultimas linhas de ${BACKEND_LOG}:" >&2
    tail -n 20 "${BACKEND_LOG}" >&2 || true
    exit 1
fi

# ---------------------------------------------------------------------------
# 3. Sobe o frontend (streamlit)
# ---------------------------------------------------------------------------
echo "[run_app] Subindo frontend (streamlit) em http://${API_HOST}:${FRONTEND_PORT} ..."
echo "[run_app] Logs do frontend: ${FRONTEND_LOG}"
"${PYTHON_BIN}" -m streamlit run src/frontend/app.py \
    --server.port "${FRONTEND_PORT}" \
    > "${FRONTEND_LOG}" 2>&1 &
FRONTEND_PID=$!

echo ""
echo "[run_app] Aplicacao no ar:"
echo "[run_app]   Backend  (API)      -> http://${API_HOST}:${API_PORT}  (docs em /docs)"
echo "[run_app]   Frontend (Streamlit) -> http://${API_HOST}:${FRONTEND_PORT}"
echo "[run_app] Pressione Ctrl+C para encerrar os dois servicos."
echo ""

# ---------------------------------------------------------------------------
# 4. Mantem o script vivo enquanto os servicos rodam.
#    Se qualquer um dos processos cair, derrubamos o outro via trap EXIT.
#    Usamos polling (nao `wait -n`) para compatibilidade com bash 3.2 (macOS).
# ---------------------------------------------------------------------------
while kill -0 "${BACKEND_PID}" 2>/dev/null && kill -0 "${FRONTEND_PID}" 2>/dev/null; do
    sleep 1
done

echo "[run_app] Um dos servicos encerrou; derrubando o restante."
# O trap EXIT cuida do cleanup final.
