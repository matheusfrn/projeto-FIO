# data/raw/

Diretório para os dados brutos do case (CSV).

**Não versionado** — este diretório está no `.gitignore`.

Coloque aqui o arquivo CSV fornecido para o case Sicredi antes de rodar o script de importação:

```
data/raw/case_transacoes_cartoes_sintetico_900k.csv
```

Em seguida, execute a carga:

```bash
python -m src.data.import_transactions
```
