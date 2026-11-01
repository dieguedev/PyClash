# Spec: MVP hasta Ayuntamiento 9 maxeado

## Problema

Como jugador de Clash of Clans en Google Play Games para Windows, subir una cuenta desde el Ayuntamiento 1 hasta el 9 exige entrar muchas veces al día: quitar obstáculos, recoger recursos, elegir qué mejorar con los constructores libres, mejorar muros, investigar en el laboratorio, atacar para conseguir recursos y volver a entrar cuando acaba una obra. Es repetitivo, depende de que yo esté delante y hace fácil gastar mal los recursos (por ejemplo, dejar el oro por debajo de lo necesario para buscar partida). El desarrollo del bot, además, se había ido de alcance (Builder Base, multi-cuenta, ADB, notificaciones múltiples...) sin que ninguna versión llevara una cuenta de principio a fin.

## Solución

Un bot de escritorio que, con el juego abierto en inglés, lleva una cuenta hasta el Ayuntamiento 9 completamente maxeado y entonces se detiene, avisa por Telegram y sale. Funciona en dos modos que el jugador elige:

- **Maxer**: maxea todo lo que permite el Ayuntamiento actual y después sube al siguiente. En TH9 no sube al TH10.
- **Rusher**: construye todo lo nuevo y solo mejora lo que hace falta para atacar y almacenar mejor (almacenes, campamentos, laboratorio, castillo del clan, Hero Hall, cuartel, calderos de hechizos y las tropas de la estrategia activa). Deja defensas, minas, recolectores, muros, trampas y héroes sin mejorar hasta llegar a TH9, y ahí maxea todo lo que queda.

En cada pasada el bot quita obstáculos, recoge recursos, mejora lo que toca según el modo, ataca con el ejército que el jugador dejó preparado hasta llenar los almacenes y entra en idle esperando a la obra que acabe antes. Cada mejora iniciada se avisa por Telegram con una captura de la aldea; un fallo que detiene al bot deja un registro y una captura y también se avisa. Una interfaz mínima permite arrancarlo, ver el registro y ajustar lo necesario.

## User Stories

