# Registro diario de actividades de ingeniería civil

Tablero en Streamlit que consulta los registros diarios del área de ingeniería
civil capturados por un bot de Telegram: labores, cuentas control, novedades,
uso de herramientas y vacaciones. Es una aplicación de **sólo lectura**: no
escribe nada en la base de datos.

Todo el tablero vive en un único archivo, [app.py](app.py).

---

## Arquitectura

```
Colaborador (Telegram)
      │
      ▼
   Flujo n8n ──────────► Supabase / Postgres
                          ├── civil_registros    (labores, cuentas control, novedades)
                          └── civil_vacaciones   (vacaciones)
                                    │
                                    ▼
                          app.py (Streamlit)
```

A diferencia del tablero de maquinaria, **no hay cruces con tablas maestras**:
el bot guarda en cada registro la descripción de la labor, la hacienda y la
herramienta en el momento de capturarlo, así que la app no depende de
`actividades` ni de `Maestro_Equipos_Zonas`.

Dos identidades distintas tocan la base de datos:

| Quién | Clave | Permisos |
|---|---|---|
| Flujo de n8n | `service_role` | Escribe. Omite RLS por diseño. |
| Tablero | `anon` + sesión de usuario | Sólo `SELECT`, y únicamente tras iniciar sesión. |

El tablero nunca usa la `service_role`. Se conecta con la clave `anon`, que por
sí sola no ve ningún dato, y a partir del login adjunta el token del usuario a
cada consulta.

---

## Ciclo de ejecución

Streamlit re-ejecuta `app.py` completo en cada interacción. El orden importa:

1. **Configuración**: `_config()` resuelve las variables de entorno.
2. **Autenticación**: `token_activo()`. Sin token válido se dibuja
   `pantalla_ingreso()` y `st.stop()` corta ahí.
3. **Carga**: `obtener_datos()` → `cargar_registros()` y `obtener_vacaciones()`
   → `cargar_vacaciones()` (ambas cacheadas).
4. **Normalización**: `normalizar()` convierte tipos, traduce las claves internas
   del bot y deriva `actividad`, `codigo` y `clasificacion`.
5. **Herramientas**: `agregar_herramientas()` expande el arreglo `herramientas`
   a una tabla larga (una fila por herramienta) y resume cada registro.
6. **Validación**: `marcar_alertas()` produce la matriz booleana de reglas,
   incluido el cruce contra vacaciones.
7. **Filtros**: Clasificación y Hacienda (sobre el selector de vista) más
   Colaborador, Actividad y Herramienta (barra lateral) recortan el DataFrame a
   `filtrados`.
8. **Render**: una sola vista de las cuatro (Detalle, Resumen, Vacaciones,
   Calidad de datos), elegida con `st.segmented_control`.

Todo lo que se dibuja parte de `filtrados`; `datos` conserva el conjunto
completo para los contadores del encabezado.

---

## Configuración

`_config(nombre, defecto)` busca el nombre primero en `st.secrets` (nube) y
luego en las variables de entorno cargadas del `.env` (local). El mismo código
corre en los dos lados sin ramas condicionales.

Recibe **un único nombre** a propósito: una cadena de alternativas permitiría
que una variable con otro nombre entrara sin que nadie lo note.

| Variable | Obligatoria | Por defecto |
|---|---|---|
| `SUPABASE_URL` | sí | — |
| `SUPABASE_KEY` | sí | — |
| `SUPABASE_TABLE` | no | `civil_registros` |
| `SUPABASE_VACACIONES_TABLE` | no | `civil_vacaciones` |

Si faltan `SUPABASE_URL` o `SUPABASE_KEY`, la app se detiene con un mensaje antes
de dibujar nada.

`SUPABASE_KEY` debe ser la clave **anon**, nunca la `service_role`: esa omite
RLS y anularía todo el control de acceso descrito abajo, y además quedaría
almacenada en un servicio externo con permiso de escritura sobre toda la base.

La app no se limita a documentarlo: `_rol_declarado()` lee el rol que declara la
clave y, si es una `service_role`, **se detiene al arrancar** con un mensaje que
explica qué reemplazar. Reconoce los dos formatos de Supabase:

