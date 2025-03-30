import logging

from django.shortcuts import render
from rest_framework.decorators import api_view
from rest_framework.response import Response


from core.services import get_route, simulate_trip


logger = logging.getLogger(__name__)
@api_view(['POST'])
def plan_trip(request):
    """
    Plan a trip from current location to pickup and dropoff locations.

    Expects a POST request with:
    - current_location: "lat,lon"
    - pickup_location: "lat,lon"
    - dropoff_location: "lat,lon"
    - current_cycle_used: float (optional, default 0)

    Returns a JSON response with route, distance, stops.
    """
    logger.debug(f"Request method: {request.method}")
    try:
        data = request.data
        # Validate and parse input data
        if not all(key in data for key in ['current_location', 'pickup_location', 'dropoff_location']):
            return Response({"error": "Missing required fields"}, status=400)

        current = [float(x) for x in data['current_location'].split(',')]  # [lat, lon]
        pickup = [float(x) for x in data['pickup_location'].split(',')]    # [lat, lon]
        dropoff = [float(x) for x in data['dropoff_location'].split(',')]  # [lat, lon]
        current_cycle_used = float(data.get('current_cycle_used', 0))      # Default to 0 if not provided

        # Validate coordinate format
        for coord in [current, pickup, dropoff]:
            if len(coord) != 2 or not (-90 <= coord[0] <= 90) or not (-180 <= coord[1] <= 180):
                return Response({"error": "Invalid coordinates"}, status=400)

        # Get route from OSRM
        logger.info(f"Planning trip: {data}")
        route_data = get_route(current, pickup, dropoff)
        if not route_data:
            return Response({"error": "Failed to get route"}, status=400)

        # Simulate the trip with HOS rules
        route_data = simulate_trip(route_data, current_cycle_used)

        # Prepare response
        response = {
            "route": route_data["geometry"],  # Polyline for map
            "distance": route_data["distance"],  # in miles
            "duration": route_data["duration"],  # in hours
            "stops": route_data["stops"],  # List of stop locations
            "current_location": current,  # [lat, lon]
            "pickup_location": pickup,   # [lat, lon]
            "dropoff_location": dropoff,  # [lat, lon]
            "daily_logs": route_data["daily_logs"],
            "activities": route_data["activities"],
        }
        return Response(response)

    except (KeyError, ValueError) as e:
        logger.error(f"Invalid request data: {e}")
        return Response({"error": "Invalid request data"}, status=400)
    except Exception as e:
        logger.error(f"Error planning trip: {e}")
        return Response({"error": "Internal server error"}, status=500)
