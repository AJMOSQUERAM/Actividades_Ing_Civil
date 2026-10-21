# Registro diario de labores de maquinaria

Tablero en Streamlit que consulta los registros de labores de maquinaria
capturados por un bot de Telegram. Es una aplicación de **sólo lectura**: no
escribe nada en la base de datos.

Todo el tablero vive en un único archivo, [app.py](app.py).

---

## Arquitectura

```
Operador (Telegram)
      │
      ▼
   Flujo n8n ──────────► Supabase / Postgres
                          ├── registro_labores_maquinaria   (registros del bot)
                          ├── actividades                   (maestra de labores)
                          └── Maestro_Equipos_Zonas         (maestra de equipos)
                                    │
                                    ▼
                          app.py (Streamlit)
```

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

1. **Configuración** — `_config()` resuelve las variables de entorno.
2. **Autenticación** — `token_activo()`. Sin token válido se dibuja
   `pantalla_ingreso()` y `st.stop()` corta ahí.
3. **Carga** — `obtener_datos(usuario_id, token)` → `cargar_registros()`
   (cacheada).
4. **Normalización** — `normalizar()` convierte tipos y deriva columnas.
5. **Enriquecimiento** — `agregar_actividades()` traduce el código de labor y
   `agregar_zonas()` cruza el equipo contra la maestra para traer zona y
   descripción.
6. **Validación** — `marcar_alertas()` produce la matriz booleana de reglas.
7. **Filtros** — Zona y Hacienda (sobre el selector de vista) más Operador,
   Suerte, Actividad y Equipo (barra lateral) recortan el DataFrame a
   `filtrados`.
8. **Render** — una sola vista de las tres (Detalle, Resumen, Calidad de
   datos), elegida con `st.segmented_control`.

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
| `SUPABASE_TABLE` | no | `registro_labores_maquinaria` |
| `SUPABASE_ACTIVIDADES_TABLE` | no | `actividades` |
| `SUPABASE_ZONAS_TABLE` | no | `Maestro_Equipos_Zonas` |

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

Todo usuario autenticado lee todos los registros. La **zona es un filtro de
visualización**, sobre el selector de vista, no un permiso: sirve para que cada persona
se concentre en su área, no para impedirle ver las demás.

La restricción por usuario quedó preparada pero no activa. Está documentada en
la sección 8 de `supabase_politicas_lectura.sql`: una política que cruza el
equipo del registro contra `Maestro_Equipos_Zonas` y lo compara con el arreglo
`zonas` del `app_metadata` del usuario, leído del token. Se dejó fuera porque
falla cerrado — quien no tenga zona asignada no vería ninguna fila —, y eso
bloquearía a los usuarios aún sin mapear. Para activarla hay que asignar primero
la zona a todos (`supabase_asignar_zonas.sql`) y después cambiar la política.

---

## Caché

### Datos (`cache_data`)

| Función | TTL | Clave de caché |
|---|---|---|
| `cargar_registros` | 300 s | `(tabla, usuario_id)` |
| `cargar_actividades` | 3600 s | `(tabla, códigos presentes, usuario_id)` |
| `cargar_zonas` | 3600 s | `(tabla, usuario_id)` |

El `usuario_id` forma parte de la clave para que un usuario no reciba datos
traídos con las credenciales de otro. El **token no entra en la clave**: se pasa
como `_token` y Streamlit ignora los argumentos que empiezan con guion bajo. Es
deliberado — el token rota cada hora, y si formara parte de la clave invalidaría
la caché en cada rotación.

`cargar_actividades` recibe la tupla de códigos que de verdad aparecen en los
registros y los pide con `.in_()`. Traer la maestra completa son ~1600 filas en
dos páginas (~800 ms); así es una sola petición (~190 ms). La caché se renueva
sólo cuando aparece un código nuevo.

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

- **Una vista a la vez.** Con `st.tabs`, Streamlit construye y envía las tres
  vistas en cada interacción. Con el selector se dibuja sólo la activa, así que
  un cambio de filtro manda un tercio de lo que mandaba.
- **Los filtros de Zona y Hacienda van arriba del selector**, no dentro de una
  vista. Si estuvieran dentro, Streamlit descartaría su valor al cambiar de
  vista, porque el widget deja de renderizarse.

### Consultas

`COLUMNAS_CONSULTA` limita el `SELECT` a los campos que se usan: las columnas
internas (`id_registro`, `fecha_original`, `telegram_chat_id`,
`telegram_usuario`) no viajan por la red.

`_descargar()` pagina de 1000 en 1000 hasta agotar la tabla, así que no depende
del límite por defecto de PostgREST.

El botón **Actualizar datos** llama a `st.cache_data.clear()`.

---

## Modelo de datos

### Columnas que llegan de la base

