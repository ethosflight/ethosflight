from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from app.database import get_connection
from app.models import FlightEvent, Alert, WeatherObservation
from datetime import datetime
import requests

app = FastAPI(
    title="EthosFlight API",
    description="AI Safety Operating Layer for Aviation",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"status": "EthosFlight is live", "version": "1.0.0"}

@app.post("/events/")
def create_event(event: FlightEvent):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO flight_events 
        (flight_id, aircraft_type, origin, destination, 
         latitude, longitude, altitude, speed, event_type, event_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, event.flight_id, event.aircraft_type, event.origin,
        event.destination, event.latitude, event.longitude,
        event.altitude, event.speed, event.event_type, event.event_data)
    conn.commit()
    return {"message": "Event logged successfully"}

@app.get("/alerts/")
def get_alerts():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM alerts ORDER BY created_at DESC")
    rows = cursor.fetchall()
    return {"alerts": [dict(zip([col[0] for col in cursor.description], row)) for row in rows]}

@app.get("/weather/fetch/{station_id}")
def fetch_weather(station_id: str):
    url = f"https://aviationweather.gov/api/data/metar?ids={station_id}&format=json"
    response = requests.get(url)
    if response.status_code != 200:
        raise HTTPException(status_code=400, detail="Weather fetch failed")
    
    data = response.json()
    if not data:
        raise HTTPException(status_code=404, detail="No weather data found")
    
    obs = data[0]
    
    def to_float(val):
        try: return float(val)
        except: return None
    
    def to_int(val):
        try: return int(val)
        except: return None

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO weather_observations
        (station_id, temperature, wind_speed, wind_direction, visibility, conditions, observed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, 
        station_id,
        to_float(obs.get("temp")),
        to_int(obs.get("wspd")),
        to_int(obs.get("wdir")),
        to_float(obs.get("visib")),
        str(obs.get("wxString", "")) or None,
        datetime.utcnow()
    )
    conn.commit()
    return {"message": f"Weather for {station_id} stored", "data": obs}

@app.get("/sentinel/analyze/{station_id}")
def sentinel_analyze(station_id: str):
    url = f"https://aviationweather.gov/api/data/metar?ids={station_id}&format=json"
    response = requests.get(url)
    if response.status_code != 200:
        raise HTTPException(status_code=400, detail="Weather fetch failed")
    
    data = response.json()
    if not data:
        raise HTTPException(status_code=404, detail="No data found")
    
    obs = data[0]
    alerts_fired = []
    conn = get_connection()
    cursor = conn.cursor()

    # First log the flight event
    cursor.execute("""
        INSERT INTO flight_events (flight_id, event_type, event_data, latitude, longitude)
        VALUES (?, ?, ?, ?, ?)
    """, station_id, 'SENTINEL_SCAN', str(obs), obs.get('lat'), obs.get('lon'))
    conn.commit()

    cursor.execute("SELECT @@IDENTITY")
    event_id = int(cursor.fetchone()[0])

    # Rule 1: Low visibility
    try:
        vis = float(str(obs.get('visib', '5')).replace('+', ''))
        if vis < 3:
            severity = 'WARNING' if vis < 1 else 'CAUTION'
            msg = f"Low visibility at {station_id}: {vis} SM"
            cursor.execute("""
                INSERT INTO alerts (event_id, alert_type, severity, message, engine)
                VALUES (?, ?, ?, ?, ?)
            """, event_id, 'VISIBILITY', severity, msg, 'SENTINEL')
            alerts_fired.append({"type": "VISIBILITY", "severity": severity, "message": msg})
    except: pass

    # Rule 2: High winds
    try:
        wspd = int(obs.get('wspd', 0) or 0)
        wgst = int(obs.get('wgst', 0) or 0)
        if wspd > 25 or wgst > 35:
            severity = 'WARNING' if wgst > 45 else 'CAUTION'
            msg = f"High winds at {station_id}: {wspd}kt wind, {wgst}kt gusts"
            cursor.execute("""
                INSERT INTO alerts (event_id, alert_type, severity, message, engine)
                VALUES (?, ?, ?, ?, ?)
            """, event_id, 'WIND', severity, msg, 'SENTINEL')
            alerts_fired.append({"type": "WIND", "severity": severity, "message": msg})
    except: pass

    # Rule 3: IFR conditions
    if obs.get('fltCat') in ['IFR', 'LIFR']:
        severity = 'WARNING' if obs.get('fltCat') == 'LIFR' else 'CAUTION'
        msg = f"{obs.get('fltCat')} conditions at {station_id}"
        cursor.execute("""
            INSERT INTO alerts (event_id, alert_type, severity, message, engine)
            VALUES (?, ?, ?, ?, ?)
        """, event_id, 'FLIGHT_CAT', severity, msg, 'SENTINEL')
        alerts_fired.append({"type": "FLIGHT_CAT", "severity": severity, "message": msg})

    # GUARDIAN — log the scan
    cursor.execute("""
        INSERT INTO audit_log (event_id, action, actor, details)
        VALUES (?, ?, ?, ?)
    """, event_id, 'SENTINEL_SCAN', 'SENTINEL_ENGINE',
        f"Scanned {station_id} — {len(alerts_fired)} alerts fired — fltCat: {obs.get('fltCat')}")

    conn.commit()
    conn.close()

    return {
        "station": station_id,
        "flight_category": obs.get('fltCat'),
        "alerts_fired": len(alerts_fired),
        "alerts": alerts_fired,
        "raw": obs
    }
