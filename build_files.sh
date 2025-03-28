#!/bin/bash
python3 -m venv .trip_env
source .tripenv/bin/activate
pip install -r requirements.txt
python3 manage.py collectstatic --no-input --clear
