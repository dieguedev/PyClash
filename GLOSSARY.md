# PyClash

Bot que automatiza el progreso de una cuenta de Clash of Clans sobre Google Play Games hasta que la aldea queda maxeada en Ayuntamiento 9.

## Language

### Modos de mejora

**Maxer**:
Modo que deja al máximo todo lo que permite el Ayuntamiento actual antes de subir el Ayuntamiento. No pasa de Ayuntamiento 9.
_Avoid_: Modo completo

**Rusher**:
Modo que, hasta Ayuntamiento 9, solo mejora lo de la lista blanca y sube el Ayuntamiento en cuanto puede. Al llegar a Ayuntamiento 9 maxea todo lo que dejó atrás.
_Avoid_: Modo rápido

**Lista blanca**:
Conjunto cerrado de edificios y tropas que el Rusher mejora antes de Ayuntamiento 9. Lo que no está en ella se deja sin mejorar.
_Avoid_: Whitelist, prioridades

**TH9 maxeado**:
Estado final del bot: Ayuntamiento en nivel 9 con todo lo que permite al máximo, muros incluidos, y como única mejora pendiente la subida a Ayuntamiento 10, que el bot nunca hace.
_Avoid_: Ayuntamiento maxeado, aldea terminada

**Simulacro**:
Forma de ejecutar en la que el bot decide como en Maxer pero no pulsa nada. No es un modo de mejora ni un perfil guardado.
_Avoid_: Dry run

### Aldea

**Obra**:
Mejora, construcción o investigación en curso que ocupa un constructor o el laboratorio.
_Avoid_: Upgrade en curso, trabajo

**Constructor**:
Cada uno de los trabajadores que realizan una obra de edificio. Quitar un obstáculo no ocupa un constructor.
_Avoid_: Builder, obrero

**Obstáculo**:
Elemento del terreno de la aldea que se retira pagando recursos, sin ocupar constructor, y que puede dar gemas.
_Avoid_: Escombro, decoración

**Reserva de oro**:
Oro mínimo que debe quedar tras cualquier gasto para poder buscar partida.
_Avoid_: Umbral, colchón

**Reserva de constructor**:
Constructor que se deja libre para que los muros avancen mientras el resto de la aldea se mejora.
_Avoid_: Constructor de muros

### Pulsaciones

**Reacción humana**:
Pausa aleatoria que el bot deja entre ver cambiar la pantalla y pulsar, y entre pulsaciones seguidas del mismo botón, para que su ritmo no se distinga del de una persona. Nunca es una espera fija, y no sustituye a esperar a que el juego responda.
_Avoid_: Delay, pausa fija, espera

### Ataque

**Farmeo**:
Atacar aldeas para conseguir recursos hasta llenar los almacenes.
_Avoid_: Farm

**Estrategia**:
Forma de desplegar las tropas de un ataque, por ejemplo BArch.
_Avoid_: Táctica, ejército

**BArch**:
Estrategia que despliega primero los bárbaros en círculos y después las arqueras. Sirve para limpiar los exteriores de la aldea.
_Avoid_: Barb-Arch

**Ejército activo**:
Ejército que el jugador deja montado en el propio juego. El bot lo usa tal cual: no lo entrena, no pulsa plantillas ni lee colas, y despliega solo las tropas que haya en la barra de batalla.
_Avoid_: Plantilla de ejército, Quick Train