| Clave | Rol detectado | Resultado |
|---|---|---|
| JWT con `role: anon` | `anon` | arranca |
| JWT con `role: service_role` | `service_role` | se detiene |
| `sb_publishable_…` | `anon` | arranca |
| `sb_secret_…` | `service_role` | se detiene |
| formato desconocido | — | arranca (no se bloquea por no poder leerla) |

---

## Autenticación

Implementada sobre Supabase Auth (correo y contraseña).

- `iniciar_sesion()` llama a `sign_in_with_password` y traduce los errores
  crudos a mensajes en español. Guarda la sesión en `st.session_state["sesion"]`
  (`access_token`, `refresh_token`, `expira_en`, `correo`, `id`).
- `token_activo()` se ejecuta en cada rerun. Si al token le quedan menos de
  `MARGEN_REFRESCO` segundos (120), lo renueva con `refresh_session`. Si la
  renovación falla, descarta la sesión y devuelve `None`, con lo que el usuario
  vuelve al formulario.
- `_cliente(usuario_id, token)` toma el cliente cacheado del usuario y llama a
  `cliente.postgrest.auth(token)`. Desde ese punto las consultas llegan a
  Postgres como rol `authenticated` y RLS decide qué filas devuelve.
- `cerrar_sesion()` cierra la sesión remota, borra `session_state` y limpia la
  caché, para que los datos de un usuario no queden en pantalla del siguiente.

Consecuencia de diseño: **el control de acceso está en la base de datos, no en
la app**. Si las políticas RLS no conceden `SELECT` al rol `authenticated`, el
login funciona pero no llega ninguna fila. Ese caso se detecta explícitamente en
`obtener_datos()` y se reporta como problema de políticas, no como "sin datos".

Las políticas RLS y las cuentas de usuario se administran directamente en
Supabase; no forman parte de este repositorio.

### Alcance

Todo usuario autenticado lee todos los registros y todas las vacaciones. Las
políticas están en `supabase_politicas_civil.sql` (local, no se publica): activan
RLS en `civil_registros` y `civil_vacaciones` y conceden `SELECT` al rol
`authenticated`. El bot no se ve afectado porque escribe con `service_role`.

---

## Caché

### Datos (`cache_data`)

| Función | TTL | Clave de caché |
|---|---|---|
| `cargar_registros` | 300 s | `(tabla, usuario_id)` |
| `cargar_vacaciones` | 300 s | `(tabla, usuario_id)` |

El `usuario_id` forma parte de la clave para que un usuario no reciba datos
traídos con las credenciales de otro. El **token no entra en la clave**: se pasa
como `_token` y Streamlit ignora los argumentos que empiezan con guion bajo. Es
deliberado: el token rota cada hora, y si formara parte de la clave invalidaría
la caché en cada rotación.

### Conexiones (`cache_resource`)

`create_client()` cuesta ~380 ms la primera vez y ~180 ms después, así que no se
crea por consulta:

- `_cliente_anonimo()` — uno por proceso, para las operaciones de Auth.
- `_cliente_sesion(usuario_id)` — uno por usuario, reutilizado entre reruns. El
  token tampoco entra en esta clave; `_cliente()` lo adjunta en cada uso con
  `postgrest.auth()`, que sólo reescribe una cabecera.

### Lo que viaja al navegador

El script corre en el servidor de Streamlit, que habla con Supabase servidor a
servidor: **la consulta no pasa por la conexión del usuario**. Por ella viaja
sólo la interfaz renderizada, y de ahí vienen las dos decisiones de estructura:

- **Una vista a la vez.** Con `st.tabs`, Streamlit construye y envía las cuatro
  vistas en cada interacción. Con el selector se dibuja sólo la activa, así que
  un cambio de filtro manda una cuarta parte de lo que mandaba.
- **Los filtros de Clasificación y Hacienda van arriba del selector**, no dentro de una
  vista. Si estuvieran dentro, Streamlit descartaría su valor al cambiar de
  vista, porque el widget deja de renderizarse.


