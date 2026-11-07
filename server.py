import asyncio
import json
import re
import socket
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles


# ----------------------------
# Config
# ----------------------------

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"

DEFAULT_CONFIG = {
    "n3fjp_host": "127.0.0.1",
    "n3fjp_port": 1100,
    "seed_count": 5000,
    "tail_count": 80,
    "refresh_seconds": 3,
    "web_host": "0.0.0.0",
    "web_port": 8080,
}


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return {**DEFAULT_CONFIG, **json.loads(CONFIG_PATH.read_text(encoding="utf-8"))}
        except Exception:
            return DEFAULT_CONFIG.copy()
    return DEFAULT_CONFIG.copy()


# ----------------------------
# N3FJP TCP API helpers
# ----------------------------

def n3fjp_cmd(
    host: str,
    port: int,
    cmd: str,
    total_timeout: float = 5.0,
    idle_timeout: float = 0.35,
) -> bytes:
    """
    Send cmd + CRLF, then read until:
      - total_timeout expires, OR
      - no data arrives for idle_timeout (after receiving at least 1 chunk)
    Returns raw bytes.
    """
    payload = (cmd.strip() + "\r\n").encode("utf-8", errors="ignore")

    chunks: List[bytes] = []
    start = time.time()
    last_data = start

    with socket.create_connection((host, port), timeout=total_timeout) as s:
        s.settimeout(idle_timeout)
        s.sendall(payload)

        while True:
            now = time.time()
            if now - start >= total_timeout:
                break
            if chunks and (now - last_data) >= idle_timeout:
                break

            try:
                data = s.recv(65535)
                if not data:
                    break
                chunks.append(data)
                last_data = time.time()
            except socket.timeout:
                continue

    return b"".join(chunks)


_TAG_RE = re.compile(r"<([A-Z0-9_]+)>(.*?)</\1>", flags=re.DOTALL | re.IGNORECASE)

def _parse_tags(block: str) -> Dict[str, str]:
    rec: Dict[str, str] = {}
    for tag, val in _TAG_RE.findall(block or ""):
        tag_u = (tag or "").upper().strip()
        if not tag_u or tag_u in ("CMD", "LISTRESPONSE"):
            continue
        rec[tag_u] = (val or "").strip()
    return rec


def parse_cmd_records(text: str) -> List[Dict[str, str]]:
    """
    Hybrid parser for N3FJP LIST responses.

    Handles BOTH formats:
      A) <LISTRESPONSE> ... </LISTRESPONSE>  (block/closed)
      B) <LISTRESPONSE><TAG>..</TAG>...<LISTRESPONSE><TAG>..</TAG>... (marker-per-record, NO closing tags)

    Returns list of dicts with UPPERCASE keys.
    """
    records: List[Dict[str, str]] = []
    if not text:
        return records

    # --- Format A: closed blocks ---
    if re.search(r"</\s*LISTRESPONSE\s*>", text, flags=re.IGNORECASE):
        blocks = re.findall(r"<LISTRESPONSE>(.*?)</LISTRESPONSE>", text, flags=re.DOTALL | re.IGNORECASE)
        for blk in blocks:
            rec = _parse_tags(blk)
            if rec:
                records.append(rec)
        return records

    # --- Format B: repeated <LISTRESPONSE> markers (no close tag) ---
    # Split on the marker, each chunk is one record-ish region until next marker.
    parts = re.split(r"<\s*LISTRESPONSE\s*>", text, flags=re.IGNORECASE)
    # parts[0] is preamble; actual record chunks start at 1
    for chunk in parts[1:]:
        rec = _parse_tags(chunk)
        if rec:
            records.append(rec)

    return records


def points_from_modetest(modetest: str) -> int:
    mt = (modetest or "").upper()
    if mt == "PH":
        return 1
    if mt in ("CW", "DIG"):
        return 2
    return 0


def build_list_cmd(n: int, include_all: bool = True) -> str:
    """
    IMPORTANT: Close the LIST tag. Missing </LIST> can cause partial/empty responses.
    """
    if include_all:
        return f"<CMD><LIST><INCLUDEALL><VALUE>{int(n)}</VALUE></LIST></CMD>"
    return f"<CMD><LIST><VALUE>{int(n)}</VALUE></LIST></CMD>"


