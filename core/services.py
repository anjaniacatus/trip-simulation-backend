import requests
import logging

from typing import List, Dict, Tuple
from datetime import datetime, timedelta
from geopy.distance import geodesic

logger = logging.getLogger(__name__)

# Constants for HOS rules and trip simulation
# Constants
HOS_RULES = {
    "PICKUP_TIME": 1.0,              # Hours for pickup
    "DROPOFF_TIME": 1.0,             # Hours for drop-off
    "CYCLE_LIMIT": 70.0,             # Max hours in 8-day cycle
    "RESET_DURATION": 34.0,          # Hours for cycle reset
    "MAX_DRIVING_HOURS_PER_DAY": 11.0,  # Daily driving limit
    "MAX_ON_DUTY_HOURS_PER_DAY": 14.0,  # Daily on-duty limit
    "MANDATORY_OFF_DUTY": 10.0,      # Mandatory off-duty after daily limit
    "REST_BREAK_DURATION": 0.5,      # 30-minute rest break
    "MILES_PER_FUELING": 1000.0,     # Miles between fueling stops
    "FUELING_TIME": 0.5,             # Hours for fueling
    "AVERAGE_SPEED": 60.0            # Miles per hour
}

def get_route(current, pickup, dropoff):
    """
    Fetch the route from OSRM for the given waypoints.

    Args:
        current (list): Current location [lat, lon]
        pickup (list): Pickup location [lat, lon]
        dropoff (list): Dropoff location [lat, lon]

    Returns:
        dict: Route data with geometry, distance, and stops, or None if failed.
    """

    # OSRM route request for all waypoints

    coords = f"{current[1]},{current[0]};{pickup[1]},{pickup[0]};{dropoff[1]},{dropoff[0]}"
    url = f"http://router.project-osrm.org/route/v1/driving/{coords}?overview=full&geometries=geojson"
    logger.info(url)
    response = requests.get(url)
    logger.info(f"from osrm : {response.status_code}")

    if response.status_code != 200 or 'routes' not in response.json() or not response.json()['routes']:
        logger.error(f"Failed to fetch route from OSRM: {response.status_code} - {response.text}")
        return None

    route_data = response.json()['routes'][0]
    return {
        "geometry": route_data['geometry']['coordinates'],  # [lon, lat]
        "distance": route_data['distance'] / 1609.34, # Convert meters to miles
        "duration": route_data['duration'] / 3600,
        "stops": []  # Will be populated in simulate_trip
    }


def simulate_trip(route_data: Dict, current_cycle_used: float = 0) -> Dict:
    """
    Simulate a trip with HOS rules, adding stops for rest, fueling, etc.

    Args:
        route_data: Dict with 'geometry' (List[lon, lat]), 'distance' (miles), 'duration' (hours).
        current_cycle_used: Hours already used in the driver's 8-day cycle.

    Returns:
        Updated route_data with stops and activities.
    """
    total_distance = route_data["distance"]
    total_duration = route_data["duration"]
    geometry = route_data["geometry"]

    # Precompute cumulative distances for efficiency
    cumulative_distances = precompute_distances(geometry)

    # Initialize trip state
    trip = TripState(
        start_time=datetime.now(),
        current_cycle_used=current_cycle_used,
        total_distance=total_distance,
        total_duration=total_duration
    )

    # Simulate trip segments
    trip.handle_pickup()
    trip.simulate_driving(cumulative_distances, geometry)
    trip.handle_dropoff()

    # Update route_data with results
    route_data["stops"] = trip.stops
    route_data["activities"] = trip.activities
    route_data["start_time"] = trip.start_time
    return route_data

