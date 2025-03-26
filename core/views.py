from django.shortcuts import render
from rest_framework.decorators import api_view
from rest_framework.response import Response
import requests
import logging


logger = logging.getLogger(__name__)
@api_view(['POST'])
def plan_trip(request):
    data = request.data
    current_loc = data['current_location']  # e.g., "40.7128,-74.0060" (lat,lon)
    pickup_loc = data['pickup_location']
    dropoff_loc = data['dropoff_location']
    cycle_used = float(data['current_cycle_used'])  # hours

    # Get route from OSRM
    logger.info(data)
    route_data = get_route(current_loc, pickup_loc, dropoff_loc)
    if not route_data:
        return Response({"error": "Failed to get route"}, status=400)


    # Prepare response
    response = {
        "route": route_data["geometry"],  # Polyline for map
        "distance": route_data["distance"],  # in miles
        "stops": route_data["stops"],  # List of stop locations
        #"daily_logs": daily_logs

    }
    logger.info(response)
    return Response(response)

def get_route(current_loc, pickup_loc, dropoff_loc):
    # Parse coordinates
    current = [float(x) for x in current_loc.split(',')]
    pickup = [float(x) for x in pickup_loc.split(',')]
    dropoff = [float(x) for x in dropoff_loc.split(',')]

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