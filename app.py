
import os
import io
import csv
import json
import time
import re
from functools import wraps
from urllib.parse import quote, unquote

import requests
from flask import Flask, jsonify, render_template, request, Response, redirect, url_for, session, flash
from werkzeug.security import check_password_hash
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "CHANGE-ME")

KOBO_BASE_URL = os.getenv("KOBO_BASE_URL", "https://kf.kobotoolbox.org").rstrip("/")
RESULTS_ASSET_UID = os.getenv("RESULTS_ASSET_UID", "").strip()
AGENTS_ASSET_UID = os.getenv("AGENTS_ASSET_UID", "a4VAzs8X6u5bq6eYWVP4o6").strip()
AGENTS_REGISTRATION_FILENAME = os.getenv("AGENTS_REGISTRATION_FILENAME", "agents_registration.csv").strip()
AGENTS_LOGIN_FILENAME = os.getenv("AGENTS_LOGIN_FILENAME", "agents_login.csv").strip()
KOBO_TOKEN = os.getenv("KOBO_API_TOKEN", "").strip()

COUNTY_MAIN_FILENAME = os.getenv("COUNTY_MAIN_FILENAME", "county_main.csv").strip()
NA_CANDIDATES_FILENAME = os.getenv("NA_CANDIDATES_FILENAME", "na_candidates.csv").strip()
CACHE_SECONDS = int(os.getenv("CACHE_SECONDS", "60"))

AUTH_USERNAME = os.getenv("AUTH_USERNAME", "").strip()
AUTH_PASSWORD_HASH = os.getenv("AUTH_PASSWORD_HASH", "").strip()

http = requests.Session()
if KOBO_TOKEN:
    http.headers.update({"Authorization": f"Token {KOBO_TOKEN}"})

_cache = {
    "submissions_at": 0.0,
    "submissions": [],
    "county_at": 0.0,
    "county_rows": [],
    "universe": None,
    "agents_at": 0.0,
    "agents_by_id": {},
    "agents_login_at": 0.0,
    "agents_login_rows": [],
    "agent_assignment_index": {},
    "candidate_config_at": 0.0,
    "candidate_config_rows": [],
    "candidate_config_by_constituency": {},
}


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if AUTH_USERNAME and AUTH_PASSWORD_HASH and not session.get("dashboard_user"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required."}), 401
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login():
    if not AUTH_USERNAME or not AUTH_PASSWORD_HASH:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if username == AUTH_USERNAME and check_password_hash(AUTH_PASSWORD_HASH, password):
            session["dashboard_user"] = username
            return redirect(url_for("index"))
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


def friendly(value):
    text = str(value or "").strip()
    if not text:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"[_-]+", " ", text)).title()


def norm_text(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).upper()


def hierarchy_key(value):
    """
    Normalize county_main relationship keys. The source CSV contains some
    references with underscores where the corresponding row name uses hyphens,
    e.g. mukurwe_ini_central -> mukurwe-ini_central.
    """
    text = str(value or "").strip().lower()
    text = re.sub(r"[-_\s]+", " ", text)
    return text.strip()


def get_value(row, path, default=None):
    if path in row:
        return row.get(path, default)
    cur = row
    for part in path.split("/"):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def kobo_paginated(url, params=None):
    results = []
    next_url = url
    first = True
    while next_url:
        r = http.get(next_url, params=params if first else None, timeout=60)
        first = False
        if not r.ok:
            raise RuntimeError(f"Kobo API HTTP {r.status_code}: {r.text[:500]}")
        payload = r.json()
        if isinstance(payload, dict):
            results.extend(payload.get("results", []))
            next_url = payload.get("next")
        elif isinstance(payload, list):
            results.extend(payload)
            next_url = None
        else:
            next_url = None
    return results


