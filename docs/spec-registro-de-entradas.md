# Spec: registro de sesiones de juego humano

## Problema

El bot pulsa con un retardo aleatorio uniforme de 0,2-0,35 s (ADR 0005), y una persona no juega así: su ritmo cambia según la tarea, tiene rachas de prisa y de cansancio, y comete errores. No hay datos reales de cómo juega una persona que permitan ajustar ese ritmo, ni imágenes variadas del juego (menús, popups, eventos, edificios en distintos niveles) con las que probar o entrenar un detector visual.

Hoy, para conseguir esos datos, el usuario tendría que grabar el vídeo por un lado y anotar sus pulsaciones por otro, y después sincronizarlos a mano buscando fotogramas. Es lento, impreciso y desanima a grabar muchas horas, que es justo lo que hace falta.

## Solución

El usuario juega con normalidad en su propia cuenta mientras graba el vídeo con OBS. Un registro de entradas, que corre a la vez en segundo plano, anota cada pulsación sobre la ventana del juego junto con el segundo exacto del vídeo en que ocurrió. Al terminar, cada **sesión de grabación** deja dos ficheros que encajan entre sí: el vídeo y el registro de entradas.

Con eso se puede, sin trabajo manual de sincronización:

- Medir el ritmo humano real (tiempo entre que algo aparece y se pulsa, duración de las pulsaciones, pausas entre acciones) para ajustar la Reacción humana.
- Extraer fotogramas con contexto de lo que el usuario estaba haciendo, para construir un conjunto de imágenes variado que luego se etiquete.

## User Stories

