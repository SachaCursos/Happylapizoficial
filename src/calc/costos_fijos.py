"""
Normalized monthly fixed costs in CLP neto.
Total: ~1,038,550 CLP/month.
Weekly proration: 1,038,550 / 4.33 = ~239,894 CLP/week.
"""

COSTOS_FIJOS_MENSUAL: dict[str, float] = {
    "Previred (remuneraciones)": 407000,
    "Bodega": 183000,
    "Klaviyo": 137000,
    "Shopify plan anual": 44900,
    "Zoko (WhatsApp)": 38400,
    "Revie": 14500,
    "Selleasy": 5400,
    "Contabilidad": 67227,
    "ChatGPT": 18300,
    "Claude.ai": 21900,
    "Microsoft 365": 7900,
    "Canva": 8300,
    "GoDaddy": 8333,
    "Mantencion TC Santander": 15500,
    "Oficina virtual": 13500,
    "WOM": 16807,
    "Cuenta bancaria": 16807,
}

TOTAL_CF_MENSUAL: float = sum(COSTOS_FIJOS_MENSUAL.values())
TOTAL_CF_SEMANAL: float = TOTAL_CF_MENSUAL / 4.33

PYMESPACE_TARIFAS = [
    (400, 1000),   # <= 400 pedidos → 1000 neto/pedido
    (600, 735),    # 401-600 pedidos → 735 neto/pedido
    (float("inf"), 600),  # >600 pedidos → 600 neto/pedido
]


def pymespace_costo(pedidos: int) -> float:
    """Calculate Pymespace cost based on actual order count (REGLA 5)."""
    for limite, tarifa in PYMESPACE_TARIFAS:
        if pedidos <= limite:
            return pedidos * tarifa
    return pedidos * 600
