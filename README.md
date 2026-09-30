# Cliente API Mr Bot (Mis Comprobantes y módulos AFIP)

Cliente Tkinter y librerías Python para usar los endpoints de api-bots.mrbot.com.ar (Mis Comprobantes, RCEL, SCT, SRT Alícuotas ART, CCMA, Apócrifos y Consulta CUIT), más el módulo `Webservices` para generación automática de token/sign vía API de certificados y apertura directa de DFE/Facturación web.

## Contenido rápido
- Qué necesitas
- Instalación y configuración
- Ejecutar la GUI
- Uso programático
- Estructura del proyecto
- Endpoints y módulos clave
- Tests y soporte
- Releases por tag (GitHub Actions)

## Qué necesitas
- Python 3.8+
- Cuenta y API key en api-bots.mrbot.com.ar
- Dependencias: `pip install -r requirements.txt`

## Instalación y configuración
```bash
git clone https://github.com/abustosp/bot-mis-comprobantes-cliente.git
cd bot-mis-comprobantes-cliente
python3 -m venv venv
source venv/bin/activate          # en Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # edita con tus credenciales
```

`.env` mínimo:
```env
URL=https://api-bots.mrbot.com.ar
MAIL=tu_email@ejemplo.com
API_KEY=tu_api_key
NOTIFICACION_MESSAGEBOX=false
```

Si `NOTIFICACION_MESSAGEBOX=true`, la GUI muestra un `messagebox` al finalizar los procesos masivos con descarga:
- `info` si todas las descargas fueron correctas.
- `warning` si hubo advertencias o fallas parciales.
- `error` si todas fallaron.

### Credenciales cifradas en tránsito
La clave fiscal nunca viaja en texto plano: el cliente la cifra con la clave pública RSA
del servidor y la envía en el campo `clave_encriptada` de cada request de bots
(consultas individuales y masivas de todos los módulos).
- Clave pública: `GET /api/v1/security/public-key` (RSA-OAEP-SHA256, respuesta Base64).
- Si el servidor no responde o responde con un algoritmo inesperado, la request **no se envía**
  (no hay fallback a texto plano).
- El log de la GUI muestra el body tal como se envía: `clave_encriptada` con su valor **sin
  censurar** (es el único caso donde no se censura, porque ya está cifrado). La clave en texto
  plano no se escribe nunca en el log ni en los archivos de salida.
- La clave pública se cachea en memoria; si el servidor rota su par de claves, el primer
  request falla con `No se pudo desencriptar la credencial` y la cache se invalida sola,
  así la consulta siguiente ya usa la clave nueva.
- Opcional en `.env`: `PUBLIC_KEY_CACHE_SEC` (segundos de cache, default `3600`).

```env
PUBLIC_KEY_CACHE_SEC=3600
```

Para Webservices (token/sign automático vía API de certificados):
```env
WSAA_TESTING=true
WSAA_SERVICE=veconsumerws
CERT_API_URL=https://api-certificados.mrbot.com.ar/
CERT_API_EMAIL=tu_email_api_certificados
CERT_API_KEY=tu_api_key_api_certificados
CERT_API_CN=mrbot
CERT_API_CUIT_REPRESENTANTE=20300111222
AFIP_CERT_PATH=/ruta/certificado.crt
AFIP_KEY_PATH=/ruta/llave_privada.key
```

Archivos de entrada:
- `Descarga-Mis-Comprobantes.xlsx` o `.csv` (plantillas en la raíz).
- Excels de ejemplo en `ejemplos_api/` (la GUI los genera si faltan).

## Ejecutar la GUI
```bash
python mrbot.py
```
Desde la GUI puedes:
- Editar base URL, API key y mail.
- Procesar Mis Comprobantes masivo (usa `mrbot_app.mis_comprobantes.consulta_mc_csv`).
- Consultar RCEL, SCT, SRT Alícuotas ART, CCMA, Apócrifos y CUIT (individual/masivo según módulo).
- Descargar Mis Retenciones y Mis Retenciones IVA Simple (individual o masivo por Excel).
  - El Excel de Mis Retenciones acepta la columna opcional `exportar_para_aplicativo` (default `NO`):
    con `SI` exporta para SIAP y solo admite los impuestos 216, 217, 219, 767 y 353. Si la celda
    queda vacía (o la columna no está), se usa el check "Exportar para aplicativo" de la ventana.
  - El Excel de Mis Retenciones IVA Simple usa las columnas `procesar`, `cuit_representante`,
    `clave_representante`, `cuit_representado`, `denominacion`, `desde`, `hasta`,
    `ubicacion_descarga`, `proxy_request` y `retry` (plantilla en `ejemplos_api/`).
- Abrir `Webservices` y seleccionar servicio (`veconsumerws` o `wsfe`).
- Generar token/sign desde `https://api-certificados.mrbot.com.ar/` (sin consumir API middleware de e-ventanilla).
- Guardar el body del response en JSON con `Guardar token y sign` en `descargas/webservices/{servicio}/{cuit}/`.
- Abrir la web del servicio con `Abrir servicio web`:
  - `veconsumerws` -> `https://e-ventanilla.mrbot.com.ar/`
  - `wsfe` -> `https://facturador-web.mrbot.com.ar/`
- Previsualizar Excels y descargar archivos desde MinIO.

