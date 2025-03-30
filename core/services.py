import requests
import logging
from datetime import datetime, timedelta
from geopy.distance import geodesic
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

HOS_RULES = {
    "PICKUP_TIME": 1.0, "DROPOFF_TIME": 1.0, "CYCLE_LIMIT": 70.0, "RESET_DURATION": 34.0,
    "MAX_DRIVING_HOURS_PER_DAY": 11.0, "MAX_ON_DUTY_HOURS_PER_DAY": 14.0, "MANDATORY_OFF_DUTY": 10.0,
    "REST_BREAK_DURATION": 0.5, "MILES_PER_FUELING": 1000.0, "FUELING_TIME": 0.5, "AVERAGE_SPEED": 60.0
}

def get_route(current: List[float], pickup: List[float], dropoff: List[float]) -> Optional[Dict]:
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
            "geometry": route_data['geometry']['coordinates'],
            "distance": route_data['distance'] / 1609.34,
            "duration": route_data['duration'] / 3600,
            "stops": []
        }
    except requests.RequestException as e:
        logger.error(f"OSRM request failed: {e}")
        return None

class TripState:
    def __init__(self, start_time: datetime, current_cycle_used: float, total_distance: float, total_duration: float):
        self.current_time = start_time
        self.start_time = start_time
        self.cycle_hours = current_cycle_used
        self.daily_driving_hours = 0.0
        self.daily_on_duty_hours = 0.0
        self.driving_since_break = 0.0
        self.distance_traveled = 0.0
        self.remaining_distance = total_distance
        self.total_duration = 0.0
        self.activities: List[Dict] = []
        self.stops: List[Dict] = []
        self.average_speed = total_duration > 0 and total_distance / total_duration or HOS_RULES["AVERAGE_SPEED"]
        self.iteration_count = 0
        self.max_iterations = 10000
        self.distance_tolerance = 0.1
        logger.debug(f"Initialized: Distance={total_distance}, Speed={self.average_speed}, Cycle Hours={self.cycle_hours}")

    def add_activity(self, duration: float, activity_type: str, stop_reason: str = None, location: List[float] = None):
        try:
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
            # Only count on-duty time towards cycle_hours
            if activity_type in ["DRIVING", "ON_DUTY_NOT_DRIVING"]:
                self.cycle_hours += duration
                self.daily_on_duty_hours += duration
            if activity_type == "DRIVING":
                self.daily_driving_hours += duration
                self.driving_since_break += duration
            logger.debug(f"Activity: {activity_type}, Duration: {duration}, Cycle Hours: {self.cycle_hours}, Distance Traveled: {self.distance_traveled}, Remaining: {self.remaining_distance}")
        except OverflowError as e:
            logger.error(f"Date overflow in add_activity: {e}")
            raise ValueError("Simulation exceeded date range")

    def handle_pickup(self):
        self.add_activity(HOS_RULES["PICKUP_TIME"], "ON_DUTY_NOT_DRIVING")

    def handle_dropoff(self):
        self.add_activity(HOS_RULES["DROPOFF_TIME"], "ON_DUTY_NOT_DRIVING")

    def simulate_driving(self, cumulative_distances: List[float], geometry: List[List[float]]):
        fueling_stops = 0
        while self.remaining_distance > self.distance_tolerance:
            self.iteration_count += 1
            if self.iteration_count > self.max_iterations:
                logger.error(f"Simulation exceeded max iterations: {self.max_iterations}, Remaining Distance: {self.remaining_distance}")
                raise ValueError("Simulation aborted: too many iterations")

            if self.cycle_hours >= HOS_RULES["CYCLE_LIMIT"]:
                self.add_activity(HOS_RULES["RESET_DURATION"], "OFF_DUTY", "Cycle reset")
                self.cycle_hours = 0  # Reset cycle after 34-hour off-duty
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
            driving_time = min(1.0, self.remaining_distance / self.average_speed)
            distance_segment = driving_time * self.average_speed
            self.distance_traveled += distance_segment
            self.remaining_distance = max(0, self.remaining_distance - distance_segment)
            self.add_activity(driving_time, "DRIVING")
            logger.debug(f"Driving: Time={driving_time}, Distance={distance_segment}, Remaining={self.remaining_distance}")

    def reset_daily_hours(self):
        self.daily_driving_hours = 0.0
        self.daily_on_duty_hours = 0.0
        self.driving_since_break = 0.0

def precompute_distances(geometry: List[List[float]]) -> List[float]:
    try:
        distances = [0.0]
        for i in range(len(geometry) - 1):
            point1 = (geometry[i][1], geometry[i][0])
            point2 = (geometry[i + 1][1], geometry[i + 1][0])
            segment_distance = geodesic(point1, point2).miles
            distances.append(distances[-1] + segment_distance)
        return distances
    except Exception as e:
        logger.error(f"Error precomputing distances: {e}")
        raise ValueError("Failed to compute route distances")

def get_location_at_distance(cumulative_distances: List[float], geometry: List[List[float]], target_distance: float) -> List[float]:
    try:
        for i in range(len(cumulative_distances) - 1):
            if cumulative_distances[i] <= target_distance <= cumulative_distances[i + 1]:
                return geometry[i]
        return geometry[-1]
    except IndexError as e:
        logger.error(f"Index error in get_location_at_distance: {e}")
        return geometry[-1]

def simulate_trip(route_data: Dict, current_cycle_used: float = 0) -> Dict:
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
        route_data["duration"] = trip.total_duration
        return route_data
    except Exception as e:
        logger.error(f"Simulation error: {e}")
        raise ValueError(f"Failed to simulate trip: {str(e)}")