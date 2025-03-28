 #!/bin/bash
python3 -m venv .env_trip
source .trip_env/bin/activate
pip install -r requirements.txt
python3 manage.py collectstatic --no-input --clear
