# Texto conocido con la fuente del juego; EasyOCR solo para lo desconocido

Los textos y dígitos que se conocen de antemano (nombres de edificios, botones, números) se buscan renderizándolos con la fuente del juego y comparándolos por plantilla, en lugar de guardar una captura por etiqueta. El OCR queda para el texto que no se conoce, con EasyOCR (no Tesseract) como extra opcional porque arrastra PyTorch: no entra en el CI ni en los tests normales, que usan un OCR falso. Nunca se usa un OCR que envíe capturas a un servicio externo.
