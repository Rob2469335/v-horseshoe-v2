def repair_registry(registry):
    """
    Ensures missing plugins are rehydrated safely.
    """
    required = ["router"]

    for r in required:
        if not registry.get(r):
            registry.register(r, {"status": "auto-recovered"})

    return registry.all()