# ----------------------------
# Aggregation state
# ----------------------------

@dataclass
class Aggregates:
    seen_keys: Set[int] = field(default_factory=set)
    total_contacts: int = 0
    total_points: int = 0

    contacts_by_operator: Dict[str, int] = field(default_factory=dict)
    points_by_operator: Dict[str, int] = field(default_factory=dict)

    contacts_by_mode: Dict[str, int] = field(default_factory=dict)
    contacts_by_band: Dict[str, int] = field(default_factory=dict)
    contacts_by_continent: Dict[str, int] = field(default_factory=dict)
    contacts_by_state: Dict[str, int] = field(default_factory=dict)
    contacts_by_country: Dict[str, int] = field(default_factory=dict)
    
    # For Field Day multipliers
    sections_worked: Set[str] = field(default_factory=set)
    
    # Track stations (physical locations like "Little House", "Comm Trailer")
    # Format: { "stationName": { "operator": "callsign", "band": "20M", "mode": "SSB", "recent": [...] } }
    stations: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    # Track QSOs with timestamps for rate calculations
    qsos_by_hour: Dict[str, int] = field(default_factory=dict)  # "2026-01-27-14" -> count
    qsos_by_band_hour: Dict[str, Dict[str, int]] = field(default_factory=dict)  # band -> {"hour" -> count}
    all_qso_times: List[str] = field(default_factory=list)  # All timestamps for analysis

    def add_record(self, r: Dict[str, str]) -> None:
        pk_raw = r.get("FLDPRIMARYKEY") or r.get("PRIMARYKEY")
        pk: Optional[int] = None
        if pk_raw:
            try:
                pk = int(pk_raw)
            except ValueError:
                pk = None

        if pk is not None:
            if pk in self.seen_keys:
                return
            self.seen_keys.add(pk)

        self.total_contacts += 1

        op = (r.get("FLDOPERATOR") or r.get("OPERATOR") or "").strip() or "UNKNOWN"
        self.contacts_by_operator[op] = self.contacts_by_operator.get(op, 0) + 1

        pts = points_from_modetest(r.get("MODETEST", ""))
        self.total_points += pts
        self.points_by_operator[op] = self.points_by_operator.get(op, 0) + pts

        mode = (r.get("MODE") or "").strip() or "UNK"
        self.contacts_by_mode[mode] = self.contacts_by_mode.get(mode, 0) + 1

        # Band tracking
        band = (r.get("BAND") or "").strip()
        if band:
            self.contacts_by_band[band] = self.contacts_by_band.get(band, 0) + 1

        cont = (r.get("CONTINENT") or "").strip()
        if cont:
            self.contacts_by_continent[cont] = self.contacts_by_continent.get(cont, 0) + 1

        st = (r.get("STATE") or "").strip()
        if st:
            self.contacts_by_state[st] = self.contacts_by_state.get(st, 0) + 1

        ctry = (r.get("COUNTRYWORKED") or "").strip()
        if ctry:
            self.contacts_by_country[ctry] = self.contacts_by_country.get(ctry, 0) + 1
        
        # Track ARRL sections for multipliers
        section = (r.get("ARRLSECTION") or r.get("SECTION") or "").strip()
        if section:
            self.sections_worked.add(section)
        
        # Track timestamps for rate calculations
        # N3FJP uses "DATE" not "QSODATE" - critical fix!
        qso_date = (r.get("DATE") or r.get("QSODATE") or r.get("FLDQSODATE") or "").strip()
        qso_time = (r.get("TIMEON") or r.get("FLDTIMEON") or "").strip()
        
        # Debug: Log first few timestamps
        if len(self.all_qso_times) < 3:
            import sys
            print(f"DEBUG: Parsing timestamp - DATE='{qso_date}' TIMEON='{qso_time}'", file=sys.stderr)
        
        if qso_date and qso_time:
            # Parse datetime for hourly tracking
            # N3FJP formats: "01/26 21:59" or "01/26" + "21:59" or "20260126" + "2159"
            try:
                month, day, hour = None, None, None
                
                # Try MM/DD format
                if "/" in qso_date:
                    parts = qso_date.split("/")
                    if len(parts) >= 2:
                        month = parts[0].zfill(2)
                        day = parts[1].split()[0].zfill(2)  # Handle "01/26 21:59" format
                
                # Try YYYYMMDD format
                elif len(qso_date) == 8 and qso_date.isdigit():
                    month = qso_date[4:6]
                    day = qso_date[6:8]
                
                # Parse time
                if qso_time:
                    # Handle "21:59:59" or "21:59" or "2159" formats
                    time_clean = qso_time.replace(":", "").strip()
                    if len(time_clean) >= 2:
                        hour = time_clean[:2]
                
                if month and day and hour:
                    hour_key = f"2026-{month}-{day}-{hour}"
                    
                    # Debug: Log successful parse
                    if len(self.all_qso_times) < 3:
                        import sys
                        print(f"DEBUG: Successfully parsed -> hour_key='{hour_key}'", file=sys.stderr)
                    
                    # Track overall hourly QSOs
                    self.qsos_by_hour[hour_key] = self.qsos_by_hour.get(hour_key, 0) + 1
                    
                    # Track band hourly QSOs
                    if band:
                        if band not in self.qsos_by_band_hour:
                            self.qsos_by_band_hour[band] = {}
                        self.qsos_by_band_hour[band][hour_key] = self.qsos_by_band_hour[band].get(hour_key, 0) + 1
                    
                    # Store full timestamp
                    self.all_qso_times.append(f"{qso_date} {qso_time}")
                else:
                    # Debug: Log parse failure
                    if len(self.all_qso_times) < 3:
                        import sys
                        print(f"DEBUG: Parse failed - month={month} day={day} hour={hour}", file=sys.stderr)
            except Exception as e:
                # Debug: log parsing failures
                import sys
                print(f"DEBUG: Timestamp parse exception: {qso_date} {qso_time} - {e}", file=sys.stderr)
                pass
        
        # Track physical stations
        # Priority: 1) STATION field from N3FJP, 2) Operator callsign as fallback
        station = (r.get("STATION") or r.get("FLDSTATION") or "").strip()
        if not station:
            station = f"Op: {op}"  # Fallback if no station field
        
        call = (r.get("CALL") or "").strip()
        full_time = f"{qso_date} {qso_time}".strip()
        
        # Initialize station if new
        if station not in self.stations:
            self.stations[station] = {
                "name": station,
                "operator": op,
                "band": band,
                "mode": mode,
                "recent": [],
                "lastUpdate": full_time
            }
        
        # Update station with current operator/band/mode (whoever logged most recently is "current")
        self.stations[station]["operator"] = op
        self.stations[station]["band"] = band
        self.stations[station]["mode"] = mode
        self.stations[station]["lastUpdate"] = full_time
        
        # Add to recent QSOs (keep last 5)
        qso_entry = {
            "call": call,
            "operator": op,  # Track which operator made this contact
            "band": band,
            "mode": mode,
            "time": full_time
        }
        self.stations[station]["recent"].insert(0, qso_entry)
        self.stations[station]["recent"] = self.stations[station]["recent"][:5]

    def _calculate_field_day_bonus(self, config: dict) -> dict:
        """Calculate Field Day bonus points based on config"""
        bonus_points = 0
        bonus_breakdown = []
        
        # Class multiplier (e.g., "2O" = 2 transmitters)
        fd_class = config.get("field_day_class", "1A")
        class_multiplier = int(fd_class[0]) if fd_class and fd_class[0].isdigit() else 1
        
        # Power multiplier (2x for emergency power)
        power_multiplier = 2 if config.get("emergency_power", False) else 1
        
        # 100% Emergency Power (100 points)
        if config.get("emergency_power", False):
            bonus_points += 100
            bonus_breakdown.append({"name": "100% Emergency Power", "points": 100})
        
        # Media Publicity (100 points)
        if config.get("media_publicity", False):
            bonus_points += 100
            bonus_breakdown.append({"name": "Media Publicity", "points": 100})
        
        # Public Location (100 points)
        if config.get("public_location", False):
            bonus_points += 100
            bonus_breakdown.append({"name": "Public Location", "points": 100})
        
        # Public Information Table (100 points)
        if config.get("public_information_table", False):
            bonus_points += 100
            bonus_breakdown.append({"name": "Public Information Table", "points": 100})
        
        # NTS Messages (10 points each, max 100)
        nts_originated = min(config.get("nts_message_originated", 0), 10)
        nts_handled = min(config.get("nts_message_handled", 0), 10)
        nts_total_points = (nts_originated + nts_handled) * 10
        if nts_total_points > 0:
            bonus_points += nts_total_points
            bonus_breakdown.append({"name": f"NTS Messages ({nts_originated + nts_handled})", "points": nts_total_points})
        
        # Satellite QSO (100 points)
        if config.get("satellite_qso", False):
            bonus_points += 100
            bonus_breakdown.append({"name": "Satellite QSO", "points": 100})
        
        # W1AW Bulletin (100 points)
        if config.get("w1aw_bulletin", False):
            bonus_points += 100
            bonus_breakdown.append({"name": "W1AW Bulletin Copy", "points": 100})
        
        # Educational Activity (100 points)
        if config.get("educational_activity", False):
            bonus_points += 100
            bonus_breakdown.append({"name": "Educational Activity", "points": 100})
        
        # Social Media (100 points)
        if config.get("social_media", False):
            bonus_points += 100
            bonus_breakdown.append({"name": "Social Media", "points": 100})
        
        # Youth Participation (20% under 18) (100 points)
        if config.get("youth_participation", False):
            bonus_points += 100
            bonus_breakdown.append({"name": "Youth Participation", "points": 100})
        
        # Site Visit by Official (100 points)
        if config.get("site_visit_official", False):
            bonus_points += 100
            bonus_breakdown.append({"name": "Site Visit by Official", "points": 100})
        
        return {
            "bonusPoints": bonus_points,
            "bonusBreakdown": bonus_breakdown,
            "classMultiplier": class_multiplier,
            "powerMultiplier": power_multiplier,
            "fdClass": fd_class
        }

    def snapshot(self, config: dict = None) -> dict:
        def sorted_rows(d: Dict[str, int], key_name: str) -> List[dict]:
            return [{key_name: k, "Contacts": v} for k, v in sorted(d.items(), key=lambda kv: kv[1], reverse=True)]

        # Calculate Field Day bonuses if config provided
        field_day_bonus = self._calculate_field_day_bonus(config) if config else None
        
        # Calculate final score with multipliers and bonuses
        base_points = self.total_points
        bonus_points = field_day_bonus["bonusPoints"] if field_day_bonus else 0
        class_mult = field_day_bonus["classMultiplier"] if field_day_bonus else 1
        power_mult = field_day_bonus["powerMultiplier"] if field_day_bonus else 1
        
        # Field Day Formula: (QSO Points × Class × Power) + Bonus Points
        final_score = (base_points * class_mult * power_mult) + bonus_points

        result = {
            "meta": {"generatedUtc": datetime.now(timezone.utc).isoformat()},
            "totals": {
                "contacts": self.total_contacts, 
                "points": self.total_points,
                "bonusPoints": bonus_points,
                "finalScore": final_score,
                "classMultiplier": class_mult,
                "powerMultiplier": power_mult
            },
            "contactsByOperator": [
                {"Operator": k, "Contacts": v}
                for k, v in sorted(self.contacts_by_operator.items(), key=lambda kv: kv[1], reverse=True)
            ],
            "pointsByOperator": [
                {"Operator": k, "Points": v}
                for k, v in sorted(self.points_by_operator.items(), key=lambda kv: kv[1], reverse=True)
            ],
            "contactsByMode": [
                {"Mode": k, "Contacts": v}
                for k, v in sorted(self.contacts_by_mode.items(), key=lambda kv: kv[1], reverse=True)
            ],
            "contactsByContinent": sorted_rows(self.contacts_by_continent, "Continent"),
            "contactsByState": sorted_rows(self.contacts_by_state, "State"),
            "contactsByCountry": sorted_rows(self.contacts_by_country, "Country"),
            "contactsByBand": [
                {"Band": k, "Contacts": v}
                for k, v in sorted(self.contacts_by_band.items(), key=lambda kv: kv[1], reverse=True)
            ],
            "multipliers": {
                "sections": len(self.sections_worked),
                "sectionsList": sorted(list(self.sections_worked))
            },
            "stations": list(self.stations.values()),
            "rateStats": self._calculate_rates(),
        }
        
        # Add Field Day bonus breakdown if available
        if field_day_bonus:
            result["fieldDayBonus"] = field_day_bonus
        
        return result
    
    def _calculate_rates(self) -> dict:
        """Calculate QSO rates and statistics"""
        from datetime import datetime, timedelta
        
        # Calculate rate by band (QSOs per hour for each band)
        band_rates = {}
        for band, hour_data in self.qsos_by_band_hour.items():
            if hour_data:
                total_qsos = sum(hour_data.values())
                num_hours = len(hour_data)
                band_rates[band] = round(total_qsos / num_hours, 1) if num_hours > 0 else 0
        
        # If no hourly data, fall back to simple calculation using total contacts
        if not band_rates and self.contacts_by_band:
            # Assume event duration (Field Day is 24 hours)
            assumed_hours = 24
            for band, count in self.contacts_by_band.items():
                band_rates[band] = round(count / assumed_hours, 1)
        
        # Calculate overall hourly breakdown
        hourly_totals = sorted([(k, v) for k, v in self.qsos_by_hour.items()], key=lambda x: x[0])
        
        # Find best hour
        best_hour = max(self.qsos_by_hour.items(), key=lambda x: x[1]) if self.qsos_by_hour else (None, 0)
        
        # Calculate current rates (last 20/60 minutes)
        rate_20min = 0
        rate_60min = 0
        
        if self.all_qso_times:
            total_qsos = len(self.all_qso_times)
            num_hours = len(self.qsos_by_hour) if self.qsos_by_hour else 1
            
            # For live operation: count contacts in last 20 and 60 minutes
            # Since we don't have actual timestamps, use last N contacts
            # In a real live scenario, this would check actual time
            
            # Simple approach: last 20 contacts = last 20 minutes (if busy)
            # Multiply by 3 to get hourly rate
            recent_20 = min(len(self.all_qso_times), 20)
            recent_60 = min(len(self.all_qso_times), 60)
            
            # If we have at least 20 contacts, calculate rate
            if len(self.all_qso_times) >= 20:
                rate_20min = recent_20 * 3  # 20 contacts in 20 min = 60/hr
            else:
                # Not enough data, show average
                rate_20min = round(total_qsos / max(num_hours, 1))
            
            if len(self.all_qso_times) >= 60:
                rate_60min = recent_60  # 60 contacts in 60 min = rate
            else:
                # Not enough data, show average
                rate_60min = round(total_qsos / max(num_hours, 1))
        
        return {
            "bandRates": [{"band": k, "rate": v} for k, v in sorted(band_rates.items(), key=lambda x: x[1], reverse=True)],
            "hourlyTotals": [{"hour": h, "qsos": q} for h, q in hourly_totals],
            "bestHour": {"hour": best_hour[0], "qsos": best_hour[1]} if best_hour[0] else None,
            "rate20min": rate_20min,
            "rate60min": rate_60min,
            "totalHours": len(self.qsos_by_hour)
        }


