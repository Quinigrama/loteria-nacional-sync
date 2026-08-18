"""
Sincronizador de resultados de Lotería Nacional -> Google Sheets

Lee sorteos desde la API pública de Loterías y Apuestas del Estado (SELAE)
y los escribe en una hoja de Google Sheets con el esquema:

    id_sorteo, fecha, tipo_sorteo, nombre_sorteo, serie,
    categoria_premio, numero, importe_euros

Cada PREMIO individual de cada sorteo es una fila (no cada sorteo).

Requisitos:
    pip install gspread google-auth requests

Configuración necesaria antes de ejecutar:
    1. CREDENTIALS_FILE: ruta al JSON de la cuenta de servicio.
    2. SPREADSHEET_ID: ID de tu Google Sheet (está en la URL, entre /d/ y /edit).
    3. Compartir la hoja con el email "client_email" del JSON como Editor.
"""

import html
import json
import time
from datetime import datetime, date, timedelta
from typing import Optional

import requests  # se mantiene como fallback / para otros usos
from curl_cffi import requests as curl_requests
import gspread
from google.oauth2.service_account import Credentials


# ============================================================
# CONFIGURACIÓN — edita estos valores
# ============================================================
CREDENTIALS_FILE = "gen-lang-client-0434997779-37d9dd4b06e8.json"   # archivo JSON de la cuenta de servicio
SPREADSHEET_ID = "1pcp0YOiIOofaDyPopOuegVH8syaCNQ4OIyxXRtBC4aA"
WORKSHEET_NAME = "Hoja 1"                # nombre de la pestaña dentro del Sheet

API_BASE = "https://www.loteriasyapuestas.es/servicios/buscadorSorteos"
GAME_ID = "LNAC"  # Lotería Nacional

# Cabeceras para que la petición se parezca a la de un navegador real.
# Sin esto, el servidor de SELAE devuelve 403 Forbidden a peticiones de 'requests'.
HEADERS_HTTP = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9",
    "Referer": "https://www.loteriasyapuestas.es/es/resultados/loteria-nacional",
}

# Cabeceras esperadas en la hoja (deben coincidir con la fila 1 existente)
HEADERS = [
    "id_sorteo", "fecha", "tipo_sorteo", "nombre_sorteo",
    "serie", "categoria_premio", "numero", "importe_euros",
]

# Bloques de premios dentro de cada sorteo, tal y como los devuelve la API.
# 'single' = el campo es un objeto único; 'list' = el campo es un array de objetos.
PRIZE_FIELDS = [
    ("primerPremio", "single"),
    ("segundoPremio", "single"),
    ("tercerosPremios", "list"),
    ("cuartosPremios", "list"),
    ("quintosPremios", "list"),
    ("extraccionesDeCincoCifras", "list"),
    ("extraccionesDeCuatroCifras", "list"),
    ("extraccionesDeTresCifras", "list"),
    ("extraccionesDeDosCifras", "list"),
    ("reintegros", "list"),
]

# Nombre legible de categoría a usar si la API no rellena 'literalPremio'
# para ese bloque (ocurre en algunas pedreas/extracciones).
CAMPO_A_CATEGORIA = {
    "primerPremio": "1er Premio",
    "segundoPremio": "2º Premio",
    "tercerosPremios": "3er Premio",
    "cuartosPremios": "4º Premio",
    "quintosPremios": "5º Premio",
    "extraccionesDeCincoCifras": "Extracción 5 cifras",
    "extraccionesDeCuatroCifras": "Extracción 4 cifras",
    "extraccionesDeTresCifras": "Extracción 3 cifras",
    "extraccionesDeDosCifras": "Extracción 2 cifras",
    "reintegros": "Reintegro",
}

# Número de cifras que debe tener el campo 'numero' según el bloque de origen.
# Los premios mayores (1er-5º) son siempre números de 5 cifras (el décimo
# completo), pero las extracciones/pedreas y el reintegro son de longitud
# fija menor, y rellenar todas con zfill(5) falsearía la cifra real
# (ej. '2685' de 4 cifras no debe convertirse en '02685', que parece de 5).
CAMPO_A_LONGITUD = {
    "primerPremio": 5,
    "segundoPremio": 5,
    "tercerosPremios": 5,
    "cuartosPremios": 5,
    "quintosPremios": 5,
    "extraccionesDeCincoCifras": 5,
    "extraccionesDeCuatroCifras": 4,
    "extraccionesDeTresCifras": 3,
    "extraccionesDeDosCifras": 2,
    "reintegros": 1,
}