| Columna | Tipo tras normalizar | Nota |
|---|---|---|
| `ficha`, `nombre_completo` | texto | Identifican al operador. |
| `ip_equipo`, `implemento_1`, `implemento_2` | texto | Códigos de equipo. |
| `tiene_implemento`, `tiene_observaciones` | booleano | Llegan como `"true"`/`"false"`. |
| `cantidad_implementos` | numérico | |
| `fecha` | datetime | Se parsea `%d/%m/%Y` y, si falla, en modo mixto con `dayfirst`. |
| `hda`, `ste`, `labor` | **texto** | Son códigos, no números. Ver *Gráficos*. |
| `hora_inicio`, `hora_final` | texto `HH:MM` | |
| `horometro_inicio`, `horometro_final`, `horometro_diferencia` | numérico | |
| `horas_trabajadas`, `area_trabajada` | numérico | Acepta coma o punto decimal. |
| `observaciones` | texto | |
| `registrado_en` | datetime | Llega en UTC con precisión variable (`format="ISO8601"`); se resta 5 h fijas para hora de Colombia. Se muestra como *Fecha digitación*. Ver *Fechas*. |

Las columnas internas de la tabla (`id_registro`, `fecha_original`,
`telegram_chat_id`, `telegram_usuario`) se cargan pero nunca se muestran: la
lista `ORDEN_COLUMNAS` define qué se ve y en qué orden.

### Columnas derivadas

| Columna | Origen |
|---|---|
| `horas_calendario` | `hora_final − hora_inicio` en horas decimales; suma 24 si cruza medianoche. Es la referencia para contrastar `horas_trabajadas`. |
| `clave` | Concatenación de `ficha`, `fecha`, `hora_inicio` y `labor`. Identificador estable de una fila. |
| `actividad` | Nombre de la labor, resuelto contra la maestra. |
| `zona`, `nombre_equipo` | Zona y descripción del equipo, resueltas contra `Maestro_Equipos_Zonas`. |
| `tiene_alerta`, `detalle_alertas` | Resumen de `marcar_alertas()`. |

### Cruce con la maestra de equipos

`ip_equipo` se cruza contra `Maestro_Equipos_Zonas` (`Cod_equipo` → `Zona`,
`D_equipo`) en `agregar_zonas()`. Dos detalles del cruce:

- La llave se normaliza a mayúsculas y sin espacios en ambos lados. Los códigos
  provienen de capturas distintas y no siempre coinciden en caja.
- El cruce es por la izquierda: un equipo ausente de la maestra **conserva su
  registro** y queda en `Sin zona`, con el código como descripción. Nunca se
  pierde una fila por un faltante del maestro.

El nombre de la tabla lleva mayúsculas y PostgREST es sensible a ellas, igual
que sus columnas (`Cod_equipo`, `D_equipo`, `Zona`).

### Traducción de labores

`labor` es el **código** de la actividad (p. ej. `3080115139`), no su nombre.
`agregar_actividades()` lo cruza contra `actividades` usando las columnas
`codigo` → `nome`.

Degradación deliberada: si la maestra no se puede leer o el código no existe,
la columna `actividad` conserva el código en lugar de quedar vacía, y la app
avisa. Nunca se pierde la fila por un problema de la maestra.

---

## Validaciones

`marcar_alertas()` devuelve un DataFrame booleano con una columna por regla.
`REGLAS` guarda la descripción que se muestra en la interfaz.

| Regla | Condición |
|---|---|
| Horómetro sin avance | `Δ ≤ 0` y `horas > 0` |
| Horómetro final menor al inicial | `horometro_final < horometro_inicio` |
| Avance de horómetro mayor a las horas | `Δ > horas + 1` |
| Horas no coinciden con el horario | `abs(horas − horas_calendario) > 0.5` |
| Sin área registrada | `area_trabajada ≤ 0` |

Las tolerancias (`+1` hora y `0.5` h) absorben redondeos de captura. La tercera
regla es la que detecta el error habitual de anotar kilometraje en el campo del
horómetro.

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
`FORMATO_FECHA`. Aplica igual a `fecha` y a `registrado_en` (*Fecha digitación*).

Tres detalles que no son evidentes:

- **Las tablas reciben fechas, no texto.** `para_mostrar()` no convierte a
  cadena a propósito: si lo hiciera, al hacer clic en el encabezado la tabla
  ordenaría alfabéticamente y mandaría el día antes que el mes (`01/12/26` antes
  de `14/09/26`). El formato lo aplica `CONFIG_COLUMNAS` con
  `st.column_config.DateColumn`, de modo que el orden sigue el valor real.
- **Las exportaciones sí van formateadas.** `para_exportar()` convierte las
  fechas a texto justo antes de generar el Excel o el CSV. Sin eso el CSV
  saldría en formato ISO.
- **`registrado_en` se parsea con `format="ISO8601"`.** Postgres recorta los
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
| Agregar un filtro global | `_opciones()` + `_seleccion()` arriba del selector de vista, y el bucle que aplica los filtros |
| Agregar un filtro secundario | `multiselector()` en la barra lateral |
| Cambiar el formato de las fechas | `FORMATO_FECHA` y `CONFIG_COLUMNAS` |
| Cambiar colores | Las constantes de paleta y `.streamlit/config.toml` |
| Cambiar el formato de exportación | `a_excel()` / `a_csv()` |

---

## Limitaciones conocidas

- **Zona horaria fija.** `registrado_en` se ajusta restando 5 horas. Es correcto
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
