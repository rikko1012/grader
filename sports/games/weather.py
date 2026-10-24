"""Kickoff weather for outdoor NFL games: the National Weather Service hourly forecast (api.weather.gov - US
government data, free, no key; it asks for an identifying User-Agent, which net.py sends).

ESPN's game data carries no weather (checked 2026-10-23 on completed and upcoming 2026 games), so the forecast is the
one weather source. Indoor venues (ESPN's venue flag) get none; games outside the US get none (NWS covers the US).
Stadium coordinates are fixed public facts, listed below by ESPN venue name.
"""
import re

from .. import net

API = "https://api.weather.gov"
STADIUMS = {               # outdoor and retractable-roof NFL stadiums, 2026 (lat, lon)
    "Highmark Stadium": (42.774, -78.787), "Hard Rock Stadium": (25.958, -80.239),
    "Gillette Stadium": (42.091, -71.264), "MetLife Stadium": (40.814, -74.074),
    "M&T Bank Stadium": (39.278, -76.623), "Paycor Stadium": (39.095, -84.516),
    "Huntington Bank Field": (41.506, -81.700), "Acrisure Stadium": (40.447, -80.016),
    "EverBank Stadium": (30.324, -81.637), "Nissan Stadium": (36.166, -86.771),
    "Empower Field at Mile High": (39.744, -105.020), "GEHA Field at Arrowhead Stadium": (39.049, -94.484),
    "Lincoln Financial Field": (39.901, -75.168), "Northwest Stadium": (38.908, -76.864),
    "Soldier Field": (41.862, -87.617), "Lambeau Field": (44.501, -88.062),
    "Bank of America Stadium": (35.226, -80.853), "Raymond James Stadium": (27.976, -82.503),
    "Levi's Stadium": (37.403, -121.970), "Lumen Field": (47.595, -122.332),
    "State Farm Stadium": (33.528, -112.263), "AT&T Stadium": (32.748, -97.093), "NRG Stadium": (29.685, -95.411),
    "Lucas Oil Stadium": (39.760, -86.164), "Mercedes-Benz Stadium": (33.755, -84.401),
    "SoFi Stadium": (33.953, -118.339),
}


def coords(venue_name):
    return STADIUMS.get((venue_name or "").strip())


def _mph(s):
    nums = [int(x) for x in re.findall(r"\d+", s or "")]
    return max(nums) if nums else None


def forecast(lat, lon, start_iso, points_cache=None):
    """-> {"temp_f", "wind_mph", "precip_pct", "forecast_for", "office"} for the hour containing the start, or None
    when the start is beyond the hourly forecast (about 7 days)."""
    key = f"{lat:.3f},{lon:.3f}"
    hourly = (points_cache or {}).get(key)
    if not hourly:
        p = net.get_json(f"{API}/points/{key}").get("properties") or {}
        hourly = p.get("forecastHourly")
        if points_cache is not None and hourly:
            points_cache[key] = hourly
    if not hourly:
        return None
    periods = (net.get_json(hourly).get("properties") or {}).get("periods") or []
    from ..books.store import dt
    s = dt(start_iso)
    for per in periods:
        a, b = dt(per.get("startTime")), dt(per.get("endTime"))
        if a and b and a <= s < b:
            return {"temp_f": per.get("temperature") if per.get("temperatureUnit") == "F" else None,
                    "wind_mph": _mph(per.get("windSpeed")),
                    "precip_pct": (per.get("probabilityOfPrecipitation") or {}).get("value"),
                    "short": per.get("shortForecast"), "forecast_for": per.get("startTime"),
                    "office": hourly.split("/gridpoints/")[-1].split("/")[0] if "/gridpoints/" in hourly else None}
    return None
