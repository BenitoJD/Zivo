from app.eta.scheduler_registry import eta_scheduler, get_scheduler_definition


def test_eta_scheduler_registry_defaults_to_module_function_key():
    @eta_scheduler(every_minutes=10)
    def sample_schedule():
        return None

    schedule_key = f"{sample_schedule.__module__}.{sample_schedule.__name__}"
    registered = get_scheduler_definition(schedule_key)
    assert registered is not None
    assert registered.every_minutes == 10


def test_eta_scheduler_registry_supports_custom_key():
    @eta_scheduler(key="custom.schedule", every_minutes=5)
    def custom_schedule():
        return None

    registered = get_scheduler_definition("custom.schedule")
    assert registered is not None
    assert registered.key == "custom.schedule"
    assert registered.fn is custom_schedule
