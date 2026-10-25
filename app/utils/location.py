import math


EARTH_RADIUS_KM = 6371.0


def calculate_distance_km(
    latitude1: float,
    longitude1: float,
    latitude2: float,
    longitude2: float,
) -> float:
    """Calculate the great-circle distance between two coordinates in kilometers."""
    lat1 = math.radians(latitude1)
    lon1 = math.radians(longitude1)
    lat2 = math.radians(latitude2)
    lon2 = math.radians(longitude2)

    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1

    haversine_value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1)
        * math.cos(lat2)
        * math.sin(delta_lon / 2) ** 2
    )

    # Protect against floating-point rounding outside the valid range.
    haversine_value = min(1.0, max(0.0, haversine_value))

    central_angle = 2 * math.asin(math.sqrt(haversine_value))

    return round(EARTH_RADIUS_KM * central_angle, 2)