# ============================================================
# 1. EXTRACCIÓN DESDE LA API DE SELAE
# ============================================================
def fetch_sorteos(fecha_inicio: str, fecha_fin: str) -> list[dict]:
    """
    Descarga los sorteos de Lotería Nacional celebrados entre dos fechas.

    fecha_inicio / fecha_fin: strings en formato 'AAAAMMDD', ej. '20260101'
    """
    session = curl_requests.Session()

    # Paso 1: visitar la página HTML normal primero. Esto permite que Akamai
    # asigne las cookies de sesión que luego exige para aceptar la llamada
    # al endpoint de datos. Sin este paso previo, el servicio devuelve 403
    # aunque la petición vaya con headers y huella TLS correctos.
    pagina = session.get(
        "https://www.loteriasyapuestas.es/es/resultados/loteria-nacional",
        headers=HEADERS_HTTP, timeout=30, impersonate="chrome124",
    )
    print(f"[DEBUG] Visita previa a la página: status {pagina.status_code}, "
          f"cookies recibidas: {list(session.cookies.keys())}")

    params = {
        "game_id": GAME_ID,
        "celebrados": "true",
        "fechaInicioInclusiva": fecha_inicio,
        "fechaFinInclusiva": fecha_fin,
    }
    resp = session.get(
        API_BASE, params=params, headers=HEADERS_HTTP, timeout=30,
        impersonate="chrome124",
    )
    if resp.status_code != 200:
        print(f"[DEBUG] Status: {resp.status_code}")
        print(f"[DEBUG] Headers respuesta: {dict(resp.headers)}")
        print(f"[DEBUG] Cuerpo (primeros 500 caracteres): {resp.text[:500]}")
    resp.raise_for_status()
    return resp.json()


def es_extraordinario(nombre_sorteo: str) -> bool:
    """Determina si un sorteo es extraordinario según su nombre."""
    nombre = (nombre_sorteo or "").upper()
    return "EXTRAORDINARIO" in nombre or "NAVIDAD" in nombre or "NIÑO" in nombre


def normaliza_premio(premio_obj: dict, fecha: str, id_sorteo: str,
                     tipo_sorteo: str, nombre_sorteo: str,
                     campo_origen: str) -> Optional[dict]:
    """Convierte un objeto de premio de la API en una fila de nuestro esquema."""
    if not premio_obj:
        return None

    numero = premio_obj.get("decimo")
    if numero is None:
        return None

    importe = premio_obj.get("prize")
    literal = premio_obj.get("literalPremio")
    if isinstance(literal, dict):
        categoria = literal.get("es")
    elif literal:
        categoria = str(literal)
    else:
        categoria = None

    if not categoria:
        # Fallback: algunos bloques (pedreas) no traen literalPremio relleno;
        # usamos el nombre del campo de origen como categoría legible.
        categoria = CAMPO_A_CATEGORIA.get(campo_origen, campo_origen)

    categoria = html.unescape(categoria)  # decodifica entidades tipo &#x00ba; -> º

    # 'tabla' indica la serie en sorteos extraordinarios; si no existe, serie = 1
    serie = premio_obj.get("tabla") or "1"

    longitud = CAMPO_A_LONGITUD.get(campo_origen, 5)

    return {
        "id_sorteo": id_sorteo,
        "fecha": fecha,
        "tipo_sorteo": tipo_sorteo,
        "nombre_sorteo": nombre_sorteo if tipo_sorteo == "EXTRAORDINARIO" else "",
        "serie": serie,
        "categoria_premio": categoria,
        "numero": str(numero).zfill(longitud),  # conserva ceros a la izquierda según cifras reales
        "importe_euros": importe if importe is not None else "",
    }


