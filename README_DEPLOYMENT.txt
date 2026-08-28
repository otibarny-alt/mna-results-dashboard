2027 MNA RESULTS DASHBOARD - V1
===========================================

PURPOSE
-------
A separate Render/GitHub dashboard for the Kobo project:
"2027 GUBERNATORIAL RESULTS TRANSMISSION".

It follows the same visual layout and administration workflow as the existing
Presidential Results Dashboard, but correctly handles the fact that each county
has its own National Assembly candidate list and number of candidates.

SOURCE FORM MAPPING
-------------------
The dashboard reads these Kobo fields:
- electorals_units/selected_county
- electorals_units/selected_constituency
- electorals_units/selected_ward
- electorals_units/selected_poll_station
- electorals_units/selected_stream
- National Assembly_votes/candidate1_votes ... candidate15_votes
- National Assembly_votes/total_valid_votes_cast
- National Assembly_votes/total_rejected_ballots
- National Assembly_votes/total_registered_voters
- National Assembly_votes/uncast_votes
- National Assembly_votes/form_35a

MEDIA FILES EXPECTED IN THE GUBERNATORIAL KOBO PROJECT
-------------------------------------------------------
- county_main.csv
- agents_login.csv
- National Assembly_candidates.csv

National Assembly_candidates.csv must contain:
- county_name
- no_of_candidates
- candidate1_name ... candidate15_name

DYNAMIC CANDIDATE LOGIC
-----------------------
Governor candidates are county-specific, therefore candidate tallies are shown only
after a county is selected.

At National level, reporting progress remains available, but the candidate panel asks
the administrator to select a county.

When a county is selected:
1. The dashboard loads that county's row from National Assembly_candidates.csv.
2. It reads no_of_candidates.
3. It displays ONLY candidate1_name through candidateN_name.
4. It sums the corresponding candidateN_votes fields from Kobo.
5. Constituency and Ward filters retain the same county candidate list while reducing
   the tally to those geographic units.

REPORTING FEATURES
------------------
- National reporting overview
- County reporting overview
- Constituency filter
- Ward filter
- County-specific National Assembly candidate tallies
- Candidate vote chart
- Reporting-progress chart
- Expected / Reported / Pending streams
- Polling Centres Complete / Partial / Not Started
- Reporting Details with:
  County, Constituency, Polling Station Stream, Registered Voters,
  Agent ID, Agent Phone, Status, Submitted
- Recent Results
- Form 35A image viewing
- Admin username/password login
- Manual Refresh

DEPLOYMENT
----------
Create a NEW GitHub repository, for example:
  kenya-National Assembly-results-dashboard

This should be separate from:
- odm-member-photo-verifier
- kenya-presidential-results-dashboard

Upload the CONTENTS of this ZIP to the repository root.

Create a NEW Render Web Service connected to that repository.

Build Command:
  pip install -r requirements.txt

Start Command:
  gunicorn app:app

Required Render Environment Variables:
  KOBO_BASE_URL=https://kf.kobotoolbox.org
  RESULTS_ASSET_UID=<GUBERNATORIAL KOBO ASSET UID>
  KOBO_API_TOKEN=<YOUR KOBO TOKEN>
  COUNTY_MAIN_FILENAME=county_main.csv
  NA_CANDIDATES_FILENAME=National Assembly_candidates.csv
  AGENTS_ASSET_UID=a4VAzs8X6u5bq6eYWVP4o6
  AGENTS_REGISTRATION_FILENAME=agents_registration.csv
  AGENTS_LOGIN_FILENAME=agents_login.csv
  CACHE_SECONDS=60
  FLASK_SECRET_KEY=<LONG RANDOM SECRET>
  AUTH_USERNAME=admin
  AUTH_PASSWORD_HASH=<WERKZEUG PASSWORD HASH>

IMPORTANT
---------
The National Assembly Kobo Asset UID is not stored in the uploaded XML, so you must enter
the National Assembly Results project's actual Asset UID in Render as RESULTS_ASSET_UID.


V2 - AGENT PHONE FIX
--------------------
The National Assembly Results form's agents_login.csv uses the column:
    agent_phone_no

V1 inherited the Presidential dashboard's generic phone-column list and did not
include agent_phone_no. As a result, pending/assigned streams could show the Agent ID
but leave Agent Phone blank.

