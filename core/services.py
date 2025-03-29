import requests
import logging
from datetime import datetime, timedelta
from geopy.distance import geodesic
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# Constants for HOS rules and trip simulation
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
    "AVERAGE_SPEED": 60.0            # Default miles per hour, overridden by dynamic speed
}

def get_route(current: List[float], pickup: List[float], dropoff: List[float]) -> Optional[Dict]:
    """
    Fetch the route from OSRM for the given waypoints.

    Args:
        current: Current location [lat, lon]
        pickup: Pickup location [lat, lon]
        dropoff: Dropoff location [lat, lon]

    Returns:
        Dict with geometry, distance, duration, and stops, or None if failed.
    """
    coords = f"{current[1]},{current[0]};{pickup[1]},{pickup[0]};{dropoff[1]},{dropoff[0]}"
    url = f"http://router.project-osrm.org/route/v1/driving/{coords}?overview=full&geometries=geojson"
    logger.info(f"Fetching route: {url}")
    try:
        response = requests.get(url, timeout=10)
        logger.info(f"OSRM response: {response.status_code}")
        if response.status_code != 200 or 'routes' not in response.json() or not response.json()['routes']:
            logger.error(f"Failed to fetch route from OSRM: {response.status_code} - {response.text}")
            return None
        route_data = response.json()['routes'][0]
        return {
            "geometry": route_data['geometry']['coordinates'],  # [lon, lat]
            "distance": route_data['distance'] / 1609.34,       # Meters to miles
            "duration": route_data['duration'] / 3600,          # Seconds to hours
            "stops": []
        }
    except requests.RequestException as e:
        logger.error(f"OSRM request failed: {e}")
        return None

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
        # Dynamic speed based on OSRM's estimate, fallback to default
        self.average_speed = total_distance / total_duration if total_duration > 0 else HOS_RULES["AVERAGE_SPEED"]

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
            if self.cycle_hours >= HOS_RULES["CYCLE_LIMIT"]:
                self.add_activity(HOS_RULES["RESET_DURATION"], "OFF_DUTY", "Cycle reset")
                self.reset_daily_hours()
                continue

            if (self.daily_driving_hours >= HOS_RULES["MAX_DRIVING_HOURS_PER_DAY"] or 
                self.daily_on_duty_hours >= HOS_RULES["MAX_ON_DUTY_HOURS_PER_DAY"]):
                self.add_activity(HOS_RULES["MANDATORY_OFF_DUTY"], "OFF_DUTY", "Daily limit reached")
                self.reset_daily_hours()
                continue

            if self.driving_since_break >= 8:
                location = get_location_at_distance(cumulative_distances, geometry, self.distance_traveled)
                self.add_activity(HOS_RULES["REST_BREAK_DURATION"], "OFF_DUTY", "30-minute rest break", location)
                self.driving_since_break = 0
                continue

            new_fueling_stops = int(self.distance_traveled / HOS_RULES["MILES_PER_FUELING"])
            if new_fueling_stops > fueling_stops:
                location = get_location_at_distance(cumulative_distances, geometry, self.distance_traveled)
                self.add_activity(HOS_RULES["FUELING_TIME"], "ON_DUTY_NOT_DRIVING", "Fueling stop", location)
                fueling_stops = new_fueling_stops
                continue

            driving_time = min(1.0, self.remaining_duration)
            distance_segment = driving_time * self.average_speed
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
    try:
        distances = [0.0]
        for i in range(len(geometry) - 1):
            point1 = (geometry[i][1], geometry[i][0])  # (lat, lon)
            point2 = (geometry[i + 1][1], geometry[i + 1][0])
            segment_distance = geodesic(point1, point2).miles
            distances.append(distances[-1] + segment_distance)
        return distances
    except Exception as e:
        logger.error(f"Error precomputing distances: {e}")
        raise ValueError("Failed to compute route distances")

def get_location_at_distance(cumulative_distances: List[float], geometry: List[List[float]], target_distance: float) -> List[float]:
    """Find the location at a given distance along the route."""
    try:
        for i in range(len(cumulative_distances) - 1):
            if cumulative_distances[i] <= target_distance <= cumulative_distances[i + 1]:
                return geometry[i]
        return geometry[-1]
    except IndexError as e:
        logger.error(f"Index error in get_location_at_distance: {e}")
        return geometry[-1]

def simulate_trip(route_data: Dict, current_cycle_used: float = 0) -> Dict:
    """
    Simulate a trip with HOS rules, adding stops for rest, fueling, etc.

    Args:
        route_data: Dict with 'geometry' (List[lon, lat]), 'distance' (miles), 'duration' (hours).
        current_cycle_used: Hours already used in the driver's 8-day cycle.

    Returns:
        Updated route_data with stops and activities.

    Raises:
        ValueError: If route_data is invalid or simulation fails.
    """
    try:
        if not all(k in route_data for k in ["geometry", "distance", "duration"]):
            raise ValueError("Invalid route_data: missing required fields")
 
        total_distance = route_data["distance"]
        total_duration = route_data["duration"]
        geometry = route_data["geometry"]
        if not geometry or total_distance <= 0 or total_duration <= 0:
            raise ValueError("Invalid route_data: empty geometry or non-positive distance/duration")

        cumulative_distances = precompute_distances(geometry)
        trip = TripState(
            start_time=datetime.now(),
            current_cycle_used=current_cycle_used,
            total_distance=total_distance,
            total_duration=total_duration
        )

        trip.handle_pickup()
        trip.simulate_driving(cumulative_distances, geometry)
        trip.handle_dropoff()

        route_data["stops"] = trip.stops
        route_data["activities"] = trip.activities
        route_data["start_time"] = trip.start_time
        route_data["duration"] = trip.total_duration  # Update with actual duration
        return route_data

    except Exception as e:
        logger.error(f"Simulation error: {e}")
        raise ValueError(f"Failed to simulate trip: {str(e)}")
