def reconcile_and_repair_tool_call(name, payload, tools=None):
    if name == "ask_user":
        return ("ask_user", payload)

    if name == "__blocked__":
        return ("__blocked__", payload)

    return (name, payload)
