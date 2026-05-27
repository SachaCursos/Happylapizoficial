"""
Fetch Meta Ads data directly from Marketing API.
Account: act_449499746278703 (Cuenta publicitaria happylapiz)
"""

import os
import time
import logging
import requests
import pandas as pd
from datetime import date, timedelta
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
META_AD_ACCOUNT_ID = os.getenv("META_AD_ACCOUNT_ID", "act_449499746278703")
GRAPH_URL = "https://graph.facebook.com/v20.0"

DDL_META_HISTORICO = """
CREATE TABLE IF NOT EXISTS meta_gasto_diario (
    id          SERIAL PRIMARY KEY,
    dia         DATE NOT NULL UNIQUE,
    gasto       NUMERIC,
    impresiones INTEGER,
    clics       INTEGER,
    ctr         NUMERIC,
    cpm         NUMERIC,
    cpc         NUMERIC,
    synced_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS meta_campanas_historico (
    id           SERIAL PRIMARY KEY,
    anio         INTEGER NOT NULL,
    mes          INTEGER NOT NULL,
    campaign_id  TEXT NOT NULL,
    nombre       TEXT,
    gasto        NUMERIC,
    impresiones  INTEGER,
    clics        INTEGER,
    compras      NUMERIC,
    ingresos     NUMERIC,
    roas         NUMERIC,
    synced_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (anio, mes, campaign_id)
);
"""