1. COMO jugador que graba sesiones, QUIERO empezar y parar el registro con una tecla global, PARA no tener que alternar entre ventanas mientras juego.
2. COMO jugador, QUIERO que el registro solo anote lo que ocurre sobre la ventana del juego cuando tiene el foco, PARA que no se mezcle con lo que haga en otras aplicaciones.
3. COMO jugador, QUIERO que no se registre el teclado en general, PARA que contraseñas o mensajes que escriba no queden guardados.
4. COMO jugador, QUIERO que cada pulsación y cada suelta de botón se anoten por separado, PARA poder medir cuánto dura una pulsación y reconstruir los arrastres.
5. COMO jugador, QUIERO que la rueda del ratón quede registrada, PARA conservar mi forma de hacer zoom.
6. COMO jugador, QUIERO que el movimiento del ratón se anote de forma muestreada, PARA conservar mi trayectoria de aproximación sin generar ficheros enormes.
7. COMO jugador, QUIERO que mientras arrastro el muestreo del movimiento sea más denso, PARA reconstruir bien los gestos largos como mover la vista o colocar tropas.
8. COMO jugador, QUIERO que la suelta de un botón se registre aunque ocurra fuera de la ventana, PARA no dejar pulsaciones abiertas en los datos.
9. COMO jugador, QUIERO que las posiciones queden relativas al área de juego de la ventana y normalizadas entre 0 y 1, PARA que los datos valgan aunque cambie el tamaño o la posición de la ventana.
10. COMO jugador, QUIERO que el registro anote el tamaño de la ventana al empezar y cada vez que cambie, PARA saber con qué resolución se grabó cada tramo.
11. COMO jugador, QUIERO poner una marca con una tecla durante la partida (por ejemplo, al empezar un ataque), PARA localizar después los momentos importantes sin ver horas de vídeo.
12. COMO jugador, QUIERO que cada evento lleve el segundo del vídeo de OBS en que ocurrió, PARA saltar directamente al fotograma de cualquier pulsación.
13. COMO jugador, QUIERO que ese tiempo de vídeo salga solo, sin hacer clics de referencia ni calcular desfases, PARA poder grabar muchas sesiones sin trabajo manual.
14. COMO jugador, QUIERO que el registro sepa cuándo OBS empieza y para de grabar, PARA que el tiempo de vídeo empiece en cero con la grabación.
15. COMO jugador, QUIERO que, si arranco el registro antes que la grabación de OBS, el tiempo de vídeo funcione igualmente desde que OBS empiece, PARA no depender del orden en que pulse las teclas.
16. COMO jugador, QUIERO que si pauso la grabación de OBS los eventos dejen de llevar tiempo de vídeo, PARA no guardar tiempos falsos de un vídeo cuya duración ya no coincide.
17. COMO jugador, QUIERO un aviso claro si OBS ya estaba grabando cuando arranco el registro, PARA saber que debo reiniciar la grabación si quiero tiempo de vídeo.
18. COMO jugador, QUIERO un mensaje claro si no encuentro OBS, o su servidor WebSocket está apagado, o pide una contraseña, PARA arreglarlo sin leer código.
19. COMO jugador, QUIERO poder usar el registro sin OBS, PARA grabar solo mis pulsaciones cuando no me interese el vídeo.
20. COMO jugador, QUIERO que sin tiempo de vídeo siga existiendo una forma de sincronizar a mano (un primer clic de referencia marcado en el registro), PARA no perder una sesión si falla la conexión con OBS.
21. COMO jugador, QUIERO que cada línea del registro se escriba en cuanto ocurre el evento, PARA no perder lo anterior si el programa se cierra de golpe.
22. COMO jugador, QUIERO que cada evento lleve tanto el tiempo desde que empezó el registro como la hora real, PARA poder medir intervalos con precisión y situar la sesión en el día.
23. COMO jugador, QUIERO que el registro empiece con el nivel de Ayuntamiento de la cuenta, PARA saber de qué fase del juego es cada sesión.
24. COMO jugador, QUIERO que cada sesión se guarde con un nombre que incluya la fecha, la hora y el nivel de Ayuntamiento, PARA separar entrenamiento y validación por sesión y no por imagen suelta.
25. COMO jugador, QUIERO que los ficheros de sesión se guarden fuera del control de versiones, PARA que ningún vídeo o registro con datos personales llegue al repositorio.
26. COMO desarrollador, QUIERO que el registro avise de forma visible cuando está grabando, PARA no dejarlo activo sin darme cuenta ni grabar horas de más.
27. COMO desarrollador, QUIERO poder analizar un registro y sacar las distribuciones de tiempos de reacción por tipo de acción, PARA sustituir el retardo uniforme de la Reacción humana por uno basado en datos reales.
28. COMO desarrollador, QUIERO poder extraer fotogramas del vídeo en los momentos de las pulsaciones y las marcas, PARA preparar el conjunto de imágenes que se etiquetará.
29. COMO desarrollador, QUIERO que las grabaciones usen siempre la misma resolución y proporción de ventana, PARA que el conjunto de imágenes sea homogéneo.
30. COMO desarrollador, QUIERO comprobar la precisión del tiempo de vídeo contra un clic visible en el vídeo, PARA saber si el error es menor de un fotograma antes de fiarme de los datos.
31. COMO desarrollador, QUIERO que el cálculo del tiempo de vídeo y la interpretación de los avisos de OBS se puedan probar sin OBS ni tiempo real, PARA que los tests sean rápidos y deterministas.

## Decisiones de implementación

