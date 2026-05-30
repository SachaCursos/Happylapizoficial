"""
populate_peso_cobrado.py

Cruza los CSV de facturación de Blue Express con los pedidos de Shopify
para determinar el "peso cobrado" real por producto y actualizar la columna
peso_cobrado en la tabla shopify_products.

Uso:
    DATABASE_URL=postgresql://... python populate_peso_cobrado.py

Por defecto, busca los CSV en data/blueexpress/. Se puede sobreescribir con:
    DATABASE_URL=... python populate_peso_cobrado.py --csv-dir /otra/ruta

Lógica de matching:
    REFERENCIA "14787-7058182865047" → order_number "#14787"
    REFERENCIA "14626b", "15081dev"   → order_number "#14626", "#15081"
    REFERENCIA "3000IUU"              → order_number "#3000" (menos probable)
"""

import os
import re
import csv
import sys
import argparse
import statistics
import logging
from pathlib import Path
from collections import defaultdict

from sqlalchemy import create_engine, text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    sys.exit("ERROR: falta variable de entorno DATABASE_URL")


# ---------------------------------------------------------------------------
# Parseo de CSVs Blue Express
# ---------------------------------------------------------------------------

def extract_order_number(referencia: str) -> str | None:
    """
    Extrae el número de orden Shopify del campo REFERENCIA de Blue Express.

    Formatos conocidos:
      "14787-7058182865047" → "14787"
      "14626b"              → "14626"
      "15081dev"            → "15081"
      "3000IUU"             → "3000"
      "KITMARCADORES"       → None  (no es un número)
    """
    if not referencia or not referencia.strip():
        return None
    ref = referencia.strip()
    # Formato con guion: tomar la parte antes del guion
    if "-" in ref:
        part = ref.split("-")[0]
    else:
        part = ref
    # Extraer dígitos iniciales
    m = re.match(r"^(\d+)", part)
    if m:
        return m.group(1)
    return None


def parse_blueexpress_csvs(csv_dir: Path) -> list[dict]:
    """Lee todos los CSV de Blue Express en el directorio dado."""
    rows = []
    for csv_path in sorted(csv_dir.glob("*.csv")):
        log.info(f"  Leyendo {csv_path.name}…")
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                with open(csv_path, newline="", encoding=enc) as f:
                    reader = csv.DictReader(f, delimiter=";")
                    file_rows = []
                    for row in reader:
                        peso_s = (row.get("PESO COBRADO") or "").strip().replace(",", ".")
                        ref = (row.get("REFERENCIA") or "").strip()
                        fecha = (row.get("FECHA") or "").strip()
                        destino = (row.get("DESTINO") or "").strip()
                        nombre_dest = (row.get("NOMBRE DESTINATARIO") or "").strip()
                        neto_s = (row.get("NETO") or "").strip().replace(",", ".")
                        os_num = (row.get("OS") or "").strip()

                        try:
                            peso = float(peso_s)
                        except ValueError:
                            continue  # fila sin peso válido

                        if peso <= 0:
                            continue

                        file_rows.append({
                            "referencia": ref,
                            "order_num_extracted": extract_order_number(ref),
                            "fecha": fecha,
                            "destino": destino,
                            "nombre_destinatario": nombre_dest,
                            "peso_cobrado": peso,
                            "neto": float(neto_s) if neto_s else None,
                            "os": os_num,
                        })
                rows.extend(file_rows)
                log.info(f"    → {len(file_rows)} filas (encoding: {enc})")
                break
            except UnicodeDecodeError:
                continue

    log.info(f"Total filas Blue Express: {len(rows)}")
    return rows


# ---------------------------------------------------------------------------
# Matching con Shopify pedidos
# ---------------------------------------------------------------------------

def build_order_map(engine) -> dict[str, list[str]]:
    """
    Devuelve dict: order_number_sin_hash → [titulo_producto, ...]
    Ej: "14787" → ["Pack Mi Primer Taladro"]
    """
    with engine.connect() as conn:
        # Intentar con tabla shopify_pedidos del sync completo (tiene order_number como "#14787")
        rows = conn.execute(text("""
            SELECT p.order_number, lp.titulo, lp.cantidad
            FROM shopify_pedidos p
            JOIN shopify_lineas_pedido lp ON lp.order_id = p.shopify_id
            WHERE p.order_number IS NOT NULL
        """)).fetchall()

    order_map: dict[str, list[str]] = defaultdict(list)
    for order_number, titulo, cantidad in rows:
        if not order_number or not titulo:
            continue
        # Normalizar: "#14787" → "14787", "14787" → "14787"
        num = str(order_number).lstrip("#").strip()
        cantidad = cantidad or 1
        order_map[num].extend([titulo] * cantidad)

    log.info(f"Pedidos en DB: {len(order_map)}")
    return dict(order_map)


