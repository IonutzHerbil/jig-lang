# Decisions

## time

- Context: Appointments, opening hours and calendar slots all handle times of day
- Decision: A time of day is Minute (an int of minutes since midnight); durations are plain int minutes
- Rationale: One unit avoids hour/minute mix-ups; the calendar library speaks plain ints
- Enforcement: show times with clinic.schedule.clock