## Uso programático
```python
from mrbot_app.mis_comprobantes import consulta_mc, consulta_mc_csv

# Consulta individual
resp = consulta_mc(
    desde="01/01/2024",
    hasta="31/01/2024",
    cuit_inicio_sesion="20123456780",
    representado_nombre="EMPRESA SA",
    representado_cuit="30876543210",
    contrasena="clave",
    descarga_emitidos=True,
    descarga_recibidos=True,
    carga_minio=True,
    carga_json=True,
)

# Procesamiento masivo (Excel/CSV)
consulta_mc_csv("./ejemplos_api/mis_comprobantes.xlsx")
```

Descarga desde MinIO con workers concurrentes:
```python
from mrbot_app.consulta import descargar_archivos_minio_concurrente

archivos = [
    {"url": resp["mis_comprobantes_emitidos_url_minio"], "destino": "./emitidos.zip"},
    {"url": resp["mis_comprobantes_recibidos_url_minio"], "destino": "./recibidos.zip"},
]
resultados = descargar_archivos_minio_concurrente(archivos, max_workers=10)
```

## Estructura del proyecto
```
.
├── mrbot.py                 # Menú principal GUI
├── mrbot_app/               # Helpers y ventanas Tkinter por módulo
│   ├── consulta.py          # Descargas MinIO y requests restantes
│   ├── helpers.py
│   ├── seguridad.py         # Cifrado RSA de la clave fiscal (clave_encriptada)
│   ├── mis_comprobantes.py  # Lógica Mis Comprobantes (consulta y CSV masivo)
│   ├── wsaa.py              # Obtención token/sign (veconsumerws/wsfe) vía api-certificados
│   └── windows/             # mis_comprobantes, rcel, sct, srt_alicuotas, ccma, apocrifos, consulta_cuit, webservices (token/sign)
├── ejemplos_api/            # Excels de ejemplo (autogenerables)
├── Descarga-Mis-Comprobantes.{csv,xlsx}
├── tests/                   # Tests existentes (reubicados)
├── requirements.txt
├── README.md
└── LICENSE
```

## Endpoints y módulos clave
- Credenciales: `GET /api/v1/security/public-key` (clave pública para cifrar la clave fiscal, ver “Credenciales cifradas en tránsito”)
- Mis Comprobantes: `POST /api/v1/mis_comprobantes/consulta` (GUI: “Descarga Mis Comprobantes”, código: `mrbot_app.mis_comprobantes.consulta_mc`)
- RCEL: `POST /api/v1/rcel/consulta` (GUI: ventana RCEL)
- SCT: `POST /api/v1/sct/consulta` (GUI: ventana SCT con descargas MinIO)
- SRT Alícuotas ART: `POST /api/v1/srt/alicuotas/consulta` (GUI: ventana SRT con JSON individual por CUIT y Excel consolidado)
- CCMA: `POST /api/v1/ccma/consulta`
- Apócrifos: `GET /api/v1/apoc/consulta/{cuit}`
- Consulta CUIT: `POST /api/v1/consulta_cuit/{individual|masivo}`
- Requests restantes: `GET /api/v1/user/consultas/{email}`
- Webservices:
  - Token/sign automático: `POST https://api-certificados.mrbot.com.ar/api/v1/token_sign/`
  - Servicios soportados por GUI: `veconsumerws` (DFE) y `wsfe` (Facturación)
  - Apertura web directa por servicio:
    - `https://e-ventanilla.mrbot.com.ar/`
    - `https://facturador-web.mrbot.com.ar/`

Helpers reutilizables: `mrbot_app/helpers.py` (safe_get/safe_post, previews de DataFrame, parseo de booleanos, etc.).

## Tests y validación
```bash
python -m py_compile mrbot.py mrbot_app/*.py mrbot_app/windows/*.py
# Tests de cifrado de credenciales (herméticos, sin red ni credenciales)
pytest tests/test_seguridad_clave.py tests/test_claves_cifradas_en_requests.py
# Ventanas reales (UI y Excel) contra transporte mockeado; requiere display
pytest tests/test_ventanas_clave_encriptada.py
# Tests (algunos requieren credenciales/Excels)
pytest tests  # o python tests/test_sct_descarga.py o python tests/test_srt_alicuotas_descarga.py
```

## Releases por tag (GitHub Actions)
Al crear y pushear una tag, GitHub Actions compila el ejecutable para Linux y Windows, arma los ZIPs desde `./Ejecutable` y los publica en el release de esa misma tag.

Formato de tag soportado:
- `YYYYMMDD` (ejemplo: `20260223`)
- `YYYYMMDD_HHMMSS` (ejemplo: `20260223_062352`)

Nombres de ZIP generados:
- `mrbot-refactored.<tag>.Linux.zip`
- `mrbot-refactored.<tag>.Windows.zip`

Cada ZIP incluye el ejecutable, `bin/`, `ejemplos_api/` y un `.env` de plantilla generado desde
`.env.example` (sin las variables de tests de integración). Completá `MAIL` y `API_KEY` en ese
`.env` antes de ejecutar.

Comandos de ejemplo para publicar una versión:
```bash
# primera release del dia
git tag 20260223
git push origin 20260223

# segunda release del dia (con hora)
git tag 20260223_062352
git push origin 20260223_062352
```

## Soporte, licencia y donaciones
- Issues y soporte: https://github.com/abustosp/bot-mis-comprobantes-cliente/issues
- Licencia: ver `LICENSE`
- Donaciones: https://cafecito.app/abustos