def ensure_shopify_products_table(engine) -> None:
    """Crea shopify_products y columna peso_cobrado si no existen."""
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS shopify_products (
                id          SERIAL PRIMARY KEY,
                title       TEXT UNIQUE NOT NULL,
                largo_cm    NUMERIC,
                ancho_cm    NUMERIC,
                alto_cm     NUMERIC,
                peso_fisico_g NUMERIC,
                peso_cobrado  NUMERIC,
                updated_at  TIMESTAMPTZ DEFAULT NOW()
            )
        """))
        # Agrega columna si no existe (para tablas ya existentes)
        conn.execute(text(
            "ALTER TABLE shopify_products ADD COLUMN IF NOT EXISTS peso_cobrado NUMERIC"
        ))
    log.info("Tabla shopify_products verificada (columna peso_cobrado presente)")


# ---------------------------------------------------------------------------
# Cálculo de peso por producto
# ---------------------------------------------------------------------------

def compute_product_weights(
    be_rows: list[dict],
    order_map: dict[str, list[str]],
) -> dict[str, list[float]]:
    """
    Para cada producto, acumula los pesos cobrados de los envíos donde
    ese producto fue el ÚNICO ítem del pedido.

    Devuelve dict: titulo_producto → [peso1, peso2, ...]
    """
    product_weights: dict[str, list[float]] = defaultdict(list)
    matched = 0
    unmatched_no_order = 0
    unmatched_multi = 0

    for row in be_rows:
        num = row["order_num_extracted"]
        if not num:
            unmatched_no_order += 1
            continue

        titulos = order_map.get(num)
        if not titulos:
            unmatched_no_order += 1
            continue

        matched += 1

        # Solo asignamos el peso cuando es un pedido de 1 producto único
        titulos_unicos = list(set(titulos))
        if len(titulos_unicos) == 1:
            product_weights[titulos_unicos[0]].append(row["peso_cobrado"])
        else:
            unmatched_multi += 1

    log.info(f"Envíos matched: {matched}, sin match: {unmatched_no_order}, multi-producto: {unmatched_multi}")
    return dict(product_weights)


def summarize_weights(product_weights: dict[str, list[float]]) -> dict[str, float]:
    """Calcula la mediana de peso por producto."""
    result = {}
    for titulo, pesos in product_weights.items():
        if pesos:
            result[titulo] = round(statistics.median(pesos), 3)
    return result


# ---------------------------------------------------------------------------
# Actualización en DB
# ---------------------------------------------------------------------------

def update_peso_cobrado(engine, peso_por_producto: dict[str, float], dry_run: bool = False) -> None:
    """
    Actualiza shopify_products.peso_cobrado para cada producto.
    Hace matching por titulo usando UPPER(TRIM(...)).
    """
    with engine.connect() as conn:
        existing = conn.execute(text(
            "SELECT title FROM shopify_products"
        )).fetchall()
    existing_titles = {row[0].upper().strip() for row in existing}

    updated = []
    not_found = []

    with engine.begin() as conn:
        for titulo, peso in peso_por_producto.items():
            titulo_upper = titulo.upper().strip()

            if titulo_upper in existing_titles:
                if not dry_run:
                    conn.execute(text("""
                        UPDATE shopify_products
                        SET peso_cobrado = :peso, updated_at = NOW()
                        WHERE UPPER(TRIM(title)) = :titulo
                    """), {"peso": peso, "titulo": titulo_upper})
                updated.append((titulo, peso))
            else:
                # Intentar insertar si no existe
                if not dry_run:
                    conn.execute(text("""
                        INSERT INTO shopify_products (title, peso_cobrado)
                        VALUES (:titulo, :peso)
                        ON CONFLICT (title) DO UPDATE
                            SET peso_cobrado = EXCLUDED.peso_cobrado,
                                updated_at   = NOW()
                    """), {"titulo": titulo, "peso": peso})
                    updated.append((titulo, peso))
                else:
                    not_found.append((titulo, peso))

    prefix = "[DRY RUN] " if dry_run else ""
    log.info(f"{prefix}Productos actualizados: {len(updated)}")
    for t, p in sorted(updated):
        log.info(f"  ✓ {t!r}: {p} kg")

    if not_found:
        log.warning(f"Productos no encontrados en shopify_products (se insertarán en modo real): {len(not_found)}")
        for t, p in sorted(not_found):
            log.warning(f"  ? {t!r}: {p} kg")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Popula peso_cobrado en shopify_products")
    parser.add_argument(
        "--csv-dir",
        default=str(Path(__file__).parent / "data" / "blueexpress"),
        help="Directorio con los CSV de Blue Express (default: data/blueexpress/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo mostrar resultados sin actualizar la DB",
    )
    args = parser.parse_args()

    csv_dir = Path(args.csv_dir)
    if not csv_dir.exists():
        sys.exit(f"ERROR: directorio {csv_dir} no existe")

    engine = create_engine(DATABASE_URL)

    log.info("=== Paso 1: Asegurar estructura de tabla ===")
    ensure_shopify_products_table(engine)

    log.info("=== Paso 2: Leer CSVs de Blue Express ===")
    be_rows = parse_blueexpress_csvs(csv_dir)

    log.info("=== Paso 3: Cargar mapa de pedidos desde DB ===")
    order_map = build_order_map(engine)

    log.info("=== Paso 4: Cruzar envíos con productos ===")
    product_weights = compute_product_weights(be_rows, order_map)

    log.info("=== Paso 5: Calcular medianas ===")
    peso_por_producto = summarize_weights(product_weights)

    log.info(f"\nResumen de pesos calculados ({len(peso_por_producto)} productos):")
    for titulo in sorted(peso_por_producto):
        pesos = product_weights[titulo]
        log.info(f"  {titulo!r}: mediana={peso_por_producto[titulo]} kg (n={len(pesos)}, rango={min(pesos):.2f}-{max(pesos):.2f})")

    log.info("=== Paso 6: Actualizar shopify_products ===")
    update_peso_cobrado(engine, peso_por_producto, dry_run=args.dry_run)

    log.info("=== Completado ===")


if __name__ == "__main__":
    main()