1. COMO jugador, QUIERO elegir entre modo Maxer y modo Rusher, PARA decidir si progreso con la cuenta completa o priorizando el farmeo.
2. COMO jugador en modo Maxer, QUIERO que el bot mejore todo lo del Ayuntamiento actual antes de subir el Ayuntamiento, PARA no dejar nada atrasado.
3. COMO jugador en modo Maxer, QUIERO que la tarjeta del Ayuntamiento no se pulse mientras queden otras mejoras del nivel, aunque salga la primera por coste, PARA no subir de nivel con la cuenta a medias.
4. COMO jugador, QUIERO elegir si el bot empieza por el coste máximo o por el mínimo, PARA mejorar primero lo más caro o lo más barato según mi estrategia.
5. COMO jugador, QUIERO que el bot use el filtro y el orden del propio juego, PARA que localice edificios con rapidez y de forma fiable.
6. COMO jugador en modo Rusher, QUIERO que el bot construya todos los edificios nuevos disponibles, PARA tener todo desbloqueado cuanto antes.
7. COMO jugador, QUIERO que el bot compre la Builder's Hut con gemas, activada por defecto y desactivable en Ajustes, PARA tener un constructor más y acelerar mucho el progreso.
8. COMO jugador en modo Rusher, QUIERO que se mejoren los almacenes, PARA tener más capacidad y no perder botín.
9. COMO jugador en modo Rusher, QUIERO que se mejoren los campamentos, PARA llevar más tropas a los ataques.
10. COMO jugador en modo Rusher, QUIERO que se mejoren el laboratorio, el castillo del clan y el Hero Hall, PARA investigar mejor, recibir más tropas y tener el edificio de héroes listo.
11. COMO jugador en modo Rusher, QUIERO que se mejoren el cuartel y los calderos de hechizos, PARA desbloquear tropas y hechizos nuevos.
12. COMO jugador en modo Rusher, QUIERO que no se mejoren defensas, minas, recolectores, muros, trampas ni héroes hasta TH9, PARA gastar solo en lo que acelera el farmeo.
13. COMO jugador en modo Rusher, QUIERO que al llegar a TH9 el bot maxee todo lo que dejó atrás, PARA terminar con la cuenta completa.
14. COMO jugador, QUIERO que el bot nunca gaste gemas ni abra la tienda salvo en la compra de la Builder's Hut, PARA proteger un recurso que no se recupera.
15. COMO jugador, QUIERO que el bot no confirme una mejora cuyo coste sale en rojo, PARA que nunca se intente una compra imposible.
16. COMO jugador, QUIERO que cada mejora se verifique (el constructor pasa a ocupado) antes de darla por hecha, PARA no registrar mejoras que no empezaron.
17. COMO jugador, QUIERO que el bot exija ver el nombre del edificio en pantalla antes de confirmar, PARA que no compre otra cosa por un clic equivocado.
18. COMO jugador, QUIERO que ninguna mejora ni construcción deje el oro por debajo del mínimo para buscar partida, PARA que el farmeo nunca se bloquee por «You need more Gold».
19. COMO jugador, QUIERO que el bot mantenga un constructor libre para los muros, PARA que los muros avancen mientras el resto se mejora.
20. COMO jugador, QUIERO que los muros se mejoren por lotes, PARA no pulsar uno a uno.
21. COMO jugador, QUIERO que el bot elija oro o elixir para los muros según el nivel del muro, PARA no gastar elixir donde solo vale oro.
22. COMO jugador, QUIERO que el bot dé la cuenta por terminada cuando en TH9 solo queda la mejora del Ayuntamiento y los muros están al máximo, PARA que se detenga cuando no hay nada más que maxear.
23. COMO jugador, QUIERO que el bot nunca suba el Ayuntamiento de TH9 a TH10, PARA decidir yo cuándo subir.
24. COMO jugador, QUIERO que, si el bot no puede saber con seguridad en qué nivel está el Ayuntamiento, no suba nada, PARA que la duda nunca termine en una subida irreversible.
25. COMO jugador, QUIERO que al terminar el TH9 maxeado el bot se detenga, me avise por Telegram y salga, PARA enterarme sin estar delante y que no siga gastando.
26. COMO jugador, QUIERO que al empezar cada sesión se quiten los obstáculos de la aldea antes que nada, PARA liberar espacio y conseguir gemas.
27. COMO jugador, QUIERO que quitar obstáculos no ocupe un constructor, PARA que no compita con las mejoras.
28. COMO jugador, QUIERO que se quiten todos los obstáculos visibles siempre que el gasto no baje el oro de la reserva de búsqueda, PARA no quedarme sin poder atacar.
29. COMO jugador, QUIERO que los obstáculos se reconozcan con imágenes de los obstáculos comunes, PARA que el bot los encuentre aunque estén en cualquier parte de la aldea.
30. COMO jugador, QUIERO que un obstáculo no reconocido (por ejemplo de evento) se ignore y no se pulse a ciegas, PARA no tocar algo desconocido.
31. COMO jugador, QUIERO que el bot recoja los recursos de minas y recolectores, PARA no dejar producción sin cobrar.
32. COMO jugador, QUIERO que el laboratorio se use cuando esté libre, PARA que no pase tiempo parado.
33. COMO jugador en modo Rusher, QUIERO que el laboratorio mejore solo las tropas de la estrategia de ataque activa, PARA que mis tropas de farmeo sean las más fuertes.
34. COMO jugador en modo Maxer, QUIERO que el laboratorio investigue todas las tropas y hechizos, PARA maxear la cuenta completa.
35. COMO jugador, QUIERO marcar y desmarcar tropas con botones que muestran su imagen, PARA preparar de antemano las tropas de un ejército futuro.
36. COMO jugador, QUIERO que el bot compruebe que el laboratorio pasó a ocupado tras investigar, PARA no registrar una investigación que no empezó.
37. COMO jugador, QUIERO que el bot ataque con el ejército que dejé montado, PARA no tener que entrenarlo desde el bot.
38. COMO jugador, QUIERO guardar una plantilla de ejército en el juego y que el bot la use, PARA reponer tropas sin que el bot lea colas ni cantidades.
39. COMO jugador, QUIERO que la estrategia BArch despliegue primero bárbaros en círculos y después arqueras, PARA limpiar los exteriores con las arqueras detrás de los bárbaros.
40. COMO jugador en un Ayuntamiento 1, QUIERO que el bot ataque solo con bárbaros, PARA usar lo único disponible.
41. COMO jugador, QUIERO que el bot ataque cuando no tiene nada más que hacer, PARA llenar los almacenes.
42. COMO jugador, QUIERO que el bot deje de atacar cuando los almacenes están llenos y entre en idle, PARA no gastar tropas ni tiempo inútilmente.
43. COMO jugador, QUIERO que el bot respete un mínimo de botín por recurso al elegir aldea, PARA no gastar un ataque en una aldea pobre.
44. COMO jugador, QUIERO que el bot no despliegue sobre zona roja, PARA que las tropas no se pierdan fuera del área permitida.
45. COMO jugador, QUIERO que cuando falte oro para buscar partida el bot recoja las minas y ataque las siguientes aldeas sin mínimo de botín, PARA recuperar fondos sin intervenir.
46. COMO jugador, QUIERO que el bot cierre el diálogo «You need more Gold» sin pulsar el botón de gemas, PARA no gastar gemas por error.
47. COMO jugador, QUIERO que cuando no haya nada que hacer el bot espere hasta que termine la obra más corta, PARA no dejar un constructor libre esperando.
48. COMO jugador, QUIERO que el bot lea los temporizadores de las obras, PARA calcular cuánto esperar.
49. COMO jugador, QUIERO que el bot no se quede a ciegas ante una ventana de inactividad del juego, PARA que la sesión no se cierre sola.
50. COMO jugador, QUIERO que cada mejora iniciada me llegue por Telegram, PARA saber qué ha hecho el bot sin mirar.
51. COMO jugador, QUIERO que ese aviso incluya una captura de la aldea con el zoom al máximo hacia fuera, PARA ver que la mejora realmente empezó.
52. COMO jugador, QUIERO que antes de la captura el bot suelte la selección pulsando terreno vacío y haga zoom-out, PARA que la foto no salga con el edificio ampliado ni un panel abierto.
53. COMO jugador, QUIERO que el aviso solo se envíe una vez confirmada y verificada la mejora, PARA no recibir avisos de mejoras que fallaron.
54. COMO jugador, QUIERO que no hace falta un aviso especial al mejorar el Ayuntamiento, PARA que sea una mejora más.
55. COMO jugador, QUIERO que un fallo que detiene al bot cree un registro de error con fecha y hora, PARA investigar qué pasó.
56. COMO jugador, QUIERO que la captura del error se guarde en una carpeta de errores y el registro la referencie, PARA ver la pantalla en la que falló.
57. COMO jugador, QUIERO que un error que detiene al bot se avise por Telegram con el mensaje y esa captura, PARA enterarme sin estar delante.
58. COMO jugador, QUIERO que Telegram caído o lento no detenga ni retrase al bot, PARA que los avisos nunca bloqueen el trabajo.
59. COMO jugador, QUIERO que el token y el chat de Telegram se guarden como secreto, PARA que no aparezcan en ficheros legibles ni en registros.
60. COMO jugador, QUIERO pegar el token del bot y el identificador del chat en Ajustes, PARA configurar los avisos sin asistente.
61. COMO jugador, QUIERO arrancar y parar el bot desde una ventana, PARA controlarlo sin tocar ficheros.
62. COMO jugador, QUIERO ver el estado en vivo (qué tarea hace, constructores libres, recursos), PARA saber en qué punto está.
63. COMO jugador, QUIERO ver el registro de actividad con filtro por nivel, PARA revisar lo ocurrido.
64. COMO jugador, QUIERO que la interfaz no se congele mientras el bot trabaja, PARA poder pararlo cuando quiera.
65. COMO jugador, QUIERO que parar el bot lo detenga de forma cooperativa en el siguiente punto seguro, PARA no dejar una compra a medias.
66. COMO jugador, QUIERO que ante una pantalla desconocida el bot no pulse nada a ciegas y se recupere solo con una salida segura (cerrar o terreno vacío), PARA no confirmar diálogos que no entiende.
67. COMO jugador, QUIERO que, si no hay salida segura, el bot se detenga, guarde la captura y avise, PARA que nunca actúe a ciegas.
68. COMO jugador, QUIERO que el juego esté en inglés y que el bot se detenga si no lo está, PARA que no lea textos que no entiende.
69. COMO jugador, QUIERO que los ajustes de orden, reserva, tope, prioridades y exclusiones se apliquen en vivo y los de modo y ventana al reiniciar, PARA no reiniciar por cambios menores.
70. COMO jugador, QUIERO que un valor roto en la configuración se avise y use su valor por defecto solo en ese campo, PARA que un error no pierda toda la configuración.
71. COMO jugador, QUIERO que solo pueda haber una copia del programa por carpeta de datos, PARA no tener dos bots peleando por la misma ventana.
72. COMO mantenedor, QUIERO que ninguna captura de prueba con datos personales entre en el repositorio, PARA no exponer datos.
73. COMO jugador, QUIERO que el bot compre la Builder's Hut solo si el nombre es exactamente «Builder's Hut» y el precio es 500, 1 000 o 2 000 gemas, PARA que ninguna otra carta de gemas se compre por error.
74. COMO jugador, QUIERO que el bot no pulse la compra si no me alcanzan las gemas y cierre cualquier diálogo sin comprar gemas, PARA no gastar de más.
75. COMO jugador, QUIERO que la compra de la Builder's Hut se verifique comprobando que bajaron las gemas, PARA no registrar una compra que no ocurrió.
76. COMO jugador, QUIERO poder desactivar la compra de la Builder's Hut en Ajustes, PARA decidir yo si gasto gemas.
77. COMO jugador, QUIERO un único ejecutable de Windows con el OCR y sus modelos incluidos, PARA usar el bot sin instalar Python ni nada más.
78. COMO jugador, QUIERO que el ejecutable arranque sin conexión y muestre la ventana enseguida, PARA empezar a usarlo sin esperas ni descargas.
79. COMO mantenedor, QUIERO que el tamaño, el arranque y el consumo en reposo del ejecutable se midan en CI, PARA que un cambio que los empeore falle el build.

