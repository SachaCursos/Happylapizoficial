"""
load_meta_historico.py
Descarga el historial de Meta Ads mes a mes desde la Marketing API y lo guarda
en PostgreSQL (tablas meta_gasto_diario + meta_campanas_historico).

Uso:
    META_ACCESS_TOKEN=<token> DATABASE_URL=postgresql://... \\
        python load_meta_historico.py --desde 2023-01 [--hasta 2025-12]

Si se omite --hasta, se usa el mes actual.
"""

import argparse
import logging
import os
import sys
from sqlalchemy import create_engine

from src.ingestion.meta_api import backfill_meta_historico

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def parse_ym(s: str) -> tuple[int, int]:
    try:
        parts = s.split("-")
        return int(parts[0]), int(parts[1])
    except Exception:
        raise argparse.ArgumentTypeError(f"Formato inválido '{s}'. Usar YYYY-MM")


def main():
    parser = argparse.ArgumentParser(description="Backfill histórico Meta Ads → PostgreSQL")
    parser.add_argument("--desde", required=True, type=parse_ym, metavar="YYYY-MM",
                        help="Mes inicial, ej: 2023-01")
    parser.add_argument("--hasta", default=None, type=parse_ym, metavar="YYYY-MM",
                        help="Mes final inclusive (default: mes actual)")
    parser.add_argument("--sleep", default=1.0, type=float,
                        help="Segundos entre llamadas API (default: 1.0)")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        sys.exit("ERROR: falta variable de entorno DATABASE_URL")

    token = os.getenv("META_ACCESS_TOKEN", "")
    if not token:
        sys.exit("ERROR: falta variable de entorno META_ACCESS_TOKEN")

    engine = create_engine(database_url)

    desde_label = f"{args.desde[0]}-{args.desde[1]:02d}"
    hasta_label = f"{args.hasta[0]}-{args.hasta[1]:02d}" if args.hasta else "mes actual"
    log.info(f"Iniciando backfill Meta Ads: {desde_label} → {hasta_label}")

    results = backfill_meta_historico(
        engine=engine,
        desde=args.desde,
        hasta=args.hasta,
        sleep_between=args.sleep,
    )

    ok = [r for r in results if r.get("ok")]
    fail = [r for r in results if not r.get("ok")]
    total_gasto = sum(r.get("gasto_total", 0) for r in ok)
    total_dias = sum(r.get("dias_guardados", 0) for r in ok)
    total_camp = sum(r.get("campanas_guardadas", 0) for r in ok)

    log.info("\n=== Resumen ===")
    log.info(f"  Meses procesados: {len(ok)} ok, {len(fail)} con error")
    log.info(f"  Días guardados:   {total_dias:,}")
    log.info(f"  Campañas (filas): {total_camp:,}")
    log.info(f"  Gasto total:      {total_gasto:,.0f}")

    if fail:
        log.warning("  Meses con error:")
        for r in fail:
            log.warning(f"    {r['periodo']}: {r.get('error')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