def load_submissions(force=False):
    now = time.time()
    if (not force and _cache["submissions"] and now - _cache["submissions_at"] < CACHE_SECONDS):
        return _cache["submissions"]

    url = f"{KOBO_BASE_URL}/api/v2/assets/{RESULTS_ASSET_UID}/data/"
    rows = kobo_paginated(url)

    # Keep only the most recent result for each exact electoral reporting stream.
    # Use the full hierarchy so repeated stream labels in different constituencies
    # or polling stations cannot overwrite each other.
    by_stream = {}
    no_stream = []
    for row in rows:
        stream = str(get_value(row, "electorals_units/selected_stream", "") or "").strip()
        if not stream:
            no_stream.append(row)
            continue
        key = tuple(
            str(get_value(row, path, "") or "").strip()
            for path in (
                "electorals_units/selected_county",
                "electorals_units/selected_constituency",
                "electorals_units/selected_ward",
                "electorals_units/selected_poll_station",
                "electorals_units/selected_stream",
            )
        )
        prev = by_stream.get(key)
        if not prev or str(row.get("_submission_time", "")) > str(prev.get("_submission_time", "")):
            by_stream[key] = row

    result = list(by_stream.values()) + no_stream
    _cache["submissions_at"] = now
    _cache["submissions"] = result
    return result



def find_media_for_asset(asset_uid, filename):
    url = f"{KOBO_BASE_URL}/api/v2/assets/{asset_uid}/files/"
    for item in kobo_paginated(url):
        name = str((item.get("metadata") or {}).get("filename") or "").strip()
        if name.lower() == filename.lower():
            return item
    return None


def load_csv_media(asset_uid, filename):
    media = find_media_for_asset(asset_uid, filename)
    if not media or not media.get("content"):
        return []
    r = http.get(media["content"], timeout=60)
    if not r.ok:
        raise RuntimeError(f"Unable to download {filename} from Kobo (HTTP {r.status_code}).")
    text = r.content.decode("utf-8-sig", errors="replace")
    return [
        {str(k or "").strip(): ("" if v is None else str(v).strip()) for k, v in row.items()}
        for row in csv.DictReader(io.StringIO(text))
    ]


def load_agents_index(force=False):
    """
    Fast National-ID -> contact index built from agents_registration.csv only.

    This deliberately avoids downloading every live Agents Recruitment submission,
    which can be too slow for Render when the recruitment project is large.
    """
    now = time.time()
    if (
        not force
        and _cache["agents_by_id"]
        and now - _cache["agents_at"] < max(CACHE_SECONDS, 300)
    ):
        return _cache["agents_by_id"]

    by_id = {}
    if AGENTS_ASSET_UID:
        try:
            csv_rows = load_csv_media(AGENTS_ASSET_UID, AGENTS_REGISTRATION_FILENAME)
        except Exception:
            csv_rows = []

        for row in csv_rows:
            nid = str(row.get("national_id_no", "") or "").strip()
            if not nid:
                continue
            by_id[nid] = {
                "national_id": nid,
                "phone": str(row.get("phone_no", "") or "").strip(),
                "email": str(row.get("email", "") or "").strip(),
                "source": "agents_registration.csv",
            }

    _cache["agents_at"] = now
    _cache["agents_by_id"] = by_id
    return by_id


def load_agents_login(force=False):
    """
    Load agents_login.csv attached to the National Assembly Results project and build
    a direct stream -> assigned agent index. Building the index once avoids
    scanning the entire file separately for each of ~43,000 streams.
    """
    now = time.time()
    if (
        not force
        and _cache["agents_login_rows"]
        and now - _cache["agents_login_at"] < max(CACHE_SECONDS, 300)
    ):
        return _cache["agents_login_rows"]

    try:
        rows = load_csv_media(RESULTS_ASSET_UID, AGENTS_LOGIN_FILENAME)
    except Exception:
        rows = []

    stream_columns = [
        "poll_station_name",
        "selected_stream",
        "stream",
        "poll_station_stream",
        "polling_station_stream",
        "stream_name",
        "poll_station_stream_name",
        "assigned_stream",
    ]
    id_columns = [
        "agent_id_no", "national_id_no", "agent_national_id", "id_no", "agent_id"
    ]
    phone_columns = [
        "agent_phone_no",
        "phone_no",
        "phone_number",
        "agent_phone",
        "mobile_no",
    ]

    index = {}
    for row in rows:
        stream_value = next(
            (str(row.get(c) or "").strip() for c in stream_columns if str(row.get(c) or "").strip()),
            ""
        )
        if not stream_value:
            continue

        agent_id = next(
            (str(row.get(c) or "").strip() for c in id_columns if str(row.get(c) or "").strip()),
            ""
        )
        direct_phone = next(
            (str(row.get(c) or "").strip() for c in phone_columns if str(row.get(c) or "").strip()),
            ""
        )

        registered_voters = str(
            row.get("total_registered_voters", "")
            or row.get("registered_voters", "")
            or row.get("no_of_registered_voters", "")
            or ""
        ).strip()

        index[norm_text(stream_value)] = {
            "agent_id": agent_id,
            "direct_phone": direct_phone,
            "registered_voters": registered_voters,
        }

    _cache["agents_login_at"] = now
    _cache["agents_login_rows"] = rows
    _cache["agent_assignment_index"] = index
    return rows


