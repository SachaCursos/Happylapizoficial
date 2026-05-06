"""
COGS per SKU (already net of IVA).
"""

COGS_NETO: dict[str, float] = {
    "1510": 15630,   # Piedras Musicales Luminosas
    "1230": 6303,    # Pista de Autos Didactica
    "1530": 15210,   # Juego Construccion Magnetico
    "1140": 4622,    # Libro Educativo 3D
    "1210": 5042,    # Pack Mi Primer Taladro
    "1520": 11597,   # Monitor para Vehiculos
    "1150": 3361,    # Marcadores Borde Color
    "1141": 7563,    # Marcadores 60 Doble Punta
    "1142": 11849,   # Marcadores 120 Doble Punta
    "1190": 5042,    # Mochila Educativa Princesas
    "1260": 5882,    # Antigravity Car Rojo
    "1261": 5882,    # Antigravity Car Azul
    "1135": 2521,    # Alfombra Estimuladora Gateo
    "1135b": 2521,   # Alfombra Gateo 1 Metro
    "1139": 3361,    # Kit Caligrafia 4 Tematicas
    "1137": 3146,    # Cascabel Sensorial Bebes
    "DK01": 5882,    # Ducha Koala
    "CD01": 2521,    # Cubo Didactico Bebes
    "1340": 16807,   # Kit de Aseo XL
    "1330": 14034,   # Maleta de Cocina XL
    "1320": 14034,   # Maleta Hospital XL
    "1111": 3361,    # Set Acuarelas Metalizadas
    "1112": 3361,    # Plumones Acrilico 5mm
    "1125": 3361,    # Kit Pintura de Marmol
    "16000": 4202,   # Dibuja Letras con Happy Lapiz
    "16200": 3361,   # Happy Spinners
    "16100": 7563,   # Juguete Cangrejo Induccion
    "1114": 0,       # Curso Online (digital)
}

PRODUCT_NAMES: dict[str, str] = {
    "1510": "Piedras Musicales Luminosas",
    "1230": "Pista de Autos Didactica",
    "1530": "Juego Construccion Magnetico",
    "1140": "Libro Educativo 3D",
    "1210": "Pack Mi Primer Taladro",
    "1520": "Monitor para Vehiculos",
    "1150": "Marcadores Borde Color",
    "1141": "Marcadores 60 Doble Punta",
    "1142": "Marcadores 120 Doble Punta",
    "1190": "Mochila Educativa Princesas",
    "1260": "Antigravity Car Rojo",
    "1261": "Antigravity Car Azul",
    "1135": "Alfombra Estimuladora Gateo",
    "1135b": "Alfombra Gateo 1 Metro",
    "1139": "Kit Caligrafia 4 Tematicas",
    "1137": "Cascabel Sensorial Bebes",
    "DK01": "Ducha Koala",
    "CD01": "Cubo Didactico Bebes",
    "1340": "Kit de Aseo XL",
    "1330": "Maleta de Cocina XL",
    "1320": "Maleta Hospital XL",
    "1111": "Set Acuarelas Metalizadas",
    "1112": "Plumones Acrilico 5mm",
    "1125": "Kit Pintura de Marmol",
    "16000": "Dibuja Letras con Happy Lapiz",
    "16200": "Happy Spinners",
    "16100": "Juguete Cangrejo Induccion",
    "1114": "Curso Online",
}


def get_cogs_neto(sku: str) -> float:
    return COGS_NETO.get(str(sku).strip(), 0.0)


def get_product_name(sku: str) -> str:
    return PRODUCT_NAMES.get(str(sku).strip(), f"SKU {sku}")