- **Dos piezas separadas**: un registro de entradas que no sabe nada de Windows ni de OBS (recibe eventos de ratón y avisos ya interpretados), y una capa fina que conecta ese registro con el sistema real (ratón global, foco y geometría de la ventana, teclas de control, WebSocket de OBS). Esto sigue el patrón del proyecto: lógica pura inyectable y pegamento delgado.
- **Dependencias inyectadas**: reloj monotónico, hora real, estado de la ventana y destino de los eventos se reciben desde fuera, como en el resto del proyecto, para probar sin tiempo real ni pantalla.
- **Eventos registrados**: inicio y fin del registro, pulsar y soltar (con botón y posición), movimiento muestreado, rueda, cambio de tamaño de la ventana, marca, y los avisos de inicio y fin de grabación de OBS. Cada evento lleva tiempo desde el inicio del registro y hora real, y, si lo hay, tiempo de vídeo.
- **Formato**: un fichero de texto con un evento por línea, escrito y volcado al instante. Legible con cualquier herramienta y resistente a cortes.
- **Posiciones**: normalizadas de 0 a 1 respecto al área cliente de la ventana del juego, sin marcos, con el tamaño real registrado aparte. Los clics fuera de la ventana o sin foco se ignoran; la suelta tras una pulsación válida se registra siempre.
- **Muestreo del movimiento**: intervalo más corto con un botón pulsado que sin él. Con el botón libre solo se anota si el puntero está dentro de la ventana con foco.
- **Teclado**: solo las dos teclas de control (empezar/parar y marca). Nada más se escucha ni se guarda.
- **Primer clic de referencia**: el primer clic dentro de la ventana tras empezar el registro queda marcado como referencia, como respaldo de la sincronización manual.
- **Sincronización con el vídeo**: el registro se conecta al servidor WebSocket local de OBS, suscrito solo a los eventos de grabación. Al llegar el aviso de que la grabación ha empezado, hace una ráfaga corta de consultas de la duración de grabación (unas 10, menos de un segundo) y, con las de menor ida y vuelta, calcula la distancia entre su reloj local y el vídeo de esa grabación. El tiempo de vídeo de cada evento es su hora local menos esa distancia. No se usa la hora del aviso como segundo cero (ver Notas adicionales) y no hay consultas periódicas posteriores. La duración que da OBS va una constante por detrás del archivo (0,284 s en el equipo medido, ver Notas adicionales), que se suma al calcular la distancia.
- **Estados de OBS**: empezar a grabar fija el cero; parar, pausar o reanudar lo borran, porque tras una pausa no se puede saber cuánto duró. Mientras no haya cero, los eventos salen sin tiempo de vídeo.
- **Arranque con OBS ya grabando**: se consulta una vez el estado de grabación; si ya graba, se avisa al usuario de que no habrá tiempo de vídeo hasta una nueva grabación.
- **Orden de arranque**: el registro debe estar escuchando antes de que empiece la grabación de OBS; da igual si se ha pulsado ya la tecla de empezar.
- **Autenticación de OBS**: la contraseña, si existe, se toma de un argumento o de una variable de entorno, y se usa el desafío estándar del protocolo v5.
- **Uso sin OBS**: la conexión con OBS es opcional; sin ella, los eventos simplemente no llevan tiempo de vídeo.
- **Concurrencia**: ratón, teclas de control y OBS llegan por hilos distintos; la escritura al fichero está protegida para no mezclar líneas.
- **Dependencias externas**: se declaran en un extra opcional del proyecto, para no engordar la instalación normal ni el CI.
- **Resolución y proporción**: la ventana del juego debe mantenerse en una proporción 16:9 fija y el lienzo de OBS debe coincidir con el tamaño real de la ventana, sin reescalar.
- **Datos personales**: los ficheros de sesión van a la carpeta de capturas en bruto, ignorada por git; cualquier fotograma que pase a un conjunto de imágenes compartido sigue la regla de anonimización del repositorio.
- **Relación con el ADR 0005**: este registro es la fuente de datos para sustituir el retardo uniforme de la Reacción humana por distribuciones reales; el ADR se actualizará cuando haya datos suficientes.

## Decisiones de testing

- Un buen test comprueba solo comportamiento externo: dado un flujo de eventos de ratón, avisos de OBS y estados de ventana, qué eventos salen y con qué contenido. No comprueba cómo se guardan los intervalos ni qué estructuras internas se usan.
- **Seam 1, el registro de entradas**: se prueba con ratón, ventana, reloj y avisos de OBS simulados (como las tareas se prueban con `FakeDevice` y `FakeClock`). Casos: ignorar eventos sin registro activo; normalizar posiciones; ignorar clics fuera de la ventana o sin foco; suelta fuera de la ventana; muestreo del movimiento más denso al arrastrar; un solo evento de tamaño por cambio; marca solo con registro activo; clic de referencia solo el primero; tiempo de vídeo desde el aviso de inicio, antes o después de empezar el registro; sin tiempo de vídeo tras parar o pausar; sin OBS nunca hay tiempo de vídeo.
- **Seam 2, el protocolo de OBS**: se prueba con una conexión falsa que devuelve mensajes. Casos: identificación con y sin contraseña; suscripción solo a eventos de grabación; consulta del estado ignorando eventos que lleguen antes de la respuesta; traducción de cada estado de grabación a empezar o dejar de contar; ignorar eventos que no sean de grabación; fin del escuchar cuando la conexión se cierra.
- Lo que depende del sistema real (ratón global, foco y geometría de la ventana, teclas de control, conexión real con OBS) no se prueba de forma automática; se comprueba a mano en una sesión corta, y los criterios están en las Notas adicionales.
- Referencias: los tests de las tareas con dispositivo y reloj falsos, y el test del anonimizador que lanza una herramienta con la entrada estándar cerrada.