def agent_assignment_for_stream(stream_name):
    """Fast O(1) lookup of the assigned agent for a polling stream."""
    load_agents_login()
    assigned = _cache["agent_assignment_index"].get(norm_text(stream_name), {})
    if not assigned:
        return {}

    agent_id = assigned.get("agent_id", "")
    direct_phone = assigned.get("direct_phone", "")
    agent = load_agents_index().get(agent_id, {}) if agent_id else {}

    return {
        "agent_id": agent_id,
        "agent_phone": direct_phone or agent.get("phone", ""),
        "agent_email": agent.get("email", ""),
        "registered_voters": assigned.get("registered_voters", ""),
    }


def agent_contact_for_submission(submission, stream_name):
    """
    Reported stream:
      use National ID and phone_number from the national assembly-result submission first.
      registered voters come from national_assembly_votes/total_registered_voters when present.

    Pending stream:
      use stream assignment from agents_login.csv, including total_registered_voters.
    """
    if submission:
        agent_id = str(get_value(submission, "basics/national_id_no", "") or "").strip()
        result_phone = str(
            get_value(submission, "basics/phone_number", "")
            or get_value(submission, "basics/phone_no", "")
            or ""
        ).strip()
        registered_voters = str(
            get_value(submission, "national_assembly_votes/total_registered_voters", "") or ""
        ).strip()

        assigned = {}
        if not registered_voters:
            try:
                assigned = agent_assignment_for_stream(stream_name)
            except Exception:
                assigned = {}

        agent = load_agents_index().get(agent_id, {}) if agent_id and not result_phone else {}
        return {
            "agent_id": agent_id,
            "agent_phone": result_phone or agent.get("phone", ""),
            "agent_email": agent.get("email", ""),
            "registered_voters": registered_voters or assigned.get("registered_voters", ""),
        }

    return agent_assignment_for_stream(stream_name)


def find_media(filename):
    return find_media_for_asset(RESULTS_ASSET_UID, filename)


def load_county_main(force=False):
    now = time.time()
    if (not force and _cache["county_rows"] and now - _cache["county_at"] < max(CACHE_SECONDS, 300)):
        return _cache["county_rows"]

    media = find_media(COUNTY_MAIN_FILENAME)
    text = None
    if media and media.get("content"):
        r = http.get(media["content"], timeout=60)
        if r.ok:
            text = r.content.decode("utf-8-sig", errors="replace")

    if text is None:
        local = os.path.join(BASE_DIR, COUNTY_MAIN_FILENAME)
        if os.path.exists(local):
            with open(local, "r", encoding="utf-8-sig", errors="replace") as f:
                text = f.read()

    if text is None:
        raise RuntimeError(
            f"Could not load {COUNTY_MAIN_FILENAME} from Kobo media or local fallback."
        )

    rows = [
        {str(k or "").strip(): ("" if v is None else str(v).strip()) for k, v in raw.items()}
        for raw in csv.DictReader(io.StringIO(text))
    ]
    _cache["county_at"] = now
    _cache["county_rows"] = rows
    _cache["universe"] = None
    return rows



def load_candidate_config(force=False):
    """
    Load na_candidates.csv from the National Assembly Results Kobo project.

    Expected columns:
      constituency_name, no_of_candidates,
      candidate1_name ... candidate20_name
    """
    now = time.time()
    if (
        not force
        and _cache["candidate_config_rows"]
        and now - _cache["candidate_config_at"] < max(CACHE_SECONDS, 300)
    ):
        return _cache["candidate_config_rows"]

    rows = load_csv_media(RESULTS_ASSET_UID, NA_CANDIDATES_FILENAME)
    by_constituency = {}

    for row in rows:
        constituency = str(row.get("constituency_name", "") or "").strip()
        if not constituency:
            continue

        try:
            count = int(float(row.get("no_of_candidates", 0) or 0))
        except Exception:
            count = 0
        count = max(0, min(count, 20))

        candidates = []
        for i in range(1, 21):
            name = str(row.get(f"candidate{i}_name", "") or "").strip()
            if i <= count and name:
                candidates.append({"slot": i, "name": name})

        by_constituency[hierarchy_key(constituency)] = {
            "constituency_name": constituency,
            "no_of_candidates": count,
            "candidates": candidates,
        }

    _cache["candidate_config_at"] = now
    _cache["candidate_config_rows"] = rows
    _cache["candidate_config_by_constituency"] = by_constituency
    return rows


