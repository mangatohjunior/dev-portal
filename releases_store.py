"""Ledger of pipelines this portal has triggered.

GitLab records that a pipeline ran. This ledger records who asked for it and,
for production, which change request authorized it. Those facts stay fixed.
pipeline_status is updated when GitLab reports progress, so each environment
can have only one pipeline that has not finished.
"""

import csv
import io
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from config import Config
from model import DeploymentRequest

_SCHEMA = """
CREATE TABLE IF NOT EXISTS releases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    released_at TEXT NOT NULL,
    username TEXT NOT NULL,
    environment TEXT NOT NULL,
    tenant_list TEXT NOT NULL,
    pipeline_trigger TEXT NOT NULL,
    taint INTEGER NOT NULL,
    cr_number TEXT,
    change_summary TEXT NOT NULL DEFAULT '',
    gitlab_pipeline_id INTEGER,
    gitlab_web_url TEXT,
    pipeline_status TEXT,
    CHECK (environment IN ('dev', 'test', 'preprod', 'prod')),
    CHECK (
        (environment = 'prod' AND cr_number IS NOT NULL)
        OR (environment != 'prod' AND cr_number IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_releases_env_time ON releases (environment, released_at);
"""

_LOCK_SCHEMA = """
CREATE TABLE IF NOT EXISTS environment_lock (
    environment TEXT PRIMARY KEY,
    gitlab_pipeline_id INTEGER,
    state TEXT NOT NULL,
    reserved_at TEXT NOT NULL,
    reservation_token TEXT
);
"""

# GitLab statuses that mean this environment can accept another trigger.
TERMINAL_PIPELINE_STATUSES = frozenset({"success", "failed", "canceled", "skipped", "unknown"})

_CSV_HEADERS = (
    "username",
    "date_utc",
    "time_utc",
    "cr_number",
    "environment",
    "tenants",
    "pipeline_trigger",
    "taint",
    "change_summary",
    "gitlab_pipeline_id",
)

# A cell that starts with one of these is a formula when opened in a spreadsheet.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _connect(path: str) -> sqlite3.Connection:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _run(path: str, fn):
    # The sqlite3 context manager commits; it does not close. On Windows an
    # open handle keeps the ledger file locked, so close is explicit.
    conn = _connect(path)
    try:
        result = fn(conn)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class EnvironmentBusy(Exception):
    def __init__(self, environment: str, pipeline_id: int | None = None, reserving: bool = False):
        self.environment = environment
        self.pipeline_id = pipeline_id
        self.reserving = reserving
        if reserving:
            message = f"A {environment} release is already being sent."
        elif pipeline_id:
            message = (
                f"A {environment} release is already in flight (pipeline #{pipeline_id}). "
                "Wait until it finishes."
            )
        else:
            message = f"A {environment} release is already in flight. Wait until it finishes."
        super().__init__(message)


def init_db(path: str | None = None) -> str:
    path = path or Config.RELEASES_DB_PATH

    def setup(conn):
        conn.executescript(_SCHEMA)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(releases)")}
        if "pipeline_status" not in columns:
            conn.execute("ALTER TABLE releases ADD COLUMN pipeline_status TEXT")
        conn.executescript(_LOCK_SCHEMA)
        lock_columns = {row[1] for row in conn.execute("PRAGMA table_info(environment_lock)")}
        if "reservation_token" not in lock_columns:
            conn.execute("ALTER TABLE environment_lock ADD COLUMN reservation_token TEXT")

    _run(path, setup)
    return path


