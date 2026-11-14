"""Exporta exemplos do CSV original para testar o contrato do scoring."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--models", type=Path, default=Path("models"))
    args = parser.parse_args()
    names = json.loads((args.models / "feature_names.json").read_text())
    splits = json.loads((args.models / "split_manifest.json").read_text())
    required = names + ["event_external_id", "event_occurred_at", "feedback_label"]
    df = pd.read_csv(args.data, usecols=required, low_memory=False)
    target = df.feedback_label.astype("string").str.lower().eq("true")
    stamp = pd.to_datetime(df.event_occurred_at, format="mixed", utc=True, errors="coerce")
    holdout_ids = set(splits["holdout"]["event_external_ids"])
    negative_ids = df.loc[df.event_external_id.isin(holdout_ids) & ~target,
                          "event_external_id"].head(2).tolist()
    future_ids = df.loc[stamp.ge("2025-04-01") & df.amount.notna(),
                        "event_external_id"].head(2).tolist()
    selected = splits["demo"]["event_external_ids"] + negative_ids + future_ids
    indexed = df.set_index("event_external_id")
    records = []
    for identifier in selected:
        row = indexed.loc[identifier]
        record = {key: None if pd.isna(row[key]) else row[key] for key in names}
        record["event_external_id"] = identifier
        record["event_occurred_at"] = row.event_occurred_at
        # O importador do MVP usa 0.0 quando amount está ausente.
        record["amount"] = float(record["amount"] or 0.0)
        record["mcc"] = str(record["mcc"]) if record["mcc"] is not None else None
        records.append(record)
    output = args.models / "example_transactions.json"
    output.write_text(json.dumps(records, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(f"{len(records)} exemplos do CSV original salvos em {output}")


if __name__ == "__main__":
    main()