## Fuera de scope

- Entrenar el detector visual, la herramienta de etiquetado y la evaluación del modelo.
- El análisis estadístico de los registros y su integración en la Reacción humana (es la siguiente fase y usará esta como fuente).
- Imitar las decisiones del jugador (qué atacar, qué mejorar): la política sigue siendo determinista y definida por la spec del MVP.
- El planificador de sesiones del bot (franjas horarias, sesiones cortas, cierre de sesión).
- Sustituir OBS por otro grabador integrado, y soportar sistemas distintos de Windows.
- Garantizar que el bot sea indetectable: este registro solo aporta datos; no se conocen las reglas del sistema anti-bot del juego.
- Grabar audio.

## Notas adicionales

- **Medido en el equipo del usuario** (OBS 32.0.4, obs-websocket 5.6.3, 60 fps, cinco grabaciones cortas): la ida y vuelta de una petición al WebSocket local es de menos de 1 ms (mediana 0,33 ms), pero el aviso de inicio llega unos 286 ms antes del primer fotograma (entre 276 y 290 ms). Por eso el aviso no vale como segundo cero.
- **La duración de OBS cuenta fotogramas, pero va por detrás del archivo**: la duración de grabación que da OBS coincide con los fotogramas entregados dividido entre los fps (diferencia menor de 1 ms), por lo que va redondeada a fotogramas y su error máximo es de un fotograma (16,7 ms a 60 fps). El archivo resultante tiene un fotograma menos que el contador de OBS, lo que deja una incertidumbre añadida de un fotograma.
- **Corrección medida con destellos visibles** (OBS 32.0.4, obs-websocket 5.6.3, 60 fps): el tiempo del archivo es la duración de OBS más 0,284 s, idéntico en 5 destellos y con captura de pantalla DXGI y WGC (`VIDEO_LAG` en `runtime/obs_link.py`). Sin esta corrección todos los tiempos de vídeo salían ~0,27 s por debajo. Es una constante de OBS, no de la captura, pero no se sabe si vale en otros equipos, versiones o codificadores: repetir la medición con `tools/check_video_time.py` al cambiar de equipo o de versión.
- **Por validar**: las medidas se hicieron con OBS casi vacío; falta repetirlas con el juego en pantalla y la GPU cargada.
- **Descartado**: usar el aviso con una constante fija (depende de cómo arranque OBS en cada equipo) y consultar la duración de forma periódica (los relojes no derivan de forma apreciable).
- **Riesgo de entrada «raw»**: algunos juegos usan entrada directa y los enganches globales de ratón pueden no ver los movimientos. Los clics casi seguro se registran; hay que comprobar con el juego real que también salen los movimientos.
- **Alternativa de herramienta externa**: existe un grabador de código abierto para Windows (ocap) que guarda vídeo y entradas ya alineados. Se valorará comparándolo con esta solución en una sesión corta; si resulta mejor, esta spec seguirá valiendo como descripción de lo que se necesita.
- **Vocabulario nuevo**: *sesión de grabación* (los dos ficheros de una grabación), *registro de entradas*, *tiempo de vídeo* (segundo del vídeo en que ocurre un evento), *marca* y *referencia*. Conviene añadirlos al glosario al aprobar la spec. Ojo: el glosario desaconseja «sesión» para las pasadas de muros; aquí no hay confusión posible, pero debe quedar anotado.
