import pytest
from unittest.mock import patch, Mock
from datetime import datetime, timedelta
from .services import get_route, simulate_trip, TripState, HOS_RULES

MOCK_ROUTE_DATA = {
    "geometry": [[-8.096950, 31.718790], [-9.598107, 30.427755], [-6.841650, 34.020882]],
    "distance": 600.0,
    "duration": 10.0,
    "stops": []
}

def test_get_route_success():
    with patch("requests.get") as mock_get:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "routes": [{
                "geometry": {"coordinates": MOCK_ROUTE_DATA["geometry"]},
                "distance": 600 * 1609.34,
                "duration": 10 * 3600
            }]
        }
        mock_get.return_value = mock_response
        result = get_route([31.718790, -8.096950], [30.427755, -9.598107], [34.020882, -6.841650])
        assert result is not None
        assert result["distance"] == pytest.approx(600.0, rel=1e-2)
        assert result["duration"] == pytest.approx(10.0, rel=1e-2)
        assert len(result["geometry"]) == 3

def test_get_route_failure():
    with patch("requests.get") as mock_get:
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.json.return_value = {}
        mock_get.return_value = mock_response
        result = get_route([31.718790, -8.096950], [30.427755, -9.598107], [34.020882, -6.841650])
        assert result is None

def test_simulate_trip_short_trip():
    route_data = MOCK_ROUTE_DATA.copy()
    result = simulate_trip(route_data, current_cycle_used=0)
    assert result["distance"] == 600.0
    assert len(result["activities"]) > 0
    assert result["activities"][0]["activity_type"] == "ON_DUTY_NOT_DRIVING"
    assert result["activities"][-1]["activity_type"] == "ON_DUTY_NOT_DRIVING"
    rest_stops = [stop for stop in result["stops"] if stop["reason"] == "30-minute rest break"]
    assert len(rest_stops) == 1
    assert result["duration"] > 10.0

def test_simulate_trip_cycle_limit():
    route_data = MOCK_ROUTE_DATA.copy()
    route_data["distance"] = 900.0  # 15 hours at 60 mph, enough to exceed 70-hour limit with current_cycle_used
    route_data["duration"] = 15.0
    result = simulate_trip(route_data, current_cycle_used=65.0)  # 65 + 15 > 70, triggers reset
    assert any(a["activity_type"] == "OFF_DUTY" and
               timedelta(hours=HOS_RULES["RESET_DURATION"]) ==
               datetime.fromisoformat(a["end_time"]) - datetime.fromisoformat(a["start_time"])
               for a in result["activities"])
    assert result["duration"] > 15.0  # Includes reset time

def test_simulate_trip_invalid_input():
    with pytest.raises(ValueError):
        simulate_trip({"geometry": [], "distance": 0, "duration": 0})
    with pytest.raises(ValueError):
        simulate_trip({"geometry": [[0, 0]], "distance": -1, "duration": 10})

def test_trip_state_dynamic_speed():
    trip = TripState(datetime.now(), 0, 120.0, 2.0)
    assert trip.average_speed == 60.0
    trip = TripState(datetime.now(), 0, 100.0, 0)
    assert trip.average_speed == HOS_RULES["AVERAGE_SPEED"]