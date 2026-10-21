"""Dashboard del Registro Diario de Actividades de Ingeniería Civil.

Fuente de datos: las tablas de Supabase que alimenta el bot de Telegram (n8n):
`civil_registros` (labores, cuentas control y novedades) y `civil_vacaciones`.
El acceso es con usuario y contraseña de Supabase Auth: cada consulta viaja con
el token de quien inició sesión, así que las políticas RLS se aplican por usuario.

Ejecutar:  streamlit run app.py
"""

from __future__ import annotations

import base64
import io
import json
import os
import time
from pathlib import Path
import pandas as pd
import altair as alt
import streamlit as st
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")

def _config(nombre: str, defecto: str = "") -> str:
    """Toma el valor de los Secrets de Streamlit Cloud y, si no está, del .env local.

    Recibe un único nombre a propósito: una cadena de alternativas permitiría que
    una variable con otro nombre —por ejemplo una service_role— entrara sin que
    nadie lo note.
    """
    try:
        valor = st.secrets[nombre]
    except Exception:  # sin archivo de secrets (ejecución local)
        valor = None
    if not valor:
        valor = os.getenv(nombre)
    return str(valor).strip() if valor else defecto


def _rol_declarado(clave: str) -> str:
    """Rol que declara la clave de Supabase, o cadena vacía si no se puede leer."""
    if clave.startswith("sb_secret_"):
        return "service_role"
    if clave.startswith("sb_publishable_"):
        return "anon"
    if clave.count(".") != 2:  # no es un JWT: formato desconocido
        return ""
    try:
        carga = clave.split(".")[1]
        carga += "=" * (-len(carga) % 4)
        return str(json.loads(base64.urlsafe_b64decode(carga)).get("role", ""))
    except Exception:
        return ""


SUPABASE_URL = _config("SUPABASE_URL")
SUPABASE_KEY = _config("SUPABASE_KEY")
SUPABASE_TABLA = _config("SUPABASE_TABLE", "civil_registros")
VACACIONES_TABLA = _config("SUPABASE_VACACIONES_TABLE", "civil_vacaciones")

# Jornada ordinaria (06:00 a 15:00). Un día que no la complete, o que la supere
# sin reportar horas adicionales, queda marcado en la vista de calidad.
JORNADA_HORAS = 9.0

# Paleta de marca, validada para fondo claro.
#
# VERDE es el color institucional y pinta todas las barras de magnitud: al ser
# series únicas, cada gráfico se distingue por su título y no por el matiz, lo
# que evita depender del color para leer el dato. Su contraste contra el fondo es
# 2.21:1, por debajo de 3:1, así que las barras llevan siempre su valor escrito
# al lado y existe la vista de tabla en Detalle.
#
# VERDE_OSCURO es el único que admite texto blanco encima (5.1:1); sobre VERDE el
# texto va oscuro (8.7:1), nunca blanco.
#
# ROJO queda reservado para estado (alertas) y no se usa como color de serie.
VERDE = "#7DBE00"
VERDE_OSCURO = "#4e7a00"
VERDE_TENUE = "#eff7e0"
ROJO = "#e34948"
TINTA = "#0b0b0b"
TINTA_SUAVE = "#52514e"
SUPERFICIE = "#fcfcfb"
REJILLA = "#eceae5"

ETIQUETAS = {
    "fecha": "Fecha",
    "ficha": "Ficha",
    "colaborador": "Colaborador",
    "tipo_registro": "Tipo de registro",
    "clasificacion": "Clasificación",
    "codigo": "Código",
    "actividad": "Actividad",
    "labor_codigo": "Código labor",
    "labor_desc": "Labor",
    "cuenta_control": "Cuenta control",
    "cuenta_desc": "Descripción cuenta",
    "novedad_tipo": "Novedad",
    "hacienda_codigo": "Código hacienda",
    "hacienda_nombre": "Hacienda",
    "zona_cosecha": "Zona cosecha",
    "cuenta_gasto": "Cuenta gasto",
    "hora_inicio": "Hora inicio",
    "hora_fin": "Hora fin",
    "horas_totales": "Horas",
    "horas_normales": "Horas normales",
    "horas_adicionales": "Horas adicionales",
    "tipo_hora_adicional": "Tipo hora adicional",
    "herramientas_texto": "Herramientas",
    "herramientas_desc": "Descripción herramienta",
    "horas_herramienta": "Horas herramienta",
    "herramienta_ip": "Herramienta",
    "herramienta_desc": "Descripción herramienta",
    "herramienta_horas": "Horas de uso",
    "created_at": "Fecha digitación",
    "fecha_inicio": "Inicio",
    "fecha_reintegro": "Reintegro",
    "dias": "Días",
    "estado": "Estado",
}

FORMATO_FECHA = "%d/%m/%y"  # DD/MM/AA, igual en fecha y fecha de digitación

# Columnas que se piden a la base. Se excluyen las internas (id, id_registro,
# origen, chat_id) y las que el bot no llena (labor_grupo, clase_labor).
COLUMNAS_CONSULTA = ",".join([
    "ficha", "colaborador", "fecha", "tipo_registro", "labor_codigo", "labor_desc",
    "cuenta_control", "cuenta_desc", "ubicacion_tipo", "hacienda_codigo",
    "hacienda_nombre", "zona_cosecha", "hora_inicio", "hora_fin", "horas_totales",
    "uso_herramienta", "herramienta_ip", "herramienta_desc", "herramienta_horas",
    "herramientas", "tipo_labor", "cuenta_gasto", "novedad_tipo", "horas_normales",
    "horas_adicionales", "tipo_hora_adicional", "created_at",
])
COLUMNAS_VACACIONES = "ficha,colaborador,fecha_inicio,fecha_reintegro,dias,created_at"

COLUMNAS_NUMERICAS = [
    "horas_totales",
    "horas_normales",
    "horas_adicionales",
    "herramienta_horas",
]
COLUMNAS_BOOLEANAS = ["uso_herramienta"]
COLUMNAS_TEXTO = [
    "ficha",
    "colaborador",
    "tipo_registro",
    "labor_codigo",
    "labor_desc",
    "cuenta_control",
    "cuenta_desc",
    "ubicacion_tipo",
    "hacienda_codigo",
    "hacienda_nombre",
    "zona_cosecha",
    "hora_inicio",
    "hora_fin",
    "herramienta_ip",
    "herramienta_desc",
    "tipo_labor",
    "cuenta_gasto",
    "novedad_tipo",
    "tipo_hora_adicional",
]

