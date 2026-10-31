# Verificación manual de la recuperación de pantalla (issue #6)

Las pruebas automáticas usan capturas reales anonimizadas y un dispositivo falso: comprueban que
cada pantalla se reconoce y que la recuperación solo elige salidas seguras. **No comprueban que el
juego reaccione como se supone** a esos toques. Eso requiere tocar la ventana real y no se ha hecho.

## Vías de escape sin verificar en el juego

| Pantalla | Escape | Qué comprobar |
|---|---|---|
| Menú de ataque («Multiplayer / Find a Match») | tocar un cuadrado de terreno a la derecha del menú | que el menú se cierra y no se abre nada (cuidado: el terreno se oscurece con el menú abierto) |
| Exploración de rival («Next» / «End Battle») | botón «End Battle» | que vuelve a la aldea sin coste (no se ha soltado ninguna tropa) |
| Colocando un edificio | botón rojo ✗ | que cancela la colocación y no gasta nada |

El botón de cerrar de los paneles, el «Return Home» del resultado y el «Cancel»/«Okay» del aviso
«Surrender?» sí son botones identificados por su texto o forma en capturas reales, pero tampoco se han
pulsado en el juego desde el bot.

## Cómo probarlo

1. Abrir cada pantalla a mano y ejecutar la recuperación desde la pantalla de pruebas del dispositivo.
2. Para la exploración de rival, hacerlo **sin soltar tropas**.
3. Con una batalla en curso, comprobar que por defecto **no** se rinde (`allow_surrender=False`).

## Pantallas sin firma (no hay captura real todavía)

Builder Base, tienda, laboratorio, mascotas, fase de colocación, cofre/recompensa e inicio de sesión
de Supercell. Se clasifican como desconocidas: salen por un botón de cerrar localizado o terreno
vacío, y si no hay ninguno, la tarea falla guardando una captura. **El inicio de sesión de Supercell
no tiene firma**, así que hoy no se detecta como tal: el mecanismo de parada existe (`Screen.LOGIN`),
pero falta una captura para medir su firma. Hasta entonces el formulario se trataría como pantalla
desconocida; no hay botón de cerrar rojo ni terreno, así que no se tocaría nada.

## Reabrir el juego

`RecoveryHooks.relaunch` y `restore_window` son opcionales. El dispositivo de Google Play Games no
implementa todavía un «reabrir el juego»: sin ese gancho, el juego cerrado o la ventana perdida
detienen la tarea con un mensaje claro en lugar de reabrir.

## Requisitos de la ventana

Las firmas se midieron con una ventana de ~993 px de alto y se comprueban a 720 px (y a 0,8× y 1,5×
de eso). Por debajo de ~720 px los textos pequeños bajan de umbral.
