# Happy Lápiz — Claude Code Project

## Negocio
E-commerce chileno de juguetes educativos.
Canales: Shopify (web principal), Mercado Libre.
Publicidad: Meta Ads únicamente.
Cuenta Meta Ads: act_449499746278703

## Archivos maestros (fuente de verdad)
- products.json → catálogo completo con SKUs, costos, dimensiones, keywords
- commissions.json → comisiones por canal y tasas de IVA
- shipping_rates.json → tarifario BluExpress y costos de fulfillment

## Reglas importantes
- IVA Chile: 19%. Siempre dividir valores brutos por 1.19 para obtener neto
- Moneda: CLP en todo momento
- ROAS mínimo saludable: 3.5x (umbral de alerta)
- El token META_ACCESS_TOKEN expira cada ~60 días → variable de entorno en Railway

## Scripts
- run_weekly.py → reporte semanal (lunes, semana anterior)
- run_monthly.py → reporte mensual (día 1, mes anterior)
- fetch_meta_ads.py → solo fetching de datos
- generate_report.py → solo generación del reporte
- send_report.py → solo envío de email

## Variables de entorno requeridas
META_ACCESS_TOKEN, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, REPORT_EMAIL_TO
