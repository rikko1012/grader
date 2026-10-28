from sports.model import fwd


def test_forecast_address_space_tracks_plan_without_changing_rss_guard():
    assert fwd.vm_cap_mb({"SPORTS_MEMORY_MB": "512"}) == 640
    assert fwd.vm_cap_mb({"SPORTS_MEMORY_MB": "2048"}) == 1280
    assert fwd.vm_cap_mb({"SPORTS_MEMORY_MB": "2048", "SPORTS_FORECAST_MAX_VM_MB": "900"}) == 900