### Consultas

`COLUMNAS_CONSULTA` limita el `SELECT` a los campos que se usan: las columnas
internas (`id`, `id_registro`, `origen`, `chat_id`) y las que el bot no llena
(`labor_grupo`, `clase_labor`) no viajan por la red.

`_descargar()` pagina de 1000 en 1000 hasta agotar la tabla, así que no depende
del límite por defecto de PostgREST.

El botón **Actualizar datos** llama a `st.cache_data.clear()`.

---

## Modelo de datos

### `civil_registros`

Cada fila es un tramo horario de un colaborador. `tipo_registro` define qué
columnas aplican:

| `tipo_registro` | Columnas propias |
|---|---|
| `labor` | `labor_codigo`, `labor_desc`, `tipo_labor` y, según el tipo, `hacienda_*`, `zona_cosecha` o `cuenta_gasto` |
| `cuenta_control` | `cuenta_control` (empieza por `160`), `cuenta_desc` |
| `novedad` | `novedad_tipo` (`permiso_calamidad`, `permiso_cita_medica`) |

Comunes a todos: `ficha`, `colaborador`, `fecha`, `hora_inicio`, `hora_fin`,
`horas_totales`, `horas_normales`, `horas_adicionales`, `tipo_hora_adicional`
(`extra` / `banco`) y `created_at` (*Fecha digitación*).

La base ya impide varios errores, y la app no los vuelve a validar: cruces de
horario del mismo colaborador (`sin_cruce_horario`), `hora_fin ≤ hora_inicio`,
inicio antes de las 06:00, herramienta marcada sin IP y labor sin ubicación.
Varias de esas restricciones son `NOT VALID`: rigen para filas nuevas, pero las
anteriores a su creación pueden no cumplirlas.

### Columnas derivadas

| Columna | Origen |
|---|---|
| `actividad` | `labor_desc` en labores; `cuenta_desc` (o `Cuenta <código>`) en cuentas control; el nombre de la novedad en novedades. Un solo campo para filtrar y graficar. |
| `codigo` | `labor_codigo` o `cuenta_control`. |
| `clasificacion` | `Administrativa` / `Campo` / `Cosecha` para las labores; `Cuenta control` o `Novedad` para lo demás. Alimenta el filtro superior. |
| `horas_calendario` | `hora_fin − hora_inicio` en horas decimales. Referencia para contrastar `horas_totales`. |
| `herramientas_texto`, `horas_herramienta` | IPs usadas en el registro y suma de sus horas. |
| `clave` | `ficha`, `fecha` y `hora_inicio`. Identifica la fila (la base garantiza que no se repite). |
| `tiene_alerta`, `detalle_alertas` | Resumen de `marcar_alertas()`. |

Las claves internas del bot (`cuenta_control`, `permiso_cita_medica`, `banco`…)
se traducen en pantalla con los diccionarios `NOMBRES_*`.

### Herramientas

Un registro puede usar varias herramientas. El bot las guarda en el arreglo
`jsonb` `herramientas` (`[{ip, desc, horas}, …]`); los registros anteriores a ese
arreglo las tienen en las columnas sueltas `herramienta_ip`, `herramienta_desc`
y `herramienta_horas`.

`expandir_herramientas()` replica la vista `v_civil_registros_herramientas`:
usa el arreglo y, si está vacío, las columnas sueltas. Se hace en pandas y no
consultando la vista para no tener que abrir otra política de lectura. El
resultado es una tabla larga con `indice` apuntando al registro, que alimenta el
filtro de herramienta, el gráfico de horas por herramienta y la hoja
*Herramientas* del Excel.

### `civil_vacaciones`

`fecha_inicio`, `fecha_reintegro` (puede ser nula: vacaciones abiertas) y
`dias`, calculado por la base. El rango es `[inicio, reintegro)`: el día de
reintegro ya es laboral. `normalizar_vacaciones()` deriva `estado`
(*Programadas*, *En curso*, *Finalizadas*) respecto a la fecha de hoy en
Colombia.