def aplanar_sorteo(sorteo: dict) -> list[dict]:
    """Convierte un sorteo completo de la API en una lista de filas (una por premio)."""
    filas = []

    fecha_completa = sorteo.get("fecha_sorteo", "")
    fecha = fecha_completa.split(" ")[0] if fecha_completa else ""
    nombre_sorteo_raw = (sorteo.get("nombre") or "").strip()
    tipo_sorteo = "EXTRAORDINARIO" if es_extraordinario(nombre_sorteo_raw) else "NORMAL"
    id_sorteo = f"{fecha}-{sorteo.get('id_sorteo', '')}"

    for campo, forma in PRIZE_FIELDS:
        valor = sorteo.get(campo)
        if not valor:
            continue

        objetos = [valor] if forma == "single" else valor
        for obj in objetos:
            fila = normaliza_premio(obj, fecha, id_sorteo, tipo_sorteo, nombre_sorteo_raw, campo)
            if fila:
                filas.append(fila)

    return filas


# ============================================================
# 2. ESCRITURA EN GOOGLE SHEETS
# ============================================================
def conectar_hoja():
    """Autentica con la cuenta de servicio y devuelve la worksheet objetivo."""
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
    cliente = gspread.authorize(creds)
    hoja = cliente.open_by_key(SPREADSHEET_ID)
    worksheet = hoja.worksheet(WORKSHEET_NAME)
    forzar_columna_numero_como_texto(worksheet)
    return worksheet


def forzar_columna_numero_como_texto(worksheet):
    """
    Aplica formato de texto a la columna 'numero' (índice en HEADERS) para
    que Sheets no le quite los ceros a la izquierda (ej. '01' -> 1).
    Se aplica a un rango amplio de filas para cubrir cargas futuras.
    """
    col_idx = HEADERS.index("numero") + 1  # gspread usa columnas 1-indexadas
    col_letra = gspread.utils.rowcol_to_a1(1, col_idx).rstrip("1")
    rango = f"{col_letra}1:{col_letra}100000"
    worksheet.format(rango, {"numberFormat": {"type": "TEXT"}})


def ids_sorteo_existentes(worksheet) -> set[str]:
    """Lee la columna id_sorteo ya presente en la hoja, para no duplicar filas."""
    valores = worksheet.col_values(1)  # columna A = id_sorteo
    return set(valores[1:])  # nos saltamos la cabecera


def escribir_filas(worksheet, filas: list[dict]):
    """Añade filas nuevas al final de la hoja, respetando el orden de HEADERS."""
    if not filas:
        print("No hay filas nuevas que escribir.")
        return

    filas_existentes = ids_sorteo_existentes(worksheet)
    filas_nuevas = [f for f in filas if f["id_sorteo"] not in filas_existentes]

    # Nota: el filtro anterior es a nivel de SORTEO (todas sus filas se añaden
    # o ninguna). Si el sorteo ya existe pero quieres forzar reescritura,
    # hay que borrar sus filas manualmente antes de re-ejecutar.

    if not filas_nuevas:
        print("Todos los sorteos del rango ya estaban cargados. Nada que hacer.")
        return

    valores = [[f[campo] for campo in HEADERS] for f in filas_nuevas]
    worksheet.append_rows(valores, value_input_option="RAW")
    print(f"Añadidas {len(valores)} filas nuevas "
          f"({len(set(f['id_sorteo'] for f in filas_nuevas))} sorteos).")


# ============================================================
# 3. ORQUESTACIÓN
# ============================================================
def sincronizar(fecha_inicio: str, fecha_fin: str, dry_run: bool = False):
    """
    Descarga sorteos del rango de fechas y los vuelca en la hoja.

    fecha_inicio / fecha_fin: 'AAAAMMDD'
    dry_run: si True, no escribe en Sheets, solo muestra lo que haría.
    """
    print(f"Descargando sorteos LNAC entre {fecha_inicio} y {fecha_fin}...")
    sorteos = fetch_sorteos(fecha_inicio, fecha_fin)
    print(f"  -> {len(sorteos)} sorteos encontrados.")

    todas_las_filas = []
    for sorteo in sorteos:
        todas_las_filas.extend(aplanar_sorteo(sorteo))

    print(f"  -> {len(todas_las_filas)} filas de premios generadas.")

    if dry_run:
        print("\n[DRY RUN] Primeras 5 filas que se escribirían:")
        for f in todas_las_filas[:5]:
            print(" ", f)
        return todas_las_filas

    worksheet = conectar_hoja()
    escribir_filas(worksheet, todas_las_filas)
    return todas_las_filas


