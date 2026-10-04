NEXT = {"red": "green", "green": "yellow", "yellow": "red", "flashing": "flashing"}

def traffic_light(events: list[str]) -> str:
    state = "red"
    for ev in events:
        if ev == "timer":
            state = NEXT[state]
        elif ev == "emergency":
            state = "flashing"
        elif ev == "reset":
            state = "red" if state == "flashing" else state
        else:
            raise ValueError(f"unknown event {ev}")
    return state