La vista **Vacaciones** muestra las que se cruzan con el rango de fechas, no
sólo las que empiezan en él, y respeta el filtro de colaborador.

---

## Validaciones

`marcar_alertas()` devuelve un DataFrame booleano con una columna por regla.
`REGLAS` guarda la descripción que se muestra en la interfaz.

| Regla | Condición |
|---|---|
| Horas no coinciden con el horario | `abs(horas_totales − horas_calendario) > 0.05` |
| Normales + adicionales no suman el total | `abs(horas_normales + horas_adicionales − horas_totales) > 0.05`, sólo si hay `horas_normales` |
| Herramienta con más horas que la labor | suma de horas de herramientas `> horas_totales` |
| Jornada incompleta | horas del colaborador en el día `< JORNADA_HORAS` |
| Jornada larga sin horas adicionales | horas del día `> JORNADA_HORAS` y ninguna adicional |
| Registro durante vacaciones | la fecha cae en `[fecha_inicio, fecha_reintegro)` de unas vacaciones del mismo colaborador |

`JORNADA_HORAS` vale 9 (06:00 a 15:00). Las reglas de jornada suman el día
completo del colaborador y marcan todos los tramos de ese día. Se calculan sobre
`datos`, antes de filtrar, así que filtrar por clasificación o actividad no hace
aparecer jornadas incompletas que no lo son.

Para agregar una regla: añada la fila en `marcar_alertas()` **y** la entrada
correspondiente en `REGLAS`. La vista de calidad recorre `REGLAS`, así que una
regla sin descripción no se muestra.

---

## Gráficos

Dos constructores sobre **Altair**, ambos con el mismo tratamiento visual:

- `barras_horizontales()` — magnitudes por categoría.
- `barras_por_dia()` — serie temporal.

Se usa Altair y no Plotly por el peso que viaja al navegador. Medido sobre los
archivos del paquete, ya comprimidos:

| Librería de gráficos | Descarga |
|---|---|
| Plotly | 1.966 KB |
| Vega-Lite (Altair y `st.*_chart`) | 281 KB |

Vega-Lite ya viene en el paquete de Streamlit; los chunks de Plotly se descargan
aparte la primera vez que aparece un gráfico. Para comparar, la tabla completa
con 5.000 registros pesa 82 KB y cada especificación de gráfico menos de 1 KB:
el cuello de botella en una conexión lenta es la librería, no los datos.

Decisiones que conviene no revertir sin motivo:

- **Encoding nominal (`categoria:N`) en el eje Y.** Los códigos de hacienda
  llegan como `'080104'`. Un eje cuantitativo los leería como magnitudes: en la
  versión con Plotly el eje llegó a mostrar `82.5k` como marca.
- **`sort="-x"`.** Ordena las barras por magnitud, no por nombre.
- **`labelLimit=240`.** Los nombres de actividad son largos; con el límite por
  defecto quedan recortados con elipsis.
- **Sin truncar las categorías en los datos.** Dos nombres con el mismo prefijo
  colapsarían en una sola barra y sumarían sus valores.
- **Valor escrito al lado de cada barra.** No es decoración: el verde de marca
  da 2.21:1 contra el fondo, por debajo del umbral de 3:1 para marcas de datos,
  así que el color nunca es lo único que comunica la magnitud.

### Paleta

| Constante | Uso |
|---|---|
| `VERDE` `#7DBE00` | Color institucional. Pinta todas las barras de magnitud. |
| `VERDE_OSCURO` `#4e7a00` | El único que admite texto blanco encima (5.1:1). |
| `VERDE_TENUE` `#eff7e0` | Fondos suaves. |
| `ROJO` `#e34948` | Reservado para estado (alertas). No se usa como color de serie. |
| `TINTA`, `TINTA_SUAVE` | Texto principal y secundario. |
| `SUPERFICIE`, `REJILLA` | Fondo y líneas de rejilla. |

Sobre `VERDE` el texto va **oscuro** (8.7:1), nunca blanco (2.3:1). Como las
series son únicas, cada gráfico se distingue por su título y no por el matiz.

