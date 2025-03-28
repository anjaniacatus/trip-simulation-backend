import requests
import logging

from django.shortcuts import render
from rest_framework.decorators import api_view
from rest_framework.response import Response


from core.services import get_route


logger = logging.getLogger(__name__)
@api_view(['POST'])
def plan_trip(request):
    data = request.data
    current = [float(x) for x in data['current_location'].split(',')] # [lat, lon]
    pickup = [float(x) for x in data['pickup_location'].split(',')] # [lat, lon]
    dropoff = [float(x) for x in data['dropoff_location'].split(',')] #

    # Get route from OSRM
    logger.info(data)
    route_data = get_route(current, pickup, dropoff)
    if not route_data:
        return Response({"error": "Failed to get route"}, status=400)

    # Prepare response
    response = {
        "route": route_data["geometry"],  # Polyline for map
        "distance": route_data["distance"],  # in miles
        "stops": route_data["stops"],
        "current_location": current,  # [lat, lon]
        "pickup_location":  pickup,   # [lat, lon]
        "dropoff_location": dropoff# List of stop locations
        #"daily_logs": daily_logs
    }
    return Response(response)