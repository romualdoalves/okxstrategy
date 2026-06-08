#!/usr/bin/env python3
"""
populate_strategies_db.py — Insere estratégias migradas na tabela 'strategies'.

Execute: python scripts/populate_strategies_db.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.database import SessionLocal, StrategyModel
from backend.strategies.registry import REGISTRY


def populate() -> None:
    db = SessionLocal()
    inserted = 0
    skipped = 0

    for strategy_id, cls in sorted(REGISTRY.items()):
        info = cls.info()

        existing = db.query(StrategyModel).filter_by(strategy_id=strategy_id).first()
        if existing:
            skipped += 1
            continue

        # Lê o código do arquivo factory
        factory_dir = Path(__file__).resolve().parent.parent / "backend" / "strategies" / "factory"
        code_file = factory_dir / f"{strategy_id.lower()}.py"
        code_py = code_file.read_text(encoding="utf-8") if code_file.exists() else None

        db.add(StrategyModel(
            strategy_id=strategy_id,
            name=info.name,
            description=info.description,
            source_text=None,  # nativas não têm texto fonte
            plan_json={"category": _guess_category(strategy_id)},
            code_py=code_py,
            status="deployed",
            deployed_at=None,
            validation_report={},
        ))
        inserted += 1

    db.commit()
    db.close()

    print(f"Inseridas: {inserted}")
    print(f"Ignoradas (já existiam): {skipped}")


def _guess_category(sid: str) -> str:
    prefix = sid[:2]
    return {
        "TF": "Trend Following",
        "MR": "Mean Reversion",
        "PA": "Price Action",
        "SC": "Scalping",
        "RG": "Regime",
        "IF": "Information",
        "NW": "Network",
        "FX": "Factory AI",
        "T0": "Test",
    }.get(prefix, "Unknown")


if __name__ == "__main__":
    populate()