El tema claro se fija en [.streamlit/config.toml](.streamlit/config.toml) para
que la preferencia del navegador no altere el contraste.

---

## Fechas

El formato de presentación es **DD/MM/AA** y vive en una sola constante,
`FORMATO_FECHA`. Aplica igual a `fecha`, a las fechas de vacaciones y a `created_at` (*Fecha digitación*).

Tres detalles que no son evidentes:

- **Las tablas reciben fechas, no texto.** `para_mostrar()` no convierte a
  cadena a propósito: si lo hiciera, al hacer clic en el encabezado la tabla
  ordenaría alfabéticamente y mandaría el día antes que el mes (`01/12/26` antes
  de `14/09/26`). El formato lo aplica `CONFIG_COLUMNAS` con
  `st.column_config.DateColumn`, de modo que el orden sigue el valor real.
- **Las exportaciones sí van formateadas.** `para_exportar()` convierte las
  fechas a texto justo antes de generar el Excel o el CSV. Sin eso el CSV
  saldría en formato ISO.
- **`created_at` se parsea con `format="ISO8601"`.** Postgres recorta los
  microsegundos en cero, así que la misma columna trae `...02.384+00` y
  `...00+00`. Con inferencia automática pandas toma el formato de la primera
  fila y convierte el resto a `NaT`, dejando celdas vacías al azar.

---

## Estado de carga

La tarjeta "Cargando registros" se muestra sólo mientras la sesión no tenga
datos, controlada por `st.session_state["datos_listos"]`. En los reruns por
cambio de filtro los datos vienen de la caché; mostrarla ahí daba la impresión
de que la app volvía a consultar la base. `cerrar_sesion()` borra el marcador
para que el siguiente ingreso vuelva a mostrarla.

---

## Puntos de extensión

| Quiero… | Dónde |
|---|---|
| Cambiar cómo se llama un campo en pantalla | `ETIQUETAS` |
| Mostrar u ocultar columnas del detalle, o reordenarlas | `ORDEN_COLUMNAS` |
| Agregar una validación | `marcar_alertas()` + `REGLAS` |
| Cambiar la duración de la jornada | `JORNADA_HORAS` |
| Traducir una clave nueva del bot | Los diccionarios `NOMBRES_*` |
| Agregar un filtro global | `_opciones()` + `_seleccion()` arriba del selector de vista, y el bucle que aplica los filtros |
| Agregar un filtro secundario | `multiselector()` en la barra lateral |
| Cambiar el formato de las fechas | `FORMATO_FECHA` y `CONFIG_COLUMNAS` |
| Cambiar colores | Las constantes de paleta y `.streamlit/config.toml` |
| Cambiar el formato de exportación | `a_excel()` / `a_csv()` |

---

## Limitaciones conocidas

- **Zona horaria fija.** `created_at` y el estado de las vacaciones se ajustan restando 5 horas. Es correcto
  para Colombia, que no aplica horario de verano, pero rompería en otra zona.
- **Sólo lectura.** No hay forma de corregir un registro desde el tablero; las
  inconsistencias que muestra la vista de calidad se corrigen en el origen.
- **Estado no compartido.** Filtros y sesión viven en `st.session_state`, es
  decir por pestaña del navegador. Nada se persiste entre visitas.
- **Sin paginación en la interfaz.** Todo el conjunto filtrado se carga en
  memoria y se dibuja. Con decenas de miles de registros habría que paginar la
  consulta o agregar en la base.

---

## Archivos del proyecto

| Archivo | Contenido |
|---|---|
| [app.py](app.py) | La aplicación completa. |
| [requirements.txt](requirements.txt) | Dependencias. |
| [.streamlit/config.toml](.streamlit/config.toml) | Tema visual. |
| [.env.example](.env.example) | Plantilla de variables locales. |
| [.streamlit/secrets.toml.example](.streamlit/secrets.toml.example) | Plantilla de variables en la nube. |
| `supabase_politicas_civil.sql` | Políticas RLS de lectura. Local, no se publica. |
