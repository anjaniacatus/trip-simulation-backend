import requests
import logging

from datetime import datetime, timedelta


logger = logging.getLogger(__name__)

# Constants for HOS rules and trip simulation
AVERAGE_SPEED = 50  # miles/hour (assumption for calculating stop locations)
MILES_PER_FUELING = 1000  # Fuel every 1,000 miles
FUELING_TIME = 1  # 1 hour for fueling (on-duty not driving)
PICKUP_TIME = 1  # 1 hour for pickup (on-duty not driving)
DROPOFF_TIME = 1  # 1 hour for drop-off (on-duty not driving)
MAX_DRIVING_HOURS_PER_DAY = 11  # HOS: 11 hours driving per day
MAX_ON_DUTY_HOURS_PER_DAY = 14  # HOS: 14 hours on-duty per day
REST_BREAK_DURATION = 1.5  # 30 minutes rest after 8 hours driving
MANDATORY_OFF_DUTY = 10  # 10 hours off-duty after 14 hours on-duty
CYCLE_LIMIT = 70  # 70 hours over 8 days
RESET_DURATION = 34  # 34-hour reset if cycle limit is reached


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
    response = requests.get(url)

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


def simulate_trip(route_data, current_cycle_used=0):
    """
    Simulate the trip with HOS rules, adding stops for fueling, rest breaks, etc.

    Args:
        route_data (dict): Route data from get_route, containing geometry, distance, and duration.
        current_cycle_used (float): Hours already used in the driver's 8-day cycle.

    Returns:
        dict: Updated route data with stops and activities for daily logs.
    """
    distance = route_data["distance"]  # in miles
    duration = route_data["duration"]  # in hours
    geometry = route_data["geometry"]  # [lon, lat]

    # Start time (assume trip starts now)
    start_time = datetime.now()
    current_time = start_time

    # Track driver's hours
    cycle_hours = current_cycle_used  # Total hours in the 8-day cycle
    daily_driving_hours = 0  # Driving hours in the current day
    daily_on_duty_hours = 0  # On-duty hours in the current day
    driving_since_last_break = 0  # Hours driven since last 30-minute break

    # Track activities for daily logs
    activities = []  # List of (start_time, end_time, activity_type)
    stops = []  # List of stops (rests, fueling)

    # Helper function to add an activity
    def add_activity(start, end, activity_type):
        activities.append({
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
            'activity_type': activity_type
        })

    # Helper function to find the location at a given distance along the route
    def get_location_at_distance(target_distance):
        cumulative_distance = 0
        for i in range(len(geometry) - 1):
            lon1, lat1 = geometry[i]
            lon2, lat2 = geometry[i + 1]
            segment_distance = ((lon2 - lon1) ** 2 + (lat2 - lat1) ** 2) ** 0.5 * 69  # Convert to miles (approximation)
            cumulative_distance += segment_distance
            if cumulative_distance >= target_distance:
                return geometry[i]
        return geometry[-1]

    # 1. Pickup (1 hour, on-duty not driving)
    activity_end_time = current_time + timedelta(hours=PICKUP_TIME)
    add_activity(current_time, activity_end_time, 'ON_DUTY_NOT_DRIVING')
    current_time = activity_end_time
    daily_on_duty_hours += PICKUP_TIME
    cycle_hours += PICKUP_TIME

    # 2. Driving loop with HOS rules
    remaining_distance = distance
    remaining_duration = duration
    distance_traveled = 0

    while remaining_distance > 0:
        # Check cycle limit (70 hours over 8 days)
        if cycle_hours >= CYCLE_LIMIT:
            reset_end_time = current_time + timedelta(hours=RESET_DURATION)
            add_activity(current_time, reset_end_time, 'OFF_DUTY')
            current_time = reset_end_time
            cycle_hours = 0
            daily_driving_hours = 0
            daily_on_duty_hours = 0
            driving_since_last_break = 0
            continue

        # Check daily driving and on-duty limits
        if daily_driving_hours >= MAX_DRIVING_HOURS_PER_DAY or daily_on_duty_hours >= MAX_ON_DUTY_HOURS_PER_DAY:
            off_duty_end_time = current_time + timedelta(hours=MANDATORY_OFF_DUTY)
            add_activity(current_time, off_duty_end_time, 'OFF_DUTY')
            current_time = off_duty_end_time
            daily_driving_hours = 0
            daily_on_duty_hours = 0
            driving_since_last_break = 0
            continue

        # Check for mandatory 30-minute rest break after 8 hours of driving
        if driving_since_last_break >= 8:
            rest_end_time = current_time + timedelta(hours=REST_BREAK_DURATION)
            stop_location = get_location_at_distance(distance_traveled)
            add_activity(current_time, rest_end_time, 'OFF_DUTY')
            stops.append({
                'time': (current_time - start_time).total_seconds() / 3600,
                'location': stop_location,
                'reason': '30-minute rest break'
            })
            current_time = rest_end_time
            daily_on_duty_hours += REST_BREAK_DURATION
            cycle_hours += REST_BREAK_DURATION
            driving_since_last_break = 0
            continue

        # Check for fueling stops (every 1000 miles)
        if distance_traveled > 0 and distance_traveled % MILES_PER_FUELING < (distance_traveled - (distance_traveled % MILES_PER_FUELING)):
            fueling_end_time = current_time + timedelta(hours=FUELING_TIME)
            stop_location = get_location_at_distance(distance_traveled)
            add_activity(current_time, fueling_end_time, 'ON_DUTY_NOT_DRIVING')
            stops.append({
                'time': (current_time - start_time).total_seconds() / 3600,
                'location': stop_location,
                'reason': 'Fueling stop'
            })
            current_time = fueling_end_time
            daily_on_duty_hours += FUELING_TIME
            cycle_hours += FUELING_TIME
            continue

        # Drive for up to 1 hour or until the trip is complete
        driving_time = min(1.0, remaining_duration)
        distance_this_segment = driving_time * AVERAGE_SPEED
        distance_traveled += distance_this_segment
        remaining_distance -= distance_this_segment
        remaining_duration -= driving_time

        driving_end_time = current_time + timedelta(hours=driving_time)
        add_activity(current_time, driving_end_time, 'DRIVING')
        current_time = driving_end_time
        daily_driving_hours += driving_time
        daily_on_duty_hours += driving_time
        cycle_hours += driving_time
        driving_since_last_break += driving_time

    # 3. Drop-off (1 hour, on-duty not driving)
    dropoff_end_time = current_time + timedelta(hours=DROPOFF_TIME)
    add_activity(current_time, dropoff_end_time, 'ON_DUTY_NOT_DRIVING')
    current_time = dropoff_end_time
    daily_on_duty_hours += DROPOFF_TIME
    cycle_hours += DROPOFF_TIME

    # Update route_data with stops and activities
    route_data["stops"] = stops
    route_data["activities"] = activities
    route_data["start_time"] = start_time

    return route_data