V2 now:
- Reads agent_phone_no directly from agents_login.csv.
- Still supports phone_no, phone_number, agent_phone and mobile_no.
- For REPORTED streams, continues to prefer basics/phone_number from the actual
  National Assembly-results submission.
- Falls back to the Agents Recruitment contact index when needed.


V3 - SUPPORT FOR UP TO 15 GUBERNATORIAL CANDIDATES
--------------------------------------------------
The Kobo National Assembly Results form now supports candidate1 through candidate15.

Dashboard support now includes:
- no_of_candidates from 1 to 15
- candidate1_name ... candidate15_name
- candidate1_votes ... candidate15_votes

Only the candidates configured for the selected county are displayed and tallied.
County, constituency and ward filters continue to use that county's candidate list.

V4 - TURNOUT / REGISTERED / REJECTED TOTALS
-------------------------------------------
At every dashboard filter scope (National, County, Constituency, Ward, and a
single polling-station stream when filtered), Total Registered Voters is summed
from total_registered_voters in agents_login.csv across all expected streams.

Total Votes Cast = Valid Votes Cast + Rejected Votes reported by agents.
Uncast Votes = Total Registered Voters - Total Votes Cast.
Voter Turnout % = Total Votes Cast / Total Registered Voters * 100.

This means unreported streams still contribute their registered voters to the
denominator, while only submitted results contribute votes cast/rejected.

V5 - FILTER TO POLLING STATION AND STREAM
-----------------------------------------
The dashboard hierarchy is now:
National -> County -> Constituency -> Ward -> Polling Station -> Stream

Polling Station:
- aggregates every stream belonging to the selected polling station
- recalculates registered voters, total votes cast, valid votes, rejected votes,
  uncast votes, turnout, reporting progress and candidate tallies

Stream:
- narrows all dashboard figures to the selected individual polling stream
- reporting details and recent Form 35A results use the same filter scope

The two new filters are cascaded from county_main.csv and require no new Render
environment variables.


NATIONAL ASSEMBLY DASHBOARD V1
------------------------------
This dashboard follows the same reporting, turnout, agent and drill-down requirements
as the latest Gubernatorial dashboard, but National Assembly candidate lists and tallies
are constituency-specific.

Kobo form fields supported:
- national_assembly_votes/candidate1_votes ... candidate20_votes
- national_assembly_votes/total_valid_votes_cast
- national_assembly_votes/total_rejected_ballots
- national_assembly_votes/total_registered_voters
- national_assembly_votes/uncast_votes
- national_assembly_votes/form_35a

Candidate configuration:
- NA_CANDIDATES_FILENAME=na_candidates.csv
- keyed by constituency_name
- expected columns: constituency_name, no_of_candidates,
  candidate1_name ... candidate20_name

Results scope and drill-down:
- County is used to navigate to the correct constituency.
- Candidate tallies begin at Constituency level because National Assembly candidates
  differ by constituency.
- Drill-down: Constituency -> Ward -> Polling Station -> Stream.
- At County/National scope the dashboard can still show turnout and reporting progress,
  but it intentionally does not mix candidate tallies across constituencies.

Turnout and reporting requirements:
- Registered voters are summed from agents_login.csv total_registered_voters for ALL
  expected streams in the selected scope, including streams that have not reported.
- Valid Votes Cast = submitted national_assembly_votes/total_valid_votes_cast.
- Rejected Votes = submitted national_assembly_votes/total_rejected_ballots.
- Total Votes Cast = Valid Votes + Rejected Votes.
- Uncast Votes = Registered Voters - Total Votes Cast.
- Voter Turnout % = Total Votes Cast / Registered Voters * 100.
- Reporting progress shows expected, reported and pending streams plus polling-centre
  completion status.
- Reporting details include agent ID, agent phone, registered voters and status.
- Recent results include secure Form 35A viewing through the dashboard proxy.

Render:
Build Command: pip install -r requirements.txt
Start Command: gunicorn app:app
Required election-specific env values:
RESULTS_ASSET_UID=<NATIONAL_ASSEMBLY_KOBO_ASSET_UID>
NA_CANDIDATES_FILENAME=na_candidates.csv


V2: Reported polling-station streams now include a direct View Form 35A link in Reporting Details. Pending streams show no form link.