def candidate_config_for_constituency(constituency):
    if not constituency:
        return {"constituency_name": "", "no_of_candidates": 0, "candidates": []}

    load_candidate_config()
    config = _cache["candidate_config_by_constituency"].get(hierarchy_key(constituency))
    if config:
        return config

    # county_main may use a coded constituency value while na_candidates.csv
    # uses its human-readable label. Try the label as a fallback.
    universe = build_universe()
    label = universe.get("constituency_labels", {}).get(constituency, "")
    if label:
        config = _cache["candidate_config_by_constituency"].get(hierarchy_key(label))
        if config:
            return config

    return {"constituency_name": constituency, "no_of_candidates": 0, "candidates": []}


def build_universe():
    """
    Build electoral hierarchy from county_main.csv using normalized relationship keys.

    Some county_main references mix hyphens and underscores between a child row's
    *_key value and the parent's name. Exact-string joins therefore leave County /
    Constituency blank for valid streams. hierarchy_key() makes those joins tolerant.
    """
    if _cache["universe"] is not None:
        return _cache["universe"]

    rows = load_county_main()

    county_rows = {}
    constituency_rows = {}
    ward_rows = {}
    station_rows = {}
    stream_rows = {}

    for r in rows:
        typ = r.get("list_name", "")
        name = r.get("name", "")
        if not name:
            continue

        key = hierarchy_key(name)
        if typ == "county":
            county_rows[key] = r
        elif typ == "constituency":
            constituency_rows[key] = r
        elif typ == "ward":
            # A small number of ward names repeat nationally. Keep first matching
            # normalized name; explicit constituency values in result submissions
            # remain authoritative for REPORTED streams.
            ward_rows.setdefault(key, r)
        elif typ == "poll_station":
            station_rows.setdefault(key, r)
        elif typ == "poll_station_stream":
            stream_rows[hierarchy_key(name)] = r

    county_labels = {
        r.get("name", ""): (r.get("label") or friendly(r.get("name", "")))
        for r in county_rows.values()
    }
    constituency_labels = {
        r.get("name", ""): (r.get("label") or friendly(r.get("name", "")))
        for r in constituency_rows.values()
    }
    ward_labels = {
        r.get("name", ""): (r.get("label") or friendly(r.get("name", "")))
        for r in ward_rows.values()
    }
    station_labels = {
        r.get("name", ""): (r.get("label") or friendly(r.get("name", "")))
        for r in station_rows.values()
    }

    streams = {}
    for _, r in stream_rows.items():
        stream_name = r.get("name", "")
        station_ref = r.get("poll_station_key", "")
        station_row = station_rows.get(hierarchy_key(station_ref), {})

        station = station_row.get("name", "") or station_ref

        ward_ref = station_row.get("ward_key", "")
        ward_row = ward_rows.get(hierarchy_key(ward_ref), {})
        ward = ward_row.get("name", "") or ward_ref

        constituency_ref = ward_row.get("constituency_key", "")
        constituency_row = constituency_rows.get(hierarchy_key(constituency_ref), {})
        constituency = constituency_row.get("name", "") or constituency_ref

        county_ref = constituency_row.get("county_key", "")
        county_row = county_rows.get(hierarchy_key(county_ref), {})
        county = county_row.get("name", "") or county_ref

        streams[stream_name] = {
            "stream": stream_name,
            "stream_label": r.get("label", "") or friendly(stream_name),
            "county": county,
            "county_label": county_row.get("label", "") or friendly(county),
            "constituency": constituency,
            "constituency_label": constituency_row.get("label", "") or friendly(constituency),
            "ward": ward,
            "ward_label": ward_row.get("label", "") or friendly(ward),
            "poll_station": station,
            "poll_station_label": station_row.get("label", "") or friendly(station),
        }

    universe = {
        "streams": streams,
        "county_labels": county_labels,
        "constituency_labels": constituency_labels,
        "ward_labels": ward_labels,
        "station_labels": station_labels,
    }
    _cache["universe"] = universe
    return universe


