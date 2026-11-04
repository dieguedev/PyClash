# Las pulsaciones siguen un ritmo humano y la espera es por sondeo

El bot no debe pulsar a una velocidad que una persona no pueda sostener, ni con un ritmo exacto que delate a un programa. Decidimos dos cosas: (1) esperar al juego por **sondeo** (mirar la pantalla cada ~0,1 s y seguir en cuanto sale lo esperado), nunca con pausas fijas, de modo que el tiempo entre pasos es lo que tarda el juego en responder más lo que tarda la visión en leerlo; y (2) añadir una **reacción humana** aleatoria, uniforme entre 0,2 y 0,35 s, antes de pulsar lo que acaba de aparecer o cambiar, y una pausa aleatoria de 0,24 a 0,34 s entre pulsaciones seguidas del mismo botón (los «+1» de los muros).

La base de los rangos: la reacción visual simple de un adulto ronda los 0,2 s (207 ms de media en un estudio de laboratorio; 268 ms con una elección entre dos opciones, [Perceptual and Motor Skills, 1986](https://doi.org/10.2466/pms.1986.62.1.195); las fuentes divulgativas dan 0,2-0,27 s). El tope de 0,35 s deja margen para llevar el cursor al botón. Las pausas entre «+1» son el doble de la primera versión (0,12 s fijos), que a ojo ya no parecía humana.

Se descartó la pausa fija «de aspecto humano» (una constante se reconoce igual que ninguna pausa) y esperar solo lo que tarde la visión (más rápido de lo que una persona reacciona). El coste es tiempo: un lote de muros tarda algo más que el mínimo posible, y es deliberado.

Hoy solo lo cumple `WallBatchTask` (`poll_s`, `ui_timeout_s`, `reaction_min_s`, `reaction_max_s`, `tap_gap_*_s` en `WallConfig`). El resto de tareas (mejoras, construir, farmeo) conservan esperas fijas de `settle_s`: cuando se toquen, deben pasar al mismo esquema, y los rangos se ajustan en un único sitio por tarea.
