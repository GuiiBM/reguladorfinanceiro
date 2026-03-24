#!/bin/bash
source venv/bin/activate
(sleep 2 && xdg-open http://localhost:5000) &
python app.py