class ScoreboardApiPoller:
    def __init__(self, host: str, port: int, seed_count: int, tail_count: int, refresh_seconds: int, config: dict = None):
        self.host = host
        self.port = port
        self.seed_count = seed_count
        self.tail_count = tail_count
        self.refresh_seconds = refresh_seconds
        self.config = config or {}

        self.agg = Aggregates()
        self._lock = asyncio.Lock()

        self.diag: Dict[str, Any] = {
            "n3fjp": {"host": host, "port": port},
            "seed": {},
            "poll": {},
        }

    async def _do_list(self, n: int, which: str, total_timeout: float, idle_timeout: float) -> List[Dict[str, str]]:
        cmd = build_list_cmd(n, include_all=True)
        raw = await asyncio.to_thread(n3fjp_cmd, self.host, self.port, cmd, total_timeout, idle_timeout)
        text = raw.decode("utf-8", errors="ignore")
        recs = parse_cmd_records(text)

        # Debug: Log first record's fields to see what's available
        if recs and which == "seed":
            print("=== SAMPLE N3FJP RECORD FIELDS ===")
            print(f"Available fields: {', '.join(sorted(recs[0].keys()))}")
            print("===================================")

        self.diag[which] = {
            "requested": n,
            "totalTimeout": float(total_timeout),
            "idleTimeout": float(idle_timeout),
            "lastAtUtc": datetime.now(timezone.utc).isoformat(),
            "recordsParsed": len(recs),
            "rawBytes": len(raw),
            # helpful sanity fields
            "hasListResponseMarker": ("<LISTRESPONSE" in text.upper()),
            "hasListResponseClose": ("</LISTRESPONSE>" in text.upper()),
            "sampleFields": list(recs[0].keys()) if recs else []
        }
        return recs

    async def seed(self):
        recs = await self._do_list(self.seed_count, "seed", total_timeout=60.0, idle_timeout=1.75)
        async with self._lock:
            for r in recs:
                self.agg.add_record(r)

    async def poll_once(self):
        recs = await self._do_list(self.tail_count, "poll", total_timeout=8.0, idle_timeout=0.75)
        async with self._lock:
            for r in recs:
                self.agg.add_record(r)

    async def poll_forever(self, stop_event: asyncio.Event):
        try:
            await self.seed()
        except Exception as e:
            self.diag["seedError"] = str(e)

        while not stop_event.is_set():
            try:
                await self.poll_once()
            except Exception as e:
                self.diag["pollError"] = str(e)

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.refresh_seconds)
            except asyncio.TimeoutError:
                pass

    async def get_snapshot(self) -> dict:
        async with self._lock:
            return self.agg.snapshot(self.config)

    async def get_diag(self) -> dict:
        return self.diag


