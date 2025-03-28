import requests
import logging

logger = logging.getLogger(__name__)

def get_route(current, pickup, dropoff):
    # OSRM route request for all waypoints
    coords = f"{current[1]},{current[0]};{pickup[1]},{pickup[0]};{dropoff[1]},{dropoff[0]}"
    url = f"http://router.project-osrm.org/route/v1/driving/{coords}?overview=full&geometries=geojson"
    response = requests.get(url)

    if response.status_code != 200 or 'routes' not in response.json() or not response.json()['routes']:
        return None

    route_data = response.json()['routes'][0]
    return {
        "geometry": route_data['geometry']['coordinates'],  # [lon, lat]
        "distance": route_data['distance'] / 1609.34,  # Convert meters to miles
        "stops": []  # Will be populated in simulate_trip
    }