def to_int(value):
    try:
        return int(float(value))
    except Exception:
        return 0


def resolve_submission_geo(row, universe):
    """
    Results submissions contain explicit county/constituency/ward/station/stream
    selections. Prefer those values for reported results; use county_main only
    to supply friendly labels / expected-stream hierarchy.
    """
    stream = str(get_value(row, "electorals_units/selected_stream", "") or "").strip()
    selected_county = str(get_value(row, "electorals_units/selected_county", "") or "").strip()
    selected_constituency = str(get_value(row, "electorals_units/selected_constituency", "") or "").strip()
    selected_ward = str(get_value(row, "electorals_units/selected_ward", "") or "").strip()
    selected_station = str(get_value(row, "electorals_units/selected_poll_station", "") or "").strip()

    known = universe["streams"].get(stream, {})

    county = selected_county or known.get("county", "")
    constituency = selected_constituency or known.get("constituency", "")
    ward = selected_ward or known.get("ward", "")
    station = selected_station or known.get("poll_station", "")

    return {
        "stream": stream,
        "stream_label": known.get("stream_label") or friendly(stream),
        "county": county,
        "county_label": universe["county_labels"].get(county, friendly(county)),
        "constituency": constituency,
        "constituency_label": universe["constituency_labels"].get(
            constituency, friendly(constituency)
        ),
        "ward": ward,
        "ward_label": universe["ward_labels"].get(ward, friendly(ward)),
        "poll_station": station,
        "poll_station_label": universe["station_labels"].get(
            station, friendly(station)
        ),
    }


def filtered_submissions(county="", constituency="", ward="", poll_station="", stream=""):
    universe = build_universe()
    rows = []
    for sub in load_submissions():
        geo = resolve_submission_geo(sub, universe)
        if county and geo["county"] != county:
            continue
        if constituency and geo["constituency"] != constituency:
            continue
        if ward and geo["ward"] != ward:
            continue
        if poll_station and geo["poll_station"] != poll_station:
            continue
        if stream and geo["stream"] != stream:
            continue
        rows.append((sub, geo))
    return rows


def expected_streams(county="", constituency="", ward="", poll_station="", stream=""):
    universe = build_universe()
    vals = []
    for s in universe["streams"].values():
        if county and s["county"] != county:
            continue
        if constituency and s["constituency"] != constituency:
            continue
        if ward and s["ward"] != ward:
            continue
        if poll_station and s["poll_station"] != poll_station:
            continue
        if stream and s["stream"] != stream:
            continue
        vals.append(s)
    return vals


