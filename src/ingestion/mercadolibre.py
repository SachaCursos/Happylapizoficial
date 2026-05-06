"""
Parse MercadoLibre monthly XLSX reports for incremental loading.
FULL = numero_de_paquete filled (ML despacha)
FLEX = numero_de_paquete empty (Sacha despacha)
"""

import pandas as pd
from pathlib import Path
from sqlalchemy.engine import Engine


COLUMN_MAP = {
    "N° de factura fiscal": "n_de_factura_fiscal",
    "Fecha del cargo": "fecha_del_cargo",
    "Número del cargo": "numero_del_cargo",
    "Detalle": "detalle",
    "Descontado de la operación": "descontado_de_la_operacion",
    "Estado del cargo": "estado_del_cargo",
    "Cargo que bonifica": "cargo_que_bonifica",
    "Valor del cargo": "valor_del_cargo",
    "Porcentaje por categoría": "porcentaje_por_categoria",
    "Costo por categoría": "costo_por_categoria",
    "Costo fijo": "costo_fijo",
    "Subtotal sin descuento": "subtotal_sin_descuento",
    "Valor del descuento": "valor_del_descuento",
    "Motivo del descuento": "motivo_del_descuento",
    "Número de venta": "numero_de_venta",
    "Pago": "pago",
    "Fecha de venta": "fecha_de_venta",
    "Canal de venta": "canal_de_venta",
    "Cliente": "cliente",
    "Cantidad vendida": "cantidad_vendida",
    "Precio unitario": "precio_unitario",
    "Total de la venta": "total_de_la_venta",
    "Número de envío": "numero_de_envio",
    "Número de paquete": "numero_de_paquete",
    "Envío a cargo del cliente": "envio_a_cargo_del_cliente",
    "Número de publicación": "numero_de_publicacion",
    "Título de publicación": "titulo_de_publicacion",
    "Tipo de publicación": "tipo_de_publicacion",
    "Categoría de la publicación": "categoria_de_la_publicacion",
    "Código ML": "codigo_ml",
    "Sección de Mercado Libre y Mercado Pago": "seccion_de_mercado_libre_y_mercado_pago",
}


def parse_ml_xlsx(filepath: str | Path, mes: int, anio: int) -> pd.DataFrame:
    """Parse a ML billing XLSX and return a clean DataFrame."""
    df = pd.read_excel(filepath, dtype=str)
    df.rename(columns=COLUMN_MAP, inplace=True)
    df.columns = [c.lower().replace(" ", "_") for c in df.columns]

    df["mes"] = mes
    df["anio"] = anio

    numeric_cols = [
        "valor_del_cargo", "total_de_la_venta", "precio_unitario",
        "cantidad_vendida", "subtotal_sin_descuento", "valor_del_descuento",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].str.replace(",", "."), errors="coerce").fillna(0)

    date_cols = ["fecha_del_cargo", "fecha_de_venta"]
    for col in date_cols:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    return df


def load_ml_to_db(df: pd.DataFrame, engine: Engine, table: str = "ml_facturacion") -> int:
    """Append ML DataFrame to PostgreSQL table. Returns rows inserted."""
    df.to_sql(table, engine, if_exists="append", index=False, method="multi")
    return len(df)


def classify_despacho(row: pd.Series) -> str:
    """Return 'FULL' if ML dispatches, 'FLEX' if Sacha dispatches."""
    paquete = str(row.get("numero_de_paquete", "")).strip()
    return "FULL" if paquete else "FLEX"
