#!/bin/bash
source venv/bin/activate
# A saída do navegador é redirecionada para /dev/null: sem isso, o Chrome/
# Chromium despeja no terminal seu próprio ruído interno (erros de
# certificado/SSL de domínios externos, registro de push do Google, decoder
# de vídeo por hardware, etc.) misturado com o log do Flask, dando a
# impressão de que são erros da aplicação.
(sleep 2 && xdg-open http://localhost:5000 >/dev/null 2>&1) &
python app.py
