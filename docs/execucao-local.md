# Executar a aplicação localmente

Execute os comandos na raiz do repositório com Python 3.12. O ambiente da
aplicação é separado do ambiente de treinamento para preservar sua reprodução.

```bash
python3 -m venv .venv-app
.venv-app/bin/python -m pip install -r requirements.txt
```

Com o CSV do case na raiz, faça a carga inicial do SQLite. A carga é idempotente;
o banco local não é versionado. Registros sem rótulo ou data são descartados
pelo importador e os alertas são gerados pelo percentil 95 do score externo.

```bash
DATABASE_URL=sqlite:///./data/fio.db OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv-app/bin/python -m src.data.import_transactions \
  --csv case_transacoes_cartoes_sintetico_900k.csv
```

Inicie a API em um terminal:

```bash
DATABASE_URL=sqlite:///./data/fio.db OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv-app/bin/python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000
```

Inicie a interface em outro terminal:

```bash
FIO_API_BASE_URL=http://127.0.0.1:8000 \
  .venv-app/bin/python -m streamlit run src/frontend/app.py \
  --server.address 127.0.0.1 --server.port 8501 \
  --server.headless true --browser.gatherUsageStats false
```

Abra a interface em <http://localhost:8501> e a documentação da API em
<http://localhost:8000/docs>. Encerre cada servidor com `Ctrl+C` no seu terminal.

Os artefatos do diretório `models/` são detectados automaticamente. Garanta
que os cinco arquivos e o `artifact_manifest.json` do ZIP estejam presentes
antes de iniciar esta versão protegida. Os `.pkl` da versão ativa devem
acompanhar o repositório; o ZIP é uma alternativa de instalação.
Consulte `GET /model/status` para verificar a versão carregada e `mock_mode: false`.
A tela inicial também informa esse estado. Os dossiês apresentam o score
do modelo, os fatores SHAP e os avisos de qualidade de entrada. A fila de
alertas usa o score externo para triagem; o score do modelo é calculado na
investigação. Consulte [qualidade do scoring](scoring-quality.md) e o
[model card](../models/model_card.md) para as limitações do case sintético.