## Decisiones de implementación

- **Alcance**: llegar a TH9 maxeado y parar. Quedan fuera Builder Base, mascotas (no existen hasta TH14), multi-cuenta (post-MVP), backend ADB, modo Ciclo, diagnóstico, notificaciones distintas de Telegram, ataque con héroes y hechizos y asistentes. Entran la compra de la Builder's Hut con gemas y el empaquetado en un único ejecutable.
- **Builder's Hut**: es la única excepción a no gastar gemas. Va activada por defecto y el jugador puede desactivarla; no hay tope de gemas (el máximo posible es 3 500). Solo actúa por el nombre exacto «Builder's Hut» y con uno de los precios 500, 1 000 o 2 000, y verifica que las gemas bajaron. Cualquier otra carta con gemas se descarta. Se registra en una decisión de arquitectura.
- **Empaquetado**: un único ejecutable de Windows con el OCR y sus modelos incluidos, sin internet para arrancar. El tamaño, el arranque y el consumo en reposo se verifican en CI con límites fijados tras medir el primer build real.
- **Política de mejoras (reabre la política pura)**:
  - Maxer maxea el nivel actual y entonces ofrece el Ayuntamiento como candidato. Nunca lo ofrece mientras quede otra mejora válida, aunque el orden por coste lo coloque primero.
  - Rusher funciona con lista blanca: lo que no está en la lista se deja hasta TH9. Sube el Ayuntamiento en cuanto puede.
  - En TH9 ninguno de los dos modos ofrece la subida a TH10.
  - La reserva de constructor para muros pasa a estar activa por defecto en Maxer.
  - Un lote de muros necesita un constructor libre pero no lo ocupa.
