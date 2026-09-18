2027 MEMBER OF NATIONAL ASSEMBLY SIMULATION RESULTS DASHBOARD — FRESH V1

This is a separate TRAINING / SIMULATION ONLY dashboard for member of national assembly results.

DEPLOYMENT
1. Create a new GitHub repository for the member of national assembly dashboard.
2. Upload all files from this folder to the repository root.
3. Create a new Render Web Service using Python.
4. Build command: pip install -r requirements.txt
5. Start command: gunicorn app:app --workers 2 --threads 2 --timeout 60

ENVIRONMENT VARIABLES
FLASK_SECRET_KEY=<long random secret>
SIMULATION_BASE_URL=https://YOUR-VOTING-SIMULATION.onrender.com
SIMULATION_DASHBOARD_API_KEY=<same value as DASHBOARD_API_KEY on the Voting Simulation>
SIMULATION_DASHBOARD_PATH=/api/dashboard/mna
AUTH_USERNAME=admin
AUTH_PASSWORD_HASH=<Werkzeug-compatible password hash>
CACHE_SECONDS=10
UPSTREAM_TIMEOUT_SECONDS=30

Optional filenames:
COUNTY_MAIN_FILENAME=county_main.csv
AGENTS_LOGIN_FILENAME=agents_login.csv

The dashboard server calls the Voting Simulation MNA feed: /api/dashboard/mna
No direct DATABASE_URL is required for this dashboard.

V4 FAST STREAM SUBMISSION FILTER
- Adds a paginated Polling Station Stream Submission Status section.
- Filters: All Streams, Closed & Submitted, Not Yet Submitted.
- Uses the same cached member of national assembly snapshot already loaded by the dashboard, so it does not create a second upstream call during normal loading.
- Uses local county_main.csv hierarchy to classify every expected stream, including streams that have never opened.
- Page size is 100 streams to keep browser rendering fast.
- No new environment variables are required.

V6 EMAIL RESULTS ADDITION
- Adds Email Results beside Print Results.
- Generates and attaches a Member of National Assembly Simulation Results PDF.
- Shows each candidate's county and registered constituency in the dashboard table, chart, printout, PDF and email body.
- Configure the SMTP variables shown in .env.example on the dashboard service.

MNA CONSTITUENCY RESTRICTION
- Each MNA candidate is displayed only within the constituency where the candidate is registered.
- A candidate never appears as a zero-vote candidate in another constituency.
- Selecting a ward does not remove the registered constituency's MNA candidates because the office is constituency-wide.