# Los valores que guarda el bot son claves internas; en pantalla se traducen.
NOMBRES_TIPO_REGISTRO = {
    "labor": "Labor",
    "cuenta_control": "Cuenta control",
    "novedad": "Novedad",
}
NOMBRES_TIPO_LABOR = {
    "administrativa": "Administrativa",
    "campo": "Campo",
    "cosecha": "Cosecha",
}
NOMBRES_NOVEDAD = {
    "permiso_calamidad": "Permiso por calamidad",
    "permiso_cita_medica": "Permiso por cita médica",
}
NOMBRES_HORA_ADICIONAL = {"extra": "Extra", "banco": "Banco de horas"}

# Orden de columnas en las tablas de detalle.
ORDEN_COLUMNAS = [
    "fecha",
    "clasificacion",
    "codigo",
    "actividad",
    "hacienda_nombre",
    "zona_cosecha",
    "cuenta_gasto",
    "ficha",
    "colaborador",
    "hora_inicio",
    "hora_fin",
    "horas_totales",
    "horas_normales",
    "horas_adicionales",
    "tipo_hora_adicional",
    "herramientas_texto",
    "herramientas_desc",
    "horas_herramienta",
]


# --------------------------------------------------------------------------- #
# Carga y normalización
# --------------------------------------------------------------------------- #
def _a_booleano(serie: pd.Series) -> pd.Series:
    return serie.astype(str).str.strip().str.lower().isin(["true", "1", "t", "si", "sí", "yes", "y"])


def _a_fecha(serie: pd.Series) -> pd.Series:
    """Las columnas `date` de Postgres llegan como AAAA-MM-DD."""
    return pd.to_datetime(serie, errors="coerce", format="ISO8601")


def _a_digitacion(serie: pd.Series) -> pd.Series:
    # El bot guarda en UTC; se muestra en hora de Colombia (UTC-5).
    # format="ISO8601" es necesario: Postgres recorta los microsegundos en
    # cero, así que la misma columna trae "...02.384+00" y "...00+00". Con
    # inferencia automática pandas toma el formato de la primera fila y
    # convierte el resto a NaT, dejando la fecha de digitación en blanco.
    return pd.to_datetime(
        serie, errors="coerce", utc=True, format="ISO8601"
    ).dt.tz_localize(None) - pd.Timedelta(hours=5)


def _a_horas(serie: pd.Series) -> pd.Series:
    """Convierte 06:30 en 6.5 para contrastar el horario contra las horas reportadas."""
    partes = serie.astype(str).str.strip().str.extract(r"^(\d{1,2}):(\d{2})")
    horas = pd.to_numeric(partes[0], errors="coerce")
    minutos = pd.to_numeric(partes[1], errors="coerce")
    return horas + minutos / 60


def _lista_herramientas(valor) -> list[dict]:
    """El jsonb llega como lista desde PostgREST, o como texto si viene de otra fuente."""
    if isinstance(valor, str):
        try:
            valor = json.loads(valor)
        except ValueError:
            return []
    return [e for e in valor if isinstance(e, dict)] if isinstance(valor, list) else []


def expandir_herramientas(df: pd.DataFrame) -> pd.DataFrame:
    """Una fila por herramienta usada, con el índice del registro en `indice`.

    Replica la vista `v_civil_registros_herramientas`: toma el arreglo
    `herramientas` y, si está vacío, las columnas sueltas de los registros
    anteriores a ese arreglo. Se hace aquí y no con la vista para no tener que
    abrir otra política de lectura.
    """
    filas: list[dict] = []
    columnas = ["herramientas", "herramienta_ip", "herramienta_desc", "herramienta_horas"]
    valores = [df[c] if c in df.columns else pd.Series(None, index=df.index) for c in columnas]
    for indice, lista, ip, desc, horas in zip(df.index, *valores):
        elementos = _lista_herramientas(lista)
        if not elementos and ip:
            elementos = [{"ip": ip, "desc": desc, "horas": horas}]
        for elemento in elementos:
            filas.append({
                "indice": indice,
                "herramienta_ip": str(elemento.get("ip") or "").strip(),
                "herramienta_desc": str(elemento.get("desc") or "").strip(),
                "herramienta_horas": pd.to_numeric(elemento.get("horas"), errors="coerce"),
            })
    usadas = pd.DataFrame(
        filas, columns=["indice", "herramienta_ip", "herramienta_desc", "herramienta_horas"]
    )
    usadas["herramienta"] = (
        usadas["herramienta_ip"] + " · " + usadas["herramienta_desc"]
    ).str.strip(" ·")
    return usadas


def normalizar(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]

    for columna in COLUMNAS_NUMERICAS:
        if columna in df.columns:
            df[columna] = pd.to_numeric(
                df[columna].astype(str).str.strip().str.replace(",", ".", regex=False),
                errors="coerce",
            )
    for columna in COLUMNAS_BOOLEANAS:
        if columna in df.columns:
            df[columna] = _a_booleano(df[columna])
    for columna in COLUMNAS_TEXTO:
        if columna not in df.columns:
            df[columna] = ""
        df[columna] = (
            df[columna].astype(str).str.strip().replace({"nan": "", "None": "", "<NA>": ""})
        )

    df["fecha"] = _a_fecha(df["fecha"]) if "fecha" in df.columns else pd.NaT
    if "created_at" in df.columns:
        df["created_at"] = _a_digitacion(df["created_at"])
    df["horas_adicionales"] = df.get("horas_adicionales", pd.Series(0.0, index=df.index)).fillna(0)

    # Postgres entrega `time` como 06:00:00; los segundos no aportan.
    df["hora_inicio"] = df["hora_inicio"].str[:5]
    df["hora_fin"] = df["hora_fin"].str[:5]
    df["horas_calendario"] = _a_horas(df["hora_fin"]) - _a_horas(df["hora_inicio"])

    # Actividad y código comunes a los tres tipos de registro, para filtrar y
    # graficar sin distinguir de dónde salió cada uno.
    tipo = df["tipo_registro"]
    novedad = df["novedad_tipo"].map(NOMBRES_NOVEDAD).fillna(df["novedad_tipo"])
    cuenta = df["cuenta_desc"].where(df["cuenta_desc"] != "", "Cuenta " + df["cuenta_control"])
    df["actividad"] = df["labor_desc"].where(tipo == "labor", cuenta.where(tipo == "cuenta_control", novedad))
    df["codigo"] = df["labor_codigo"].where(tipo == "labor", df["cuenta_control"])

    # Clasificación: el tipo de labor para las labores y el tipo de registro
    # para lo demás. Es el corte que interesa al digitar.
    df["clasificacion"] = df["tipo_labor"].map(NOMBRES_TIPO_LABOR).where(
        tipo == "labor", tipo.map(NOMBRES_TIPO_REGISTRO)
    ).fillna("Labor")
    df["tipo_registro"] = tipo.map(NOMBRES_TIPO_REGISTRO).fillna(tipo)
    df["tipo_hora_adicional"] = (
        df["tipo_hora_adicional"].map(NOMBRES_HORA_ADICIONAL).fillna(df["tipo_hora_adicional"])
    )

    df["clave"] = (
        df["ficha"]
        + "|"
        + df["fecha"].dt.strftime("%Y-%m-%d").fillna("")
        + "|"
        + df["hora_inicio"]
    )

    df = df.sort_values(["fecha", "colaborador", "hora_inicio"], na_position="last")
    return df.reset_index(drop=True)