- **Condición de parada**: la cuenta está en TH9, la única mejora pendiente es la subida del Ayuntamiento y los muros están al máximo. Si no se puede saber con seguridad el nivel del Ayuntamiento, el bot no sube nada y no da la cuenta por terminada.
- **Lectura del nivel**: el número de la insignia de una fila es el nivel al que se mejora, así que en TH9 la tarjeta del Ayuntamiento ofrece el 10. Los niveles de edificios se leen con plantillas reales del juego, devolviendo «no sé» ante la duda.
- **Filtros del juego**: además del orden por coste (ya existente), se aplican los filtros de categoría (Army, Defense, Resource, New) para localizar edificios. El estado de cada filtro se verifica antes de actuar y no se pulsa un filtro ya activo.
- **Laboratorio**: se identifica cada tropa por su carta en la barra lateral. En Rusher, la lista de tropas a investigar sale de la estrategia activa más un selector de botones con la imagen de cada tropa (marcable y desmarcable). En Maxer se investiga todo. Se verifica que el laboratorio pasa a ocupado.
- **Obstáculos**: tarea de prioridad máxima al empezar la sesión. Detección por imágenes de los obstáculos comunes a la escala fija de zoom máximo. No ocupa constructor y respeta la reserva de oro. Los obstáculos desconocidos se ignoran.
- **Zoom**: el zoom-out máximo (rueda del ratón) es el estado normal de la aldea. Se aplica al empezar la sesión, tras cada ataque y tras cada mejora. No se asume dónde queda la cámara, solo la escala.
- **Ataque**: el jugador monta el ejército en una plantilla guardada en el juego; el bot solo la pulsa tras verificar la pantalla. Estrategia BArch en TH2–TH9 y solo bárbaros en TH1. Ataca cuando no hay nada más que hacer, hasta llenar los almacenes, y después entra en idle. La composición 70 % bárbaros y 30 % arqueras queda anotada como idea futura.
- **Recuperar oro**: se mantiene la política actual (recoger minas y atacar las siguientes 3 aldeas sin mínimo de botín), con una nota para revisar su diseño.
- **Idle**: el orquestador lee el tiempo restante de las obras y espera hasta la que termine antes. La protección ante la ventana de inactividad ya existe.
- **Avisos por Telegram**:
  - Una interfaz de canal con implementación de Telegram. Cola con límite de frecuencia, tiempos de espera cortos y reintentos acotados, sin bloquear nunca al bot.
  - Eventos: mejora iniciada (con captura de la aldea), error que detiene al bot (con el texto del error y la captura) y TH9 maxeado.
  - El token y el chat se guardan como secreto en el almacén del sistema o en un fichero restringido, nunca en ficheros legibles, registros ni diagnósticos.
  - Las capturas pueden mostrar el nombre del jugador: decisión explícita del jugador, y solo van a su propio chat.
