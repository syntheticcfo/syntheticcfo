"""
Tool implementations for the synthetic cfo MCP server.

Each function is a pure Python entry point that returns a JSON-serialisable
dict; server.py wraps them as MCP tools. Kept separate from the transport so the
same logic can be unit-tested without an MCP client, and so the package can
later be lifted onto a hosted server without change.

Determinism guarantee: generate_dataset drives engine.orchestrator.run_job, the
same code path as the batch product, so the same (config, seed) yields a
byte-identical package. No LLM touches the financial data.
"""
import os
import csv
import glob
import json
import sqlite3
import tempfile
from collections import Counter

# The engine is imported lazily inside the functions so that `import mcp_server`
# is cheap and the heavy scientific stack only loads when a tool actually runs.
#
# TWO MODES, decided once per call:
#   hosted   - SCFO_API_KEY is set: every tool drives app.syntheticcfo.com through
#              the Python client (the same API the app uses), downloads the package
#              and works on the extracted files. This is what a customer gets from
#              the zip served at /sdk/mcp_server.zip; nothing but the `mcp` package
#              and the single-file client is needed.
#   local    - no key: the engine in this repository runs in-process (self-hosted).

# Where generated packages land. Override with SCFO_MCP_OUTPUT_DIR; defaults to a
# stable per-user directory so repeated calls in a session are easy to find.
def _out_base():
    d = os.environ.get("SCFO_MCP_OUTPUT_DIR") or os.path.join(
        tempfile.gettempdir(), "synthetic_cfo_mcp")
    os.makedirs(d, exist_ok=True)
    return d


def _hosted():
    return bool((os.environ.get("SCFO_API_KEY") or "").strip())


def _client():
    """The Python client: the installed package, else the copy shipped beside this
    file in the zip (the app serves both from /sdk)."""
    try:
        from syntheticcfo import Client
    except ImportError:
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from syntheticcfo import Client
    return Client()


def _answer_key_files(pkg):
    """One answer key per package, whatever the layout (mirrors engine.labels so a
    hosted caller without the engine still gets every year of an arc once and a
    group's merged key once, never the per-leg copies on top of it)."""
    root = os.path.join(pkg, "Ground_Truth_Labels.csv")
    if os.path.isfile(root):
        return [root]
    hits = sorted(glob.glob(os.path.join(pkg, "FY*", "Ground_Truth_Labels.csv")))
    if hits:
        return hits
    hits = sorted(glob.glob(os.path.join(pkg, "FY*", "01_Audit_Evidence", "Ground_Truth_Labels.csv")))
    if hits:
        return hits
    single = os.path.join(pkg, "01_Audit_Evidence", "Ground_Truth_Labels.csv")
    return [single] if os.path.isfile(single) else []