def summary_payload(county="", constituency="", ward="", poll_station="", stream=""):
    subs = filtered_submissions(county, constituency, ward, poll_station, stream)
    expected = expected_streams(county, constituency, ward, poll_station, stream)
    expected_ids = {s["stream"] for s in expected}
    reported_ids = {geo["stream"] for _, geo in subs if geo["stream"]}
    reported_expected = expected_ids & reported_ids

    # Registered voters are the electoral denominator and must come from
    # agents_login.csv for ALL expected streams, including streams that have
    # not reported yet. Votes cast and rejected ballots come from submitted
    # national assembly results.
    total_valid = rejected = 0
    for row, _ in subs:
        total_valid += to_int(get_value(row, "national_assembly_votes/total_valid_votes_cast", 0))
        rejected += to_int(get_value(row, "national_assembly_votes/total_rejected_ballots", 0))

    registered = 0
    for stream in expected:
        assignment = agent_assignment_for_stream(stream["stream"])
        registered += to_int(assignment.get("registered_voters", 0))

    # A cast ballot is either a valid vote or a rejected ballot.
    total_votes_cast = total_valid + rejected
    uncast = max(0, registered - total_votes_cast)
    turnout_percent = round(total_votes_cast / registered * 100, 2) if registered else 0
    uncast_percent = round(uncast / registered * 100, 2) if registered else 0

    # National Assembly candidates are constituency-specific. Candidate tallies
    # are calculated only after a constituency has been selected.
    candidates = []
    candidate_series = []
    candidate_config = candidate_config_for_constituency(constituency) if constituency else {
        "constituency_name": "",
        "no_of_candidates": 0,
        "candidates": [],
    }

    if constituency:
        candidate_totals = {}
        for item in candidate_config["candidates"]:
            candidate_totals[item["slot"]] = 0

        for row, _ in subs:
            for item in candidate_config["candidates"]:
                slot = item["slot"]
                candidate_totals[slot] += to_int(
                    get_value(row, f"national_assembly_votes/candidate{slot}_votes", 0)
                )

        grand_candidate_votes = sum(candidate_totals.values())
        for item in candidate_config["candidates"]:
            votes = candidate_totals.get(item["slot"], 0)
            candidates.append({
                "slot": item["slot"],
                "candidate": item["name"],
                "votes": votes,
                "share": round(votes / grand_candidate_votes * 100, 2)
                         if grand_candidate_votes else 0,
            })

        candidates.sort(key=lambda x: (-x["votes"], x["candidate"]))
        candidate_series = [
            {
                "candidate": item["name"],
                "votes": candidate_totals.get(item["slot"], 0),
            }
            for item in candidate_config["candidates"]
        ]

    # Polling-centre completion: all expected streams for centre reported.
    station_expected = {}
    for s in expected:
        station_expected.setdefault(s["poll_station"], set()).add(s["stream"])
    complete = partial = not_started = 0
    for _, stream_ids in station_expected.items():
        got = len(stream_ids & reported_ids)
        if got == 0:
            not_started += 1
        elif got == len(stream_ids):
            complete += 1
        else:
            partial += 1

    return {
        "filters": {
            "county": county,
            "constituency": constituency,
            "ward": ward,
            "poll_station": poll_station,
            "stream": stream,
        },
        "candidate_scope": "constituency" if constituency else "select_constituency",
        "candidate_config": {
            "constituency_name": candidate_config.get("constituency_name", ""),
            "no_of_candidates": candidate_config.get("no_of_candidates", 0),
        },
        "candidates": candidates,
        "candidate_series": candidate_series,
        "totals": {
            "registered_voters": registered,
            "valid_votes": total_valid,
            "rejected_ballots": rejected,
            "total_votes_cast": total_votes_cast,
            "uncast_votes": uncast,
            "turnout_percent": turnout_percent,
            "uncast_percent": uncast_percent,
        },
        "reporting": {
            "expected_streams": len(expected_ids),
            "reported_streams": len(reported_expected),
            "pending_streams": max(0, len(expected_ids) - len(reported_expected)),
            "reporting_percent": round(
                len(reported_expected) / len(expected_ids) * 100, 2
            ) if expected_ids else 0,
            "polling_centres_complete": complete,
            "polling_centres_partial": partial,
            "polling_centres_not_started": not_started,
        },
        "last_updated": max(
            [str(r.get("_submission_time", "")) for r, _ in subs],
            default=""
        ),
    }


@app.get("/")
@login_required
def index():
    return render_template("index.html")


@app.get("/api/summary")
@login_required
def api_summary():
    county = request.args.get("county", "").strip()
    constituency = request.args.get("constituency", "").strip()
    ward = request.args.get("ward", "").strip()
    poll_station = request.args.get("poll_station", "").strip()
    stream = request.args.get("stream", "").strip()
    try:
        return jsonify(summary_payload(county, constituency, ward, poll_station, stream))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500



@app.get("/api/hierarchy-check")
@login_required
def api_hierarchy_check():
    """Small diagnostic to verify county_main parent relationships."""
    u = build_universe()
    county = request.args.get("county", "").strip()
    constituency = request.args.get("constituency", "").strip()

    expected = expected_streams(county, constituency)
    submitted = filtered_submissions(county, constituency)

    sample_expected = expected[:5]
    sample_submitted = [
        {
            "stream": geo["stream"],
            "county": geo["county"],
            "constituency": geo["constituency"],
            "poll_station": geo["poll_station"],
            "submission_time": str(row.get("_submission_time", "")),
        }
        for row, geo in submitted[:5]
    ]

    return jsonify({
        "county": county,
        "constituency": constituency,
        "expected_streams": len(expected),
        "submitted_streams": len(submitted),
        "sample_expected": sample_expected,
        "sample_submitted": sample_submitted,
    })