def agregar_herramientas(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Resume en cada registro las herramientas que usó."""
    usadas = expandir_herramientas(df)
    por_registro = usadas.groupby("indice").agg(
        herramientas_texto=(
            "herramienta_ip",
            lambda ips: " · ".join(ips),
        ),
        # Mismo orden que las IPs, para que cada descripción corresponda a la suya.
        herramientas_desc=(
            "herramienta_desc",
            lambda descripciones: " · ".join(descripciones),
        ),
        horas_herramienta=("herramienta_horas", "sum"),
    )
    df = df.join(por_registro)
    df["herramientas_texto"] = df["herramientas_texto"].fillna("")
    df["herramientas_desc"] = df["herramientas_desc"].fillna("")
    return df, usadas


def normalizar_vacaciones(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for columna in ("ficha", "colaborador"):
        df[columna] = df[columna].astype(str).str.strip()
    df["fecha_inicio"] = _a_fecha(df["fecha_inicio"])
    df["fecha_reintegro"] = _a_fecha(df["fecha_reintegro"])
    df["dias"] = pd.to_numeric(df["dias"], errors="coerce")
    if "created_at" in df.columns:
        df["created_at"] = _a_digitacion(df["created_at"])

    hoy = (pd.Timestamp.now(tz="UTC").tz_localize(None) - pd.Timedelta(hours=5)).normalize()
    # El reintegro es el día en que vuelve: las vacaciones terminan el día anterior.
    df["estado"] = "En curso"
    df.loc[df["fecha_inicio"] > hoy, "estado"] = "Programadas"
    df.loc[df["fecha_reintegro"] <= hoy, "estado"] = "Finalizadas"
    return df.sort_values("fecha_inicio", ascending=False).reset_index(drop=True)


@st.cache_resource(show_spinner=False)
def _cliente_anonimo():
    """Cliente sin sesión, para las operaciones de Auth.

    `create_client` cuesta ~380 ms, así que se crea una sola vez por proceso.
    """
    from supabase import create_client

    return create_client(SUPABASE_URL, SUPABASE_KEY)


@st.cache_resource(show_spinner=False, max_entries=20)
def _cliente_sesion(usuario_id: str):
    """Un cliente por usuario, reutilizado entre reruns.

    El token NO forma parte de la clave porque rota cada hora; se adjunta en
    cada uso con `postgrest.auth()`, que sólo reescribe la cabecera.
    """
    from supabase import create_client

    return create_client(SUPABASE_URL, SUPABASE_KEY)


def _cliente(usuario_id: str, token: str):
    cliente = _cliente_sesion(usuario_id)
    cliente.postgrest.auth(token)
    return cliente


def _descargar(tabla: str, usuario_id: str, token: str, columnas: str = "*") -> pd.DataFrame:
    cliente = _cliente(usuario_id, token)
    filas: list[dict] = []
    tamano, pagina = 1000, 0
    while True:
        respuesta = (
            cliente.table(tabla)
            .select(columnas)
            .range(pagina * tamano, pagina * tamano + tamano - 1)
            .execute()
        )
        lote = respuesta.data or []
        filas.extend(lote)
        if len(lote) < tamano:
            break
        pagina += 1
    return pd.DataFrame(filas)


@st.cache_data(ttl=300, show_spinner=False)
def cargar_registros(tabla: str, usuario_id: str, _token: str) -> pd.DataFrame:
    """Registros del usuario. `_token` lleva guion bajo para quedar fuera de la
    clave de caché: así la rotación horaria del token no obliga a recargar."""
    return _descargar(tabla, usuario_id, _token, COLUMNAS_CONSULTA)


@st.cache_data(ttl=300, show_spinner=False)
def cargar_vacaciones(tabla: str, usuario_id: str, _token: str) -> pd.DataFrame:
    return _descargar(tabla, usuario_id, _token, COLUMNAS_VACACIONES)


def obtener_datos(usuario_id: str, token: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Lee los registros como el usuario autenticado y los deja listos para graficar.

    Devuelve los registros y la tabla larga de herramientas usadas.
    """
    try:
        datos = cargar_registros(SUPABASE_TABLA, usuario_id, token)
    except Exception as error:
        st.error(f"No se pudo consultar la base de datos: {error}")
        return pd.DataFrame(), pd.DataFrame()

    if datos.empty:
        st.warning(
            f"La consulta a «{SUPABASE_TABLA}» no devolvió registros. Si la tabla tiene "
            "datos, revise que exista la política de lectura (SELECT) para el rol "
            "`authenticated`."
        )
        return pd.DataFrame(), pd.DataFrame()

    return agregar_herramientas(normalizar(datos))


def obtener_vacaciones(usuario_id: str, token: str) -> pd.DataFrame:
    """Vacaciones registradas. Si no se pueden leer, la app sigue sin ellas."""
    try:
        vacaciones = cargar_vacaciones(VACACIONES_TABLA, usuario_id, token)
    except Exception as error:
        st.warning(f"No se pudo leer la tabla de vacaciones: {error}")
        return pd.DataFrame()
    if vacaciones.empty:
        return vacaciones
    return normalizar_vacaciones(vacaciones)


# --------------------------------------------------------------------------- #
# Autenticación con Supabase Auth
# --------------------------------------------------------------------------- #
MARGEN_REFRESCO = 120  # segundos antes del vencimiento en que se renueva el token


def _guardar_sesion(respuesta) -> None:
    sesion = respuesta.session
    st.session_state["sesion"] = {
        "access_token": sesion.access_token,
        "refresh_token": sesion.refresh_token,
        "expira_en": float(sesion.expires_at or 0),
        "correo": getattr(respuesta.user, "email", ""),
        "id": getattr(respuesta.user, "id", ""),
    }


def cerrar_sesion() -> None:
    try:
        _cliente_anonimo().auth.sign_out()
    except Exception:  # cerrar la sesión local es lo que importa
        pass
    st.session_state.pop("sesion", None)
    st.session_state.pop("datos_listos", None)
    st.cache_data.clear()


def iniciar_sesion(correo: str, contrasena: str) -> str | None:
    """Devuelve un mensaje de error, o None si la sesión quedó abierta."""
    try:
        respuesta = _cliente_anonimo().auth.sign_in_with_password(
            {"email": correo.strip().lower(), "password": contrasena}
        )
    except Exception as error:
        detalle = str(error).lower()
        if "invalid login" in detalle or "credentials" in detalle:
            return "Correo o contraseña incorrectos."
        if "not confirmed" in detalle:
            return "El usuario aún no está confirmado. Pida que lo activen en Supabase."
        if "rate limit" in detalle or "too many" in detalle:
            return "Demasiados intentos seguidos. Espere un momento y vuelva a intentar."
        return f"No se pudo iniciar sesión: {error}"

    if not respuesta.session:
        return "El servidor no devolvió una sesión válida."
    _guardar_sesion(respuesta)
    return None


def token_activo() -> str | None:
    """Token vigente del usuario, renovado si está por vencer."""
    sesion = st.session_state.get("sesion")
    if not sesion:
        return None
    if sesion["expira_en"] and time.time() > sesion["expira_en"] - MARGEN_REFRESCO:
        try:
            _guardar_sesion(_cliente_anonimo().auth.refresh_session(sesion["refresh_token"]))
        except Exception:
            st.session_state.pop("sesion", None)
            return None
    return st.session_state["sesion"]["access_token"]


def pantalla_ingreso() -> None:
    _, centro, _ = st.columns([1, 1.6, 1])
    with centro:
        st.write("")
        with st.container(border=True):
            encabezado_marca("Ingrese con el usuario que le asignaron.")
            with st.form("ingreso", border=False):
                correo = st.text_input("Correo", placeholder="nombre@empresa.com")
                contrasena = st.text_input("Contraseña", type="password", placeholder="••••••••")
                enviar = st.form_submit_button("Ingresar", width="stretch")
        if enviar:
            if not correo or not contrasena:
                st.error("Escriba el correo y la contraseña.")
                return
            error = iniciar_sesion(correo, contrasena)
            if error:
                st.error(error)
            else:
                st.rerun()


# --------------------------------------------------------------------------- #
# Validaciones previas a la digitación
# --------------------------------------------------------------------------- #
# Lo que la base ya impide (cruces de horario, herramienta sin IP, labor sin
# ubicación) no se repite aquí: estas reglas cubren lo que la base deja pasar.
REGLAS = {
    "Horas no coinciden con el horario": "Las horas del registro no cuadran con hora inicio / hora fin.",
    "Normales + adicionales no suman el total": "Las horas normales más las adicionales no dan las horas del registro.",
    "Herramienta con más horas que la labor": "Las horas de uso de las herramientas superan las horas del registro.",
    "Jornada incompleta": f"El colaborador registró menos de {JORNADA_HORAS:g} horas en el día.",
    "Jornada larga sin horas adicionales": (
        f"El colaborador registró más de {JORNADA_HORAS:g} horas en el día y ninguna como adicional."
    ),
    "Registro durante vacaciones": "Hay registros en una fecha en que el colaborador figura de vacaciones.",
}


def _en_vacaciones(df: pd.DataFrame, vacaciones: pd.DataFrame) -> pd.Series:
    """Registros cuya fecha cae dentro de unas vacaciones del mismo colaborador.

    Igual que la restricción de la base, el rango es [inicio, reintegro): el día
    de reintegro ya es laboral, y sin reintegro las vacaciones siguen abiertas.
    """
    resultado = pd.Series(False, index=df.index)
    if vacaciones.empty:
        return resultado
    cruce = (
        df[["ficha", "fecha"]]
        .reset_index()
        .merge(vacaciones[["ficha", "fecha_inicio", "fecha_reintegro"]], on="ficha")
    )
    dentro = (cruce["fecha"] >= cruce["fecha_inicio"]) & (
        cruce["fecha_reintegro"].isna() | (cruce["fecha"] < cruce["fecha_reintegro"])
    )
    resultado.loc[cruce.loc[dentro, "index"].unique()] = True
    return resultado


def marcar_alertas(df: pd.DataFrame, vacaciones: pd.DataFrame) -> pd.DataFrame:
    horas = df["horas_totales"].fillna(0)
    adicionales = df["horas_adicionales"].fillna(0)
    dia = df.groupby(["ficha", "fecha"])
    horas_dia = dia["horas_totales"].transform("sum")
    adicionales_dia = dia["horas_adicionales"].transform("sum")

    alertas = pd.DataFrame(index=df.index)
    alertas["Horas no coinciden con el horario"] = (horas - df["horas_calendario"]).abs() > 0.05
    # horas_normales sólo existe en los registros recientes; sin dato no se evalúa.
    alertas["Normales + adicionales no suman el total"] = df["horas_normales"].notna() & (
        (df["horas_normales"] + adicionales - horas).abs() > 0.05
    )
    alertas["Herramienta con más horas que la labor"] = df["horas_herramienta"].fillna(0) > horas + 0.01
    alertas["Jornada incompleta"] = horas_dia < JORNADA_HORAS - 0.01
    alertas["Jornada larga sin horas adicionales"] = (horas_dia > JORNADA_HORAS + 0.01) & (
        adicionales_dia <= 0
    )
    alertas["Registro durante vacaciones"] = _en_vacaciones(df, vacaciones)
    return alertas.fillna(False).astype(bool)


# --------------------------------------------------------------------------- #
# Gráficos
# --------------------------------------------------------------------------- #
def _afinar(grafico, titulo: str, alto: int):
    """Estilo común: tipografía recesiva, sin marco y con el título a la izquierda."""
    return (
        grafico.properties(
            height=alto,
            title=alt.TitleParams(titulo, fontSize=16, color=TINTA, anchor="start", dy=-6),
        )
        .configure_view(strokeWidth=0)
        .configure_axis(
            labelColor=TINTA_SUAVE,
            labelFontSize=12,
            domainColor=REJILLA,
            tickColor=REJILLA,
            gridColor=REJILLA,
        )
    )


def barras_horizontales(datos: pd.DataFrame, categoria: str, valor: str, titulo: str,
                        unidad: str, color: str = VERDE, alto: int | None = None):
    """Barras horizontales con el valor escrito al lado de cada barra.

    Se usa Altair y no Plotly: Vega-Lite ya viene en el paquete de Streamlit
    (281 KB) mientras Plotly agrega cerca de 2 MB de descarga en la primera
    carga, que es lo que pesa cuando la conexión es lenta.
    """
    datos = datos.rename(columns={categoria: "categoria", valor: "valor"})
    datos["categoria"] = datos["categoria"].astype(str)

    base = alt.Chart(datos).encode(
        # sort="-x" ordena por magnitud; el eje es nominal, así que los códigos
        # de hacienda nunca se interpretan como números.
        y=alt.Y("categoria:N", sort="-x", title=None, axis=alt.Axis(labelLimit=240)),
        x=alt.X("valor:Q", title=None, axis=alt.Axis(grid=True, tickCount=4)),
        tooltip=[
            alt.Tooltip("categoria:N", title=" "),
            alt.Tooltip("valor:Q", title=unidad, format=",.2f"),
        ],
    )
    barras = base.mark_bar(color=color, cornerRadiusEnd=4, height={"band": 0.62})
    etiquetas = base.mark_text(
        align="left", dx=6, color=TINTA_SUAVE, fontSize=12
    ).encode(text=alt.Text("valor:Q", format=",.1f"))

    alto = alto or max(170, 30 * len(datos) + 60)
    return _afinar(barras + etiquetas, titulo, alto)


def barras_por_dia(datos: pd.DataFrame, valor: str, titulo: str):
    datos = datos.rename(columns={valor: "valor"})
    base = alt.Chart(datos).encode(
        x=alt.X("fecha:T", title=None, axis=alt.Axis(format="%d/%m", grid=False)),
        y=alt.Y("valor:Q", title=None, axis=alt.Axis(grid=True, tickCount=4)),
        tooltip=[
            alt.Tooltip("fecha:T", title=" ", format="%d/%m/%y"),
            alt.Tooltip("valor:Q", title="horas", format=",.2f"),
        ],
    )
    barras = base.mark_bar(color=VERDE, cornerRadiusEnd=4, width={"band": 0.6})
    etiquetas = base.mark_text(
        dy=-9, color=TINTA_SUAVE, fontSize=12
    ).encode(text=alt.Text("valor:Q", format=",.1f"))
    return _afinar(barras + etiquetas, titulo, 280)


# --------------------------------------------------------------------------- #
# Exportación
# --------------------------------------------------------------------------- #
def a_excel(hojas: dict[str, pd.DataFrame]) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as escritor:
        for nombre, tabla in hojas.items():
            tabla.to_excel(escritor, sheet_name=nombre[:31], index=False)
    return buffer.getvalue()


def a_csv(tabla: pd.DataFrame) -> bytes:
    return tabla.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")


def para_mostrar(df: pd.DataFrame, columnas: list[str] | None = None) -> pd.DataFrame:
    """Tabla lista para `st.dataframe`, con las fechas como fechas reales.

    No se convierten a texto a propósito: si lo fueran, al hacer clic en el
    encabezado la tabla ordenaría alfabéticamente y mandaría el día antes que
    el mes. El formato DD/MM/AA lo aplica CONFIG_COLUMNAS en la presentación,
    de modo que el orden sigue siendo cronológico.
    """
    columnas = [c for c in (columnas or df.columns) if c in df.columns]
    return df[columnas].copy().rename(columns=ETIQUETAS)


def para_exportar(tabla: pd.DataFrame) -> pd.DataFrame:
    """Copia con las fechas ya formateadas, para Excel y CSV."""
    tabla = tabla.copy()
    for columna in tabla.select_dtypes(include="datetime").columns:
        tabla[columna] = tabla[columna].dt.strftime(FORMATO_FECHA)
    return tabla


# Formato de las columnas de fecha en las tablas. Streamlit muestra DD/MM/AA y
# ordena por el valor real al hacer clic en el encabezado.
CONFIG_COLUMNAS = {
    ETIQUETAS[columna]: st.column_config.DateColumn(ETIQUETAS[columna], format="DD/MM/YY")
    for columna in ("fecha", "fecha_inicio", "fecha_reintegro")
} | {
    ETIQUETAS["created_at"]: st.column_config.DatetimeColumn(
        ETIQUETAS["created_at"], format="DD/MM/YY HH:mm"
    ),
}


# --------------------------------------------------------------------------- #
# Interfaz
# --------------------------------------------------------------------------- #
st.set_page_config(page_title="Actividades Ing. Civil", page_icon="🏗️", layout="wide")

# Hoja de estilos. Los selectores por data-testid son los puntos de enganche de
# Streamlit; si alguno cambia de nombre en una versión futura, la regla
# simplemente deja de aplicar y la app sigue funcionando sin romperse.
ESTILOS = f"""
<style>
  :root {{
    --verde: {VERDE};
    --verde-oscuro: {VERDE_OSCURO};
    --verde-tenue: {VERDE_TENUE};
    --tinta: {TINTA};
    --tinta-suave: {TINTA_SUAVE};
    --rejilla: {REJILLA};
  }}

  /* Títulos con un acento de marca a la izquierda */
  h1 {{
    font-weight: 700 !important;
    letter-spacing: -0.02em;
    padding-left: 14px;
    border-left: 5px solid var(--verde);
    line-height: 1.15;
  }}
  h2, h3 {{ font-weight: 650 !important; color: var(--tinta); }}

  /* Tarjetas de indicadores */
  div[data-testid="stMetric"] {{
    background: #ffffff;
    border: 1px solid var(--rejilla);
    border-top: 3px solid var(--verde);
    border-radius: 12px;
    padding: 14px 18px;
    box-shadow: 0 1px 2px rgba(11, 11, 11, 0.04);
  }}
  div[data-testid="stMetricValue"] {{
    font-size: 1.65rem;
    font-weight: 700;
    color: var(--tinta);
  }}
  div[data-testid="stMetricLabel"] {{
    color: var(--tinta-suave);
    font-size: 0.82rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }}

  /* Pestañas: la activa queda marcada en verde */
  button[data-baseweb="tab"] {{ font-weight: 600; }}
  div[data-baseweb="tab-highlight"] {{ background-color: var(--verde) !important; }}

  /* Botones. Sobre el verde el texto va oscuro: en blanco no alcanza contraste */
  div.stButton > button, div.stFormSubmitButton > button {{
    background: var(--verde);
    color: var(--tinta) !important;
    border: 1px solid var(--verde-oscuro);
    border-radius: 9px;
    font-weight: 650;
    transition: filter 120ms ease;
  }}
  div.stButton > button:hover, div.stFormSubmitButton > button:hover {{
    filter: brightness(1.06);
    border-color: var(--verde-oscuro);
  }}
  div[data-testid="stDownloadButton"] > button {{
    background: #ffffff;
    color: var(--tinta) !important;
    border: 1px solid var(--rejilla);
    border-radius: 9px;
    font-weight: 600;
  }}
  div[data-testid="stDownloadButton"] > button:hover {{
    border-color: var(--verde-oscuro);
    background: var(--verde-tenue);
  }}

  /* Barra lateral */
  section[data-testid="stSidebar"] {{ border-right: 1px solid var(--rejilla); }}
  section[data-testid="stSidebar"] h2 {{
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--tinta-suave);
  }}

  /* Etiquetas de los filtros seleccionados */
  span[data-baseweb="tag"] {{
    background-color: var(--verde) !important;
    color: var(--tinta) !important;
  }}

  /* Pantalla de ingreso y estado de carga */
  .marca {{
    display: flex; align-items: center; gap: 12px; margin-bottom: 4px;
  }}
  .marca-punto {{
    width: 34px; height: 34px; border-radius: 10px;
    background: var(--verde); flex: 0 0 auto;
    display: grid; place-items: center; font-size: 1.1rem;
  }}
  .marca-texto {{
    font-size: 1.32rem; font-weight: 700; color: var(--tinta);
    letter-spacing: -0.01em; line-height: 1.2;
  }}
  .marca-sub {{ color: var(--tinta-suave); font-size: 0.9rem; margin: 2px 0 18px; }}

  .cargando {{
    border: 1px solid var(--rejilla);
    border-radius: 14px;
    background: #ffffff;
    padding: 34px 32px;
    max-width: 440px;
    margin: 56px auto;
    text-align: center;
    box-shadow: 0 2px 10px rgba(11, 11, 11, 0.05);
  }}
  .cargando-titulo {{
    font-size: 1.05rem; font-weight: 650; color: var(--tinta); margin-bottom: 6px;
  }}
  .cargando-nota {{ color: var(--tinta-suave); font-size: 0.86rem; }}
  .cargando-pista {{
    position: relative; height: 6px; border-radius: 99px;
    background: var(--verde-tenue); overflow: hidden; margin: 20px 0 14px;
  }}
  .cargando-pista::after {{
    content: ""; position: absolute; inset: 0;
    width: 40%; border-radius: 99px; background: var(--verde);
    animation: avance 1.1s ease-in-out infinite;
  }}
  @keyframes avance {{
    0%   {{ transform: translateX(-105%); }}
    100% {{ transform: translateX(255%); }}
  }}
  @media (prefers-reduced-motion: reduce) {{
    .cargando-pista::after {{ animation: none; width: 100%; opacity: 0.55; }}
  }}
</style>
"""
st.markdown(ESTILOS, unsafe_allow_html=True)

TARJETA_CARGANDO = """
<div class="cargando">
  <div class="cargando-titulo">Cargando registros</div>
  <div class="cargando-nota">Consultando la base de datos…</div>
  <div class="cargando-pista"></div>
  <div class="cargando-nota">Puede tardar unos segundos la primera vez del día.</div>
</div>
"""


def encabezado_marca(subtitulo: str) -> None:
    st.markdown(
        f"""
        <div class="marca">
          <div class="marca-punto">🏗️</div>
          <div class="marca-texto">Actividades de ingeniería civil</div>
        </div>
        <div class="marca-sub">{subtitulo}</div>
        """,
        unsafe_allow_html=True,
    )

if not (SUPABASE_URL and SUPABASE_KEY):
    st.error(
        "Faltan las credenciales de conexión. Configúrelas en el archivo .env "
        "(local) o en los Secrets de Streamlit Cloud."
    )
    st.stop()

# La app debe conectarse con la clave anon. Una service_role omite RLS, con lo
# que el control de acceso dejaría de existir, y además quedaría almacenada en
# un servicio externo con permiso de escritura sobre toda la base. Antes esto
# podía pasar inadvertido, así que se verifica al arrancar y se detiene.
if _rol_declarado(SUPABASE_KEY) == "service_role":
    st.error(
        "La clave configurada es una **service_role** y esta aplicación no debe "
        "usarla: omite las políticas RLS y anula el control de acceso. "
        "Reemplácela por la clave **anon** del proyecto "
        "(Supabase → Settings → API) en el .env o en los Secrets."
    )
    st.stop()

token = token_activo()
if not token:
    pantalla_ingreso()
    st.stop()

with st.sidebar:
    st.header("Sesión")
    st.caption(st.session_state["sesion"]["correo"])
    if st.button("Actualizar datos", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    if st.button("Cerrar sesión", width="stretch"):
        cerrar_sesion()
        st.rerun()

# La tarjeta se muestra solo mientras no haya datos en la sesión. En los reruns
# por cambio de filtro los datos vienen de la caché, y hacerla aparecer daba la
# impresión de que la app volvía a consultar la base.
marcador_carga = st.empty()
if not st.session_state.get("datos_listos"):
    marcador_carga.markdown(TARJETA_CARGANDO, unsafe_allow_html=True)
usuario_id = st.session_state["sesion"]["id"]
datos, herramientas = obtener_datos(usuario_id, token)
vacaciones = obtener_vacaciones(usuario_id, token) if not datos.empty else pd.DataFrame()
marcador_carga.empty()
if not datos.empty:
    st.session_state["datos_listos"] = True

if datos.empty:
    st.title("Registro diario de actividades de ingeniería civil")
    st.stop()

alertas = marcar_alertas(datos, vacaciones)
datos["tiene_alerta"] = alertas.any(axis=1)
datos["detalle_alertas"] = alertas.apply(lambda fila: " · ".join(alertas.columns[fila]), axis=1)

with st.sidebar:
    st.header("Filtros")

    fechas_validas = datos["fecha"].dropna()
    if fechas_validas.empty:
        rango = None
    else:
        minimo, maximo = fechas_validas.min().date(), fechas_validas.max().date()
        rango = st.date_input(
            "Rango de fechas",
            value=(minimo, maximo),
            min_value=minimo,
            max_value=maximo,
            format="DD/MM/YYYY",
        )

    def multiselector(columna: str, etiqueta: str, origen: pd.DataFrame = datos) -> list[str]:
        if columna not in origen.columns:
            return []
        opciones = sorted(v for v in origen[columna].dropna().unique() if str(v).strip())
        return st.multiselect(etiqueta, opciones, placeholder="Todos")

    colaboradores = multiselector("colaborador", "Colaborador")
    actividades = multiselector("actividad", "Actividad")
    herramientas_elegidas = multiselector("herramienta", "Herramienta", herramientas)
    solo_alertas = st.checkbox("Sólo registros con alerta")
    solo_herramienta = st.checkbox("Sólo registros con herramienta")

# Opciones tomadas del conjunto completo, para que no cambien al filtrar.
def _opciones(columna: str) -> list[str]:
    if columna not in datos.columns:
        return []
    return sorted(v for v in datos[columna].dropna().unique() if str(v).strip())


CLASIFICACIONES_DISPONIBLES = _opciones("clasificacion")
HACIENDAS_DISPONIBLES = _opciones("hacienda_nombre")


# Los selectores se dibujan más abajo, sobre el selector de vista; Streamlit deja
# su valor en session_state antes de ejecutar el script, así que aquí ya están.
def _seleccion(clave: str, validas: list[str]) -> list[str]:
    return [v for v in (st.session_state.get(clave) or []) if v in validas]


clasificaciones = _seleccion("filtro_clasificacion", CLASIFICACIONES_DISPONIBLES)
haciendas = _seleccion("filtro_hacienda", HACIENDAS_DISPONIBLES)

filtrados = datos.copy()
if rango and isinstance(rango, (list, tuple)) and len(rango) == 2:
    desde, hasta = pd.Timestamp(rango[0]), pd.Timestamp(rango[1])
    filtrados = filtrados[filtrados["fecha"].between(desde, hasta) | filtrados["fecha"].isna()]
else:
    desde = hasta = None
for columna, seleccion in [
    ("clasificacion", clasificaciones),
    ("colaborador", colaboradores),
    ("hacienda_nombre", haciendas),
    ("actividad", actividades),
]:
    if seleccion:
        filtrados = filtrados[filtrados[columna].isin(seleccion)]
if herramientas_elegidas:
    con_elegidas = herramientas.loc[herramientas["herramienta"].isin(herramientas_elegidas), "indice"]
    filtrados = filtrados[filtrados.index.isin(con_elegidas)]
if solo_alertas:
    filtrados = filtrados[filtrados["tiene_alerta"]]
if solo_herramienta:
    filtrados = filtrados[filtrados["herramientas_texto"] != ""]

# Herramientas de los registros que quedaron tras los filtros.
herramientas_filtradas = herramientas[herramientas["indice"].isin(filtrados.index)]

st.title("Registro diario de actividades de ingeniería civil")
st.caption(
    f"{len(filtrados)} de {len(datos)} registros · último registro recibido: "
    + (
        datos["created_at"].max().strftime(FORMATO_FECHA + " %H:%M")
        if "created_at" in datos.columns and datos["created_at"].notna().any()
        else "sin dato"
    )
)

if filtrados.empty:
    st.warning("Ningún registro cumple los filtros seleccionados.")
    st.stop()

columnas_kpi = st.columns(6)
columnas_kpi[0].metric("Registros", f"{len(filtrados):,}".replace(",", "."))
columnas_kpi[1].metric("Horas", f"{filtrados['horas_totales'].sum():,.1f}")
columnas_kpi[2].metric("Horas adicionales", f"{filtrados['horas_adicionales'].sum():,.1f}")
columnas_kpi[3].metric("Colaboradores", filtrados["ficha"].nunique())
columnas_kpi[4].metric("Horas herramienta", f"{herramientas_filtradas['herramienta_horas'].sum():,.1f}")
columnas_kpi[5].metric("Con alerta", int(filtrados["tiene_alerta"].sum()))

col_clasificacion, col_hacienda = st.columns(2)
col_clasificacion.multiselect(
    "Clasificación",
    CLASIFICACIONES_DISPONIBLES,
    key="filtro_clasificacion",
    placeholder="Todas las clasificaciones",
    help="Tipo de labor (administrativa, campo, cosecha), cuenta control o novedad. "
    "Aplica a todas las vistas.",
)
col_hacienda.multiselect(
    "Hacienda",
    HACIENDAS_DISPONIBLES,
    key="filtro_hacienda",
    placeholder="Todas las haciendas",
    help="Aplica a todas las vistas.",
)

# Un selector en lugar de pestañas: con st.tabs, Streamlit construye y envía
# todas las vistas en cada interacción. Dibujando sólo la activa, cada cambio de
# filtro manda una fracción de lo que mandaba.
VISTAS = ["Detalle", "Resumen", "Vacaciones", "Calidad de datos"]
vista_activa = st.segmented_control(
    "Vista", VISTAS, default="Detalle", key="vista", label_visibility="collapsed"
) or "Detalle"

if vista_activa == "Resumen":
    por_clasificacion = filtrados.groupby("clasificacion", as_index=False)["horas_totales"].sum()
    st.altair_chart(
        barras_horizontales(por_clasificacion, "clasificacion", "horas_totales",
                            "Horas por clasificación", "horas"),
    )

    izquierda, derecha = st.columns(2)

    por_colaborador = (
        filtrados.groupby("colaborador", as_index=False)["horas_totales"]
        .sum()
        .nlargest(20, "horas_totales")
    )
    izquierda.altair_chart(
        barras_horizontales(por_colaborador, "colaborador", "horas_totales",
                            "Horas por colaborador", "horas"),
    )

    en_hacienda = filtrados[filtrados["hacienda_nombre"] != ""]
    if en_hacienda.empty:
        derecha.info("Ningún registro filtrado tiene hacienda.")
    else:
        por_hacienda = en_hacienda.groupby("hacienda_nombre", as_index=False)["horas_totales"].sum()
        derecha.altair_chart(
            barras_horizontales(por_hacienda, "hacienda_nombre", "horas_totales",
                                "Horas por hacienda", "horas"),
        )

    por_dia = filtrados.dropna(subset=["fecha"]).groupby("fecha", as_index=False)["horas_totales"].sum()
    st.altair_chart(barras_por_dia(por_dia, "horas_totales", "Horas por día"))

    izquierda, derecha = st.columns(2)
    por_actividad = (
        filtrados.groupby("actividad", as_index=False)["horas_totales"]
        .sum()
        .nlargest(15, "horas_totales")
    )
    izquierda.altair_chart(
        barras_horizontales(por_actividad, "actividad", "horas_totales",
                            "Horas por actividad", "horas"),
    )

    if herramientas_filtradas.empty:
        derecha.info("Ningún registro filtrado usó herramienta.")
    else:
        por_herramienta = (
            herramientas_filtradas.groupby("herramienta", as_index=False)["herramienta_horas"]
            .sum()
            .nlargest(15, "herramienta_horas")
        )
        derecha.altair_chart(
            barras_horizontales(por_herramienta, "herramienta", "herramienta_horas",
                                "Horas de uso por herramienta", "horas"),
        )

    st.subheader("Consolidado por colaborador")
    tabla_colaborador = (
        filtrados.groupby(["ficha", "colaborador"], as_index=False)
        .agg(
            dias=("fecha", "nunique"),
            registros=("clave", "count"),
            horas=("horas_totales", "sum"),
            adicionales=("horas_adicionales", "sum"),
            horas_herramienta=("horas_herramienta", "sum"),
            alertas=("tiene_alerta", "sum"),
        )
        .sort_values("horas", ascending=False)
        .rename(
            columns={
                "ficha": "Ficha",
                "colaborador": "Colaborador",
                "dias": "Días",
                "registros": "Registros",
                "horas": "Horas",
                "adicionales": "Horas adicionales",
                "horas_herramienta": "Horas herramienta",
                "alertas": "Alertas",
            }
        )
    )
    st.dataframe(tabla_colaborador, hide_index=True, width="stretch")

if vista_activa == "Detalle":
    st.subheader("Registros")
    columnas_detalle = [c for c in ORDEN_COLUMNAS if c in filtrados.columns] + ["created_at"]
    vista = para_mostrar(filtrados, columnas_detalle)
    st.dataframe(
        vista,
        hide_index=True,
        width="stretch",
        height=520,
        column_config=CONFIG_COLUMNAS,
    )

    # El Excel lleva además una hoja con cada herramienta en su propia fila, que
    # es como se digita el uso de equipos.
    detalle_herramientas = herramientas_filtradas.merge(
        filtrados[["fecha", "ficha", "colaborador", "codigo", "actividad", "hora_inicio", "hora_fin"]],
        left_on="indice",
        right_index=True,
    )
    hoja_herramientas = para_mostrar(
        detalle_herramientas,
        ["fecha", "ficha", "colaborador", "codigo", "actividad", "hora_inicio", "hora_fin",
         "herramienta_ip", "herramienta_desc", "herramienta_horas"],
    )

    descarga_1, descarga_2 = st.columns(2)
    descarga_1.download_button(
        "Descargar Excel",
        a_excel({
            "Registros": para_exportar(vista),
            "Herramientas": para_exportar(hoja_herramientas),
        }),
        file_name="actividades_ing_civil.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    descarga_2.download_button(
        "Descargar CSV",
        a_csv(para_exportar(vista)),
        file_name="actividades_ing_civil.csv",
        mime="text/csv",
    )

if vista_activa == "Vacaciones":
    st.subheader("Vacaciones")
    if vacaciones.empty:
        st.info(
            f"«{VACACIONES_TABLA}» no devolvió registros. Si la tabla tiene datos, revise "
            "que exista la política de lectura (SELECT) para el rol `authenticated`."
        )
    else:
        periodo = vacaciones
        if desde is not None:
            # Se muestran las que se cruzan con el rango, no sólo las que empiezan en él.
            periodo = periodo[
                (periodo["fecha_inicio"] <= hasta)
                & (periodo["fecha_reintegro"].isna() | (periodo["fecha_reintegro"] > desde))
            ]
        if colaboradores:
            periodo = periodo[periodo["colaborador"].isin(colaboradores)]

        if periodo.empty:
            st.info("No hay vacaciones que se crucen con los filtros seleccionados.")
        else:
            kpi_1, kpi_2, kpi_3 = st.columns(3)
            kpi_1.metric("En curso", int((periodo["estado"] == "En curso").sum()))
            kpi_2.metric("Programadas", int((periodo["estado"] == "Programadas").sum()))
            kpi_3.metric("Finalizadas", int((periodo["estado"] == "Finalizadas").sum()))

            tabla_vacaciones = para_mostrar(
                periodo,
                ["estado", "ficha", "colaborador", "fecha_inicio", "fecha_reintegro", "dias", "created_at"],
            )
            st.dataframe(
                tabla_vacaciones,
                hide_index=True,
                width="stretch",
                column_config=CONFIG_COLUMNAS,
            )
            st.download_button(
                "Descargar Excel",
                a_excel({"Vacaciones": para_exportar(tabla_vacaciones)}),
                file_name="vacaciones_ing_civil.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

if vista_activa == "Calidad de datos":
    st.subheader("Alertas encontradas")
    conteo = (
        alertas.loc[filtrados.index]
        .sum()
        .reset_index()
        .set_axis(["regla", "registros"], axis=1)
    )
    conteo = conteo[conteo["registros"] > 0]

    if conteo.empty:
        st.success("Ningún registro filtrado presenta inconsistencias.")
    else:
        st.altair_chart(
            barras_horizontales(conteo, "regla", "registros", "Registros por tipo de alerta",
                                "registros", color=ROJO),
        )
        for regla, descripcion in REGLAS.items():
            afectados = filtrados[alertas.loc[filtrados.index, regla]]
            if afectados.empty:
                continue
            with st.expander(f"{regla} — {len(afectados)} registro(s)"):
                st.caption(descripcion)
                st.dataframe(
                    para_mostrar(afectados, [c for c in ORDEN_COLUMNAS if c in afectados.columns]),
                    hide_index=True,
                    width="stretch",
                    column_config=CONFIG_COLUMNAS,
                )