# ----------------------------
# FastAPI app
# ----------------------------

cfg = load_config()
poller = ScoreboardApiPoller(
    host=cfg["n3fjp_host"],
    port=int(cfg["n3fjp_port"]),
    seed_count=int(cfg["seed_count"]),
    tail_count=int(cfg["tail_count"]),
    refresh_seconds=int(cfg["refresh_seconds"]),
    config=cfg
)

_stop = asyncio.Event()

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(poller.poll_forever(_stop))
    yield
    _stop.set()
    try:
        await asyncio.wait_for(task, timeout=2.0)
    except Exception:
        pass

app = FastAPI(lifespan=lifespan)

# API routes FIRST
@app.get("/api/snapshot")
async def api_snapshot():
    return JSONResponse(await poller.get_snapshot())

@app.get("/api/config")
async def api_config():
    """Return club/event configuration for frontend"""
    return JSONResponse({
        "club_name": cfg.get("club_name", "Amateur Radio Club"),
        "callsign": cfg.get("callsign", "N0CALL"),
        "event_name": cfg.get("event_name", "Field Day"),
        "home_lat": cfg.get("home_lat", 0),
        "home_lon": cfg.get("home_lon", 0),
        "home_location": cfg.get("home_location", ""),
        "weather_enabled": cfg.get("weather_enabled", False),
        "band_goals": cfg.get("band_goals", {}),
        "hidden_panels": cfg.get("hidden_panels", [])
    })

@app.get("/api/diag")
async def api_diag():
    return JSONResponse(await poller.get_diag())

# Alias so your /api/debug URL works
@app.get("/api/debug")
async def api_debug():
    return JSONResponse(await poller.get_diag())

@app.get("/health")
async def health():
    return {"ok": True}

# Static LAST
www_dir = BASE_DIR / "www"
if www_dir.exists():
    app.mount("/", StaticFiles(directory=str(www_dir), html=True), name="www")


if __name__ == "__main__":
    print("Start with:")
    print(f"  python -m uvicorn server:app --host {cfg['web_host']} --port {cfg['web_port']}")