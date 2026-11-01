# Solo se controla Google Play Games, sin ADB ni emulador

El bot actúa únicamente sobre la ventana de Google Play Games de Windows (captura de pantalla y entrada de ratón). Se descartaron el control por ADB, BlueStacks y el modo «abrir, trabajar, cerrar y esperar» porque el jugador solo usa Google Play Games y cada backend extra multiplica lo que hay que mantener y verificar. El resto del programa solo conoce la interfaz `Device`, así que añadir otro backend más adelante no obliga a reescribir nada.
