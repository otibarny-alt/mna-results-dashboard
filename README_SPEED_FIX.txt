MEMBER OF NATIONAL ASSEMBLY LIVE DATA ARCHITECTURE

- Page HTML loads without waiting for the Voting Simulation service.
- Browser makes one summary request at a time.
- Dashboard server caches the upstream MNA snapshot briefly.
- Voting Simulation exposes /api/dashboard/mna using the anonymous dashboard event mirror.
- Existing unmirrored local simulation votes are caught up automatically.
- No direct PostgreSQL connection is required in the dashboard service.
