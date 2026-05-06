"""
Parse bank statement (cartola bancaria) XLSX files.
tipo A = abono (credit), tipo C = cargo (debit).
IMPORTANT: 'DAMJANIC SILVA' transfers = personal deposits/withdrawals, NOT sales.
IMPORTANT: SII payments = IVA pass-through, NOT operating expenses.
"""

import pandas as pd
from pathlib import Path
from sqlalchemy.engine import Engine


EXCLUDE_DESCRIPTIONS = ["DAMJANIC SILVA", "SERVICIO DE IMPUESTOS INTERNOS", "SII"]


def parse_cartola_xlsx(filepath: str | Path, mes: int, anio: int) -> pd.DataFrame:
    """Parse a bank statement XLSX and return clean DataFrame."""
    df = pd.read_excel(filepath, dtype=str)
    df.columns = [c.lower().strip().replace(" ", "_") for c in df.columns]

    rename = {"monto": "monto", "descripcion": "descripcion", "fecha": "fecha", "tipo": "tipo"}
    df.rename(columns=rename, inplace=True)

    df["monto"] = pd.to_numeric(
        df["monto"].str.replace(".", "").str.replace(",", "."), errors="coerce"
    ).fillna(0)
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    df["mes"] = mes
    df["anio"] = anio

    return df


def filter_operational(df: pd.DataFrame) -> pd.DataFrame:
    """Remove personal transfers and SII payments from operating view."""
    mask = ~df["descripcion"].str.upper().str.contains(
        "|".join(EXCLUDE_DESCRIPTIONS), na=False
    )
    return df[mask].copy()


def get_blueexpress_payment(df: pd.DataFrame) -> float:
    """
    Extract BluExpress payment from cartola.
    Payment in month N = cost devengado in month N-1 (REGLA 4).
    """
    mask = df["descripcion"].str.upper().str.contains("BLUEXPRESS|BLUE EXPRESS", na=False)
    pagos = df[mask & (df["tipo"] == "C")]
    return float(pagos["monto"].sum())


def get_pymespace_payment(df: pd.DataFrame) -> float:
    """Extract Pymespace payment from cartola."""
    mask = df["descripcion"].str.upper().str.contains("PYMESPACE|PYMESP", na=False)
    pagos = df[mask & (df["tipo"] == "C")]
    return float(pagos["monto"].sum())


def load_cartola_to_db(df: pd.DataFrame, engine: Engine) -> int:
    """Append cartola DataFrame to PostgreSQL. Returns rows inserted."""
    df.to_sql("cartola_bancaria", engine, if_exists="append", index=False, method="multi")
    return len(df)