def record_release(
    *,
    username: str,
    environment: str,
    tenant_list: str,
    pipeline_trigger: str,
    taint: bool,
    cr_number: str | None,
    change_summary: str,
    gitlab_pipeline_id: int | None,
    gitlab_web_url: str | None,
    pipeline_status: str | None = None,
    released_at: str | None = None,
    path: str | None = None,
) -> int:
    row = _normalize_row(
        username=username,
        environment=environment,
        tenant_list=tenant_list,
        pipeline_trigger=pipeline_trigger,
        taint=taint,
        cr_number=cr_number,
        change_summary=change_summary,
        gitlab_pipeline_id=gitlab_pipeline_id,
        gitlab_web_url=gitlab_web_url,
        pipeline_status=pipeline_status,
        released_at=released_at,
    )
    path = init_db(path)

    def insert(conn):
        cursor = conn.execute(_INSERT_SQL, _insert_params(row))
        return int(cursor.lastrowid)

    return _run(path, insert)


def import_sample_releases(fixture_path: str, path: str | None = None, now: datetime | None = None) -> int:
    """Replace rows that share the fixture's pipeline ids, then insert the fixture.

    Each row is `daysAgo` plus a UTC clock time, measured from `now`, so loading
    the file again keeps the chart inside the default window. Real releases use
    other pipeline ids and are left in place. Sample ids are 900001 and up.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware.")
    raw_rows = json.loads(_read_text(fixture_path))
    if not isinstance(raw_rows, list) or not raw_rows:
        raise ValueError("Sample fixture must be a non-empty list.")

    rows = []
    for item in raw_rows:
        taint = item.get("taint", False)
        if not isinstance(taint, bool):
            raise ValueError("taint must be a JSON boolean.")
        # Same environment, tenant, and prod CR rules as a real trigger.
        # The fixture stores taint as a fact, not as a replace payload.
        request = DeploymentRequest(
            targetEnvironment=item["environment"],
            allowProd=item["environment"] == "prod",
            tenantList=item["tenantList"],
            pipelineTrigger=item["pipelineTrigger"],
            taint=False,
            crNumber=item.get("crNumber"),
        )
        released_at = _fixture_timestamp(item, now)
        rows.append(
            _normalize_row(
                username=str(item["username"]),
                environment=request.targetEnvironment,
                tenant_list=request.tenantList,
                pipeline_trigger=request.pipelineTrigger,
                taint=taint,
                cr_number=request.crNumber,
                change_summary=str(item.get("changeSummary") or ""),
                gitlab_pipeline_id=int(item["gitlabPipelineId"]),
                gitlab_web_url=None,
                pipeline_status=None,
                released_at=released_at,
            )
        )

    pipeline_ids = [row["gitlab_pipeline_id"] for row in rows]
    if len(pipeline_ids) != len(set(pipeline_ids)):
        raise ValueError("Sample pipeline ids must be unique.")

    path = init_db(path)

    def write(conn):
        conn.executemany(
            "DELETE FROM releases WHERE gitlab_pipeline_id = ?",
            [(pipeline_id,) for pipeline_id in pipeline_ids],
        )
        conn.executemany(_INSERT_SQL, [_insert_params(row) for row in rows])
        return len(rows)

    return _run(path, write)


_INSERT_SQL = """
INSERT INTO releases (
    released_at, username, environment, tenant_list, pipeline_trigger,
    taint, cr_number, change_summary, gitlab_pipeline_id, gitlab_web_url, pipeline_status
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _normalize_row(
    *,
    username: str,
    environment: str,
    tenant_list: str,
    pipeline_trigger: str,
    taint: bool,
    cr_number: str | None,
    change_summary: str,
    gitlab_pipeline_id: int | None,
    gitlab_web_url: str | None,
    pipeline_status: str | None,
    released_at: str | None,
) -> dict:
    if environment == "prod":
        if not cr_number:
            raise ValueError("A production release requires a change request number.")
    else:
        cr_number = None
    return {
        "released_at": _utc_timestamp(released_at),
        "username": username,
        "environment": environment,
        "tenant_list": tenant_list,
        "pipeline_trigger": pipeline_trigger,
        "taint": 1 if taint else 0,
        "cr_number": cr_number,
        "change_summary": change_summary,
        "gitlab_pipeline_id": gitlab_pipeline_id,
        "gitlab_web_url": _safe_gitlab_url(gitlab_web_url),
        "pipeline_status": pipeline_status,
    }


def _insert_params(row: dict) -> tuple:
    return (
        row["released_at"],
        row["username"],
        row["environment"],
        row["tenant_list"],
        row["pipeline_trigger"],
        row["taint"],
        row["cr_number"],
        row["change_summary"],
        row["gitlab_pipeline_id"],
        row["gitlab_web_url"],
        row["pipeline_status"],
    )


def _fixture_timestamp(item: dict, now: datetime) -> str:
    """hoursAgo, or a calendar day plus a clock time. A future clock time rolls back one day."""
    if "hoursAgo" in item:
        hours_ago = float(item["hoursAgo"])
        if hours_ago < 0:
            raise ValueError("hoursAgo cannot be negative.")
        return (now - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")

    days_ago = int(item["daysAgo"])
    if days_ago < 0:
        raise ValueError("daysAgo cannot be negative.")
    clock = datetime.strptime(str(item["timeUtc"]), "%H:%M:%S").time()
    released = datetime.combine(now.date() - timedelta(days=days_ago), clock, tzinfo=timezone.utc)
    if released > now:
        released -= timedelta(days=1)
    return released.strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_timestamp(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    return value


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def portal_started_pipeline(pipeline_id: int, path: str | None = None) -> bool:
    path = init_db(path)
    row = _run(
        path,
        lambda conn: conn.execute(
            "SELECT 1 FROM releases WHERE gitlab_pipeline_id = ?",
            (pipeline_id,),
        ).fetchone(),
    )
    return row is not None


def latest_release_for_environment(environment: str, path: str | None = None) -> dict | None:
    path = init_db(path)
    row = _run(
        path,
        lambda conn: conn.execute(
            """
            SELECT * FROM releases
            WHERE environment = ?
            ORDER BY released_at DESC, id DESC
            LIMIT 1
            """,
            (environment,),
        ).fetchone(),
    )
    return dict(row) if row else None


def set_pipeline_status(pipeline_id: int, status: str, path: str | None = None) -> None:
    # Only the short status words GitLab uses. Anything else is dropped.
    if (
        not isinstance(status, str)
        or not status
        or len(status) > 32
        or any(ch not in "abcdefghijklmnopqrstuvwxyz_" for ch in status)
    ):
        return
    path = init_db(path)
    _run(
        path,
        lambda conn: conn.execute(
            "UPDATE releases SET pipeline_status = ? WHERE gitlab_pipeline_id = ?",
            (status, pipeline_id),
        ),
    )


def get_environment_lock(environment: str, path: str | None = None) -> dict | None:
    path = init_db(path)
    row = _run(
        path,
        lambda conn: conn.execute(
            "SELECT * FROM environment_lock WHERE environment = ?",
            (environment,),
        ).fetchone(),
    )
    return dict(row) if row else None


def reserve_environment(environment: str, path: str | None = None) -> str:
    """Hold the environment while a trigger is sent to GitLab. Returns the holder's token."""
    path = init_db(path)
    reserved_at = _utc_timestamp(None)
    token = secrets.token_urlsafe(16)

    def work(conn):
        try:
            conn.execute(
                """
                INSERT INTO environment_lock (
                    environment, gitlab_pipeline_id, state, reserved_at, reservation_token
                ) VALUES (?, NULL, 'reserving', ?, ?)
                """,
                (environment, reserved_at, token),
            )
        except sqlite3.IntegrityError as exc:
            raise EnvironmentBusy(environment, reserving=True) from exc

    _run(path, work)
    return token


def release_environment(environment: str, token: str, path: str | None = None) -> bool:
    """Drop this request's reservation. A different holder's lock is left alone."""
    path = init_db(path)

    def work(conn):
        cursor = conn.execute(
            """
            DELETE FROM environment_lock
            WHERE environment = ? AND reservation_token = ? AND state = 'reserving'
            """,
            (environment, token),
        )
        return cursor.rowcount > 0

    return _run(path, work)


def release_stale_reservation(environment: str, max_age: timedelta, path: str | None = None) -> bool:
    """Drop a reservation whose request died before GitLab answered."""
    cutoff = (datetime.now(timezone.utc) - max_age).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = init_db(path)

    def work(conn):
        cursor = conn.execute(
            """
            DELETE FROM environment_lock
            WHERE environment = ? AND state = 'reserving' AND reserved_at < ?
            """,
            (environment, cutoff),
        )
        return cursor.rowcount > 0

    return _run(path, work)


def note_pipeline_running(environment: str, pipeline_id: int, token: str, path: str | None = None) -> bool:
    """Attach the GitLab id to this request's hold. Returns false if that hold is gone."""
    path = init_db(path)

    def work(conn):
        cursor = conn.execute(
            """
            UPDATE environment_lock
            SET gitlab_pipeline_id = ?, state = 'running', reserved_at = ?
            WHERE environment = ? AND reservation_token = ? AND state = 'reserving'
            """,
            (pipeline_id, _utc_timestamp(None), environment, token),
        )
        return cursor.rowcount > 0

    return _run(path, work)


def clear_environment_lock(pipeline_id: int, path: str | None = None) -> None:
    path = init_db(path)
    _run(
        path,
        lambda conn: conn.execute(
            "DELETE FROM environment_lock WHERE gitlab_pipeline_id = ?",
            (pipeline_id,),
        ),
    )


def list_releases(path: str | None = None) -> list[dict]:
    """Every environment, oldest first. This feeds the timeline."""
    path = init_db(path)
    rows = _run(
        path,
        lambda conn: [dict(row) for row in conn.execute("SELECT * FROM releases ORDER BY released_at ASC, id ASC").fetchall()],
    )
    return [_to_api(row) for row in rows]


def list_prod_audit(path: str | None = None) -> list[dict]:
    """Production rows only, newest first. Non-prod cannot be requested here."""
    path = init_db(path)
    rows = _run(
        path,
        lambda conn: [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM releases WHERE environment = 'prod' ORDER BY released_at DESC, id DESC"
            ).fetchall()
        ],
    )
    return [_to_api(row) for row in rows]


def prod_audit_csv(path: str | None = None) -> str:
    """UTF-8 CSV of production releases, with a BOM so Excel keeps the columns."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(_CSV_HEADERS)
    for row in list_prod_audit(path):
        writer.writerow(
            [
                _csv_cell(row["username"]),
                row["dateUtc"],
                row["timeUtc"],
                _csv_cell(row["crNumber"]),
                row["environment"],
                _csv_cell(row["tenantList"]),
                row["pipelineTrigger"],
                "true" if row["taint"] else "false",
                _csv_cell(row["changeSummary"]),
                "" if row["gitlabPipelineId"] is None else row["gitlabPipelineId"],
            ]
        )
    return "\ufeff" + buffer.getvalue()


def _to_api(row: dict) -> dict:
    date_utc, time_utc = row["released_at"].replace("Z", "").split("T", 1)
    return {
        "id": row["id"],
        "releasedAt": row["released_at"],
        "dateUtc": date_utc,
        "timeUtc": time_utc,
        "username": row["username"],
        "environment": row["environment"],
        "tenantList": row["tenant_list"],
        "pipelineTrigger": row["pipeline_trigger"],
        "taint": bool(row["taint"]),
        "crNumber": row["cr_number"],
        "changeSummary": row["change_summary"],
        "gitlabPipelineId": row["gitlab_pipeline_id"],
        "gitlabWebUrl": row["gitlab_web_url"],
    }


def _safe_gitlab_url(url: str | None) -> str | None:
    base = Config.GITLAB_BASE_URL.rstrip("/") + "/"
    if not url or not url.startswith(base):
        return None
    # Block userinfo, backslashes, and whitespace so the browser link stays on GitLab.
    if any(ch in url for ch in "\\@ \t\r\n"):
        return None
    return url


def _csv_cell(value: str | None) -> str:
    text = "" if value is None else str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text
