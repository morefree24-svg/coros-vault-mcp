import os
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Mount
import uvicorn

TOKEN = os.environ.get("MCP_PATH_TOKEN", "").strip()
if len(TOKEN) < 16:
    raise RuntimeError("MCP_PATH_TOKEN must be at least 16 characters")

PORT = int(os.environ.get("PORT", "8100"))
DB_PATH = Path(os.environ.get("COROS_DB_PATH", "/data/coros.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

mcp = FastMCP("COROS Running Vault")

def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c

def init_db():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            label_id TEXT PRIMARY KEY,
            run_date TEXT NOT NULL,
            sport_type INTEGER,
            location TEXT,
            start_ts INTEGER,
            end_ts INTEGER,
            duration_sec INTEGER,
            distance_km REAL,
            avg_pace_sec REAL,
            avg_hr REAL,
            calories REAL,
            raw_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_runs_date ON runs(run_date);

        CREATE TABLE IF NOT EXISTS laps (
            label_id TEXT NOT NULL,
            lap_index INTEGER NOT NULL,
            distance_km REAL,
            duration_sec INTEGER,
            pace_sec REAL,
            avg_hr REAL,
            max_hr REAL,
            cadence REAL,
            raw_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(label_id, lap_index)
        );

        CREATE TABLE IF NOT EXISTS daily (
            day TEXT PRIMARY KEY,
            sleep_score REAL,
            sleep_minutes REAL,
            hrv_avg REAL,
            hrv_baseline REAL,
            resting_hr REAL,
            short_load REAL,
            long_load REAL,
            load_ratio REAL,
            recovery_pct REAL,
            raw_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS fitness (
            captured_at TEXT PRIMARY KEY,
            vo2max REAL,
            running_level REAL,
            threshold_pace_sec REAL,
            pred_5k_sec REAL,
            pred_10k_sec REAL,
            pred_half_sec REAL,
            pred_marathon_sec REAL,
            raw_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS raw_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        """)

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def pace_to_sec(value: Any):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace("/km", "").strip()
    if ":" in s:
        parts = s.split(":")
        try:
            return int(parts[-2]) * 60 + float(parts[-1])
        except Exception:
            return None
    try:
        return float(s)
    except Exception:
        return None

def duration_to_sec(value: Any):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip()
    try:
        parts = [int(float(x)) for x in s.split(":")]
        if len(parts) == 3:
            return parts[0]*3600 + parts[1]*60 + parts[2]
        if len(parts) == 2:
            return parts[0]*60 + parts[1]
        return int(float(s))
    except Exception:
        return None

@mcp.tool()
def coros_vault_upsert_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Store or update normalized COROS run summaries. Use label_id as the stable activity key."""
    ts = now_iso()
    with conn() as c:
        for r in runs:
            label_id = str(r.get("label_id") or r.get("labelId") or "")
            if not label_id:
                raise ValueError("Each run requires label_id/labelId")
            run_date = str(r.get("run_date") or r.get("date") or "")
            if not run_date:
                raise ValueError(f"Run {label_id} requires date/run_date")
            c.execute("""
                INSERT INTO runs(label_id, run_date, sport_type, location, start_ts, end_ts,
                  duration_sec, distance_km, avg_pace_sec, avg_hr, calories, raw_json, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(label_id) DO UPDATE SET
                  run_date=excluded.run_date, sport_type=excluded.sport_type,
                  location=excluded.location, start_ts=excluded.start_ts, end_ts=excluded.end_ts,
                  duration_sec=excluded.duration_sec, distance_km=excluded.distance_km,
                  avg_pace_sec=excluded.avg_pace_sec, avg_hr=excluded.avg_hr,
                  calories=excluded.calories, raw_json=excluded.raw_json,
                  updated_at=excluded.updated_at
            """, (
                label_id, run_date, r.get("sport_type") or r.get("sportType"),
                r.get("location"), r.get("start_ts") or r.get("startTimestamp"),
                r.get("end_ts") or r.get("endTimestamp"),
                duration_to_sec(r.get("duration_sec") or r.get("duration")),
                r.get("distance_km") or r.get("distanceKm"),
                pace_to_sec(r.get("avg_pace_sec") or r.get("average_pace") or r.get("avgPace")),
                r.get("avg_hr") or r.get("avgHR"),
                r.get("calories"),
                json.dumps(r, ensure_ascii=False), ts
            ))
    return {"ok": True, "upserted": len(runs)}

@mcp.tool()
def coros_vault_upsert_laps(label_id: str, laps: list[dict[str, Any]]) -> dict[str, Any]:
    """Store per-kilometer/per-lap details for one COROS activity."""
    ts = now_iso()
    with conn() as c:
        for i, lap in enumerate(laps, start=1):
            idx = int(lap.get("lap_index") or lap.get("lapIndex") or i)
            c.execute("""
                INSERT INTO laps(label_id, lap_index, distance_km, duration_sec, pace_sec,
                  avg_hr, max_hr, cadence, raw_json, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(label_id, lap_index) DO UPDATE SET
                  distance_km=excluded.distance_km, duration_sec=excluded.duration_sec,
                  pace_sec=excluded.pace_sec, avg_hr=excluded.avg_hr,
                  max_hr=excluded.max_hr, cadence=excluded.cadence,
                  raw_json=excluded.raw_json, updated_at=excluded.updated_at
            """, (
                label_id, idx, lap.get("distance_km") or lap.get("distanceKm"),
                duration_to_sec(lap.get("duration_sec") or lap.get("duration")),
                pace_to_sec(lap.get("pace_sec") or lap.get("pace") or lap.get("avgPace")),
                lap.get("avg_hr") or lap.get("avgHR"),
                lap.get("max_hr") or lap.get("maxHR"),
                lap.get("cadence") or lap.get("avgCadence"),
                json.dumps(lap, ensure_ascii=False), ts
            ))
    return {"ok": True, "label_id": label_id, "upserted": len(laps)}

@mcp.tool()
def coros_vault_upsert_daily(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Store daily recovery/training/sleep/HRV metrics. One row per date."""
    ts = now_iso()
    with conn() as c:
        for r in records:
            day = str(r.get("day") or r.get("date") or "")
            if not day:
                raise ValueError("Each daily record requires day/date")
            c.execute("""
                INSERT INTO daily(day, sleep_score, sleep_minutes, hrv_avg, hrv_baseline,
                  resting_hr, short_load, long_load, load_ratio, recovery_pct, raw_json, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(day) DO UPDATE SET
                  sleep_score=COALESCE(excluded.sleep_score,daily.sleep_score),
                  sleep_minutes=COALESCE(excluded.sleep_minutes,daily.sleep_minutes),
                  hrv_avg=COALESCE(excluded.hrv_avg,daily.hrv_avg),
                  hrv_baseline=COALESCE(excluded.hrv_baseline,daily.hrv_baseline),
                  resting_hr=COALESCE(excluded.resting_hr,daily.resting_hr),
                  short_load=COALESCE(excluded.short_load,daily.short_load),
                  long_load=COALESCE(excluded.long_load,daily.long_load),
                  load_ratio=COALESCE(excluded.load_ratio,daily.load_ratio),
                  recovery_pct=COALESCE(excluded.recovery_pct,daily.recovery_pct),
                  raw_json=excluded.raw_json, updated_at=excluded.updated_at
            """, (
                day, r.get("sleep_score"), r.get("sleep_minutes"),
                r.get("hrv_avg"), r.get("hrv_baseline"), r.get("resting_hr"),
                r.get("short_load"), r.get("long_load"), r.get("load_ratio"),
                r.get("recovery_pct"), json.dumps(r, ensure_ascii=False), ts
            ))
    return {"ok": True, "upserted": len(records)}

@mcp.tool()
def coros_vault_store_fitness(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Store a COROS fitness assessment snapshot such as VO2max and race predictions."""
    captured = str(snapshot.get("captured_at") or now_iso())
    with conn() as c:
        c.execute("""
            INSERT OR REPLACE INTO fitness(captured_at, vo2max, running_level, threshold_pace_sec,
              pred_5k_sec, pred_10k_sec, pred_half_sec, pred_marathon_sec, raw_json)
            VALUES(?,?,?,?,?,?,?,?,?)
        """, (
            captured, snapshot.get("vo2max"), snapshot.get("running_level"),
            pace_to_sec(snapshot.get("threshold_pace_sec") or snapshot.get("threshold_pace")),
            duration_to_sec(snapshot.get("pred_5k_sec") or snapshot.get("pred_5k")),
            duration_to_sec(snapshot.get("pred_10k_sec") or snapshot.get("pred_10k")),
            duration_to_sec(snapshot.get("pred_half_sec") or snapshot.get("pred_half")),
            duration_to_sec(snapshot.get("pred_marathon_sec") or snapshot.get("pred_marathon")),
            json.dumps(snapshot, ensure_ascii=False)
        ))
    return {"ok": True, "captured_at": captured}

@mcp.tool()
def coros_vault_store_raw(source: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Fallback raw snapshot storage for COROS data that has not yet been normalized."""
    captured = now_iso()
    with conn() as c:
        cur = c.execute(
            "INSERT INTO raw_snapshots(source,captured_at,payload_json) VALUES(?,?,?)",
            (source, captured, json.dumps(payload, ensure_ascii=False))
        )
        rid = cur.lastrowid
    return {"ok": True, "id": rid, "source": source, "captured_at": captured}

@mcp.tool()
def coros_vault_recent_runs(days: int = 30, limit: int = 100) -> list[dict[str, Any]]:
    """Query locally cached run summaries."""
    cutoff = (datetime.now().date() - timedelta(days=max(days, 1)-1)).isoformat()
    with conn() as c:
        rows = c.execute("""
            SELECT label_id,run_date,sport_type,location,duration_sec,distance_km,
                   avg_pace_sec,avg_hr,calories
            FROM runs WHERE run_date >= ?
            ORDER BY run_date DESC, start_ts DESC LIMIT ?
        """, (cutoff, min(max(limit,1),500))).fetchall()
    return [dict(r) for r in rows]

@mcp.tool()
def coros_vault_run_laps(label_id: str) -> list[dict[str, Any]]:
    """Query locally cached laps for one run."""
    with conn() as c:
        rows = c.execute("""
            SELECT label_id,lap_index,distance_km,duration_sec,pace_sec,avg_hr,max_hr,cadence
            FROM laps WHERE label_id=? ORDER BY lap_index
        """, (label_id,)).fetchall()
    return [dict(r) for r in rows]

@mcp.tool()
def coros_vault_daily(days: int = 30) -> list[dict[str, Any]]:
    """Query cached daily recovery, HRV, sleep, resting HR, and training-load metrics."""
    cutoff = (datetime.now().date() - timedelta(days=max(days,1)-1)).isoformat()
    with conn() as c:
        rows = c.execute("""
            SELECT day,sleep_score,sleep_minutes,hrv_avg,hrv_baseline,resting_hr,
                   short_load,long_load,load_ratio,recovery_pct
            FROM daily WHERE day >= ? ORDER BY day DESC
        """, (cutoff,)).fetchall()
    return [dict(r) for r in rows]

@mcp.tool()
def coros_vault_summary(days: int = 28) -> dict[str, Any]:
    """Return a compact local running/training trend summary for the last N days."""
    cutoff = (datetime.now().date() - timedelta(days=max(days,1)-1)).isoformat()
    with conn() as c:
        run = c.execute("""
            SELECT COUNT(*) n, COALESCE(SUM(distance_km),0) km,
                   AVG(avg_hr) avg_hr, AVG(avg_pace_sec) avg_pace_sec
            FROM runs WHERE run_date >= ?
        """, (cutoff,)).fetchone()
        daily = c.execute("""
            SELECT AVG(hrv_avg) hrv_avg, AVG(resting_hr) resting_hr,
                   AVG(load_ratio) load_ratio, AVG(sleep_minutes) sleep_minutes
            FROM daily WHERE day >= ?
        """, (cutoff,)).fetchone()
        latest_fit = c.execute("""
            SELECT captured_at,vo2max,running_level,threshold_pace_sec,
                   pred_5k_sec,pred_10k_sec,pred_half_sec,pred_marathon_sec
            FROM fitness ORDER BY captured_at DESC LIMIT 1
        """).fetchone()
    return {
        "days": days,
        "runs": dict(run),
        "daily_averages": dict(daily),
        "latest_fitness": dict(latest_fit) if latest_fit else None
    }

@mcp.tool()
def coros_vault_status() -> dict[str, Any]:
    """Check local COROS cache health and record counts."""
    with conn() as c:
        runs = c.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        laps = c.execute("SELECT COUNT(*) FROM laps").fetchone()[0]
        daily = c.execute("SELECT COUNT(*) FROM daily").fetchone()[0]
        fitness = c.execute("SELECT COUNT(*) FROM fitness").fetchone()[0]
        raw = c.execute("SELECT COUNT(*) FROM raw_snapshots").fetchone()[0]
        last_run = c.execute("SELECT MAX(run_date) FROM runs").fetchone()[0]
        last_daily = c.execute("SELECT MAX(day) FROM daily").fetchone()[0]
    return {
        "ok": True,
        "db_path": str(DB_PATH),
        "runs": runs, "laps": laps, "daily": daily,
        "fitness_snapshots": fitness, "raw_snapshots": raw,
        "latest_run_date": last_run, "latest_daily_date": last_daily
    }

init_db()

# FastMCP's streamable HTTP app serves /mcp internally.
mcp_http = mcp.streamable_http_app()
app = Starlette(routes=[Mount(f"/{TOKEN}", app=mcp_http)])

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
