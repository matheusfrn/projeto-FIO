"""
src/frontend/app.py — Interface Streamlit da Plataforma Fio.

Telas:
  1. Lista de Alertas  — GET /alerts (paginada, filtro por status)
  2. Dossie Investigativo — GET /alerts/{id}/dossier (10 secoes)

Regras de interface implementadas:
  RN-03  badge de cor: verde (baixo) / amarelo (medio) / vermelho (alto)
  RN-07  alertas relacionados (cartao/estabelecimento) com janela de 30d
  RN-08  disclaimer sempre visivel no topo da tela de dossie (nunca oculto)
  RN-09  formulario de decisao com 3 valores permitidos
  RN-11  campos mock sinalizados com [SIMULADO]
  RN-18  campo de justificativa livre (D5)
  RN-19  cluster_comparison_status exibido como indisponivel
  N4     historico do estabelecimento com janela, suporte e disponibilidade
"""

import logging
import math
import os
from datetime import date
from urllib.parse import quote

import altair as alt
import pandas as pd
import requests
import streamlit as st

logger = logging.getLogger(__name__)

API_BASE = os.getenv("FIO_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

# ---------------------------------------------------------------------------
# Design tokens
# ---------------------------------------------------------------------------

_CSS = """
<style>
/* ── Web font: carrega Inter de verdade (antes só havia fallback system-ui) ── */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

/* ──────────────────────────────────────────────────────────────────────────
   Design tokens — paleta coerente definida uma única vez.
   Dark theme, accent azul #3b82f6, cores de risco RN-03 (verde/âmbar/vermelho).
   ────────────────────────────────────────────────────────────────────────── */
:root {
    /* Surfaces (neutros ligeiramente mais frios para um ar mais premium) */
    --surface-base:      #0d1016;   /* fundo da app */
    --surface-raised:    #151a24;   /* cartões / blocos */
    --surface-sunken:    #0b0e14;   /* sidebar / trilhos */
    --surface-hover:     #1a2130;   /* hover de linhas */

    /* Borders */
    --border-subtle:     #1e2535;
    --border-faint:      #1a2030;
    --border-strong:     #2d3748;
    --border-accent:     #3d4f68;

    /* Text ramp */
    --text-strong:       #f1f5f9;
    --text-primary:      #e2e8f0;
    --text-secondary:    #cbd5e1;
    --text-muted:        #94a3b8;
    --text-faint:        #64748b;
    --text-dim:          #475569;
    --text-dimmer:       #334155;

    /* Accent (azul primário) */
    --accent:            #3b82f6;
    --accent-light:      #60a5fa;
    --accent-dark:       #2563eb;
    --accent-glow:       rgba(59,130,246,0.35);

    /* Risco — RN-03: verde=baixo / âmbar=medio / vermelho=alto */
    --risk-red:          #f87171;
    --risk-red-core:     #ef4444;
    --risk-amber:        #fbbf24;
    --risk-amber-core:   #f59e0b;
    --risk-green:        #4ade80;
    --risk-green-core:   #22c55e;

    /* Indigo auxiliar (badge simulado / banner info) */
    --indigo:            #818cf8;
    --indigo-light:      #a5b4fc;

    /* Gradiente de marca: o "fio" que conecta (accent azul → indigo) */
    --brand-thread:      linear-gradient(90deg, var(--accent) 0%, var(--indigo) 100%);

    /* Radii */
    --radius-sm:   6px;
    --radius-md:   8px;
    --radius-lg:   12px;

    /* Elevation (sombras multi-camada suaves) */
    --shadow-sm:   0 1px 2px rgba(0,0,0,0.25);
    --shadow-md:   0 1px 2px rgba(0,0,0,0.24), 0 6px 16px rgba(0,0,0,0.18);
    --shadow-lg:   0 2px 6px rgba(0,0,0,0.3), 0 14px 32px rgba(0,0,0,0.26);

    /* Transições */
    --ease:        0.18s cubic-bezier(0.4, 0, 0.2, 1);
}

/* ── Reset & base ── */
[data-testid="stAppViewContainer"] {
    /* Profundidade de fundo: dois brilhos de marca (accent/indigo) nos cantos
       superiores + uma vinheta inferior muito sutil que aprofunda a base. Tudo
       permanece escuro e de baixa opacidade para não reduzir contraste/leitura. */
    background:
        radial-gradient(1200px 620px at 78% -8%, rgba(59,130,246,0.09), transparent 60%),
        radial-gradient(900px 520px at 8% 4%, rgba(99,102,241,0.06), transparent 55%),
        radial-gradient(1400px 900px at 50% 118%, rgba(0,0,0,0.35), transparent 60%),
        var(--surface-base);
    background-attachment: fixed;
    color: var(--text-primary);
}
[data-testid="stHeader"] { background: transparent; }
[data-testid="stSidebar"] { background: var(--surface-sunken); }
/* Largura máxima centralizada do conteúdo (achado #6): mantém o padding-top,
   adiciona max-width confortável e centraliza sem quebrar hero/grids/tabela/dossiê */
.block-container,
[data-testid="stMainBlockContainer"] {
    padding-top: 2.5rem !important;
    max-width: 1320px !important;
    margin-inline: auto !important;
    padding-left: 2.5rem !important;
    padding-right: 2.5rem !important;
}

/* ── Clean UI: remove a "cara de Streamlit padrão" (white-label) ── */
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"] {
    visibility: hidden;
    height: 0;
}

/* ── Typography ── */
html, body, [class*="css"] {
    font-family: "Inter", "SF Pro Display", system-ui, -apple-system, sans-serif;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
    text-rendering: optimizeLegibility;
}
body, [data-testid="stAppViewContainer"] { line-height: 1.6; }
h1, h2, h3, h4, h5, h6 {
    letter-spacing: -0.02em;
    color: var(--text-strong);
    font-weight: 700;
}

/* ── Scrollbar (slim e refinada) ── */
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb {
    background: var(--border-strong);
    border-radius: 6px;
    border: 2px solid transparent;
    background-clip: padding-box;
}
::-webkit-scrollbar-thumb:hover { background: var(--border-accent); background-clip: padding-box; }
* { scrollbar-width: thin; scrollbar-color: var(--border-strong) transparent; }

/* ── Foco acessível ── */
a:focus-visible,
button:focus-visible,
[tabindex]:focus-visible,
input:focus-visible,
textarea:focus-visible,
select:focus-visible {
    outline: 2px solid var(--accent-light) !important;
    outline-offset: 2px !important;
    border-radius: 4px;
}

/* ── Alert table row ── */
.alert-row {
    display: flex;
    align-items: center;
    padding: 14px 16px;
    border-bottom: 1px solid var(--border-faint);
    cursor: pointer;
    transition: background var(--ease), box-shadow var(--ease);
    gap: 20px;
    border-radius: var(--radius-sm);
}
.alert-row:hover {
    background: var(--surface-hover);
    box-shadow: inset 3px 0 0 var(--accent), var(--shadow-sm);
}
a.alert-link, a.alert-link:visited {
    color: inherit;
    text-decoration: none !important;
    display: block;
}
.alert-id-cell { color: var(--accent-light); text-decoration: none !important; }
a.alert-link:hover .alert-id-cell { text-decoration: underline !important; }

/* ── Risk badges ── */
.badge {
    display: inline-flex;
    align-items: center;
    padding: 4px 11px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    white-space: nowrap;
}
.badge-alto    { background: rgba(239,68,68,0.15);  color: var(--risk-red);   border: 1px solid rgba(239,68,68,0.32);  }
.badge-medio   { background: rgba(245,158,11,0.15); color: var(--risk-amber); border: 1px solid rgba(245,158,11,0.32); }
.badge-baixo   { background: rgba(34,197,94,0.15);  color: var(--risk-green); border: 1px solid rgba(34,197,94,0.32);  }
.badge-simulado{ background: rgba(99,102,241,0.12); color: var(--indigo);     border: 1px solid rgba(99,102,241,0.28);}
.badge-novo    { background: rgba(59,130,246,0.12); color: var(--accent-light); border: 1px solid rgba(59,130,246,0.28);}
.badge-enc     { background: rgba(100,116,139,0.15);color: var(--text-muted);  border: 1px solid rgba(100,116,139,0.3);}

/* ── Disclaimer ── */
.disclaimer-bar {
    background: linear-gradient(90deg, rgba(245,158,11,0.10), rgba(245,158,11,0.04));
    border: 1px solid rgba(245,158,11,0.28);
    border-left: 3px solid var(--risk-amber);
    border-radius: var(--radius-md);
    padding: 12px 18px;
    font-size: 13px;
    color: #e2c178;
    line-height: 1.55;
    margin-bottom: 20px;
    box-shadow: var(--shadow-sm);
}

/* ── Premium card depth — usada em métricas, gráficos e blocos de destaque ── */
.premium-card {
    box-shadow: var(--shadow-md);
    transition: transform var(--ease), box-shadow var(--ease), border-color var(--ease);
}
.premium-card:hover {
    transform: translateY(-2px);
    box-shadow: var(--shadow-lg);
    border-color: var(--border-strong) !important;
}

/* ── KPI stat tiles (home) — altura consistente + barra de acento ── */
.stat-card {
    position: relative;
    overflow: hidden;
    border-radius: var(--radius-md);
    padding: 22px 24px;
    min-height: 132px;
    display: flex;
    flex-direction: column;
    justify-content: flex-start;
}
.stat-card-accent {
    position: absolute;
    top: 0;
    left: 0;
    width: 3px;
    height: 100%;
    opacity: 0.8;
    border-radius: var(--radius-md) 0 0 var(--radius-md);
}
/* Card principal (hierarquia): realce discreto para guiar o olhar ao card de
   maior importância ("Valor em Risco"). Fina linha superior âmbar + glow muito
   sutil — percebido como destaque, nunca como alerta de erro. */
.stat-card.is-feature {
    border-top: 2px solid rgba(245,158,11,0.55) !important;
    box-shadow: 0 0 0 1px rgba(245,158,11,0.10), 0 10px 28px rgba(245,158,11,0.08);
}

/* ── Score gauge ── */
.score-block {
    background: var(--surface-raised);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-lg);
    padding: 22px 26px;
    display: flex;
    align-items: center;
    gap: 24px;
    box-shadow: var(--shadow-md);
}
.score-number {
    font-size: 54px;
    font-weight: 800;
    line-height: 1;
    letter-spacing: -2.5px;
    font-variant-numeric: tabular-nums;
}
.score-bar-wrap {
    flex: 1;
    height: 7px;
    background: var(--border-subtle);
    border-radius: 4px;
    overflow: hidden;
}
.score-bar-fill {
    height: 100%;
    border-radius: 4px;
    transition: width 0.5s cubic-bezier(0.4, 0, 0.2, 1);
}

/* ── Deviation card ── */
.dev-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 14px;
}
.dev-card {
    background: var(--surface-raised);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md);
    padding: 16px 18px;
    box-shadow: var(--shadow-sm);
    transition: transform var(--ease), box-shadow var(--ease), border-color var(--ease);
}
.dev-card:hover {
    transform: translateY(-2px);
    box-shadow: var(--shadow-md);
    border-color: var(--border-strong);
}
.dev-card.alert-card {
    border-color: rgba(239,68,68,0.35);
    background: rgba(239,68,68,0.05);
}
.dev-label {
    font-size: 11px;
    color: var(--text-faint);
    text-transform: uppercase;
    letter-spacing: 0.07em;
    margin-bottom: 6px;
}
.dev-value {
    font-size: 23px;
    font-weight: 700;
    color: var(--text-primary);
    line-height: 1.2;
    letter-spacing: -0.02em;
    font-variant-numeric: tabular-nums;
}
.dev-value.warn { color: var(--risk-red); }
.dev-value.ok   { color: var(--risk-green); }
.dev-sub {
    font-size: 11px;
    color: var(--text-faint);
    margin-top: 4px;
}

/* ── Timeline ── */
.timeline-item {
    position: relative;
    display: flex;
    gap: 14px;
    padding: 11px 0;
    border-bottom: 1px solid var(--border-faint);
    align-items: flex-start;
}
/* "Fio" conector: linha vertical contínua atrás dos dots, reforçando a leitura
   cronológica e a metáfora do fio da investigação. Centrada no dot (x≈4px).
   Trim no topo do primeiro item e na base do último para a linha começar/terminar
   exatamente nos nós, sem sobra. Centro do dot ≈ 20px do topo do item. */
.timeline-item::before {
    content: "";
    position: absolute;
    left: 3px;
    top: 0;
    bottom: 0;
    width: 2px;
    background: linear-gradient(180deg, var(--accent) 0%, var(--indigo) 100%);
    opacity: 0.30;
}
.timeline-item:first-child::before { top: 20px; }
.timeline-item:last-child::before  { bottom: calc(100% - 20px); }
.tl-dot {
    position: relative;
    z-index: 1;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--accent-light);
    /* Anel accent sutil em volta do nó, destacando-o sobre o fio. */
    box-shadow: 0 0 0 3px rgba(59,130,246,0.14);
    margin-top: 5px;
    flex-shrink: 0;
}
.tl-date { font-size: 12px; color: var(--text-faint); white-space: nowrap; }
.tl-amount { font-size: 14px; font-weight: 600; color: var(--text-primary); font-variant-numeric: tabular-nums; }
.tl-meta { font-size: 12px; color: var(--text-faint); }

/* ── Section header ── */
.section-label {
    position: relative;
    font-size: 11px;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 0.12em;
    font-weight: 700;
    margin-bottom: 14px;
    padding-bottom: 8px;
    padding-left: 12px;
    border-bottom: 1px solid var(--border-subtle);
    line-height: 1.4;
}
/* Barra esquerda com gradiente de marca (accent → indigo), coerente com o
   motivo de "fio" que percorre a interface. Desenhada via pseudo-elemento
   para não interferir no hairline inferior. */
.section-label::before {
    content: "";
    position: absolute;
    left: 0;
    top: 0;
    bottom: 1px;
    width: 3px;
    border-radius: 2px;
    background: var(--brand-thread);
}
/* Nó no topo da barra: pequeno dot accent que "pontua" o fio. */
.section-label::after {
    content: "";
    position: absolute;
    left: -1px;
    top: -2px;
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--accent-light);
    box-shadow: 0 0 7px var(--accent-glow);
}

/* ── Info grid ── */
.info-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 14px;
}
.info-cell {}
.info-cell-label { font-size: 11px; color: var(--text-dim); margin-bottom: 3px; letter-spacing: 0.02em; }
.info-cell-value { font-size: 14px; color: var(--text-secondary); font-weight: 500; }

/* ── Trace step ── */
.trace-step {
    display: flex;
    gap: 12px;
    padding: 11px 0;
    border-bottom: 1px solid var(--border-faint);
}
.trace-num {
    width: 22px;
    height: 22px;
    border-radius: 50%;
    background: var(--border-subtle);
    color: var(--text-faint);
    font-size: 11px;
    font-weight: 600;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    margin-top: 1px;
}
.trace-source { font-size: 11px; color: var(--accent-light); margin-bottom: 3px; }
.trace-result { font-size: 13px; color: var(--text-muted); }

/* ── Priority banner ── */
.priority-banner {
    border-radius: var(--radius-md);
    padding: 15px 18px;
    font-size: 14px;
    font-weight: 500;
    border: 1px solid;
    border-left-width: 3px;
    margin-bottom: 12px;
    box-shadow: var(--shadow-sm);
}
.priority-alto {
    background: linear-gradient(90deg, rgba(239,68,68,0.10), rgba(239,68,68,0.04));
    border-color: rgba(239,68,68,0.3);
    border-left-color: var(--risk-red);
    color: #fca5a5;
}
.priority-medio {
    background: linear-gradient(90deg, rgba(245,158,11,0.10), rgba(245,158,11,0.04));
    border-color: rgba(245,158,11,0.3);
    border-left-color: var(--risk-amber);
    color: #fde68a;
}
.priority-info {
    background: linear-gradient(90deg, rgba(99,102,241,0.10), rgba(99,102,241,0.04));
    border-color: rgba(99,102,241,0.3);
    border-left-color: var(--indigo);
    color: var(--indigo-light);
}

/* ── Decision panel ── */
.decision-done {
    background: linear-gradient(90deg, rgba(34,197,94,0.09), rgba(34,197,94,0.03));
    border: 1px solid rgba(34,197,94,0.25);
    border-left: 3px solid var(--risk-green);
    border-radius: var(--radius-md);
    padding: 18px 22px;
    box-shadow: var(--shadow-md);
}
.decision-label {
    font-size: 13px;
    color: var(--risk-green);
    font-weight: 600;
    margin-bottom: 6px;
}
.decision-just {
    font-size: 13px;
    color: var(--text-muted);
    line-height: 1.6;
}

/* ── Factor row ── */
.factor-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 9px 0;
    border-bottom: 1px solid var(--border-faint);
}
.factor-dir-up   { color: var(--risk-red);   font-size: 13px; font-weight: 700; }
.factor-dir-down { color: var(--risk-green); font-size: 13px; font-weight: 700; }
.factor-dir-neu  { color: var(--text-faint); font-size: 13px; }
.factor-name { font-size: 13px; color: var(--text-secondary); font-weight: 500; flex: 1; }
.factor-val  { font-size: 12px; color: var(--text-faint); }
.factor-shap { font-size: 12px; color: var(--text-dim); font-family: ui-monospace, "SF Mono", monospace; }

/* ── Top nav bar ── */
.nav-bar {
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 0 0 20px 0;
    border-bottom: 1px solid var(--border-subtle);
    margin-bottom: 24px;
}
.nav-logo {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    font-size: 19px;
    font-weight: 800;
    color: var(--text-strong);
    letter-spacing: -0.6px;
}
.nav-logo .nav-logo-mark {
    display: inline-flex;
    align-items: center;
    color: var(--accent);
    flex-shrink: 0;
}
.nav-logo .nav-logo-text { color: var(--text-strong); }
.nav-logo .nav-logo-text span { color: var(--accent); }
.nav-subtitle {
    font-size: 12px;
    color: var(--text-dim);
    margin-left: auto;
    letter-spacing: 0.01em;
}

/* ── Hero (home) — acento gráfico de assinatura ── */
.hero-thread {
    width: 52px;
    height: 3px;
    border-radius: 2px;
    margin-bottom: 20px;
    background: var(--brand-thread);
    box-shadow: 0 0 10px rgba(59,130,246,0.35);
}
.hero-title {
    /* Gradiente editorial sutil (text-strong → accent-light). Fallback sólido
       em text-strong garante legibilidade caso o navegador não aplique o clip. */
    color: var(--text-strong);
    background: linear-gradient(100deg, var(--text-strong) 62%, var(--accent-light) 100%);
    -webkit-background-clip: text;
    background-clip: text;
    -webkit-text-fill-color: transparent;
}

/* ── Table header ── */
.table-header {
    display: flex;
    padding: 8px 16px;
    font-size: 11px;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 600;
    border-bottom: 1px solid var(--border-subtle);
    gap: 20px;
}
.table-header > div { white-space: nowrap; }

/* ── Pagination ── */
.pagination-info {
    font-size: 13px;
    color: var(--text-muted);
    text-align: center;
    min-width: 140px;
    padding-top: 6px;
    font-variant-numeric: tabular-nums;
}
.pagination-info b { color: var(--text-primary); }

/* ── Empty state ── */
.empty-state {
    text-align: center;
    padding: 60px 20px;
    color: var(--text-dim);
    font-size: 14px;
}

/* ── Override Streamlit defaults ── */
div[data-testid="stButton"] > button,
div[data-testid="stDownloadButton"] > button,
div[data-testid="stLinkButton"] > a {
    background: var(--surface-raised) !important;
    border: 1px solid var(--border-strong) !important;
    color: var(--text-primary) !important;
    border-radius: var(--radius-sm) !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    padding: 7px 16px !important;
    transition: background var(--ease), border-color var(--ease), transform 0.12s ease, box-shadow var(--ease) !important;
    box-shadow: var(--shadow-sm) !important;
}
div[data-testid="stButton"] > button:hover,
div[data-testid="stDownloadButton"] > button:hover,
div[data-testid="stLinkButton"] > a:hover {
    background: #273044 !important;
    border-color: var(--border-accent) !important;
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(0,0,0,0.32) !important;
}
div[data-testid="stButton"] > button:active,
div[data-testid="stDownloadButton"] > button:active {
    transform: translateY(0);
}
div[data-testid="stButton"] > button[kind="primary"] {
    background: var(--accent) !important;
    border: none !important;
    color: white !important;
    font-weight: 600 !important;
    box-shadow: 0 2px 10px var(--accent-glow) !important;
}
div[data-testid="stButton"] > button[kind="primary"]:hover {
    background: var(--accent-dark) !important;
    box-shadow: 0 6px 18px rgba(59,130,246,0.48) !important;
}
div[data-testid="stForm"] {
    background: var(--surface-raised) !important;
    border: 1px solid var(--border-subtle) !important;
    border-radius: var(--radius-lg) !important;
    padding: 22px !important;
    box-shadow: var(--shadow-md) !important;
}
div[data-testid="stSelectbox"] label,
div[data-testid="stTextArea"] label {
    color: var(--text-muted) !important;
    font-size: 13px !important;
    font-weight: 500 !important;
}
div[data-testid="stTextArea"] textarea,
div[data-testid="stSelectbox"] div[data-baseweb="select"] > div {
    border-radius: var(--radius-sm) !important;
    transition: border-color var(--ease), box-shadow var(--ease) !important;
}
div[data-testid="stFormSubmitButton"] > button {
    background: var(--accent) !important;
    border: none !important;
    color: white !important;
    font-weight: 600 !important;
    border-radius: var(--radius-sm) !important;
    padding: 9px 24px !important;
    font-size: 13px !important;
    transition: background var(--ease), transform 0.12s ease, box-shadow var(--ease) !important;
    box-shadow: 0 2px 10px var(--accent-glow) !important;
}
div[data-testid="stFormSubmitButton"] > button:hover {
    background: var(--accent-dark) !important;
    transform: translateY(-1px);
    box-shadow: 0 6px 18px rgba(59,130,246,0.45) !important;
}
div[data-testid="stExpander"],
div[data-testid="stPopover"] > div {
    background: var(--surface-raised) !important;
    border: 1px solid var(--border-subtle) !important;
    border-radius: var(--radius-md) !important;
    box-shadow: var(--shadow-md) !important;
}
div[data-testid="stMetric"] {
    background: var(--surface-raised);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md);
    padding: 14px 16px;
    box-shadow: var(--shadow-sm);
    transition: transform var(--ease), box-shadow var(--ease), border-color var(--ease);
}
div[data-testid="stMetric"]:hover {
    transform: translateY(-1px);
    box-shadow: var(--shadow-md);
    border-color: var(--border-strong);
}

/* ══════════════════════════════════════════════════════════════════════════
   NOVOS OVERRIDES — refinamento dos componentes nativos do Streamlit onde a
   aparência default ainda "vazava". Só estilo; nenhuma lógica Python mudou.
   ══════════════════════════════════════════════════════════════════════════ */

/* ── (#1) Sliders nativos (st.slider) ─────────────────────────────────────
   BaseWeb renderiza trilho grosso + thumb azul default e balões de valor que
   colidem. Afinamos o trilho, refinamos o thumb e tratamos a tipografia dos
   valores. Seletores: container data-testid="stSlider"; internos data-baseweb. */
div[data-testid="stSlider"] label {
    color: var(--text-muted) !important;
    font-size: 13px !important;
    font-weight: 500 !important;
}
/* Oculta o tick bar min/max (redundante com os balões de valor sobre o thumb;
   os testids corretos nesta versão são stSliderTickBar/Min/Max). Remover elimina
   a dupla exibição de "0.00 ... 8500.00" que poluía e sobrepunha os balões. */
div[data-testid="stSlider"] div[data-testid="stSliderTickBar"] {
    display: none !important;
}
/* Thumb refinado: redondo, ~15px, cor de acento; anel de glow no hover/focus.
   O thumb é o div[role="slider"] dentro do data-baseweb="slider". */
div[data-testid="stSlider"] div[role="slider"] {
    background: var(--accent) !important;
    border: 2px solid var(--surface-raised) !important;
    box-shadow: 0 0 0 4px transparent, var(--shadow-sm) !important;
    height: 15px !important;
    width: 15px !important;
    transition: box-shadow var(--ease), transform 0.12s ease !important;
}
div[data-testid="stSlider"] div[role="slider"]:hover,
div[data-testid="stSlider"] div[role="slider"]:focus {
    box-shadow: 0 0 0 5px var(--accent-glow) !important;
    transform: scale(1.05);
}
/* Trilho fino: os divs internos (sem testid) do data-baseweb="slider" formam
   a trilha. Afina a altura para ~4px. */
div[data-testid="stSlider"] div[data-baseweb="slider"] > div > div {
    height: 4px !important;
}
/* Base do trilho (não-preenchido) em borda sutil. */
div[data-testid="stSlider"] div[data-baseweb="slider"] > div:first-child > div:first-child {
    background: var(--border-subtle) !important;
}
/* Faixa preenchida: o BaseWeb pinta o segmento preenchido via background inline.
   Força esse segmento para a cor de acento. */
div[data-testid="stSlider"] div[data-baseweb="slider"] div[style*="background"] {
    background-color: var(--accent) !important;
}
/* Balão de valor sobre o thumb — discreto, números tabulares, sem fundo.
   testid correto nesta versão: stSliderThumbValue. */
div[data-testid="stSliderThumbValue"] {
    color: var(--text-muted) !important;
    font-size: 11px !important;
    font-weight: 600 !important;
    background: transparent !important;
    font-variant-numeric: tabular-nums;
}

/* ── (#2) Banners nativos (st.warning/error/success/info) ──────────────────
   Alinha ao visual do .disclaimer-bar: fundo sutil em gradiente, borda 1px +
   border-left 3px na cor semântica, radius md, 13.5px.
   Estrutura real nesta versão: div[data-testid="stAlert"] >
   div[data-testid="stAlertContainer"][role="alert"][data-baseweb="notification"].
   NÃO existe atributo kind nem seletor de texto para o ícone material, então a
   diferenciação fina por tipo em CSS puro não é possível. Adotamos o padrão
   ÂMBAR (warning) como cor semântica — warning é o tipo dominante na app
   (dossiê gerando + quality_warnings do scoring). */
div[data-testid="stAlert"] {
    border-radius: var(--radius-md) !important;
    box-shadow: var(--shadow-sm) !important;
    overflow: hidden;
}
div[data-testid="stAlert"] [data-testid="stAlertContainer"] {
    background: linear-gradient(90deg, rgba(245,158,11,0.10), rgba(245,158,11,0.04)) !important;
    border: 1px solid rgba(245,158,11,0.28) !important;
    border-left: 3px solid var(--risk-amber) !important;
    border-radius: var(--radius-md) !important;
    padding: 12px 16px !important;
    font-size: 13.5px !important;
    line-height: 1.55 !important;
    color: #e2c178 !important;
    box-shadow: none !important;
}
/* Ícone material nativo: discreto, sem brilho default. */
div[data-testid="stAlert"] [data-testid="stAlertContainer"] svg,
div[data-testid="stAlert"] [data-testid="stAlertContainer"] span[data-testid="stIconMaterial"] {
    opacity: 0.85;
}

/* ── (#3) Inputs de texto (st.text_input) ─────────────────────────────────
   Inclui a busca no topo da lista. Coerente com o textarea já estilizado.
   Seletor: data-testid="stTextInput" > input. */
div[data-testid="stTextInput"] label {
    color: var(--text-muted) !important;
    font-size: 13px !important;
    font-weight: 500 !important;
}
div[data-testid="stTextInput"] input,
div[data-testid="stTextInput"] div[data-baseweb="input"],
div[data-testid="stTextInput"] div[data-baseweb="base-input"] {
    background: var(--surface-raised) !important;
    border-radius: var(--radius-sm) !important;
    color: var(--text-primary) !important;
}
div[data-testid="stTextInput"] div[data-baseweb="input"],
div[data-testid="stTextInput"] div[data-baseweb="base-input"] {
    border: 1px solid var(--border-subtle) !important;
    transition: border-color var(--ease), box-shadow var(--ease) !important;
}
div[data-testid="stTextInput"] input {
    border: none !important;
    padding: 10px 12px !important;
    font-size: 14px !important;
}
div[data-testid="stTextInput"] input::placeholder {
    color: var(--text-faint) !important;
}
div[data-testid="stTextInput"] div[data-baseweb="input"]:focus-within,
div[data-testid="stTextInput"] div[data-baseweb="base-input"]:focus-within {
    border-color: var(--accent) !important;
    box-shadow: 0 0 0 3px var(--accent-glow) !important;
}
/* Mantém o textarea já estilizado coerente com os novos inputs */
div[data-testid="stTextArea"] textarea {
    background: var(--surface-raised) !important;
    border: 1px solid var(--border-subtle) !important;
    color: var(--text-primary) !important;
}
div[data-testid="stTextArea"] textarea::placeholder {
    color: var(--text-faint) !important;
}
div[data-testid="stTextArea"] textarea:focus {
    border-color: var(--accent) !important;
    box-shadow: 0 0 0 3px var(--accent-glow) !important;
}

/* ── (#4) Multiselect + corpo do selectbox + menus dropdown ────────────────
   Harmoniza controle fechado e menu aberto. Seletores: stMultiSelect /
   stSelectbox para o controle; data-baseweb popover/menu/listbox para o menu. */
div[data-testid="stMultiSelect"] label,
div[data-testid="stSelectbox"] label {
    color: var(--text-muted) !important;
    font-size: 13px !important;
    font-weight: 500 !important;
}
/* Corpo do controle (fechado) — select e multiselect */
div[data-testid="stSelectbox"] div[data-baseweb="select"] > div,
div[data-testid="stMultiSelect"] div[data-baseweb="select"] > div {
    background: var(--surface-raised) !important;
    border: 1px solid var(--border-subtle) !important;
    border-radius: var(--radius-sm) !important;
    color: var(--text-primary) !important;
    transition: border-color var(--ease), box-shadow var(--ease) !important;
}
div[data-testid="stSelectbox"] div[data-baseweb="select"] > div:focus-within,
div[data-testid="stMultiSelect"] div[data-baseweb="select"] > div:focus-within {
    border-color: var(--accent) !important;
    box-shadow: 0 0 0 3px var(--accent-glow) !important;
}
/* Chips/tags do multiselect */
div[data-testid="stMultiSelect"] span[data-baseweb="tag"] {
    background: rgba(59,130,246,0.16) !important;
    color: var(--accent-light) !important;
    border: 1px solid rgba(59,130,246,0.32) !important;
    border-radius: var(--radius-sm) !important;
}
div[data-testid="stMultiSelect"] span[data-baseweb="tag"] svg {
    fill: var(--accent-light) !important;
}
/* Menu dropdown BaseWeb (portal/popover) — compartilhado por select/multiselect/date */
div[data-baseweb="popover"] ul[role="listbox"],
div[data-baseweb="popover"] div[data-baseweb="menu"],
ul[data-baseweb="menu"] {
    background: var(--surface-raised) !important;
    border: 1px solid var(--border-subtle) !important;
    border-radius: var(--radius-md) !important;
    box-shadow: var(--shadow-lg) !important;
}
/* Itens do menu (#9: hover/selecionado com accent) */
li[role="option"] {
    background: transparent !important;
    color: var(--text-secondary) !important;
    transition: background var(--ease), color var(--ease) !important;
}
li[role="option"]:hover,
li[role="option"][aria-selected="true"] {
    background: var(--surface-hover) !important;
    color: var(--accent-light) !important;
}

/* ── (#5) date_input (st.date_input) ──────────────────────────────────────
   Campo coerente com os demais inputs; popover do calendário com superfície
   elevada. Seletor: data-testid="stDateInput" + data-baseweb="calendar". */
div[data-testid="stDateInput"] label {
    color: var(--text-muted) !important;
    font-size: 13px !important;
    font-weight: 500 !important;
}
div[data-testid="stDateInput"] div[data-baseweb="input"],
div[data-testid="stDateInput"] div[data-baseweb="base-input"] {
    background: var(--surface-raised) !important;
    border: 1px solid var(--border-subtle) !important;
    border-radius: var(--radius-sm) !important;
    transition: border-color var(--ease), box-shadow var(--ease) !important;
}
div[data-testid="stDateInput"] input {
    background: transparent !important;
    border: none !important;
    color: var(--text-primary) !important;
    padding: 10px 12px !important;
    font-size: 14px !important;
}
div[data-testid="stDateInput"] div[data-baseweb="input"]:focus-within,
div[data-testid="stDateInput"] div[data-baseweb="base-input"]:focus-within {
    border-color: var(--accent) !important;
    box-shadow: 0 0 0 3px var(--accent-glow) !important;
}
/* Popover do calendário */
div[data-baseweb="calendar"],
div[data-baseweb="popover"] div[data-baseweb="calendar"] {
    background: var(--surface-raised) !important;
    border: 1px solid var(--border-subtle) !important;
    border-radius: var(--radius-md) !important;
    box-shadow: var(--shadow-lg) !important;
}
div[data-baseweb="calendar"] [aria-selected="true"],
div[data-baseweb="calendar"] [role="gridcell"]:hover {
    background: var(--accent) !important;
    color: #fff !important;
}

/* ── (#7) ::selection ─────────────────────────────────────────────────────*/
::selection {
    background: rgba(59,130,246,0.3);
    color: var(--text-strong);
}

/* ── (#8) Spinner (st.spinner) ────────────────────────────────────────────*/
div[data-testid="stSpinner"] i,
div[data-testid="stSpinner"] svg circle {
    border-top-color: var(--accent) !important;
    stroke: var(--accent) !important;
}
div[data-testid="stSpinner"] > div {
    color: var(--text-muted) !important;
    font-size: 13px !important;
}
</style>
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Logomark "Fio": um fio/curva suave passando por 3 nós (pontos), evocando a
# metáfora de conectar/traçar evidências numa investigação. SVG inline em
# currentColor (herda --accent via .nav-logo-mark), ~22px, sem asset externo.
_NAV_LOGO_MARK_SVG = (
    '<svg class="nav-logo-mark" width="22" height="22" viewBox="0 0 24 24" fill="none" '
    'xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">'
    '<path d="M3 16.5 C7 16.5, 8 7.5, 12 7.5 C16 7.5, 17 16.5, 21 16.5" '
    'stroke="currentColor" stroke-width="1.75" stroke-linecap="round" fill="none" '
    'opacity="0.9"/>'
    '<circle cx="3" cy="16.5" r="1.9" fill="currentColor"/>'
    '<circle cx="12" cy="7.5" r="2.1" fill="currentColor"/>'
    '<circle cx="21" cy="16.5" r="1.9" fill="currentColor"/>'
    '</svg>'
)


def _nav_logo_html() -> str:
    """HTML do wordmark da marca para a nav-bar. Antepõe ao wordmark 'Fio.' um
    pequeno glifo SVG (o "fio" conectando 3 nós) e mantém o ponto em azul accent.
    Coerente com o design-system e sem depender de nenhum asset externo."""
    return f'<div class="nav-logo">{_NAV_LOGO_MARK_SVG}<span class="nav-logo-text">Fio<span>.</span></span></div>'

_RISK_COLOR = {
    "alto":  ("#f87171", "#ef4444"),
    "medio": ("#fbbf24", "#f59e0b"),
    "baixo": ("#4ade80", "#22c55e"),
}

_DECISION_LABELS = {
    "fraude_confirmada": "Fraude Confirmada",
    "legitima":          "Transação Legítima",
    "em_analise":        "Em Análise",
}

_DECISION_BADGE_CLASS = {
    "fraude_confirmada": "badge-alto",
    "legitima": "badge-baixo",
    "em_analise": "badge-novo",
}

_DECISION_SHORT_LABELS = {
    "fraude_confirmada": "Fraude Confirmada",
    "legitima": "Legítima",
    "em_analise": "Em Análise",
}


def _badge_decision(decision: str | None) -> str:
    if not decision:
        return ""
    cls = _DECISION_BADGE_CLASS.get(decision, "badge-enc")
    label = _DECISION_SHORT_LABELS.get(decision, decision)
    return f'<span class="badge {cls}">{label}</span>'

_PRIORITY_MAP = {
    "valor_atipico":             ("Valor Atípico",                  "alto"),
    "mcc_incomum":               ("Categoria de Estabelecimento Incomum (MCC)", "medio"),
    "hora_incomum":              ("Horário Incomum",                "medio"),
    "estabelecimento_historico": ("Histórico no Estabelecimento",   "medio"),
    "sem_historico_suficiente":  ("Histórico Insuficiente",         "info"),
}


_RISK_LABELS = {"alto": "Alto", "medio": "Médio", "baixo": "Baixo"}


_CATEGORY_LABELS = {
    "credito": "Crédito",
    "debito": "Débito",
    "presencial": "Presencial",
    "online": "Online",
    "cartao_presente": "Cartão presente",
    "cartao_nao_presente": "Cartão não presente",
    "ecommerce": "E-commerce",
    "pos": "POS",
    "chip": "Chip",
}


def _fmt_category(v) -> str:
    """Humaniza um valor categórico snake_case (ex.: cartao_presente -> Cartão presente),
    para consistência com os mesmos valores já humanizados em Fatores Relevantes."""
    if v is None or v == "":
        return "—"
    key = str(v).strip().lower()
    if key in _CATEGORY_LABELS:
        return _CATEGORY_LABELS[key]
    text = key.replace("_", " ")
    return text[:1].upper() + text[1:] if text else "—"


def _fmt_risk_label(v: str | None) -> str:
    """Formata um nível de risco textual (ex.: customer_risk_rating) de forma consistente
    com os badges de risco exibidos no restante da interface."""
    if not v:
        return "—"
    return _RISK_LABELS.get(v.lower(), v.replace("_", " ").strip().capitalize())


def _badge(risk: str | None) -> str:
    if not risk:
        return ""
    cls = f"badge badge-{risk}"
    return f'<span class="{cls}">{_RISK_LABELS.get(risk, risk)}</span>'


def _status_badge(status: str | None) -> str:
    if not status:
        return ""
    s = (status or "").upper()
    if s == "NOVO":
        return '<span class="badge badge-novo">Novo</span>'
    if s == "ENCERRADO":
        return '<span class="badge badge-enc">Encerrado</span>'
    return f'<span class="badge badge-enc">{status}</span>'


def _fmt_brl(v) -> str:
    if v is None:
        return "—"
    try:
        return f"R$ {float(v):,.2f}"
    except Exception:
        return str(v)


def _fmt_dt(s: str | None) -> str:
    if not s:
        return "—"
    return s[:16].replace("T", "  ")


def _fmt_code(v) -> str:
    """Remove sufixo '.0' de códigos numéricos armazenados como texto (ex.: MCC)."""
    if v is None:
        return "—"
    s = str(v)
    if s.endswith(".0"):
        try:
            return str(int(float(s)))
        except Exception:
            return s
    return s


_FEATURE_LABELS = {
    "amount": "Valor da Transação",
    "modalidade_transacao": "Modalidade da Transação",
    "tipo_transacao": "Tipo de Transação",
    "pos_environment": "Ambiente do Ponto de Venda",
    "currency_code": "Moeda da Transação",
    "mcc": "Categoria do Estabelecimento (MCC)",
    "fraud_score": "Score Externo (Bandeira)",
    "visa_fraud_score": "Score Externo Visa",
    "mc_fraud_score": "Score Externo Mastercard",
    "card_presence_flag": "Cartão Presente",
    "cvv2_presence_indicator": "CVV Informado",
    "device_ip_country_code": "País do IP do Dispositivo",
    "customer_risk_rating": "Classificação de Risco do Associado",
    "customer_segment": "Segmento do Associado",
    "acceptor_risk": "Risco do Estabelecimento",
}


def _feature_label(feature: str) -> str:
    """Traduz o nome técnico da feature para um rótulo legível ao analista."""
    if feature in _FEATURE_LABELS:
        return _FEATURE_LABELS[feature]
    return feature.replace("_", " ").strip().capitalize()


def _fmt_factor_value(feature: str, value) -> str:
    """Formata o valor de um fator SHAP para exibição (sem sufixos '.0' crus)."""
    if value is None:
        return "—"
    if feature == "amount":
        return _fmt_brl(value)
    if isinstance(value, bool):
        return "Sim" if value else "Não"
    if isinstance(value, float):
        return str(int(value)) if value == int(value) else f"{value:.2f}"
    if isinstance(value, str):
        return _fmt_category(value)
    return str(value)


# Sentinel distinto de None: sinaliza "API inalcançável" (sem conexão), separando
# essa causa de uma resposta HTTP válida sem dado útil (ex.: 404 — não encontrado).
# As telas chamadoras usam isso para não confundir "API fora do ar" com "não existe".
_CONN_ERROR = object()


def _get(path: str, params: dict | None = None):
    """Busca dados da API.

    Retorna o JSON em caso de sucesso, `_CONN_ERROR` se a API estiver inalcançável,
    ou None para qualquer outra resposta não-2xx (ex.: 404). A tela chamadora decide
    a mensagem — nunca expõe o erro técnico cru ao usuário.
    """
    try:
        r = requests.get(f"{API_BASE}{path}", params=params, timeout=12)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 202:
            return {"_generating": True}
        logger.warning("GET %s retornou status %s", path, r.status_code)
        return None
    except requests.exceptions.RequestException:
        logger.exception("Erro de conexão com a API em GET %s", path)
        return _CONN_ERROR


def _api_offline_banner(detail: str = "Verifique se o servidor está rodando em localhost:8000.") -> None:
    """Banner padrão de API indisponível — visual único reutilizado em todas as telas."""
    st.markdown(
        f'<div style="padding:14px 16px;background:rgba(239,68,68,0.06);'
        f'border:1px solid rgba(239,68,68,0.25);border-radius:6px;'
        f'color:#fca5a5;font-size:13px;">'
        f'<strong>API indisponível.</strong> {detail}</div>',
        unsafe_allow_html=True,
    )


def _alert_html(msg: str) -> str:
    """Prepara a mensagem para render HTML: escapa o essencial e converte trechos
    entre crases (`...`) — que no st.alert viravam código inline — em <code>…</code>.
    As mensagens são controladas (sem input de usuário), então o escaping é básico."""
    def _esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    out: list[str] = []
    parts = msg.split("`")
    for i, part in enumerate(parts):
        if i % 2 == 1:
            # trecho que estava entre crases → código inline
            out.append(f"<code>{_esc(part)}</code>")
        else:
            out.append(_esc(part))
    return "".join(out)


def _alert_error(msg: str) -> None:
    """Banner de ERRO (vermelho) — mesmo visual do .disclaimer-bar, cor semântica de risco alto."""
    st.markdown(
        f'<div style="background:linear-gradient(90deg,rgba(239,68,68,0.10),rgba(239,68,68,0.04));'
        f'border:1px solid rgba(239,68,68,0.28);border-left:3px solid var(--risk-red);'
        f'border-radius:var(--radius-md);padding:13px 16px;font-size:13.5px;line-height:1.55;'
        f'color:#fca5a5;box-shadow:var(--shadow-sm);margin-bottom:14px;">'
        f'{_alert_html(msg)}</div>',
        unsafe_allow_html=True,
    )


def _alert_success(msg: str) -> None:
    """Banner de SUCESSO (verde) — mesmo visual do .disclaimer-bar, cor semântica de risco baixo."""
    st.markdown(
        f'<div style="background:linear-gradient(90deg,rgba(34,197,94,0.10),rgba(34,197,94,0.04));'
        f'border:1px solid rgba(34,197,94,0.28);border-left:3px solid var(--risk-green);'
        f'border-radius:var(--radius-md);padding:13px 16px;font-size:13.5px;line-height:1.55;'
        f'color:#86efac;box-shadow:var(--shadow-sm);margin-bottom:14px;">'
        f'{_alert_html(msg)}</div>',
        unsafe_allow_html=True,
    )


def _alert_info(msg: str) -> None:
    """Banner INFORMATIVO (indigo) — mesmo visual do .disclaimer-bar, cor auxiliar indigo."""
    st.markdown(
        f'<div style="background:linear-gradient(90deg,rgba(99,102,241,0.12),rgba(99,102,241,0.05));'
        f'border:1px solid rgba(99,102,241,0.28);border-left:3px solid var(--indigo);'
        f'border-radius:var(--radius-md);padding:13px 16px;font-size:13.5px;line-height:1.55;'
        f'color:var(--indigo-light);box-shadow:var(--shadow-sm);margin-bottom:14px;">'
        f'{_alert_html(msg)}</div>',
        unsafe_allow_html=True,
    )


def _goto_alert_list(status: list[str] | None = None, risk: list[str] | None = None) -> None:
    """Navega para a lista de alertas pré-filtrada — fecha o ciclo insight → ação dos KPIs da home."""
    fv = st.session_state.get("alerts_filters_version", 0)
    if status is not None:
        st.session_state[f"alerts_status_ms_{fv}"] = status
    if risk is not None:
        st.session_state[f"alerts_risk_ms_{fv}"] = risk
    st.session_state["alerts_page"] = 1
    st.session_state["page"] = "list"
    st.rerun()


def _post(path: str, body: dict) -> dict | None:
    """Envia dados à API. Em falha, exibe uma mensagem amigável (nunca o corpo
    da resposta ou a exceção crua) e registra o detalhe técnico no log do servidor."""
    try:
        r = requests.post(f"{API_BASE}{path}", json=body, timeout=12)
        if r.status_code in (200, 201):
            return r.json()
        if r.status_code == 422:
            _alert_error("Não foi possível registrar a decisão — verifique os campos do formulário.")
        else:
            _alert_error("Não foi possível registrar a decisão. Tente novamente em instantes.")
        logger.warning("POST %s retornou status %s: %s", path, r.status_code, r.text)
        return None
    except requests.exceptions.RequestException:
        _alert_error("Erro de conexão com a API. Verifique se o servidor está rodando em localhost:8000.")
        logger.exception("Erro de conexão com a API em POST %s", path)
        return None


# ---------------------------------------------------------------------------
# Tela 1 — Lista de Alertas
# ---------------------------------------------------------------------------

def page_alert_list() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)

    # Nav
    col_nav, col_title = st.columns([1.4, 8.6])
    with col_nav:
        if st.button("← Home"):
            st.session_state["page"] = "home"
            st.rerun()
    with col_title:
        st.markdown(
            '<div class="nav-bar">'
            f'{_nav_logo_html()}'
            '<div class="nav-subtitle">Plataforma de Apoio à Investigação de Fraudes &mdash; Sicredi</div>'
            '</div>',
            unsafe_allow_html=True,
        )

    if "alerts_page" not in st.session_state:
        st.session_state["alerts_page"] = 1

    # Bounds conhecidos da base (case sintético Sicredi, transações alertadas de 2025)
    _AMOUNT_BOUNDS = (0.0, 8500.0)
    _SCORE_BOUNDS = (0, 999)
    _DATE_BOUNDS = (date(2025, 1, 1), date(2025, 12, 31))

    # Sufixo de versão nas keys dos widgets de filtro: popar session_state não é
    # suficiente para "resetar" sliders/date_input visualmente neste Streamlit —
    # o widget web mantém a posição antiga mesmo com o valor já limpo no backend.
    # Trocar a key força o remount do componente com o `value` default correto.
    if "alerts_filters_version" not in st.session_state:
        st.session_state["alerts_filters_version"] = 0
    fv = st.session_state["alerts_filters_version"]

    _SORT_OPTIONS = {
        "score_desc": "Score externo (maior primeiro)",
        "date_desc": "Data (mais recente)",
        "date_asc": "Data (mais antiga)",
        "amount_desc": "Valor (maior primeiro)",
        "amount_asc": "Valor (menor primeiro)",
    }

    # ── Busca livre ───────────────────────────────────────────────────────
    search_query = st.text_input(
        "Buscar",
        placeholder="Buscar por ID do alerta, ID/nome do associado ou nome do estabelecimento...",
        key=f"alerts_search_{fv}",
        label_visibility="collapsed",
        help="Busca livre por ID do alerta (ex.: ALERT_9243...), ID ou nome do associado, "
             "ou nome do estabelecimento. Combina com os demais filtros abaixo.",
    )

    # ── Filtros principais ──────────────────────────────────────────────
    col_status, col_risk, col_sort, col_size, col_clear = st.columns([1.9, 1.7, 1.9, 1.1, 1.3])
    with col_clear:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        if st.button("Limpar filtros", key="alerts_clear_filters", use_container_width=True,
                     help="Remove todos os filtros aplicados (busca, status, risco, período, valor e score) e volta para a página 1."):
            st.session_state["alerts_filters_version"] += 1
            st.session_state["alerts_page"] = 1
            st.rerun()
    with col_status:
        status_filter = st.multiselect(
            "Status", options=["NOVO", "EM_ANALISE", "ENCERRADO"], default=[],
            placeholder="Todos", key=f"alerts_status_ms_{fv}",
            help="Filtra por status do alerta. Deixe vazio para ver todos os status.",
        )
    with col_risk:
        risk_filter = st.multiselect(
            "Risco", options=["alto", "medio", "baixo"], default=[],
            format_func=lambda r: {"alto": "Alto", "medio": "Médio", "baixo": "Baixo"}[r],
            placeholder="Todos", key=f"alerts_risk_ms_{fv}",
            help="Nível de risco calculado a partir do score externo da bandeira (escala 0–999): "
                 "baixo < 40%, médio entre 40–70%, alto ≥ 70%. Deixe vazio para ver todos.",
        )
    with col_sort:
        sort_key = st.selectbox(
            "Ordenar por", options=list(_SORT_OPTIONS.keys()), format_func=lambda k: _SORT_OPTIONS[k],
            index=0, key=f"alerts_sort_{fv}",
            help="Define a ordem dos resultados. Por padrão, os casos de maior risco aparecem primeiro.",
        )
    with col_size:
        size = st.selectbox(
            "Itens por página", options=[20, 50, 100], index=0, key=f"alerts_page_size_{fv}",
            help="Quantidade de alertas exibidos em cada página da lista.",
        )

    # ── Filtros avançados (período, valor, score) ────────────────────────
    with st.expander("Filtros avançados — período, valor e score externo"):
        col_date, col_amount, col_score = st.columns(3)
        with col_date:
            date_range = st.date_input(
                "Período (data do evento)",
                value=_DATE_BOUNDS, min_value=_DATE_BOUNDS[0], max_value=_DATE_BOUNDS[1],
                key=f"alerts_date_range_{fv}",
                help="Filtra pela data em que a transação suspeita ocorreu.",
            )
        with col_amount:
            amount_range = st.slider(
                "Valor da transação (R$)",
                min_value=_AMOUNT_BOUNDS[0], max_value=_AMOUNT_BOUNDS[1], value=_AMOUNT_BOUNDS, step=50.0,
                key=f"alerts_amount_range_{fv}",
                help="Recorta os alertas pelo valor (R$) da transação suspeita.",
            )
        with col_score:
            score_range = st.slider(
                "Score Externo (0–999)",
                min_value=_SCORE_BOUNDS[0], max_value=_SCORE_BOUNDS[1], value=_SCORE_BOUNDS, step=1,
                key=f"alerts_score_range_{fv}",
                help="Score de risco calculado pela bandeira do cartão (0–999) no momento da transação "
                     "— não é o score do modelo Fio, calculado apenas dentro do dossiê.",
            )

    # Mudar qualquer filtro, ordenação ou o tamanho de página sempre volta para a página 1
    filter_signature = (search_query, tuple(status_filter), tuple(risk_filter), sort_key, size, date_range, amount_range, score_range)
    if st.session_state.get("alerts_filter_signature") != filter_signature:
        st.session_state["alerts_filter_signature"] = filter_signature
        st.session_state["alerts_page"] = 1
        st.session_state.pop("alerts_csv_bytes", None)

    page = st.session_state["alerts_page"]
    params: dict = {"page": page, "size": size, "sort": sort_key}
    if search_query:
        params["q"] = search_query.strip()
    if status_filter:
        params["status"] = status_filter
    if risk_filter:
        params["risk"] = risk_filter

    active_advanced: list[str] = []
    if isinstance(date_range, tuple) and len(date_range) == 2 and date_range != _DATE_BOUNDS:
        params["date_from"] = date_range[0].isoformat()
        params["date_to"] = f"{date_range[1].isoformat()}T23:59:59"
        active_advanced.append("Período")
    if amount_range != _AMOUNT_BOUNDS:
        params["amount_min"], params["amount_max"] = amount_range
        active_advanced.append("Valor")
    if score_range != _SCORE_BOUNDS:
        params["score_min"], params["score_max"] = score_range
        active_advanced.append("Score")

    with st.spinner("Carregando alertas..."):
        data = _get("/alerts", params=params)
    if data is None or data is _CONN_ERROR:
        _api_offline_banner()
        return

    items = data.get("items", [])
    total = data.get("total", 0)
    aggregates = data.get("aggregates") or {}
    total_pages = max(1, math.ceil(total / size)) if size else 1

    # Se o filtro reduziu o total de páginas, volta para a última válida
    if page > total_pages:
        st.session_state["alerts_page"] = total_pages
        st.rerun()

    range_start = (page - 1) * size + 1 if items else 0
    range_end = range_start + len(items) - 1 if items else 0

    def _pagination_bar(key_prefix: str) -> None:
        st.markdown('<div style="margin-top:6px"></div>', unsafe_allow_html=True)
        col_l, col_prev, col_info, col_next, col_r = st.columns([2, 1.5, 1.8, 1.5, 2])
        with col_prev:
            if st.button("← Anterior", disabled=(page <= 1),
                         use_container_width=True, key=f"{key_prefix}_prev"):
                st.session_state["alerts_page"] = page - 1
                st.rerun()
        with col_info:
            st.markdown(
                f'<div class="pagination-info">Página <b>{page}</b> de <b>{total_pages:,}</b></div>',
                unsafe_allow_html=True,
            )
        with col_next:
            if st.button("Próxima →", disabled=(page >= total_pages),
                         use_container_width=True, key=f"{key_prefix}_next"):
                st.session_state["alerts_page"] = page + 1
                st.rerun()

    # Cabecalho da tabela
    advanced_note = (
        f' &nbsp;·&nbsp; <span style="color:#818cf8">Filtros avançados ativos: {", ".join(active_advanced)}</span>'
        if active_advanced else ""
    )
    st.markdown(f"""
    <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:16px;">
        <div style="font-size:14px; color:#cbd5e1; font-weight:600;"><span style="color:#f1f5f9">{total:,}</span> alertas encontrados{advanced_note}</div>
        <div style="font-size:11px; color:#64748b; font-variant-numeric:tabular-nums;">Mostrando {range_start:,}&ndash;{range_end:,} de {total:,} &nbsp;·&nbsp; Página {page} de {total_pages:,}</div>
    </div>
    """, unsafe_allow_html=True)

    # ── Agregados do recorte filtrado + exportação ────────────────────────
    if items:
        col_agg, col_export = st.columns([4, 1.4])
        with col_agg:
            agg_value = _fmt_brl(aggregates.get("total_value"))
            agg_score = aggregates.get("avg_score")
            agg_score_str = f"{agg_score:.0f}/999" if agg_score is not None else "—"
            st.markdown(
                f'<div class="premium-card" style="font-size:12px; color:#94a3b8; padding:12px 16px; background:#141822; '
                f'border:1px solid #1e2535; border-radius:8px;">'
                f'<strong style="color:#f1f5f9">Valor total filtrado:</strong> {agg_value} '
                f'&nbsp;·&nbsp; <strong style="color:#f1f5f9">Score externo médio:</strong> {agg_score_str} '
                f'<span style="color:#64748b">(calculado sobre todos os {total:,} alertas filtrados, não só a página atual)</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
        with col_export:
            export_params = {k: v for k, v in params.items() if k not in ("page", "size")}
            if st.button("Preparar CSV", key="alerts_prepare_csv", use_container_width=True,
                         help="Gera um CSV com todos os alertas filtrados (não só a página exibida) para baixar em seguida."):
                try:
                    with st.spinner("Gerando CSV..."):
                        r = requests.get(f"{API_BASE}/alerts/export", params=export_params, timeout=30)
                    if r.status_code == 200:
                        st.session_state["alerts_csv_bytes"] = r.content
                    else:
                        _alert_error("Não foi possível gerar o CSV. Tente novamente.")
                except requests.exceptions.RequestException:
                    _alert_error("Erro de conexão ao gerar o CSV.")
            if st.session_state.get("alerts_csv_bytes"):
                st.download_button(
                    "Baixar CSV", data=st.session_state["alerts_csv_bytes"],
                    file_name="alertas_fio.csv", mime="text/csv",
                    key="alerts_download_csv", use_container_width=True,
                )
                mail_subject = quote(f"Fio — Exportação de alertas ({total:,} registros)")
                mail_body = quote(
                    "Segue o resumo da exportação de alertas da Plataforma Fio.\n\n"
                    f"Total de registros: {total:,}\n"
                    f"Valor total filtrado: {agg_value}\n"
                    f"Score externo médio: {agg_score_str}\n\n"
                    "IMPORTANTE: anexe manualmente o arquivo alertas_fio.csv já baixado. "
                    "Por limitação de segurança dos navegadores, não é possível anexar "
                    "arquivos automaticamente a um rascunho de e-mail a partir de uma página web."
                )
                st.link_button(
                    "Enviar por e-mail", url=f"mailto:?subject={mail_subject}&body={mail_body}",
                    use_container_width=True,
                    help="Abre um rascunho de e-mail no seu cliente padrão, com o resumo pronto. "
                         "Anexe o CSV baixado manualmente — navegadores não permitem anexar "
                         "arquivos automaticamente via mailto.",
                )
        st.markdown("<br>", unsafe_allow_html=True)

    st.markdown("""
    <div class="table-header">
        <div style="flex:1.5">ID do Alerta</div>
        <div style="flex:2.5">Associado</div>
        <div style="flex:1.4; text-align:right">Score Externo</div>
        <div style="flex:1.3; text-align:right">Valor</div>
        <div style="flex:2">Data / Hora</div>
        <div style="flex:1">Risco</div>
        <div style="flex:1">Status</div>
    </div>
    """, unsafe_allow_html=True)

    if not items:
        st.markdown(
            '<div class="empty-state">Nenhum alerta encontrado com os filtros aplicados.<br>'
            '<span style="font-size:12px;">Tente ampliar o período, o intervalo de valor/score ou use '
            '<strong>Limpar filtros</strong> acima.</span></div>',
            unsafe_allow_html=True,
        )
        return

    rows_html = ""
    for item in items:
        alert_id   = item.get("alert_id", "")
        short_id   = alert_id[-12:] if len(alert_id) > 12 else alert_id
        name       = item.get("customer_name") or item.get("customer_id") or "—"
        amount     = _fmt_brl(item.get("amount"))
        dt         = _fmt_dt(item.get("event_occurred_at"))
        risk       = item.get("risk_level")
        status     = item.get("status")
        fs_raw     = item.get("fraud_score")
        fs_disp    = (
            f'{fs_raw:.0f}<span style="color:#475569">/999</span>'
            if fs_raw is not None else "—"
        )

        rows_html += f"""
        <a class="alert-link" href="?page=dossier&alert_id={alert_id}" target="_self">
            <div class="alert-row">
                <div class="alert-id-cell" style="flex:1.5; font-family:monospace; font-size:12px;">{short_id}</div>
                <div style="flex:2.5; font-size:13px; color:#cbd5e1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis">{name}</div>
                <div style="flex:1.4; text-align:right; font-size:13px; color:#94a3b8; font-family:monospace; font-variant-numeric:tabular-nums">{fs_disp}</div>
                <div style="flex:1.3; text-align:right; font-size:13px; color:#e2e8f0; font-weight:600; font-variant-numeric:tabular-nums">{amount}</div>
                <div style="flex:2; font-size:12px; color:#64748b; font-family:monospace; font-variant-numeric:tabular-nums">{dt}</div>
                <div style="flex:1">{_badge(risk)}</div>
                <div style="flex:1">{_status_badge(status)}</div>
            </div>
        </a>"""

    st.markdown(rows_html, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    _pagination_bar("bottom")


# ---------------------------------------------------------------------------
# Tela 2 — Dossie Investigativo
# ---------------------------------------------------------------------------

def page_dossier(alert_id: str) -> None:
    st.markdown(_CSS, unsafe_allow_html=True)

    # Nav
    col_back, col_title = st.columns([1.4, 7.6])
    with col_back:
        if st.button("Voltar"):
            st.session_state["page"] = "list"
            st.session_state.pop("selected_alert_id", None)
            st.rerun()

    with col_title:
        st.markdown(f"""
        <div class="nav-bar" style="padding-bottom:16px;">
            {_nav_logo_html()}
            <div style="font-size:12px; color:#475569; font-family:monospace;">
                {alert_id}
            </div>
        </div>
        """, unsafe_allow_html=True)

    # RN-08: Disclaimer — sempre visivel no topo
    st.markdown(
        '<div class="disclaimer-bar">'
        '<strong>Aviso:</strong> Este dossiê é um apoio à investigação. '
        'A decisão de fraude ou legitimidade é de <strong>responsabilidade exclusiva do analista humano</strong>. '
        'O sistema não bloqueia cartões nem confirma fraudes automaticamente.'
        '</div>',
        unsafe_allow_html=True,
    )

    with st.spinner("Carregando dossiê..."):
        data = _get(f"/alerts/{alert_id}/dossier")

    if data is _CONN_ERROR:
        _api_offline_banner()
        return
    if data is None:
        _alert_error(f"Alerta `{alert_id}` não encontrado.")
        return

    if data.get("_generating"):
        st.warning("Dossiê sendo gerado. Aguarde e recarregue.")
        if st.button("Recarregar"):
            st.rerun()
        return

    scoring   = data.get("scoring") or {}
    deviations = data.get("deviations") or {}
    timeline  = data.get("timeline") or []
    tx        = data.get("transaction") or {}
    customer  = data.get("customer") or {}
    mock_mode = scoring.get("mock_mode", False)

    for quality_warning in scoring.get("quality_warnings") or []:
        st.warning(quality_warning)

    # ── Layout principal: coluna larga + coluna de decisao ──
    col_main, col_side = st.columns([7, 3])

    with col_main:

        # ── 1. Score de Risco ──────────────────────────────────
        st.markdown('<div class="section-label">Score de Risco</div>', unsafe_allow_html=True)

        risk_level = scoring.get("risk_level") or "medio"
        fraud_score = scoring.get("fraud_score_model")
        if fraud_score is None:
            _alert_error("Este dossiê não possui score calculado. Verifique a geração antes de analisar o risco.")
            return
        score_pct = int(fraud_score * 100)
        text_color, bar_color = _RISK_COLOR.get(risk_level, ("#94a3b8", "#64748b"))

        mock_note = ""
        if mock_mode:
            mock_note = '<span class="badge badge-simulado" style="margin-left:12px">Simulado</span>'

        st.html(f"""
        <div class="score-block">
            <div>
                <div class="score-number" style="color:{text_color}">{score_pct}</div>
                <div style="font-size:11px; color:#475569; margin-top:2px">de 100</div>
            </div>
            <div style="flex:1">
                <div style="display:flex; align-items:center; gap:10px; margin-bottom:10px;">
                    {_badge(risk_level)}
                    {mock_note}
                </div>
                <div class="score-bar-wrap">
                    <div class="score-bar-fill" style="width:{score_pct}%; background:{bar_color};"></div>
                </div>
                <div style="font-size:11px; color:#475569; margin-top:6px;">
                    Versão do modelo: <span style="font-family:monospace">{scoring.get("model_version","—")}</span>
                </div>
            </div>
        </div>
        """)

        st.markdown("<br>", unsafe_allow_html=True)

        # ── Resumo da investigação (TL;DR) ─────────────────────
        # O "indício dominante" ficava só no fim da página, depois de toda a
        # evidência — o analista só descobria o porquê do alerta depois de
        # rolar a tela inteira. Repetimos aqui, de forma compacta, logo após
        # o score, para orientar a leitura antes do detalhe (a seção completa
        # com o rastro de investigação continua mais abaixo).
        priority_preview = data.get("investigation_priority")
        if priority_preview:
            p_label_preview, p_cls_preview = _PRIORITY_MAP.get(priority_preview, (priority_preview, "info"))
            st.markdown(
                f'<div class="priority-banner priority-{p_cls_preview}" style="margin-bottom:0;">'
                f'Indício dominante: <strong>{p_label_preview}</strong>'
                f'</div>',
                unsafe_allow_html=True,
            )
            st.markdown("<br>", unsafe_allow_html=True)

        # ── 2. Fatores Relevantes (SHAP) ──────────────────────
        st.markdown('<div class="section-label">Fatores Relevantes</div>', unsafe_allow_html=True)

        top_factors = scoring.get("top_factors") or []
        if top_factors:
            rows_html = ""
            for f in top_factors:
                direction = f.get("direction", "")
                if direction == "aumenta_risco":
                    dir_html = '<span class="factor-dir-up">+</span>'
                elif direction == "reduz_risco":
                    dir_html = '<span class="factor-dir-down">-</span>'
                else:
                    dir_html = '<span class="factor-dir-neu">~</span>'
                feat = f.get("feature", "?")
                val  = _fmt_factor_value(feat, f.get("value"))
                shap = f.get("shap_value", 0)
                mock_tag = ' <span class="badge badge-simulado" style="font-size:10px;padding:1px 6px">sim</span>' if mock_mode else ""
                rows_html += f"""
                <div class="factor-row">
                    {dir_html}
                    <span class="factor-name">{_feature_label(feat)}{mock_tag}</span>
                    <span class="factor-val">{val}</span>
                    <span class="factor-shap">{shap:+.3f}</span>
                </div>"""
            st.markdown(rows_html, unsafe_allow_html=True)
        else:
            st.markdown('<div style="color:#475569; font-size:13px; padding:12px 0">Nenhum fator disponível.</div>', unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # ── 3. Transacao ──────────────────────────────────────
        st.markdown('<div class="section-label">Transação</div>', unsafe_allow_html=True)

        event_at = tx.get("event_occurred_at", "")
        st.markdown(f"""
        <div class="info-grid">
            <div class="info-cell">
                <div class="info-cell-label">Valor</div>
                <div class="info-cell-value" style="font-size:18px;font-weight:700;color:#e2e8f0">{_fmt_brl(tx.get("amount"))}</div>
            </div>
            <div class="info-cell">
                <div class="info-cell-label">Data / Hora</div>
                <div class="info-cell-value" style="font-family:monospace">{_fmt_dt(event_at)}</div>
            </div>
            <div class="info-cell">
                <div class="info-cell-label">Modalidade</div>
                <div class="info-cell-value">{_fmt_category(tx.get("modalidade_transacao"))}</div>
            </div>
            <div class="info-cell">
                <div class="info-cell-label">Ambiente POS</div>
                <div class="info-cell-value">{_fmt_category(tx.get("pos_environment"))}</div>
            </div>
            <div class="info-cell">
                <div class="info-cell-label">MCC</div>
                <div class="info-cell-value" style="font-family:monospace">{_fmt_code(tx.get("mcc"))}</div>
            </div>
            <div class="info-cell">
                <div class="info-cell-label">Moeda</div>
                <div class="info-cell-value">{tx.get("currency_code") or "—"}</div>
            </div>
        </div>
        <div style="margin-top:12px; padding:12px 16px; background:#141822; border:1px solid #1e2535; border-radius:8px; font-size:13px; color:#94a3b8;">
            <strong style="color:#f1f5f9">{tx.get("acceptor_name") or "—"}</strong>
            &nbsp;·&nbsp; {tx.get("acceptor_city") or ""} / {tx.get("acceptor_region") or ""}
            &nbsp;·&nbsp; {tx.get("acceptor_country_code") or ""}
        </div>
        """, unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # ── 4. Contexto do Associado ──────────────────────────
        st.markdown('<div class="section-label">Contexto do Associado</div>', unsafe_allow_html=True)

        balance = customer.get("customer_total_balance")
        limit   = customer.get("card_credit_limit")
        st.markdown(f"""
        <div class="info-grid">
            <div class="info-cell">
                <div class="info-cell-label">Nome</div>
                <div class="info-cell-value">{customer.get("customer_name") or "—"}</div>
            </div>
            <div class="info-cell">
                <div class="info-cell-label">Segmento</div>
                <div class="info-cell-value">{customer.get("customer_segment") or "—"}</div>
            </div>
            <div class="info-cell">
                <div class="info-cell-label">Região</div>
                <div class="info-cell-value">{customer.get("customer_region") or "—"}</div>
            </div>
            <div class="info-cell">
                <div class="info-cell-label">Classificação de Risco</div>
                <div class="info-cell-value">{_fmt_risk_label(customer.get("customer_risk_rating"))}</div>
            </div>
            <div class="info-cell">
                <div class="info-cell-label">KYC</div>
                <div class="info-cell-value">{customer.get("customer_kyc_lvl") or "—"}</div>
            </div>
            <div class="info-cell">
                <div class="info-cell-label">Saldo Total</div>
                <div class="info-cell-value">{_fmt_brl(balance)}</div>
            </div>
            <div class="info-cell">
                <div class="info-cell-label">Cartão</div>
                <div class="info-cell-value" style="font-family:monospace;font-size:12px">{customer.get("card_id") or "—"}</div>
            </div>
            <div class="info-cell">
                <div class="info-cell-label">Tipo / Bandeira</div>
                <div class="info-cell-value">{_fmt_category(customer.get("card_type"))} / {customer.get("card_brand") or "—"}</div>
            </div>
            <div class="info-cell">
                <div class="info-cell-label">Limite do Cartão</div>
                <div class="info-cell-value">{_fmt_brl(limit)}</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # ── 4b. Casos Relacionados do Associado ────────────────
        # Fecha a lacuna de investigar um alerta isolado sem saber se a mesma
        # pessoa já teve decisões anteriores ou outros casos abertos ao mesmo
        # tempo — exatamente o retrabalho que o dossiê existe para eliminar.
        st.markdown('<div class="section-label">Casos Relacionados do Associado</div>', unsafe_allow_html=True)

        related = data.get("related_cases") or {}
        prior_decisions = related.get("prior_decisions") or []
        other_pending = related.get("other_pending") or []
        other_pending_count = related.get("other_pending_count") or 0

        if not prior_decisions and not other_pending_count:
            st.markdown(
                '<div style="color:#475569; font-size:13px; padding:4px 0 12px 0;">'
                'Nenhum outro caso registrado para este associado.</div>',
                unsafe_allow_html=True,
            )
        else:
            if other_pending_count:
                plural = "s" if other_pending_count != 1 else ""
                st.markdown(
                    f'<div style="background:rgba(99,102,241,0.07);border:1px solid rgba(99,102,241,0.25);'
                    f'border-radius:6px;padding:12px 16px;font-size:13px;color:#a5b4fc;margin-bottom:10px;">'
                    f'Este associado tem <strong>{other_pending_count} outro{plural} alerta{plural} pendente{plural}</strong>'
                    f' — considere revisar em conjunto.</div>',
                    unsafe_allow_html=True,
                )
                for case in other_pending:
                    c1, c2, c3 = st.columns([2.5, 5, 1.2])
                    with c1:
                        st.markdown(f'<div style="padding-top:8px;font-weight:600;color:#e2e8f0;">{_fmt_brl(case.get("amount"))}</div>', unsafe_allow_html=True)
                    with c2:
                        fs = case.get("fraud_score")
                        fs_html = f'score externo {fs:.0f}/999' if fs is not None else ''
                        st.markdown(
                            f'<div style="padding-top:8px;font-size:12px;color:#64748b;">'
                            f'{_fmt_dt(case.get("event_occurred_at"))} &nbsp;·&nbsp; {fs_html}</div>',
                            unsafe_allow_html=True,
                        )
                    with c3:
                        if st.button("Abrir", key=f"related_pending_{case['alert_id']}", use_container_width=True):
                            st.session_state["selected_alert_id"] = case["alert_id"]
                            st.rerun()

            if prior_decisions:
                if other_pending_count:
                    st.markdown("<div style='margin-top:14px'></div>", unsafe_allow_html=True)
                st.markdown(
                    '<div style="font-size:11px;color:#475569;text-transform:uppercase;'
                    'letter-spacing:0.06em;margin-bottom:8px;">Decisões anteriores deste associado</div>',
                    unsafe_allow_html=True,
                )
                for pd in prior_decisions:
                    c1, c2, c3 = st.columns([2.2, 5.5, 1.2])
                    with c1:
                        st.markdown(f'<div style="padding-top:8px;">{_badge_decision(pd.get("decision"))}</div>', unsafe_allow_html=True)
                    with c2:
                        st.markdown(
                            f'<div style="padding-top:8px;font-size:12px;color:#64748b;">'
                            f'{_fmt_brl(pd.get("amount"))} &nbsp;·&nbsp; {_fmt_dt(pd.get("event_occurred_at"))} '
                            f'&nbsp;·&nbsp; decidido em {_fmt_dt(pd.get("decided_at"))}</div>',
                            unsafe_allow_html=True,
                        )
                    with c3:
                        if st.button("Abrir", key=f"related_decided_{pd['alert_id']}", use_container_width=True):
                            st.session_state["selected_alert_id"] = pd["alert_id"]
                            st.rerun()

        st.markdown("<br>", unsafe_allow_html=True)

        # ── 5. Desvios Comportamentais ────────────────────────
        st.markdown('<div class="section-label">Desvios Comportamentais</div>', unsafe_allow_html=True)

        if deviations.get("insufficient_history"):
            st.markdown(
                '<div style="background:rgba(99,102,241,0.07);border:1px solid rgba(99,102,241,0.25);'
                'border-radius:6px;padding:14px 16px;font-size:13px;color:#a5b4fc;">'
                'Histórico insuficiente &mdash; menos de 3 transações nos últimos 90 dias. '
                'Os campos de desvio não podem ser calculados.</div>',
                unsafe_allow_html=True,
            )
        else:
            dev_pct     = deviations.get("amount_deviation_pct")
            dev_rel     = deviations.get("amount_deviation_relevant", False)
            mcc_unusual = deviations.get("mcc_unusual", False)
            hour_unusual= deviations.get("hour_unusual", False)
            rel_card    = deviations.get("related_alerts_same_card_30d", 0)
            rel_acc     = deviations.get("related_alerts_same_acceptor_30d", 0)

            def _dev_val_class(flag):
                return "warn" if flag else "ok"

            dev_pct_str = f"{dev_pct:+.1f}%" if dev_pct is not None else "—"
            rel_total   = (rel_card or 0) + (rel_acc or 0)

            st.markdown(f"""
            <div class="dev-grid">
                <div class="dev-card {'alert-card' if dev_rel else ''}">
                    <div class="dev-label">Desvio de Valor</div>
                    <div class="dev-value {_dev_val_class(dev_rel)}">{dev_pct_str}</div>
                    <div class="dev-sub">Média 30d: {_fmt_brl(deviations.get("avg_amount_30d"))}
                    {"&nbsp;· RELEVANTE" if dev_rel else ""}</div>
                </div>
                <div class="dev-card {'alert-card' if mcc_unusual else ''}">
                    <div class="dev-label">Categoria (MCC)</div>
                    <div class="dev-value {_dev_val_class(mcc_unusual)}">{"Incomum" if mcc_unusual else "Normal"}</div>
                    <div class="dev-sub">Habitual: {_fmt_code(deviations.get("usual_mcc"))} &nbsp; Atual: {_fmt_code(deviations.get("current_mcc"))}</div>
                </div>
                <div class="dev-card {'alert-card' if hour_unusual else ''}">
                    <div class="dev-label">Horário</div>
                    <div class="dev-value {_dev_val_class(hour_unusual)}">{"Incomum" if hour_unusual else "Normal"}</div>
                    <div class="dev-sub">Faixa habitual: {deviations.get("usual_hour_range") or "—"} &nbsp; Atual: {deviations.get("current_hour") or "—"}</div>
                </div>
                <div class="dev-card {'alert-card' if rel_total > 0 else ''}">
                    <div class="dev-label">Alertas Relacionados (30d)</div>
                    <div class="dev-value {'warn' if rel_total > 0 else 'ok'}">{rel_total}</div>
                    <div class="dev-sub">Cartão: {rel_card} &nbsp;·&nbsp; Estabelecimento: {rel_acc}</div>
                </div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # ── 5b. Histórico do Estabelecimento (N4) ─────────────
        # Independente do histórico do associado — calculado mesmo quando
        # insufficient_history=True, pois diz respeito ao estabelecimento.
        st.markdown('<div class="section-label">Histórico do Estabelecimento</div>', unsafe_allow_html=True)

        acc_hist = deviations.get("acceptor_history") or {}
        window_days = acc_hist.get("window_days", 90)

        if not acc_hist.get("available"):
            reason = (
                "este estabelecimento não está identificado nesta transação"
                if not tx.get("acceptor_id")
                else f"nenhuma transação foi observada neste estabelecimento nos últimos {window_days} dias"
            )
            st.markdown(
                '<div style="background:rgba(99,102,241,0.07);border:1px solid rgba(99,102,241,0.25);'
                'border-radius:6px;padding:14px 16px;font-size:13px;color:#a5b4fc;">'
                f'Sem histórico disponível &mdash; {reason}. '
                'Ausência de registros não deve ser interpretada como evidência de baixo risco.</div>',
                unsafe_allow_html=True,
            )
        else:
            support = acc_hist.get("support", 0)
            alerts_count = acc_hist.get("alerts_count", 0)
            has_alerts = alerts_count > 0
            status_text = (
                f"{alerts_count} alerta(s) anterior(es) neste estabelecimento"
                if has_alerts else "Nenhum alerta anterior — evidência de baixo risco"
            )
            st.markdown(f"""
            <div class="dev-grid" style="grid-template-columns: repeat(3, 1fr);">
                <div class="dev-card">
                    <div class="dev-label">Janela Consultada</div>
                    <div class="dev-value">{window_days} dias</div>
                </div>
                <div class="dev-card">
                    <div class="dev-label">Suporte (Transações Observadas)</div>
                    <div class="dev-value">{support:,}</div>
                </div>
                <div class="dev-card {'alert-card' if has_alerts else ''}">
                    <div class="dev-label">Alertas Anteriores</div>
                    <div class="dev-value {'warn' if has_alerts else 'ok'}">{alerts_count}</div>
                    <div class="dev-sub">{status_text}</div>
                </div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # ── 6. Linha do Tempo ─────────────────────────────────
        st.markdown('<div class="section-label">Linha do Tempo do Associado</div>', unsafe_allow_html=True)

        if timeline:
            tl_html = ""
            for ev in timeline:
                ev_dt  = _fmt_dt(ev.get("event_occurred_at"))
                ev_amt = _fmt_brl(ev.get("amount"))
                ev_acc = ev.get("acceptor_name") or "—"
                ev_mcc = _fmt_code(ev.get("mcc"))
                ev_fs  = ev.get("fraud_score")
                fs_tag = (
                    f'<span style="font-family:monospace;color:#475569"> · score externo {ev_fs:.0f}/999</span>'
                    if ev_fs is not None else ""
                )
                tl_html += f"""
                <div class="timeline-item">
                    <div class="tl-dot"></div>
                    <div style="flex:1">
                        <div style="display:flex; gap:16px; align-items:baseline;">
                            <span class="tl-amount">{ev_amt}</span>
                            <span class="tl-date">{ev_dt}</span>
                        </div>
                        <div class="tl-meta">{ev_acc} &nbsp;·&nbsp; MCC {ev_mcc}{fs_tag}</div>
                    </div>
                </div>"""
            st.markdown(tl_html, unsafe_allow_html=True)
            st.markdown(
                '<div style="font-size:11px; color:#334155; margin-top:8px;">'
                'Score externo: pontuação de risco das bandeiras (escala 0&ndash;999), '
                'diferente do score do modelo Fio exibido no topo (escala 0&ndash;100).'
                '</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown('<div style="color:#475569; font-size:13px; padding:12px 0">Sem histórico disponível.</div>', unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # ── 7. Prioridade de Investigacao ─────────────────────
        st.markdown('<div class="section-label">Prioridade de Investigação</div>', unsafe_allow_html=True)

        priority = data.get("investigation_priority")
        p_label, p_cls = _PRIORITY_MAP.get(priority, (priority or "—", "info"))
        st.markdown(f"""
        <div class="priority-banner priority-{p_cls}">
            Indício dominante: <strong>{p_label}</strong>
        </div>
        <div style="font-size:12px; color:#475569; margin-bottom:12px;">
            Comparação com perfis similares: <em>não disponível neste ciclo</em>
            &nbsp;<span style="font-family:monospace; color:#334155">(cluster_comparison_status = nao_entregue_neste_ciclo)</span>
        </div>
        """, unsafe_allow_html=True)

        trace = data.get("investigation_trace") or []
        if trace:
            with st.popover("Como este dossiê foi construído — rastro de investigação", use_container_width=True):
                trace_html = ""
                for step in trace:
                    n      = step.get("step", "?")
                    src    = step.get("source", "")
                    reason = step.get("reason", "")
                    result = step.get("result", "")
                    gaps   = step.get("gaps") or []
                    gaps_html = "".join(f'<div style="font-size:11px;color:#f87171;margin-top:3px">Lacuna: {g}</div>' for g in gaps)
                    trace_html += f"""
                    <div class="trace-step">
                        <div class="trace-num">{n}</div>
                        <div style="flex:1">
                            <div class="trace-source">{src}</div>
                            <div style="font-size:12px;color:#64748b;margin-bottom:2px">{reason}</div>
                            <div class="trace-result">{result}</div>
                            {gaps_html}
                        </div>
                    </div>"""
                st.markdown(trace_html, unsafe_allow_html=True)

    # ── Coluna lateral: Decisao ────────────────────────────
    with col_side:
        st.markdown('<div class="section-label">Decisão do Analista</div>', unsafe_allow_html=True)

        analyst_decision = data.get("analyst_decision")

        if analyst_decision:
            label = _DECISION_LABELS.get(analyst_decision, analyst_decision)
            justification = data.get("justification_text") or ""
            decided_at = (data.get("decision_at") or "")[:16].replace("T", "  ")
            just_html = f'<div class="decision-just">{justification}</div>' if justification else ""
            st.markdown(
                f'<div class="decision-done">'
                f'<div class="decision-label">{label}</div>'
                f'{just_html}'
                f'<div style="font-size:11px; color:#475569; margin-top:8px">Registrado em {decided_at}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button(
                "Próximo alerta pendente →", key=f"next_alert_{alert_id}",
                use_container_width=True, type="primary",
                help="Abre o alerta de maior risco ainda pendente, continuando a fila sem voltar à lista.",
            ):
                with st.spinner("Buscando próximo alerta..."):
                    next_data = _get("/alerts", params={"status": ["NOVO"], "page": 1, "size": 1})
                if isinstance(next_data, dict) and next_data.get("items"):
                    st.session_state["selected_alert_id"] = next_data["items"][0]["alert_id"]
                    st.rerun()
                else:
                    _alert_info("Não há mais alertas pendentes na fila.")
        else:
            with st.form(key=f"decision_form_{alert_id}"):
                decision = st.selectbox(
                    "Decisão *",
                    options=["fraude_confirmada", "legitima", "em_analise"],
                    format_func=lambda x: _DECISION_LABELS.get(x, x),
                )
                justification_text = st.text_area(
                    "Justificativa",
                    placeholder="Descreva o raciocínio que fundamenta esta decisão...",
                    height=110,
                )
                notes = st.text_area(
                    "Notas adicionais",
                    placeholder="Observações complementares...",
                    height=70,
                )
                submitted = st.form_submit_button("Confirmar Decisão", type="primary")

            if submitted:
                result = _post(
                    f"/alerts/{alert_id}/decision",
                    {"decision": decision, "notes": notes,
                     "justification_text": justification_text},
                )
                if result:
                    _alert_success(f"Decisão registrada.")
                    st.rerun()

        # Metadata do dossie
        st.markdown(f"""
        <div style="margin-top:24px; padding:14px 16px; background:#0b0e14;
            border:1px solid #1a2030; border-radius:8px; font-size:11px; color:#334155;
            font-family:monospace; line-height:1.8;">
            dossier_id<br>
            <span style="color:#64748b">{data.get("dossier_id","—")}</span><br><br>
            model_version<br>
            <span style="color:#64748b">{data.get("model_version","—")}</span><br><br>
            gerado em<br>
            <span style="color:#64748b">{_fmt_dt(data.get("generated_at"))}</span>
        </div>
        """, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Gráficos da home (Altair, tematizados para o tema escuro da plataforma)
# ---------------------------------------------------------------------------

_ALTAIR_AXIS = {
    "gridColor": "#1a2030",
    "gridOpacity": 0.6,
    "domainColor": "#1e2535",
    "tickColor": "#1e2535",
    "labelColor": "#94a3b8",
    "titleColor": "#64748b",
    "labelFontSize": 11,
    "titleFontSize": 11,
    "labelFont": "Inter, system-ui, sans-serif",
    "titleFont": "Inter, system-ui, sans-serif",
    "titleFontWeight": 600,
    "labelPadding": 4,
    "titlePadding": 8,
}


def _altair_theme(chart: alt.Chart) -> alt.Chart:
    """Aplica o tema escuro padrão da plataforma a um gráfico Altair."""
    return (
        chart.configure(background="transparent")
        .configure_view(strokeWidth=0)
        .configure_axis(**_ALTAIR_AXIS)
        .configure_legend(
            labelColor="#cbd5e1", titleColor="#64748b",
            labelFontSize=11, titleFontSize=11,
            labelFont="Inter, system-ui, sans-serif",
        )
    )


def _render_risk_breakdown_chart(risk_breakdown: dict) -> None:
    """Gráfico de barras horizontal: distribuição de risco dos alertas pendentes."""
    order = {"Alto": 0, "Médio": 1, "Baixo": 2}
    df = pd.DataFrame([
        {"Risco": "Alto", "Alertas": risk_breakdown.get("alto", 0)},
        {"Risco": "Médio", "Alertas": risk_breakdown.get("medio", 0)},
        {"Risco": "Baixo", "Alertas": risk_breakdown.get("baixo", 0)},
    ])
    df["ordem"] = df["Risco"].map(order)

    chart = (
        alt.Chart(df)
        .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, height=26)
        .encode(
            x=alt.X("Alertas:Q", title="Alertas pendentes"),
            y=alt.Y("Risco:N", sort=alt.SortField("ordem"), title=None),
            color=alt.Color(
                "Risco:N",
                scale=alt.Scale(domain=["Alto", "Médio", "Baixo"], range=["#f87171", "#fbbf24", "#4ade80"]),
                legend=None,
            ),
            tooltip=[alt.Tooltip("Risco:N"), alt.Tooltip("Alertas:Q", format=",")],
        )
        .properties(height=150)
    )
    st.altair_chart(_altair_theme(chart), use_container_width=True)


def _render_alerts_trend_chart(alerts_by_day: list[dict]) -> None:
    """Gráfico de área: volume de alertas gerados por dia, ao longo da janela de dados."""
    if not alerts_by_day:
        st.markdown('<div style="color:#475569; font-size:13px; padding:20px 0">Sem dados suficientes para o período.</div>', unsafe_allow_html=True)
        return

    df = pd.DataFrame(alerts_by_day)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])

    chart = (
        alt.Chart(df)
        .mark_area(line={"color": "#3b82f6", "strokeWidth": 2}, color=alt.Gradient(
            gradient="linear",
            stops=[
                alt.GradientStop(color="rgba(59,130,246,0.35)", offset=0),
                alt.GradientStop(color="rgba(59,130,246,0.02)", offset=1),
            ],
            x1=1, x2=1, y1=1, y2=0,
        ))
        .encode(
            x=alt.X("date:T", title=None, axis=alt.Axis(format="%m/%Y", tickCount="month")),
            y=alt.Y("count:Q", title="Alertas gerados"),
            tooltip=[alt.Tooltip("date:T", title="Data", format="%d/%m/%Y"), alt.Tooltip("count:Q", title="Alertas")],
        )
        .properties(height=150)
        .interactive()
    )
    st.altair_chart(_altair_theme(chart), use_container_width=True)


# ---------------------------------------------------------------------------
# Tela 0 — Home / Hub
# ---------------------------------------------------------------------------

def page_home() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)

    # ── Hero ──────────────────────────────────────────────────────────────
    st.markdown(
        '<div style="padding:4px 0 34px 0;border-bottom:1px solid var(--border-subtle);margin-bottom:34px;">'
        f'<div style="margin-bottom:20px;">{_nav_logo_html()}</div>'
        '<div class="hero-thread" aria-hidden="true"></div>'
        '<div style="font-size:13px;color:#3b82f6;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;margin-bottom:14px;">'
        'Sicredi &nbsp;&middot;&nbsp; Plataforma de Apoio a Investigação</div>'
        '<div class="hero-title" style="font-size:44px;font-weight:800;letter-spacing:-1.8px;line-height:1.1;margin-bottom:18px;">'
        'Investigação de fraudes, com mais contexto</div>'
        '<div style="font-size:16px;color:#94a3b8;max-width:600px;line-height:1.7;">'
        'Consolida score de risco, explicabilidade do modelo e histórico do associado '
        'em um único dossiê investigativo &mdash; para que o analista tome a decisão '
        'com mais contexto e menos tempo gasto coletando dados.'
        '</div></div>',
        unsafe_allow_html=True,
    )

    # ── Stats em tempo real ───────────────────────────────────────────────
    stats_raw = _get("/stats")
    api_offline = stats_raw is None or stats_raw is _CONN_ERROR
    stats = stats_raw if isinstance(stats_raw, dict) else {}

    if api_offline:
        _api_offline_banner("Não foi possível carregar as estatísticas — verifique se o servidor está rodando em localhost:8000.")
        st.markdown("<br>", unsafe_allow_html=True)

    fmt = (lambda n: "—") if api_offline else (lambda n: f"{n:,}")

    alerts_novo  = stats.get("alerts_novo", 0)
    alerts_enc   = stats.get("alerts_encerrado", 0)
    total_alerts = stats.get("alerts_total", 0)
    total_tx     = stats.get("transactions_total", 0)
    total_cust   = stats.get("customers_total", 0)
    dossiers     = stats.get("dossiers_gerados", 0)
    decisoes     = stats.get("decisoes_registradas", 0)

    if api_offline:
        pct_enc_label = "—"
        decisoes_label = "—"
    else:
        pct_enc = round(alerts_enc / total_alerts * 100, 1) if total_alerts else 0
        pct_enc_label = f"{pct_enc}% do total de alertas"
        decisoes_label = f"{decisoes:,} {'decisão' if decisoes == 1 else 'decisões'} {'registrada' if decisoes == 1 else 'registradas'}"

    def _stat_card(label: str, value: str, sub: str, accent: str = "#e2e8f0", bg: str = "#141822", border: str = "#1e2535", feature: bool = False) -> str:
        feature_cls = " is-feature" if feature else ""
        return f"""<div class="premium-card stat-card{feature_cls}" style="background:{bg};border:1px solid {border};">
            <div class="stat-card-accent" style="background:{accent};"></div>
            <div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.09em;font-weight:600;margin-bottom:10px;">{label}</div>
            <div style="font-size:38px;font-weight:800;color:{accent};letter-spacing:-1.4px;line-height:1;font-variant-numeric:tabular-nums;">{value}</div>
            <div style="font-size:12px;color:#64748b;margin-top:8px;line-height:1.5;">{sub}</div>
        </div>"""

    pending_value_total = stats.get("pending_value_total")
    avg_score_novo = stats.get("avg_score_novo")
    pending_value_label = "—" if api_offline or pending_value_total is None else _fmt_brl(pending_value_total)
    avg_score_label = (
        "—" if api_offline or avg_score_novo is None
        else f'{avg_score_novo:.0f}<span style="font-size:18px;color:#475569">/999</span>'
    )

    col_r1a, col_r1b, col_r1c = st.columns(3, gap="small")
    with col_r1a:
        st.markdown(
            _stat_card("Aguardando Análise", fmt(alerts_novo), "alertas com status <strong style='color:#94a3b8'>Novo</strong>",
                       accent="#f87171", bg="rgba(239,68,68,0.06)", border="rgba(239,68,68,0.2)"),
            unsafe_allow_html=True,
        )
        if not api_offline and alerts_novo:
            if st.button("Ver fila →", key="home_goto_novo", use_container_width=True):
                _goto_alert_list(status=["NOVO"])
    with col_r1b:
        st.markdown(
            _stat_card("Valor em Risco", pending_value_label, "soma dos valores dos alertas pendentes",
                       accent="#fbbf24", bg="rgba(245,158,11,0.06)", border="rgba(245,158,11,0.2)", feature=True),
            unsafe_allow_html=True,
        )
    with col_r1c:
        st.markdown(
            _stat_card("Score Externo Médio", avg_score_label, "média entre os alertas pendentes (escala 0–999)"),
            unsafe_allow_html=True,
        )

    st.markdown("<div style='margin-top:14px'></div>", unsafe_allow_html=True)

    col_r2a, col_r2b, col_r2c = st.columns(3, gap="small")
    with col_r2a:
        st.markdown(_stat_card("Encerrados", fmt(alerts_enc), pct_enc_label, accent="#4ade80"), unsafe_allow_html=True)
        if not api_offline and alerts_enc:
            if st.button("Ver encerrados →", key="home_goto_encerrado", use_container_width=True):
                _goto_alert_list(status=["ENCERRADO"])
    with col_r2b:
        st.markdown(
            _stat_card("Transações Monitoradas", fmt(total_tx), f"{fmt(total_cust)} associados na base"),
            unsafe_allow_html=True,
        )
    with col_r2c:
        st.markdown(_stat_card("Dossiês Gerados", fmt(dossiers), decisoes_label), unsafe_allow_html=True)

    st.markdown("<div style='margin-top:20px'></div>", unsafe_allow_html=True)

    # ── Visão analítica — distribuição de risco e tendência diária ────────
    if not api_offline:
        risk_breakdown = stats.get("risk_breakdown") or {}
        alerts_by_day = stats.get("alerts_by_day") or []

        if (risk_breakdown and sum(risk_breakdown.values()) > 0) or alerts_by_day:
            st.markdown('<div class="section-label" style="margin-bottom:16px;">Visão analítica</div>', unsafe_allow_html=True)
            col_chart_a, col_chart_b = st.columns([1, 1.6])
            with col_chart_a:
                st.markdown(
                    '<div style="font-size:12px;color:#94a3b8;font-weight:600;margin-bottom:10px;">'
                    'Risco dos alertas pendentes</div>',
                    unsafe_allow_html=True,
                )
                _render_risk_breakdown_chart(risk_breakdown)
            with col_chart_b:
                st.markdown(
                    '<div style="font-size:12px;color:#94a3b8;font-weight:600;margin-bottom:10px;">'
                    'Alertas gerados por dia</div>',
                    unsafe_allow_html=True,
                )
                _render_alerts_trend_chart(alerts_by_day)

            decision_breakdown = stats.get("decision_breakdown") or {}
            if decision_breakdown:
                st.markdown(
                    '<div style="font-size:12px;color:#94a3b8;font-weight:600;margin:20px 0 10px 0;">'
                    'Decisões registradas</div>',
                    unsafe_allow_html=True,
                )
                decision_meta = {
                    "fraude_confirmada": ("#f87171", "Fraude Confirmada"),
                    "legitima": ("#4ade80", "Legítima"),
                    "em_analise": ("#60a5fa", "Em Análise"),
                }
                pills = "".join(
                    f'<div class="premium-card" style="display:flex;align-items:center;gap:10px;padding:12px 18px;'
                    f'background:#141822;border:1px solid #1e2535;border-radius:8px;min-width:170px;">'
                    f'<span style="width:9px;height:9px;border-radius:50%;'
                    f'background:{decision_meta.get(k, ("#94a3b8", k))[0]};flex-shrink:0;box-shadow:0 0 8px {decision_meta.get(k, ("#94a3b8", k))[0]};"></span>'
                    f'<span style="font-size:13px;color:#cbd5e1;">{decision_meta.get(k, (None, k))[1]}</span>'
                    f'<span style="font-size:16px;font-weight:800;color:#f1f5f9;margin-left:auto;font-variant-numeric:tabular-nums;">{v:,}</span>'
                    f'</div>'
                    for k, v in decision_breakdown.items()
                )
                st.markdown(f'<div style="display:flex;gap:12px;flex-wrap:wrap;">{pills}</div>', unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)

    # ── O que o sistema faz — 3 cards ────────────────────────────────────
    def _how_card(num: str, title: str, body: str) -> str:
        return (
            f'<div class="premium-card how-card" style="padding:20px 22px;background:#141822;border:1px solid #1e2535;border-radius:8px;min-height:148px;">'
            f'<div style="font-size:12px;font-weight:700;color:#60a5fa;margin-bottom:10px;letter-spacing:0.02em;">{num}&nbsp;&nbsp;{title}</div>'
            f'<div style="font-size:13px;color:#94a3b8;line-height:1.65;">{body}</div>'
            f'</div>'
        )

    how_cards = (
        _how_card("01", "Score de Risco",
                  "Um modelo preditivo atribui um score de 0 a 100 para cada transação suspeita, "
                  "indicando a probabilidade de fraude. Os principais fatores que influenciaram "
                  "o score são exibidos com valores SHAP.")
        + _how_card("02", "Contexto e Desvios",
                    "O dossiê compara a transação com o histórico do associado: desvio de valor, "
                    "categoria de estabelecimento (MCC), horário habitual e alertas recentes "
                    "no mesmo cartao ou estabelecimento.")
        + _how_card("03", "Decisão do Analista",
                    "Dentro de cada dossiê, o analista registra sua decisão — "
                    "Fraude Confirmada, Legítima ou Em Análise — "
                    "com justificativa. O sistema nunca decide sozinho.")
    )

    st.markdown('<div class="section-label" style="margin-bottom:16px;">Como funciona</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:36px;">{how_cards}</div>',
        unsafe_allow_html=True,
    )

    # ── CTA ───────────────────────────────────────────────────────────────
    alerta_s = "s" if alerts_novo != 1 else ""
    cta_title = (
        "Abrir a fila de alertas"
        if api_offline else
        f"{alerts_novo:,} alerta{alerta_s} aguardando análise"
    )
    cta_sub = (
        "Conecte-se à API para ver os casos ordenados por score de risco."
        if api_offline else
        "Ordenados por score de risco — os casos mais críticos aparecem primeiro."
    )
    st.markdown(
        f'<div class="premium-card" style="background:linear-gradient(135deg,rgba(59,130,246,0.1),rgba(59,130,246,0.04));'
        f'border:1px solid rgba(59,130,246,0.26);'
        f'border-radius:10px;padding:26px 30px;margin-bottom:18px;">'
        f'<div style="font-size:16px;font-weight:700;color:#f1f5f9;margin-bottom:6px;letter-spacing:-0.3px;">'
        f'{cta_title}</div>'
        f'<div style="font-size:13px;color:#94a3b8;line-height:1.6;">'
        f'{cta_sub}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if st.button("Abrir fila de alertas", type="primary"):
        st.session_state["page"] = "list"
        st.rerun()

    model_state = _get("/model/status")
    if isinstance(model_state, dict) and not model_state.get("mock_mode", True):
        _alert_info(f"Modelo {model_state.get('model_version', '—')} carregado: "
                    "os dossiês usam scoring real e explicações SHAP. A base do case é sintética.")
    elif isinstance(model_state, dict) and model_state.get("mock_mode"):
        st.warning("Modo simulado ativo: os scores não foram calculados pelo modelo treinado.")
    else:
        st.warning("Não foi possível confirmar o estado do modelo de scoring.")


# ---------------------------------------------------------------------------
# Roteamento
# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(
        page_title="Fio — Plataforma de Investigação de Fraudes",
        page_icon="🔍",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    # Navegação por link (ex.: clique no ID do alerta na lista) chega via query
    # params — convertemos para session_state uma única vez e limpamos a URL
    # em seguida, para que o resto da navegação (botões "Voltar", "Próximo
    # alerta pendente" etc.) continue funcionando normalmente por session_state.
    q_alert_id = st.query_params.get("alert_id")
    q_page = st.query_params.get("page")
    if q_alert_id or q_page:
        if q_alert_id:
            st.session_state["selected_alert_id"] = q_alert_id
        if q_page:
            st.session_state["page"] = q_page
        st.query_params.clear()

    if "page" not in st.session_state:
        st.session_state["page"] = "home"

    page = st.session_state.get("page", "home")

    if page == "dossier" and "selected_alert_id" in st.session_state:
        page_dossier(st.session_state["selected_alert_id"])
    elif page == "list":
        page_alert_list()
    else:
        page_home()


if __name__ == "__main__":
    main()
