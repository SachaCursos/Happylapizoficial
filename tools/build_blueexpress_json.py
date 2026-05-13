"""
Convierte el tarifario BluExpress (.xlsx) a data/blueexpress_tariff.json.

Uso:
    python tools/build_blueexpress_json.py <ruta_al_xlsx>

Ejecutar una sola vez cuando se actualice el tarifario.
El archivo generado queda en data/blueexpress_tariff.json y se commitea al repo.

Estructura del XLSX (Nov 2024):
  Sheet: "COMUNAS - HOME DELIVERY"
  Fila 5: encabezado — col C=REGION, D=POSTA, E=COMUNA DESTINO, F-N=tramos de peso
  Fila 6+: datos por comuna
  Tramos: 0-0.5 | 0.5-1.5 | 1.5-3 | 3-6 | 6-10 | 10-16 | 16-25 | 25-50* | >50*
  Nota: tramos 25-50 y >50 son tarifa por kg adicional (cobro variable).
"""

import json
import os
import sys

import openpyxl


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_FILE = os.path.join(BASE_DIR, "data", "blueexpress_tariff.json")

FACTOR_VOLUMETRICO = 4000  # cm³/kg
IVA = 1.19
SHEET_NAME = "COMUNAS - HOME DELIVERY"
HEADER_ROW = 5   # fila con nombres de columnas
DATA_START_ROW = 6


def build_tariff(xlsx_path):
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)

    if SHEET_NAME not in wb.sheetnames:
        print(f"ERROR: No se encontró la hoja '{SHEET_NAME}'.", file=sys.stderr)
        print(f"Hojas disponibles: {wb.sheetnames}", file=sys.stderr)
        sys.exit(1)

    ws = wb[SHEET_NAME]
    rows = list(ws.iter_rows(min_row=1, values_only=True))

    # Leer encabezado (fila 5, índice 4)
    header = rows[HEADER_ROW - 1]
    # col[2]=REGION, col[3]=POSTA, col[4]=COMUNA, col[5..]=tramos
    # Solo incluir celdas que parecen tramos de peso (contienen dígitos y guión o >)
    weight_labels = [
        str(h) for h in header[5:]
        if h is not None and any(c.isdigit() for c in str(h))
        and ("-" in str(h) or ">" in str(h))
    ]
    # Extraer límite superior de cada tramo para comparación numérica
    # Ej: "0 - 0.5" → 0.5, "0.5 - 1.5" → 1.5, "> 50 *" → 9999
    weight_bands = []
    for label in weight_labels:
        label_clean = label.replace("*", "").strip()
        if label_clean.startswith(">"):
            weight_bands.append(9999.0)
        else:
            parts = label_clean.split("-")
            try:
                weight_bands.append(float(parts[-1].strip()))
            except (ValueError, IndexError):
                weight_bands.append(9999.0)

    print(f"Tramos: {weight_labels}")
    print(f"Límites superiores: {weight_bands}")

    comunas = {}
    for row in rows[DATA_START_ROW - 1:]:
        # col indices: 2=region, 3=posta, 4=comuna, 5..=tarifas
        if not row or row[4] is None:
            continue

        comuna_name = str(row[4]).strip().upper()
        if not comuna_name or comuna_name in ("COMUNA DESTINO", ""):
            continue

        posta = str(row[3]).strip() if row[3] is not None else ""
        region = str(row[2]).strip() if row[2] is not None else ""

        tarifas_bruto = []
        for cell in row[5: 5 + len(weight_bands)]:
            try:
                tarifas_bruto.append(float(cell) if cell is not None else None)
            except (ValueError, TypeError):
                tarifas_bruto.append(None)

        tarifas_neto = [
            round(p / IVA, 0) if p is not None else None
            for p in tarifas_bruto
        ]

        comunas[comuna_name] = {
            "region": region,
            "posta": posta,
            "tarifas_neto": tarifas_neto,
        }

    output = {
        "fuente": "BluExpress propuesta nov-2024",
        "factor_volumetrico": FACTOR_VOLUMETRICO,
        "iva_aplicado": IVA,
        "nota": (
            "tarifas_neto en CLP sin IVA. "
            "Tramos 0-16 kg: tarifa plana por peso cobrado. "
            "Tramos 25-50 y >50: tarifa por kg (cobro variable)."
        ),
        "tramos_labels": weight_labels,
        "tramos_kg_max": weight_bands,
        "comunas": comunas,
    }

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"✅ {OUTPUT_FILE} generado — {len(comunas)} comunas")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Uso: python {sys.argv[0]} <ruta_al_xlsx>")
        sys.exit(1)
    build_tariff(sys.argv[1])