@app.get("/api/counties")
@login_required
def api_counties():
    u = build_universe()
    counties = sorted(
        [{"value": k, "label": v} for k, v in u["county_labels"].items()],
        key=lambda x: x["label"]
    )
    return jsonify(counties)


@app.get("/api/constituencies")
@login_required
def api_constituencies():
    county = request.args.get("county", "").strip()
    seen = {}
    for s in expected_streams(county=county):
        seen[s["constituency"]] = s["constituency_label"]
    vals = sorted([{"value": k, "label": v} for k, v in seen.items()], key=lambda x: x["label"])
    return jsonify(vals)



@app.get("/api/reporting-core-check")
@login_required
def api_reporting_core_check():
    county = request.args.get("county", "").strip()
    constituency = request.args.get("constituency", "").strip()
    ward = request.args.get("ward", "").strip()
    expected = expected_streams(county, constituency, ward)
    return jsonify({
        "ok": True,
        "expected_streams": len(expected),
        "sample": expected[:3],
    })



@app.get("/api/wards")
@login_required
def api_wards():
    county = request.args.get("county", "").strip()
    constituency = request.args.get("constituency", "").strip()
    seen = {}
    for s in expected_streams(county=county, constituency=constituency):
        if s["ward"]:
            seen[s["ward"]] = s["ward_label"]
    vals = sorted(
        [{"value": k, "label": v} for k, v in seen.items()],
        key=lambda x: x["label"]
    )
    return jsonify(vals)


@app.get("/api/polling-stations")
@login_required
def api_polling_stations():
    county = request.args.get("county", "").strip()
    constituency = request.args.get("constituency", "").strip()
    ward = request.args.get("ward", "").strip()
    seen = {}
    for s in expected_streams(county=county, constituency=constituency, ward=ward):
        if s["poll_station"]:
            seen[s["poll_station"]] = s["poll_station_label"]
    vals = sorted(
        [{"value": k, "label": v} for k, v in seen.items()],
        key=lambda x: x["label"]
    )
    return jsonify(vals)


@app.get("/api/streams")
@login_required
def api_streams():
    county = request.args.get("county", "").strip()
    constituency = request.args.get("constituency", "").strip()
    ward = request.args.get("ward", "").strip()
    poll_station = request.args.get("poll_station", "").strip()
    vals = [
        {"value": s["stream"], "label": s["stream_label"]}
        for s in expected_streams(
            county=county,
            constituency=constituency,
            ward=ward,
            poll_station=poll_station,
        )
        if s["stream"]
    ]
    vals.sort(key=lambda x: x["label"])
    return jsonify(vals)


