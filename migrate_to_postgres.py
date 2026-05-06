"""
migrate_to_postgres.py
Initial migration script to create and populate all PostgreSQL tables.
Run once: python migrate_to_postgres.py

Tables created:
  meta_ads_diario, shopify_ventas_historico, shopify_ventas_2025,
  shopify_facturas, ml_facturacion, ml_cargos_full,
  ml_notas_credito_flex, ml_notas_credito, ml_notas_debito_flex,
  ml_notas_credito_mp, mp_facturacion, ml_pagos_facturas,
  cartola_bancaria, marketing_keywords, blueexpress_tarifario
"""

import os
import pandas as pd
from pathlib import Path
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL", "")

if not DATABASE_URL:
    raise ValueError("Set DATABASE_URL environment variable before running migrations.")

engine = create_engine(DATABASE_URL)

DATA_DIR = Path("data")

DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS meta_ads_diario (
        id SERIAL PRIMARY KEY,
        nombre_de_la_campana TEXT,
        nombre_del_conjunto_de_anuncios TEXT,
        nombre_del_anuncio TEXT,
        dia DATE,
        tipo_de_resultado TEXT,
        resultados NUMERIC,
        importe_gastado_clp NUMERIC,
        inicio_del_informe DATE,
        fin_del_informe DATE,
        anio INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS shopify_ventas_historico (
        id SERIAL PRIMARY KEY,
        nombre_del_producto TEXT,
        precio_de_la_variante_de_producto NUMERIC,
        dia DATE,
        nombre_del_cliente TEXT,
        ciudad_del_envio TEXT,
        ventas_totales NUMERIC
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS shopify_ventas_2025 (
        id SERIAL PRIMARY KEY,
        titulo_del_producto TEXT,
        precio_de_la_variante_de_producto NUMERIC,
        dia DATE,
        nombre_del_cliente TEXT,
        ciudad_del_envio TEXT,
        id_de_pedido TEXT,
        ventas_totales NUMERIC,
        anio INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS shopify_facturas (
        id SERIAL PRIMARY KEY,
        bill_ TEXT,
        store_name TEXT,
        shop_id TEXT,
        myshopifycom_url TEXT,
        charge_category TEXT,
        description TEXT,
        amount NUMERIC,
        currency TEXT,
        start_of_billing_cycle DATE,
        end_of_billing_cycle DATE,
        date DATE,
        "order" TEXT,
        rate NUMERIC,
        app TEXT,
        original_amount NUMERIC,
        original_currency TEXT,
        exchange_rate NUMERIC
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ml_facturacion (
        id SERIAL PRIMARY KEY,
        n_de_factura_fiscal TEXT,
        fecha_del_cargo DATE,
        numero_del_cargo TEXT,
        detalle TEXT,
        descontado_de_la_operacion TEXT,
        estado_del_cargo TEXT,
        cargo_que_bonifica TEXT,
        valor_del_cargo NUMERIC,
        porcentaje_por_categoria NUMERIC,
        costo_por_categoria NUMERIC,
        costo_fijo NUMERIC,
        subtotal_sin_descuento NUMERIC,
        valor_del_descuento NUMERIC,
        motivo_del_descuento TEXT,
        numero_de_venta TEXT,
        pago TEXT,
        fecha_de_venta DATE,
        canal_de_venta TEXT,
        cliente TEXT,
        cantidad_vendida NUMERIC,
        precio_unitario NUMERIC,
        total_de_la_venta NUMERIC,
        numero_de_envio TEXT,
        numero_de_paquete TEXT,
        envio_a_cargo_del_cliente NUMERIC,
        numero_de_publicacion TEXT,
        titulo_de_publicacion TEXT,
        tipo_de_publicacion TEXT,
        categoria_de_la_publicacion TEXT,
        codigo_ml TEXT,
        seccion_de_mercado_libre_y_mercado_pago TEXT,
        mes INTEGER,
        anio INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ml_cargos_full (
        id SERIAL PRIMARY KEY,
        n_de_factura TEXT,
        fecha_de_cargo DATE,
        n_de_cargo TEXT,
        detalle TEXT,
        n_de_cargo_bonificado TEXT,
        mes INTEGER,
        anio INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ml_notas_credito_flex (
        id SERIAL PRIMARY KEY,
        mes INTEGER,
        anio INTEGER,
        data JSONB
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ml_notas_credito (
        id SERIAL PRIMARY KEY,
        mes INTEGER,
        anio INTEGER,
        data JSONB
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ml_notas_debito_flex (
        id SERIAL PRIMARY KEY,
        mes INTEGER,
        anio INTEGER,
        data JSONB
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ml_notas_credito_mp (
        id SERIAL PRIMARY KEY,
        mes INTEGER,
        anio INTEGER,
        data JSONB
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mp_facturacion (
        id SERIAL PRIMARY KEY,
        n_de_factura_fiscal TEXT,
        fecha_del_cargo DATE,
        numero_del_cargo TEXT,
        numero_del_movimiento TEXT,
        detalle TEXT,
        cobrado_en_la_operacion TEXT,
        estado_del_cargo TEXT,
        cargo_que_anula TEXT,
        valor_del_cargo NUMERIC,
        tipo_de_operacion TEXT,
        operacion_relacionada TEXT,
        tipo_de_pago TEXT,
        numero_de_sucursal TEXT,
        nombre_de_sucursal TEXT,
        referencia_externa TEXT,
        cliente TEXT,
        valor_de_la_operacion NUMERIC,
        seccion_de_mercado_libre_y_de_mercado_pago TEXT,
        mes INTEGER,
        anio INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ml_pagos_facturas (
        id SERIAL PRIMARY KEY,
        mes INTEGER,
        anio INTEGER,
        data JSONB
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cartola_bancaria (
        id SERIAL PRIMARY KEY,
        monto NUMERIC,
        descripcion TEXT,
        fecha DATE,
        tipo CHAR(1),
        mes INTEGER,
        anio INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS marketing_keywords (
        id SERIAL PRIMARY KEY,
        producto TEXT NOT NULL,
        palabras TEXT NOT NULL,
        prioridad INTEGER DEFAULT 10
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS blueexpress_tarifario (
        id SERIAL PRIMARY KEY,
        campo TEXT,
        detalle TEXT
    )
    """,
]

MARKETING_KEYWORDS_SEED = [
    ("Piedras Musicales Luminosas", "piedras musicales,piedras,black friday", 1),
    ("Pista de Autos Didactica", "pista de autos,no necesita pilas,los ninos aman esta pista", 2),
    ("Libro Educativo 3D", "estuche educativo,libro educativo,tepasoeldato", 3),
    ("Pack Mi Primer Taladro", "mi primer taladro,fernanda,melissa", 4),
    ("Juego Construccion Magnetico", "construccion,magnetico", 5),
]


def run_migrations():
    with engine.begin() as conn:
        for ddl in DDL_STATEMENTS:
            conn.execute(text(ddl.strip()))
        print("✓ Tablas creadas/verificadas")

        result = conn.execute(text("SELECT COUNT(*) FROM marketing_keywords"))
        count = result.scalar()
        if count == 0:
            for producto, palabras, prioridad in MARKETING_KEYWORDS_SEED:
                conn.execute(
                    text("INSERT INTO marketing_keywords (producto, palabras, prioridad) VALUES (:p, :w, :pr)"),
                    {"p": producto, "w": palabras, "pr": prioridad},
                )
            print(f"✓ Insertadas {len(MARKETING_KEYWORDS_SEED)} keywords de campañas")
        else:
            print(f"✓ marketing_keywords ya tiene {count} filas, sin cambios")

    print("\nMigración completada exitosamente.")
    print("Ahora puedes cargar los datos históricos desde la carpeta data/")


if __name__ == "__main__":
    run_migrations()