def _get(endpoint: str, params: dict, token: str = None) -> dict:
    p = dict(params)
    p["access_token"] = token or META_ACCESS_TOKEN
    resp = requests.get(f"{GRAPH_URL}/{endpoint}", params=p, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _month_range(anio: int, mes: int) -> tuple[str, str]:
    """Return (date_from, date_to) strings for a given month."""
    date_from = f"{anio}-{mes:02d}-01"
    if mes < 12:
        last = date(anio, mes + 1, 1) - timedelta(days=1)
    else:
        last = date(anio + 1, 1, 1) - timedelta(days=1)
    return date_from, last.strftime("%Y-%m-%d")


def fetch_meta_ads(anio: int, mes: int) -> dict:
    """
    Fetch campaigns and daily spend from Meta Marketing API for given month.
    Returns dict compatible with parse_meta_json output.
    """
    if not META_ACCESS_TOKEN:
        return _empty()

    date_from, date_to = _month_range(anio, mes)
    time_range = f"{{\"since\":\"{date_from}\",\"until\":\"{date_to}\"}}"

    try:
        daily_data = _get(f"{META_AD_ACCOUNT_ID}/insights", {
            "fields": "date_start,spend,impressions,clicks,ctr,cpm,cpc",
            "time_increment": "1",
            "time_range": time_range,
            "limit": 90,
        })
        rows_daily = daily_data.get("data", [])
        df_daily = pd.DataFrame([{
            "dia": r["date_start"],
            "gasto_clp": float(r.get("spend", 0)),
            "impresiones": int(r.get("impressions", 0)),
            "clics": int(r.get("clicks", 0)),
            "ctr": float(r.get("ctr", 0)),
            "cpm": float(r.get("cpm", 0)),
            "cpc": float(r.get("cpc", 0)),
        } for r in rows_daily])
        if not df_daily.empty:
            df_daily["dia"] = pd.to_datetime(df_daily["dia"])

        camp_data = _get(f"{META_AD_ACCOUNT_ID}/insights", {
            "fields": "campaign_id,campaign_name,spend,impressions,clicks,actions,action_values",
            "time_range": time_range,
            "level": "campaign",
            "limit": 100,
        })
        rows_camp = camp_data.get("data", [])
        campanas = []
        for r in rows_camp:
            gasto = float(r.get("spend", 0))
            actions = {a["action_type"]: float(a["value"]) for a in r.get("actions", [])}
            action_vals = {a["action_type"]: float(a["value"]) for a in r.get("action_values", [])}
            compras = actions.get("purchase", 0)
            ingresos = action_vals.get("purchase", 0)
            roas = ingresos / gasto if gasto > 0 else 0
            campanas.append({
                "id": r.get("campaign_id", ""),
                "nombre": r.get("campaign_name", ""),
                "gasto_clp": gasto,
                "impresiones": int(r.get("impressions", 0)),
                "clics": int(r.get("clicks", 0)),
                "compras": compras,
                "ingresos_clp": ingresos,
                "roas": round(roas, 2),
            })
        df_campanas = pd.DataFrame(campanas) if campanas else pd.DataFrame(
            columns=["id", "nombre", "gasto_clp", "impresiones", "clics", "compras", "ingresos_clp", "roas"]
        )

        return {
            "meta": {"cuenta": META_AD_ACCOUNT_ID, "periodo": f"{date_from}/{date_to}"},
            "gasto_diario": df_daily,
            "campanas": df_campanas,
            "adsets": pd.DataFrame(),
        }

    except Exception as e:
        log.error(f"[meta_api] fetch_meta_ads({anio}-{mes:02d}): {e}")
        return _empty()


def get_meta_gasto_mes(anio: int, mes: int) -> float:
    """Total spend for the month in the account's currency."""
    data = fetch_meta_ads(anio, mes)
    df = data.get("gasto_diario", pd.DataFrame())
    if df.empty:
        return 0.0
    return float(df["gasto_clp"].sum())


# ---------------------------------------------------------------------------
# Historical backfill
# ---------------------------------------------------------------------------

def ensure_meta_tables(engine: Engine) -> None:
    with engine.begin() as conn:
        for stmt in DDL_META_HISTORICO.split(";"):
            s = stmt.strip()
            if s:
                conn.execute(text(s))


def _save_month_to_db(engine: Engine, anio: int, mes: int, data: dict) -> dict:
    """Upsert one month of Meta data into the historico tables. Returns row counts."""
    df_daily: pd.DataFrame = data.get("gasto_diario", pd.DataFrame())
    df_camp: pd.DataFrame = data.get("campanas", pd.DataFrame())

    n_daily = 0
    n_camp = 0

    with engine.begin() as conn:
        if not df_daily.empty:
            for _, row in df_daily.iterrows():
                conn.execute(text("""
                    INSERT INTO meta_gasto_diario
                        (dia, gasto, impresiones, clics, ctr, cpm, cpc)
                    VALUES
                        (:dia, :gasto, :imp, :clics, :ctr, :cpm, :cpc)
                    ON CONFLICT (dia) DO UPDATE SET
                        gasto       = EXCLUDED.gasto,
                        impresiones = EXCLUDED.impresiones,
                        clics       = EXCLUDED.clics,
                        ctr         = EXCLUDED.ctr,
                        cpm         = EXCLUDED.cpm,
                        cpc         = EXCLUDED.cpc,
                        synced_at   = NOW()
                """), {
                    "dia":   str(row.get("dia", ""))[:10],
                    "gasto": float(row.get("gasto_clp", 0)),
                    "imp":   int(row.get("impresiones", 0)),
                    "clics": int(row.get("clics", 0)),
                    "ctr":   float(row.get("ctr", 0)),
                    "cpm":   float(row.get("cpm", 0)),
                    "cpc":   float(row.get("cpc", 0)),
                })
                n_daily += 1

        if not df_camp.empty:
            for _, row in df_camp.iterrows():
                conn.execute(text("""
                    INSERT INTO meta_campanas_historico
                        (anio, mes, campaign_id, nombre, gasto,
                         impresiones, clics, compras, ingresos, roas)
                    VALUES
                        (:anio, :mes, :cid, :nombre, :gasto,
                         :imp, :clics, :compras, :ingresos, :roas)
                    ON CONFLICT (anio, mes, campaign_id) DO UPDATE SET
                        nombre      = EXCLUDED.nombre,
                        gasto       = EXCLUDED.gasto,
                        impresiones = EXCLUDED.impresiones,
                        clics       = EXCLUDED.clics,
                        compras     = EXCLUDED.compras,
                        ingresos    = EXCLUDED.ingresos,
                        roas        = EXCLUDED.roas,
                        synced_at   = NOW()
                """), {
                    "anio":    anio,
                    "mes":     mes,
                    "cid":     str(row.get("id", "")),
                    "nombre":  str(row.get("nombre", "")),
                    "gasto":   float(row.get("gasto_clp", 0)),
                    "imp":     int(row.get("impresiones", 0)),
                    "clics":   int(row.get("clics", 0)),
                    "compras": float(row.get("compras", 0)),
                    "ingresos": float(row.get("ingresos_clp", 0)),
                    "roas":    float(row.get("roas", 0)),
                })
                n_camp += 1

    return {"dias": n_daily, "campanas": n_camp}


def backfill_meta_historico(
    engine: Engine,
    desde: tuple[int, int],
    hasta: tuple[int, int] | None = None,
    sleep_between: float = 1.0,
) -> list[dict]:
    """
    Fetch and store Meta Ads data month by month from `desde` to `hasta`.

    Args:
        engine:          SQLAlchemy engine pointing to PostgreSQL.
        desde:           (anio, mes) start, e.g. (2023, 1).
        hasta:           (anio, mes) end inclusive. Defaults to current month.
        sleep_between:   Seconds to wait between API calls to avoid rate limits.

    Returns:
        List of dicts with one entry per processed month.
    """
    if not META_ACCESS_TOKEN:
        raise RuntimeError("META_ACCESS_TOKEN no configurado")

    ensure_meta_tables(engine)

    if hasta is None:
        today = date.today()
        hasta = (today.year, today.month)

    results = []
    anio, mes = desde

    while (anio, mes) <= hasta:
        label = f"{anio}-{mes:02d}"
        log.info(f"[meta_historico] Fetching {label}…")
        try:
            data = fetch_meta_ads(anio, mes)
            n = _save_month_to_db(engine, anio, mes, data)
            gasto_total = (
                data["gasto_diario"]["gasto_clp"].sum()
                if not data["gasto_diario"].empty else 0
            )
            results.append({
                "periodo": label,
                "dias_guardados": n["dias"],
                "campanas_guardadas": n["campanas"],
                "gasto_total": round(float(gasto_total), 2),
                "ok": True,
            })
            log.info(f"  ✓ {label}: {n['dias']} días, {n['campanas']} campañas, gasto={gasto_total:.0f}")
        except Exception as e:
            log.error(f"  ✗ {label}: {e}")
            results.append({"periodo": label, "ok": False, "error": str(e)})

        # Advance to next month
        if mes == 12:
            anio += 1
            mes = 1
        else:
            mes += 1

        time.sleep(sleep_between)

    return results


def _empty() -> dict:
    return {
        "meta": {},
        "gasto_diario": pd.DataFrame(columns=["dia", "gasto_clp", "impresiones", "clics", "ctr", "cpm", "cpc"]),
        "campanas": pd.DataFrame(columns=["id", "nombre", "gasto_clp", "impresiones", "clics", "compras", "ingresos_clp", "roas"]),
        "adsets": pd.DataFrame(),
    }