def _answer_key_rows(pkg):
    import re as _re
    rows = []
    for p in _answer_key_files(pkg):
        m = _re.search(r"FY(\d{4})", os.path.relpath(p, pkg))
        with open(p, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if m and not (r.get("fiscal_year") or "").strip():
                    r["fiscal_year"] = m.group(1)
                rows.append(r)
    return rows


def _generate_hosted(params):
    """generate_dataset over the hosted API: the client validates nothing itself,
    the platform applies the same plan gates, clamps and metering as the app."""
    import zipfile
    raw = {k: v for k, v in (params or {}).items() if k in _ALLOWED_KEYS}
    c = _client()
    job = c.generate(platform=raw.get("platform") or "SAP_ECC", modules=raw.get("modules"),
                     rows=raw.get("num_rows"), industry=raw.get("industry") or "TMT",
                     fraud=raw.get("fraud_intensity") or "medium",
                     jurisdiction=raw.get("jurisdiction"), region=raw.get("region"),
                     seed=raw.get("seed"), years=int(raw.get("num_years") or 1),
                     entropy=int(raw.get("entropy_level") or 0), blind=bool(raw.get("blind_track")),
                     size_band=raw.get("size_band"),
                     fx_share=float(raw.get("foreign_currency_share") or 0.0),
                     fiscal_year=int(raw.get("fiscal_year") or 2025), wait=True, quiet=True)
    base = os.path.join(_out_base(), str(job.id))
    os.makedirs(base, exist_ok=True)
    zpath = str(job.download(base))
    pkg = os.path.join(base, "package")
    os.makedirs(pkg, exist_ok=True)
    with zipfile.ZipFile(zpath) as zf:
        zf.extractall(pkg)
    years = sorted(glob.glob(os.path.join(pkg, "FY*")))
    out = {
        "ok": True, "job_id": job.id, "seed": job.seed,
        "mode": "hosted: generated on app.syntheticcfo.com with your API key, downloaded here",
        "config": {k: raw[k] for k in ("platform", "modules", "industry", "fraud_intensity",
                                       "fiscal_year") if k in raw},
        "package_dir": pkg, "zip_path": zpath, "warnings": [],
        "reproduce": {"note": "call reproduce with this exact config + seed for a "
                              "byte-identical package", "seed": job.seed, "config": raw},
    }
    try:
        out["insights"] = job.insights()
    except Exception:
        pass
    if years:
        out["arc_years"] = [os.path.basename(y) for y in years]
        out["summary"] = _summarise_package(years[-1])
    else:
        out["summary"] = _summarise_package(pkg)
    return out


# ---------------------------------------------------------------------------
# list_capabilities
# ---------------------------------------------------------------------------
def list_capabilities():
    """Everything a caller can ask generate_dataset for. Sourced from the
    engine's own allow-lists, so it never drifts from what the engine accepts.
    Hosted mode reads the same vocabulary from the client and your plan's caps
    from the account."""
    if _hosted():
        try:
            from syntheticcfo import PLATFORMS, MODULES, INDUSTRIES, FRAUD_LEVELS
        except ImportError:
            _client()   # puts the shipped copy on sys.path
            from syntheticcfo import PLATFORMS, MODULES, INDUSTRIES, FRAUD_LEVELS
        caps = {}
        try:
            caps = (_client().me() or {}).get("caps") or {}
        except Exception:
            pass
        return {"platforms": PLATFORMS, "modules": MODULES, "industries": INDUSTRIES,
                "fraud_levels": FRAUD_LEVELS, "plan_caps": caps,
                "mode": "hosted (app.syntheticcfo.com, your API key)",
                "notes": ["All seven modules run on all three platforms.",
                          "Plan limits apply exactly as in the app; over-plan requests are refused honestly.",
                          "Same seed plus same config regenerates a byte-identical package.",
                          "No language model generates any financial data."]}
    from engine.interpreter import ALLOWED, ROW_CAP, ORACLE_ROW_CAP, ROW_MIN
    from engine.entities import VOLUME_TIERS
    modinfo = {
        "P2P": "Procure-to-Pay: vendors, purchase orders, invoices, payments",
        "O2C": "Order-to-Cash: customers, sales orders, billing, receipts",
        "CE":  "Cash & Treasury: bank statements and reconciliation",
        "R2R": "Record-to-Report: the general ledger and close",
        "HR":  "Human Capital & Payroll (with insider-threat vectors)",
        "MM":  "Materials Management & Inventory (with costing fraud)",
        "FA":  "Fixed Assets (with PP&E fraud)",
    }
    return {
        "platforms": ALLOWED["platform"],
        "modules": [{"code": m, "description": modinfo.get(m, m)} for m in ALLOWED["modules"]],
        "industries": ALLOWED["industry"],
        "fraud_levels": ALLOWED["fraud_intensity"],
        "volume_tiers": {k: v for k, v in VOLUME_TIERS.items()},
        "jurisdictions": _jurisdiction_list(),
        "row_limits": {"min": ROW_MIN, "max_sap": ROW_CAP, "max_oracle": ORACLE_ROW_CAP},
        "notes": [
            "All seven modules run on all three platforms.",
            "fraud_level 'none' produces a clean baseline with an empty answer key.",
            "Every dataset ships a four-layer package: executive brief, audit "
            "evidence (with a ground-truth fraud answer key), a SQLite database, "
            "and source workbooks that mirror the real ERP tables.",
            "Same seed plus same config regenerates a byte-identical package.",
            "No language model generates any financial data; generation is "
            "deterministic Python from accounting rules.",
        ],
    }


def _jurisdiction_list():
    try:
        from engine.company_profile import JURISDICTIONS
        return [{"code": k, "name": v.get("name", k), "currency": v.get("currency", ""),
                 "framework": v.get("framework", "")} for k, v in JURISDICTIONS.items()]
    except Exception:
        return [{"code": "US", "name": "United States", "currency": "USD", "framework": "US_GAAP"},
                {"code": "DE", "name": "Germany", "currency": "EUR", "framework": "IFRS"}]


# ---------------------------------------------------------------------------
# generate_dataset  (the headliner)
# ---------------------------------------------------------------------------
_ALLOWED_KEYS = {"platform", "modules", "industry", "jurisdiction", "region",
                 "fraud_intensity", "num_rows", "volume_tier", "fiscal_year",
                 "num_years", "seed", "company_code", "size_band",
                 "entropy_level", "blind_track", "foreign_currency_share"}


def generate_dataset(params):
    """Validate the request through the engine's own interpreter, run the
    deterministic engine, and return a summary plus the paths a caller needs.
    Multi-year requests (num_years > 1) run the fraud-arc path. With an API key
    set, the same request runs on the hosted platform instead."""
    if _hosted():
        return _generate_hosted(params)
    from engine.interpreter import _validate, DEFAULTS
    from engine.orchestrator import run_job, run_multi_year, run_migration_pair, run_consolidation

    # keep only known keys, then validate exactly as the product does
    raw = {k: v for k, v in (params or {}).items() if k in _ALLOWED_KEYS}
    warnings = []
    cfg = _validate(raw, warnings)
    # carry through the fields _validate does not (seed, company_code).
    # num_years stays as _validate resolved it: re-injecting the raw value
    # bypassed the platform-aware clamp (pair single-year, group up to 3).
    for k in ("seed", "company_code"):
        if raw.get(k) is not None:
            cfg[k] = raw[k]

    import uuid
    job_id = "mcp_" + uuid.uuid4().hex[:12]
    base = os.path.join(_out_base(), job_id)
    os.makedirs(base, exist_ok=True)
    job = dict(cfg)
    job["job_id"] = job_id

    n_years = int(job.get("num_years") or 1)
    plat = job.get("platform")
    if plat == "SAP_MIGRATION_PAIR":
        res = run_migration_pair(job, base)
    elif plat == "INTERCOMPANY_GROUP":
        res = run_consolidation(job, base)
    elif n_years > 1:
        res = run_multi_year(job, base)
    else:
        res = run_job(job, base)

    if res.get("status") != "complete":
        return {"ok": False, "error": res.get("error", "generation failed"),
                "warnings": warnings}

    # locate the package directory (single year: base/job_id; arc: base/<job_id> holds FY* dirs)
    pkg_dir = os.path.join(base, job_id)
    arc = n_years > 1
    if arc and not os.path.isdir(os.path.join(pkg_dir, "02_Database_Engine")):
        # multi-year leaves per-year folders directly under base
        pkg_dir = base

    out = {
        "ok": True,
        "job_id": job_id,
        "seed": res.get("seed"),
        "config": {k: cfg[k] for k in ("platform", "modules", "industry", "fraud_intensity",
                                       "fiscal_year") if k in cfg},
        "package_dir": pkg_dir,
        "zip_path": res.get("zip_path"),
        "warnings": warnings,
        "reproduce": {"note": "call reproduce with this exact config + seed for a "
                              "byte-identical package", "seed": res.get("seed"),
                      "config": {k: cfg.get(k) for k in _ALLOWED_KEYS if cfg.get(k) is not None}},
    }
    if not arc:
        out["summary"] = _summarise_package(pkg_dir)
    else:
        years = sorted(glob.glob(os.path.join(base, "FY*")))
        out["arc_years"] = [os.path.basename(y) for y in years]
        out["summary"] = _summarise_package(years[-1]) if years else {}
    return out


# ---------------------------------------------------------------------------
# inspect_package
# ---------------------------------------------------------------------------
def inspect_package(package_dir):
    """Given a generated package directory, return what it contains: the tables
    and row counts in the database, the fraud answer-key summary, and the
    financial-statement headline if the package ships one."""
    if not package_dir or not os.path.isdir(package_dir):
        return {"ok": False, "error": "package_dir not found: %r" % package_dir}
    return {"ok": True, **_summarise_package(package_dir, deep=True)}


def _find_db(pkg):
    for sub in ("02_Database_Engine", "02_Database"):
        hits = glob.glob(os.path.join(pkg, sub, "*.db"))
        if hits:
            return hits[0]
    hits = glob.glob(os.path.join(pkg, "**", "*.db"), recursive=True)
    return hits[0] if hits else None


def _summarise_package(pkg, deep=False):
    s = {"package_dir": pkg, "layers": sorted(
        d for d in os.listdir(pkg) if os.path.isdir(os.path.join(pkg, d)))}
    db = _find_db(pkg)
    if db:
        con = sqlite3.connect(db)
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        counts = {}
        for t in tables:
            try:
                counts[t] = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            except Exception:
                counts[t] = None
        con.close()
        s["database"] = os.path.relpath(db, pkg)
        s["tables"] = counts if deep else {"count": len(tables),
                                           "total_rows": sum(v or 0 for v in counts.values())}
    # answer key
    ak = _answer_key_summary(pkg)
    if ak is not None:
        s["fraud_answer_key"] = ak
    # statements headline
    wp = glob.glob(os.path.join(pkg, "01_Audit_Evidence", "Statement_Workpaper.csv"))
    if wp:
        s["financial_statements"] = _statement_headline(wp[0])
    # tax filing
    tf = glob.glob(os.path.join(pkg, "01_Audit_Evidence", "Tax_Filing_Summary.csv"))
    if tf:
        s["tax_filing"] = os.path.relpath(tf[0], pkg)
    return s


def _answer_key_csv(pkg):
    hits = _answer_key_files(pkg)
    return hits[0] if hits else None


def _answer_key_summary(pkg):
    if not _answer_key_files(pkg):
        return None
    rows = _answer_key_rows(pkg)
    by_vector = Counter(r.get("fraud_vector", "") for r in rows)
    by_control = Counter(r.get("control_id", "") for r in rows)
    by_module = Counter(r.get("module", "") for r in rows)
    return {
        "total_labelled_records": len(rows),
        "by_module": dict(by_module),
        "by_fraud_vector": dict(by_vector),
        "by_control": dict(by_control),
        "note": "clean baseline" if not rows else
                "each row is one injected fraud record; use query_answer_key for the rows",
    }


def _statement_headline(workpaper_csv):
    d = {}
    for r in csv.DictReader(open(workpaper_csv, newline="", encoding="utf-8")):
        if r.get("Line") in ("Revenue", "Profit for the year", "Total assets"):
            d[r["Line"]] = r.get("Amount")
        if r.get("Line") == "Revenue":
            d["Currency"] = r.get("Currency")
    return d


# ---------------------------------------------------------------------------
# query_answer_key
# ---------------------------------------------------------------------------
def query_answer_key(package_dir, module=None, control_id=None, limit=200):
    """Return the ground-truth fraud labels as structured rows, so an agent
    building or scoring a fraud detector can grade against the truth. This is the
    thing a generic synthetic-data tool does not have."""
    if not _answer_key_files(package_dir or ""):
        return {"ok": False, "error": "no Ground_Truth_Labels.csv in this package"}
    rows = _answer_key_rows(package_dir)   # every year of an arc once, a group's merged key once
    if module:
        rows = [r for r in rows if r.get("module", "").upper() == str(module).upper()]
    if control_id:
        rows = [r for r in rows if r.get("control_id", "").upper() == str(control_id).upper()]
    total = len(rows)
    lim = max(1, min(int(limit or 200), 2000))
    return {"ok": True, "total_matching": total, "returned": min(total, lim),
            "labels": rows[:lim],
            "columns": ["module", "control_id", "fraud_vector", "source_table",
                        "record_id", "detail"]}


# ---------------------------------------------------------------------------
# read_table
# ---------------------------------------------------------------------------
def read_table(package_dir, table, limit=50, where=None):
    """Read rows from a named table in the generated database, so the caller can
    actually use the data inline. `where` is an optional safe-ish filter on a
    single column: {"column": "...", "equals": "..."}."""
    db = _find_db(package_dir or "")
    if not db:
        return {"ok": False, "error": "no database found in this package"}
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    valid = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if table not in valid:
        con.close()
        return {"ok": False, "error": "unknown table %r" % table,
                "available_tables": sorted(valid)}
    lim = max(1, min(int(limit or 50), 500))
    sql = f'SELECT * FROM "{table}"'
    args = []
    if isinstance(where, dict) and where.get("column"):
        col = str(where["column"])
        cols = {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}
        if col not in cols:
            con.close()
            return {"ok": False, "error": "unknown column %r" % col,
                    "columns": sorted(cols)}
        sql += f' WHERE "{col}" = ?'
        args.append(where.get("equals"))
    sql += f" LIMIT {lim}"
    out = [dict(r) for r in con.execute(sql, args)]
    total = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    con.close()
    return {"ok": True, "table": table, "total_rows": total,
            "returned": len(out), "rows": out}


# ---------------------------------------------------------------------------
# reproduce  (prove determinism)
# ---------------------------------------------------------------------------
def reproduce(config, seed):
    """Regenerate a package from an exact config + seed and report the package
    hash, so a caller can verify byte-identical reproduction (the product's core
    guarantee). Returns the new package plus the certificate hash."""
    params = dict(config or {})
    params["seed"] = seed
    res = generate_dataset(params)
    if not res.get("ok"):
        return res
    # surface the reproducibility certificate hash line if present
    cert = glob.glob(os.path.join(res["package_dir"], "00_Executive_Brief",
                                  "Reproducibility_Certificate.txt"))
    cert_hashes = None
    if cert:
        txt = open(cert[0], encoding="utf-8").read()
        cert_hashes = [ln.strip() for ln in txt.splitlines()
                       if len(ln.strip().split()) == 2 and len(ln.strip().split()[0]) == 64]
    res["reproducibility_certificate"] = os.path.relpath(cert[0], res["package_dir"]) if cert else None
    res["file_hashes"] = cert_hashes[:12] if cert_hashes else None
    res["note"] = ("Same seed + config regenerates a byte-identical package. "
                   "Compare the SHA-256 hashes against a prior run to verify.")
    return res
