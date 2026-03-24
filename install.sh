#!/bin/bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "✓ Instalação concluída. Execute: ./run.sh"