- **Registro de errores**: al detenerse por error, se crea un fichero de registro con fecha y hora en la carpeta de datos de ejecución (si no existe ya) y la captura en una carpeta de errores, referenciada desde el registro.
- **Interfaz**: Qt con Run, Logs y Ajustes (incluye los campos de Telegram). Las páginas Tareas y Mejoras con editor de prioridades quedan fuera.
- **Arquitectura**: se mantienen las capas actuales y la regla de no tener estado global ni tiempo real; todo recibe un contexto con reloj, aleatoriedad, eventos, parada y dispositivo inyectados. La parada es cooperativa.
- **Proceso**: se retira la regla de añadir un botón de verificación manual por cada issue. La issue de verificación manual se cierra como no planeada.

## Decisiones de testing

- **Qué es un buen test**: comprueba comportamiento observable desde fuera (qué toca el bot, qué decide la política, qué se envía), nunca detalles internos como el orden de llamadas privadas o la estructura de datos intermedia.
- **Costura principal**: una sesión completa del orquestador sobre un dispositivo falso con reloj falso y capturas reales como fixtures. Cubre el orden de tareas, la parada en TH9, el zoom y la captura tras cada mejora y los avisos.
- **Costura secundaria**: la política pura, probada como funciones sin dispositivo. Aquí viven las reglas de Maxer y Rusher, la lista blanca, el orden, la reserva de oro y la condición de parada.
- **Costura nueva, mínima**: el canal de avisos, con una implementación falsa que registra lo enviado (texto y captura), sin red.
- **Visión y lectura de pantallas**: se prueban dentro de las costuras anteriores con fixtures anonimizados; no se crea una batería aparte salvo lo que ya existe.
- **Referencias previas**: los tests existentes del orquestador, del flujo de mejora, de la política de mejoras y de la política de muros son el modelo a seguir.
- **Casos obligados**: duda al leer el nivel del Ayuntamiento (no se sube nada), coste en rojo, oro por debajo de la reserva, pantalla desconocida, Telegram caído, juego en otro idioma, obstáculo no reconocido y almacenes llenos.
- **Sin pruebas con la red ni el juego en CI**: la verificación contra el juego real se hace a mano por el jugador.

## Fuera de scope

- Builder Base (barco, farmeo, carrito de elixir, mejoras y su laboratorio).
- Mascotas y Casa de Mascotas.
- Multi-cuenta y cambio de cuenta.
- Backend ADB, BlueStacks y el modo Ciclo (abrir, trabajar, cerrar y esperar).
- Diagnóstico avanzado, grabador de fixtures y canario de interfaz.
- Notificaciones por Discord o Windows, y el asistente de Telegram.
- Ataque con héroes, hechizos, terremotos y tropas de clan.
- Entrenamiento automático del ejército y la composición 70/30 de BArch.
- Asistente de laboratorio y aprendiz de constructor.
- Páginas de Tareas y Mejoras con editor de prioridades.
- Obstáculos de evento.
- Otros idiomas del juego.

## Notas adicionales

- La captura de cada mejora se toma tras confirmar y cerrar el panel, soltar la selección pulsando terreno vacío y hacer zoom-out; la vista final puede variar según dónde estaba el edificio y no importa.
- La política de recuperar oro y el diseño de los obstáculos de evento quedan marcados como «a revisar».
- Falta una captura de la aldea al zoom máximo para calibrar la detección de obstáculos, y capturas de cada obstáculo común.
- Las issues del MVP en GitHub desarrollan esta spec, con sus bloqueos como relación nativa. Si una issue contradice a la spec, manda la spec.