@app.get("/api/reporting-details")
@login_required
def api_reporting_details():
    county = request.args.get("county", "").strip()
    constituency = request.args.get("constituency", "").strip()
    ward = request.args.get("ward", "").strip()
    poll_station = request.args.get("poll_station", "").strip()
    stream = request.args.get("stream", "").strip()
    status_filter = request.args.get("status", "").strip().upper()
    search = request.args.get("search", "").strip().upper()

    try:
        page = max(1, int(request.args.get("page", "1")))
    except Exception:
        page = 1
    try:
        page_size = int(request.args.get("page_size", "200"))
    except Exception:
        page_size = 200
    page_size = max(25, min(page_size, 500))

    expected = expected_streams(county, constituency, ward, poll_station, stream)
    reported = {
        geo["stream"]: (row, geo)
        for row, geo in filtered_submissions(county, constituency, ward, poll_station, stream)
        if geo["stream"]
    }

    base_rows = []
    for geo in expected:
        stream = geo["stream"]
        sub = reported.get(stream)
        status = "REPORTED" if sub else "PENDING"

        if status_filter in {"REPORTED", "PENDING"} and status != status_filter:
            continue

        if search:
            haystack = " | ".join([
                str(geo.get("county_label", "")),
                str(geo.get("constituency_label", "")),
                str(geo.get("poll_station_label", "")),
                str(geo.get("stream_label", "")),
            ]).upper()
            if search not in haystack:
                continue

        base_rows.append({
            **geo,
            "status": status,
            "submission_time": str(sub[0].get("_submission_time", "")) if sub else "",
        })

    base_rows.sort(
        key=lambda x: (
            x.get("county_label", ""),
            x.get("constituency_label", ""),
            x.get("poll_station_label", ""),
            x.get("stream_label", ""),
        )
    )

    total_rows = len(base_rows)
    total_pages = max(1, (total_rows + page_size - 1) // page_size)
    page = min(page, total_pages)
    start_idx = (page - 1) * page_size
    page_rows = base_rows[start_idx:start_idx + page_size]

    # Contacts are supplementary. Load only CSV indexes; never let them block the table.
    if page_rows:
        try:
            load_agents_index()
        except Exception:
            app.logger.exception("Unable to load agents_registration.csv contact index")
        try:
            load_agents_login()
        except Exception:
            app.logger.exception("Unable to load agents_login.csv assignment index")

    for item in page_rows:
        stream = item["stream"]
        sub = reported.get(stream)
        contact = {}
        try:
            contact = agent_contact_for_submission(sub[0] if sub else None, stream)
        except Exception:
            app.logger.exception("Contact lookup failed for stream %s", stream)

        item["agent_id"] = contact.get("agent_id", "")
        item["agent_phone"] = contact.get("agent_phone", "")
        item["agent_email"] = contact.get("agent_email", "")
        item["registered_voters"] = contact.get("registered_voters", "")
        # Polling station name is redundant because stream_label already contains it.
        item.pop("poll_station_label", None)
        item.pop("poll_station", None)

    return jsonify({
        "rows": page_rows,
        "page": page,
        "page_size": page_size,
        "total_rows": total_rows,
        "total_pages": total_pages,
    })



def attachment_for_field(submission, field_path):
    value = str(get_value(submission, field_path, "") or "").strip()
    if not value:
        return None
    target = value.replace("\\", "/").split("/")[-1].lower()
    for a in submission.get("_attachments") or []:
        fn = str(a.get("filename") or "").replace("\\", "/").split("/")[-1].lower()
        if fn == target or (target and target in fn):
            return a
    return None


@app.get("/api/recent-results")
@login_required
def api_recent_results():
    county = request.args.get("county", "").strip()
    constituency = request.args.get("constituency", "").strip()
    ward = request.args.get("ward", "").strip()
    poll_station = request.args.get("poll_station", "").strip()
    stream = request.args.get("stream", "").strip()
    limit = min(max(int(request.args.get("limit", "25")), 1), 100)
    rows = sorted(
        filtered_submissions(county, constituency, ward, poll_station, stream),
        key=lambda x: str(x[0].get("_submission_time", "")),
        reverse=True
    )[:limit]

    result = []
    for sub, geo in rows:
        att = attachment_for_field(sub, "national_assembly_votes/form_35a")
        result.append({
            "submission_time": str(sub.get("_submission_time", "")),
            "national_id": str(get_value(sub, "basics/national_id_no", "") or ""),
            **geo,
            "form35a": bool(att and att.get("download_url")),
            "form35a_url": "/api/form35a?url=" + quote(att["download_url"], safe="") if att and att.get("download_url") else None,
        })
    return jsonify(result)


@app.get("/api/form35a")
@login_required
def proxy_form35a():
    url = unquote(request.args.get("url", ""))
    if not url or not url.startswith(KOBO_BASE_URL + "/"):
        return Response("Invalid Kobo media URL.", status=400)
    r = http.get(url, timeout=60)
    if not r.ok:
        return Response("Unable to retrieve Form 35A.", status=502)
    return Response(r.content, mimetype=r.headers.get("Content-Type", "image/jpeg"))


@app.post("/api/refresh")
@login_required
def api_refresh():
    try:
        load_submissions(force=True)
        load_county_main(force=True)
        load_agents_index(force=True)
        load_agents_login(force=True)
        load_candidate_config(force=True)
        build_universe()
        return jsonify({"success": True})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "results_asset_uid": RESULTS_ASSET_UID,
        "agents_asset_uid": AGENTS_ASSET_UID,
        "kobo_configured": bool(KOBO_TOKEN and RESULTS_ASSET_UID),
        "county_main_filename": COUNTY_MAIN_FILENAME,
        "na_candidates_filename": NA_CANDIDATES_FILENAME,
        "agents_login_filename": AGENTS_LOGIN_FILENAME,
        "agents_registration_filename": AGENTS_REGISTRATION_FILENAME,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