class TripState:
    """Manages the state of the trip simulation."""
    def __init__(self, start_time: datetime, current_cycle_used: float, total_distance: float, total_duration: float):
        self.current_time = start_time
        self.start_time = start_time
        self.cycle_hours = current_cycle_used
        self.daily_driving_hours = 0.0
        self.daily_on_duty_hours = 0.0
        self.driving_since_break = 0.0
        self.distance_traveled = 0.0
        self.remaining_distance = total_distance
        self.remaining_duration = total_duration
        self.total_duration = 0.0  # Tracks total time including stops
        self.activities: List[Dict] = []
        self.stops: List[Dict] = []

    def add_activity(self, duration: float, activity_type: str, stop_reason: str = None, location: List[float] = None):
        """Add an activity and optionally a stop."""
        end_time = self.current_time + timedelta(hours=duration)
        self.activities.append({
            "start_time": self.current_time.isoformat(),
            "end_time": end_time.isoformat(),
            "activity_type": activity_type
        })
        if stop_reason and location:
            self.stops.append({
                "time": (self.current_time - self.start_time).total_seconds() / 3600,
                "location": location,
                "reason": stop_reason
            })
        self.current_time = end_time
        self.total_duration += duration
        if activity_type == "DRIVING":
            self.daily_driving_hours += duration
            self.daily_on_duty_hours += duration
            self.driving_since_break += duration
            self.cycle_hours += duration
        elif activity_type in ["ON_DUTY_NOT_DRIVING", "OFF_DUTY"]:
            self.daily_on_duty_hours += duration
            self.cycle_hours += duration

    def handle_pickup(self):
        """Simulate pickup activity."""
        self.add_activity(HOS_RULES["PICKUP_TIME"], "ON_DUTY_NOT_DRIVING")

    def handle_dropoff(self):
        """Simulate drop-off activity."""
        self.add_activity(HOS_RULES["DROPOFF_TIME"], "ON_DUTY_NOT_DRIVING")

    def simulate_driving(self, cumulative_distances: List[float], geometry: List[List[float]]):
        """Simulate driving with HOS rules."""
        fueling_stops = 0
        while self.remaining_distance > 0:
            # Check HOS limits
            if self.cycle_hours >= HOS_RULES["CYCLE_LIMIT"]:
                self.add_activity(HOS_RULES["RESET_DURATION"], "OFF_DUTY")
                self.reset_daily_hours()
                continue

            if (self.daily_driving_hours >= HOS_RULES["MAX_DRIVING_HOURS_PER_DAY"] or 
                self.daily_on_duty_hours >= HOS_RULES["MAX_ON_DUTY_HOURS_PER_DAY"]):
                self.add_activity(HOS_RULES["MANDATORY_OFF_DUTY"], "OFF_DUTY")
                self.reset_daily_hours()
                continue

            if self.driving_since_break >= 8:
                location = get_location_at_distance(cumulative_distances, geometry, self.distance_traveled)
                self.add_activity(HOS_RULES["REST_BREAK_DURATION"], "OFF_DUTY", "30-minute rest break", location)
                self.driving_since_break = 0
                continue

            # Check fueling stop
            new_fueling_stops = int(self.distance_traveled / HOS_RULES["MILES_PER_FUELING"])
            if new_fueling_stops > fueling_stops:
                location = get_location_at_distance(cumulative_distances, geometry, self.distance_traveled)
                self.add_activity(HOS_RULES["FUELING_TIME"], "ON_DUTY_NOT_DRIVING", "Fueling stop", location)
                fueling_stops = new_fueling_stops
                continue

            # Drive for up to 1 hour
            driving_time = min(1.0, self.remaining_duration)
            distance_segment = driving_time * HOS_RULES["AVERAGE_SPEED"]
            self.distance_traveled += distance_segment
            self.remaining_distance -= distance_segment
            self.remaining_duration -= driving_time
            self.add_activity(driving_time, "DRIVING")
    
    def reset_daily_hours(self):
        """Reset daily hours after off-duty period."""
        self.daily_driving_hours = 0.0
        self.daily_on_duty_hours = 0.0
        self.driving_since_break = 0.0

def precompute_distances(geometry: List[List[float]]) -> List[float]:
    """Precompute cumulative distances along the route."""
    distances = [0.0]
    for i in range(len(geometry) - 1):
        point1 = (geometry[i][1], geometry[i][0])  # (lat, lon)
        point2 = (geometry[i + 1][1], geometry[i + 1][0])
        segment_distance = geodesic(point1, point2).miles
        distances.append(distances[-1] + segment_distance)
    return distances

def get_location_at_distance(cumulative_distances: List[float], geometry: List[List[float]], target_distance: float) -> List[float]:
    """Find the location at a given distance along the route."""
    for i in range(len(cumulative_distances) - 1):
        if cumulative_distances[i] <= target_distance <= cumulative_distances[i + 1]:
            return geometry[i]
    return geometry[-1]