@app.get("/guardian/log")
def get_audit_log():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM audit_log ORDER BY logged_at DESC")
    rows = cursor.fetchall()
    return {"logs": [dict(zip([col[0] for col in cursor.description], row)) for row in rows]}

@app.post("/guardian/log-action")
def log_action(event_id: int, action: str, actor: str, details: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO audit_log (event_id, action, actor, details)
        VALUES (?, ?, ?, ?)
    """, event_id, action, actor, details)
    conn.commit()
    conn.close()
    return {"message": "Action logged to GUARDIAN"}

# ─── PILOT LAYER ───

@app.post("/pilot/register")
def register_pilot(full_name: str, certificate_number: str, certificate_type: str, email: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO pilots (full_name, certificate_number, certificate_type, email)
        VALUES (?, ?, ?, ?)
    """, full_name, certificate_number, certificate_type, email)
    conn.commit()
    cursor.execute("SELECT @@IDENTITY")
    pilot_id = int(cursor.fetchone()[0])
    conn.close()
    return {"message": "Pilot registered", "pilot_id": pilot_id}

@app.post("/pilot/flight-log")
def add_flight_log(pilot_id: int, flight_date: str, aircraft_type: str,
                   aircraft_ident: str, origin: str, destination: str,
                   total_time: float, pic_time: float = 0, night_time: float = 0,
                   instrument_time: float = 0, day_landings: int = 0,
                   night_landings: int = 0, remarks: str = ""):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO flight_log (pilot_id, flight_date, aircraft_type, aircraft_ident,
        origin, destination, total_time, pic_time, night_time, instrument_time,
        day_landings, night_landings, remarks)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, pilot_id, flight_date, aircraft_type, aircraft_ident, origin, destination,
        total_time, pic_time, night_time, instrument_time, day_landings, night_landings, remarks)
    conn.commit()
    conn.close()
    return {"message": "Flight logged successfully"}

@app.post("/pilot/medical")
def add_medical(pilot_id: int, medical_class: int, issue_date: str, expiry_date: str, examiner: str = ""):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO medicals (pilot_id, medical_class, issue_date, expiry_date, examiner)
        VALUES (?, ?, ?, ?, ?)
    """, pilot_id, medical_class, issue_date, expiry_date, examiner)
    conn.commit()
    conn.close()
    return {"message": "Medical certificate logged"}

@app.post("/pilot/endorsement")
def add_endorsement(pilot_id: int, endorsement_type: str, issue_date: str,
                    expiry_date: str = None, endorsing_instructor: str = "", notes: str = ""):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO endorsements (pilot_id, endorsement_type, issue_date, expiry_date, endorsing_instructor, notes)
        VALUES (?, ?, ?, ?, ?, ?)
    """, pilot_id, endorsement_type, issue_date, expiry_date, endorsing_instructor, notes)
    conn.commit()
    conn.close()
    return {"message": "Endorsement logged"}

@app.get("/pilot/{pilot_id}/status")
def pilot_status(pilot_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    from datetime import date, timedelta
    today = date.today()
    
    # Medical status
    cursor.execute("SELECT * FROM medicals WHERE pilot_id=? ORDER BY expiry_date DESC", pilot_id)
    medical = cursor.fetchone()
    medical_status = None
    if medical:
        expiry = medical[4]
        days_left = (expiry - today).days
        medical_status = {
            "class": medical[2],
            "expiry": str(expiry),
            "days_remaining": days_left,
            "status": "EXPIRED" if days_left < 0 else "WARNING" if days_left < 30 else "CURRENT"
        }

    # Flight totals
    cursor.execute("""
        SELECT SUM(total_time), SUM(pic_time), SUM(night_time),
               SUM(instrument_time), SUM(day_landings), SUM(night_landings)
        FROM flight_log WHERE pilot_id=?
    """, pilot_id)
    totals = cursor.fetchone()

    # Currency — last 90 days landings
    cursor.execute("""
        SELECT SUM(day_landings + night_landings)
        FROM flight_log WHERE pilot_id=? AND flight_date >= ?
    """, pilot_id, str(today - timedelta(days=90)))
    recent_landings = cursor.fetchone()[0] or 0

    # Endorsements expiring soon
    cursor.execute("""
        SELECT endorsement_type, expiry_date FROM endorsements
        WHERE pilot_id=? AND expiry_date IS NOT NULL AND expiry_date <= ?
    """, pilot_id, str(today + timedelta(days=60)))
    expiring = cursor.fetchall()

    conn.close()
    return {
        "pilot_id": pilot_id,
        "medical": medical_status,
        "flight_totals": {
            "total_time": float(totals[0] or 0),
            "pic_time": float(totals[1] or 0),
            "night_time": float(totals[2] or 0),
            "instrument_time": float(totals[3] or 0),
            "day_landings": int(totals[4] or 0),
            "night_landings": int(totals[5] or 0)
        },
        "currency": {
            "landings_last_90_days": int(recent_landings),
            "passenger_currency": "CURRENT" if recent_landings >= 3 else "NOT CURRENT"
        },
        "endorsements_expiring_soon": [
            {"type": e[0], "expiry": str(e[1])} for e in expiring
        ]
    }