def sincronizar_historico(anyo_inicio: int, anyo_fin: int, dry_run: bool = False,
                          fecha_inicio_primer_anyo: Optional[str] = None):
    """
    Recorre año a año para cargar histórico completo sin pedir rangos
    demasiado grandes a la API en una sola llamada.

    fecha_inicio_primer_anyo: si se indica (formato 'AAAAMMDD'), se usa como
    inicio del primer año en lugar del 1 de enero (útil cuando se sabe que
    el histórico real empieza a mitad de año, como en 2008).

    Genera un archivo 'log_carga_historico.txt' con un resumen por año
    (sorteos encontrados, primera y última fecha real) para poder detectar
    fácilmente huecos sin tener que inspeccionar manualmente cada año.
    """
    resumen_lineas = []

    for anyo in range(anyo_inicio, anyo_fin + 1):
        if anyo == anyo_inicio and fecha_inicio_primer_anyo:
            fecha_inicio = fecha_inicio_primer_anyo
        else:
            fecha_inicio = f"{anyo}0101"
        fecha_fin = f"{anyo}1231"

        print(f"\n=== Año {anyo} ===")
        sorteos_raw = fetch_sorteos(fecha_inicio, fecha_fin)

        if not isinstance(sorteos_raw, list) or not sorteos_raw or not isinstance(sorteos_raw[0], dict):
            linea = f"{anyo}: SIN DATOS o respuesta inesperada (revisar manualmente)"
            print(f"  -> {linea}")
            resumen_lineas.append(linea)
            time.sleep(1)
            continue

        fechas_sorteo = sorted(s.get("fecha_sorteo", "")[:10] for s in sorteos_raw)
        primera_fecha = fechas_sorteo[0] if fechas_sorteo else "N/A"
        ultima_fecha = fechas_sorteo[-1] if fechas_sorteo else "N/A"

        filas = []
        for sorteo in sorteos_raw:
            filas.extend(aplanar_sorteo(sorteo))

        linea = (f"{anyo}: {len(sorteos_raw)} sorteos, {len(filas)} filas, "
                 f"rango real {primera_fecha} a {ultima_fecha}")
        print(f"  -> {linea}")
        resumen_lineas.append(linea)

        if not dry_run and filas:
            worksheet = conectar_hoja()
            escribir_filas(worksheet, filas)

        time.sleep(1)  # pequeña pausa por cortesía hacia el servidor

    with open("log_carga_historico.txt", "a", encoding="utf-8") as f:
        f.write(f"\n--- Carga {datetime.now().isoformat()} (dry_run={dry_run}) ---\n")
        f.write("\n".join(resumen_lineas) + "\n")

    print("\nResumen guardado en log_carga_historico.txt")
    print("Revisa especialmente líneas con muchos menos sorteos de lo esperado "
          "(~100-110/año) o rangos de fecha que no cubran todo el año.")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--debug-estructura":
        # Modo de inspección: vuelca el JSON crudo de un sorteo a un archivo
        # para poder ver exactamente cómo vienen los campos problemáticos.
        sorteos = fetch_sorteos("20260601", "20260622")
        with open("debug_sorteo.json", "w", encoding="utf-8") as f:
            json.dump(sorteos[0], f, ensure_ascii=False, indent=2)
        print("Volcado el primer sorteo completo a debug_sorteo.json")
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "--listar-fechas-anyo":
        # Lista todas las fechas de sorteo encontradas en un año, para
        # detectar huecos o confirmar si el conteo bajo es real o un
        # problema de filtrado/paginación de la API.
        anyo = sys.argv[2] if len(sys.argv) > 2 else "2008"
        sorteos = fetch_sorteos(f"{anyo}0101", f"{anyo}1231")
        fechas = sorted(set(s.get("fecha_sorteo", "")[:10] for s in sorteos if isinstance(s, dict)))
        print(f"Año {anyo}: {len(sorteos)} sorteos, {len(fechas)} fechas únicas")
        for f in fechas:
            print(" ", f)
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "--inspeccionar-fechas":
        # Comprueba las fechas reales devueltas para un año concreto,
        # para detectar si la API está devolviendo un fallback genérico
        # en lugar de filtrar correctamente por el rango pedido.
        anyo = sys.argv[2] if len(sys.argv) > 2 else "1985"
        sorteos = fetch_sorteos(f"{anyo}0101", f"{anyo}0107")
        print(f"Año pedido: {anyo} (rango {anyo}-01-01 a {anyo}-01-07)")
        if isinstance(sorteos, str):
            print(f"La API devolvió un STRING (no una lista de sorteos): {sorteos!r}")
        else:
            print(f"Total elementos: {len(sorteos)}")
            print(f"Tipo del primer elemento: {type(sorteos[0]) if sorteos else 'N/A'}")
            print(f"Primeros 3 elementos crudos: {sorteos[:3]}")
            if sorteos and isinstance(sorteos[0], dict):
                fechas = sorted(set(s.get("fecha_sorteo", "")[:10] for s in sorteos))
                print(f"Fechas únicas encontradas: {fechas}")
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "--buscar-primer-anyo":
        # Búsqueda binaria entre 1950 y 2026 para encontrar el primer año
        # en el que la API empieza a devolver sorteos reales (lista de dicts)
        # en lugar de un mensaje de "sin datos".
        def tiene_datos(anyo):
            try:
                resultado = fetch_sorteos(f"{anyo}0101", f"{anyo}0107")
                return isinstance(resultado, list) and len(resultado) > 0 and isinstance(resultado[0], dict)
            except Exception:
                return False

        lo, hi = 1950, 2026
        if not tiene_datos(hi):
            print(f"Aviso: ni siquiera {hi} devuelve datos reales. Revisa la API manualmente.")
            sys.exit(1)

        while lo < hi:
            mid = (lo + hi) // 2
            print(f"Probando año {mid}...")
            if tiene_datos(mid):
                hi = mid
            else:
                lo = mid + 1
            time.sleep(0.4)

        print(f"\nPrimer año con datos reales detectado: {hi}")
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "--sondear-anyos":
        # Sondeo rápido: comprueba cuántos sorteos hay en distintos años
        # para detectar desde cuándo la API tiene datos reales.
        anyos_a_probar = [1985, 1990, 1995, 2000, 2005, 2010, 2015, 2020, 2026]
        print("Sondeando disponibilidad de datos por año...")
        for anyo in anyos_a_probar:
            try:
                sorteos = fetch_sorteos(f"{anyo}0101", f"{anyo}0107")
                print(f"  {anyo}: {len(sorteos)} sorteos encontrados (primera semana de enero)")
            except Exception as e:
                print(f"  {anyo}: ERROR -> {e}")
            time.sleep(0.5)
        sys.exit(0)

    # Ejemplo de uso manual:

    # 1) Prueba rápida SIN escribir en Sheets (solo ver qué se generaría):
    # sincronizar("20260601", "20260622", dry_run=True)

    # 2) Cuando tengas las credenciales listas, ejecuta de verdad:
    # sincronizar("20260601", "20260622", dry_run=False)

    # 3) Carga histórica completa (ya ejecutada una vez; deja comentado para
    #    no recorrer 19 años cada vez que se ejecute el script sin argumentos):
    # sincronizar_historico(2008, 2026, dry_run=True, fecha_inicio_primer_anyo="20080802")
    # sincronizar_historico(2008, 2026, dry_run=False, fecha_inicio_primer_anyo="20080802")

    # 4) Modo por defecto: actualización periódica (pensado para Task Scheduler/cron).
    #    Descarga los últimos N días (margen de sobra) y deja que la protección
    #    anti-duplicados de escribir_filas() se encargue de no repetir sorteos
    #    ya cargados. Se puede ajustar el número de días con un argumento, ej.:
    #    python loteria_nacional_sync.py --actualizar 30
    dias_atras = 14
    if len(sys.argv) > 2 and sys.argv[1] == "--actualizar":
        dias_atras = int(sys.argv[2])

    hoy = date.today()
    hace_dias = hoy - timedelta(days=dias_atras)
    sincronizar(hace_dias.strftime("%Y%m%d"), hoy.strftime("%Y%m%d"), dry_run=False)
