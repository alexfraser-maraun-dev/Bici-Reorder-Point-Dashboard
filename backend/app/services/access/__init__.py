"""Feature access control: which surfaces exist, which are switched on, and who
may see them.

`registry` declares every gateable surface once. `service` resolves the effective
answer for a caller, and `router` exposes it to the Admin page. `main` mounts a
middleware that refuses API calls belonging to a switched-off feature, so a
dormant feature costs nothing even if a stale browser tab asks for it.
"""
