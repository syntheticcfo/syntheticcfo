"""
Tool implementations for the synthetic cfo MCP server.

Each function is a pure Python entry point that returns a JSON-serialisable
dict; server.py wraps them as MCP tools. Kept separate from the transport so the
same logic can be unit-tested without an MCP client, and so the package can
later be lifted onto a hosted server without change.

Determinism: generate_dataset runs the platform's own generation (hosted mode)
or engine.orchestrator's entry points in-process (local mode), the same code
path as the batch product either way, so the same (config, seed) on the same
engine version yields a byte-identical package; on another engine version the
same seed can build a different dataset, and the package's certificate names
the engine that built it. No LLM touches the financial data.

The shape of a package on disk (which database represents it, which files form
its one answer key) is decided by engine/layout.py and engine/labels.py and
nowhere else. This module used to keep its own copy of that rule, which drifted:
a migration pair matched none of its patterns ("no Ground_Truth_Labels.csv in
this package" for an archive shipping two), and the database of every multi-leg
package was whichever .db an unsorted recursive glob returned first, which on
ext4 depends on the filesystem's hash seed. The two engine modules use only the
standard library, so the zip served at /sdk/mcp_server.zip ships them beside this
file as _layout.py and _labels.py, and _engine_module() loads the engine's copy
when the engine is beside us and the shipped copy otherwise: one rule, not two.
"""
import os
import csv
import glob
import json
import sqlite3
import tempfile
import contextvars
from collections import Counter

# Set by the hosted connector (connector/tools_remote.py) around every call it
# makes into these functions on the platform's own copy of a package: there
# the job folder holds the facts record and the blind track's sealed sidecar,
# so _sealed_key withholds the sealed plants' count, which the downloaded
# package never states either, and the databases open read-only.
SERVER_SIDE = contextvars.ContextVar("scfo_mcp_server_side", default=False)


def _connect(db):
    """A database of a package: read-only on the platform's own copy."""
    if SERVER_SIDE.get():
        from urllib.parse import quote
        return sqlite3.connect("file:%s?mode=ro" % quote(os.path.abspath(db)), uri=True)
    return sqlite3.connect(db)

# The engine is imported lazily inside the functions so that `import mcp_server`
# is cheap and the heavy scientific stack only loads when a tool actually runs.
#
# TWO MODES, decided once per call:
#   hosted   - SCFO_API_KEY is set: every tool drives app.syntheticcfo.com through
#              the Python client (the same API the app uses), downloads the package
#              and works on the extracted files. This is what a customer gets from
#              the zip served at /sdk/mcp_server.zip and from the public
#              repository; nothing but the `mcp` package and the single-file
#              client is needed.
#   local    - no key: the tools that generate run the synthetic cfo engine
#              in-process, which needs the engine's source beside this package.
#              The engine is not published, so in the public copies these tools
#              answer with an error asking for the key instead (_no_engine).

# Where generated packages land. Override with SCFO_MCP_OUTPUT_DIR; defaults to a
# stable per-user directory so repeated calls in a session are easy to find.
def _out_base():
    d = os.environ.get("SCFO_MCP_OUTPUT_DIR") or os.path.join(
        tempfile.gettempdir(), "synthetic_cfo_mcp")
    os.makedirs(d, exist_ok=True)
    return d


def _hosted():
    return bool((os.environ.get("SCFO_API_KEY") or "").strip())


def _client_module():
    """The Python client module: the copy that travels with this server when
    there is one (beside this file in the zip the app serves at
    /sdk/mcp_server.zip, at the repository's root in the public repository), so
    the server always talks through the client it was written for; else the
    installed package (pip install syntheticcfo)."""
    import sys
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(here, "syntheticcfo.py"),
                 os.path.join(os.path.dirname(here), "syntheticcfo.py")):
        if not os.path.isfile(path):
            continue
        mod = sys.modules.get("syntheticcfo")
        if mod is not None and os.path.abspath(getattr(mod, "__file__", "") or "") == path:
            return mod
        spec = importlib.util.spec_from_file_location("syntheticcfo", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules["syntheticcfo"] = mod
        return mod
    import syntheticcfo
    return syntheticcfo


def _client():
    """A client for the account whose key SCFO_API_KEY holds."""
    return _client_module().Client()


def _no_engine():
    """What a generating tool answers without an API key when the engine is not
    beside the server, which is every public copy (the engine is not
    published): the fix, rather than an ImportError from deep inside."""
    return {"ok": False,
            "error": ("SCFO_API_KEY is not set. This server generates on app.syntheticcfo.com under "
                      "your account: create a key in the app (menu, your email, Account, API access; a "
                      "free account works: https://app.syntheticcfo.com/?signup=1) and set SCFO_API_KEY "
                      "in the environment your MCP client starts the server with.")}


def _engine_present():
    """Whether the engine's source is importable beside the server (local mode)."""
    import importlib.util
    try:
        return importlib.util.find_spec("engine.interpreter") is not None
    except Exception:   # the parent package missing raises rather than answering None
        return False


# ---------------------------------------------------------------------------
# the layout rule: engine.layout / engine.labels, or the copies the zip ships
# ---------------------------------------------------------------------------
_ENGINE_COPIES = ("layout", "labels")   # shipped as mcp_server/_layout.py, _labels.py
_mods = {}


def _engine_module(name):
    """engine.<name> when the engine is beside us (local mode), else the
    byte-identical copy the served zip ships as mcp_server/_<name>.py (hosted
    mode has no engine). Standard library only, either way."""
    if name in _mods:
        return _mods[name]
    mod = None
    try:
        import importlib
        mod = importlib.import_module("engine." + name)
    except Exception:
        mod = None
    if mod is None:
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_%s.py" % name)
        if not os.path.isfile(path):
            raise ImportError("neither engine.%s nor the shipped copy %s is available" % (name, path))
        spec = importlib.util.spec_from_file_location("mcp_server._" + name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    _mods[name] = mod
    return mod


def _layout():
    return _engine_module("layout")


def _labels():
    return _engine_module("labels")


# What each layout's representative database and answer key cover, in words
# (the rule itself lives in engine/layout.py package_db and engine/labels.py
# answer_key_files; these sentences only say what those return).
_DB_SCOPE = {
    "single": "the world's own database",
    "arc": "the consolidated database (every fiscal year, FISCAL_YEAR on each row); each year's own database is under legs",
    "group": "the merged group database (both subsidiaries' tables plus the intercompany layer); each subsidiary's own database is under legs",
    "group_arc": "the cross-year group database (every year, both subsidiaries, the intercompany layer); each year and subsidiary is under legs",
    "pair": "the SAP ECC leg (a migration pair has no merged database); the SAP S/4HANA leg is under legs and can be read with leg='SAP_S4HANA'",
}
_KEY_SCOPE = {
    "single": "the package's one answer key",
    "arc": "one key per fiscal year, read together with each row's fiscal_year attached",
    "group": "the merged key at the archive root: both subsidiaries and the intercompany layer, each row carrying its entity",
    "group_arc": "the merged key at the archive root: both subsidiaries and the intercompany layer, each row carrying its entity and fiscal year",
    "pair": "both legs ship a key; the archive's key is their union, each row tagged with the leg or legs it belongs to, and query_answer_key returns both legs' rows unless a leg is named; the app's scoring and vectors routes read one leg at a time, the SAP ECC leg unless ?leg= names the other (the scoring route also takes \"leg\" in its JSON body)",
}


def _shape(pkg):
    return _layout().shape(pkg)


def _legs(pkg):
    return _layout().legs(pkg)


def _leg_dir(pkg, leg):
    """The directory of a named leg of a multi-leg package, or None."""
    if not leg:
        return None
    for label, d in _legs(pkg):
        if label.upper() == str(leg).strip().upper():
            return d
    return None


def _rel(path, pkg):
    try:
        return os.path.relpath(path, pkg).replace(os.sep, "/")
    except Exception:
        return path


def _generate_hosted(params):
    """generate_dataset over the hosted API: the client validates nothing itself,
    the platform applies the same plan gates, sizing, clamps and metering as the
    app, and what it says when it accepts the request (the scale it chose when
    no num_rows was given, that one generation is left this month) comes back
    under warnings. Every field the platform stores for a job reaches it from
    here, so a configuration handed back under reproduce asks for the same
    world: volume_tier and company_code were dropped here until 20 October
    2026, and evaluation_mode was never passed, so reproducing an
    evaluation-mode job from its configuration built the package with the
    marker columns in it."""
    raw = {k: v for k, v in (params or {}).items() if k in _ALLOWED_KEYS}
    c = _client()
    job = c.generate(platform=raw.get("platform") or "SAP_ECC", modules=raw.get("modules"),
                     rows=raw.get("num_rows"), industry=raw.get("industry") or "TMT",
                     fraud=raw.get("fraud_intensity") or "medium",
                     jurisdiction=raw.get("jurisdiction"), region=raw.get("region"),
                     seed=raw.get("seed"), years=int(raw.get("num_years") or 1),
                     entropy=int(raw.get("entropy_level") or 0), blind=bool(raw.get("blind_track")),
                     evaluation_mode=raw.get("evaluation_mode"),
                     size_band=raw.get("size_band"),
                     fx_share=float(raw.get("foreign_currency_share") or 0.0),
                     fiscal_year=int(raw.get("fiscal_year") or 2025),
                     volume_tier=raw.get("volume_tier"), company_code=raw.get("company_code"),
                     wait=True, quiet=True)
    return _hosted_result(job, raw)


def _hosted_result(job, raw=None):
    """Download a finished hosted job, extract it beside the others, and say
    what it is. The configuration reported for reproduce is the one the
    platform stored and ran (the scale it chose included), so a later
    reproduce asks for exactly that world."""
    import zipfile
    base = os.path.join(_out_base(), str(job.id))
    os.makedirs(base, exist_ok=True)
    zpath = str(job.download(base))
    pkg = os.path.join(base, "package")
    os.makedirs(pkg, exist_ok=True)
    with zipfile.ZipFile(zpath) as zf:
        zf.extractall(pkg)
    stored = {k: v for k, v in job.config.items() if k in _ALLOWED_KEYS and k != "seed" and v is not None}
    if not stored:
        stored = {k: v for k, v in (raw or {}).items() if k != "seed" and v is not None}
        if stored.get("num_rows") is None and job.accepted.get("num_rows"):
            stored["num_rows"] = job.accepted["num_rows"]
    out = {
        "ok": True, "job_id": job.id, "seed": job.seed,
        "mode": "hosted: generated on app.syntheticcfo.com with your API key, downloaded here",
        "config": {k: stored[k] for k in ("platform", "modules", "industry", "fraud_intensity",
                                          "num_rows", "fiscal_year") if k in stored},
        "package_dir": pkg, "zip_path": zpath, "warnings": list(job.notes),
        "reproduce": {
            "job_id": job.id, "seed": job.seed, "config": stored,
            "note": ("call reproduce with this job_id for the platform's Reproduce (free on the engine "
                     "version that built it, the same files; on another engine a new dataset that counts "
                     "as one generation on a metered plan), or with this config and seed for a new "
                     "generation under your plan, which gives the same files on the same engine version"),
        },
    }
    if job.engine:
        out["engine_version"] = job.engine
    try:
        out["insights"] = job.insights()
    except Exception:
        pass
    # the whole archive, whatever its shape (the summary used to be the final
    # year's leg alone on an arc, with nothing saying so)
    out.update(_shape_info(pkg))
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
        sc = _client_module()
        me = {}
        out = {"platforms": sc.PLATFORMS,
               "combined_environments": getattr(sc, "COMBINED_ENVIRONMENTS", None),
               "modules": sc.MODULES, "industries": sc.INDUSTRIES,
               "fraud_levels": sc.FRAUD_LEVELS,
               "volume_tiers": getattr(sc, "VOLUME_TIERS", None),
               "size_bands": getattr(sc, "SIZE_BANDS", None),
               "jurisdictions": getattr(sc, "JURISDICTIONS", None),
               "regions": getattr(sc, "REGIONS", None)}
        try:
            me = _client().me() or {}
        except Exception as e:
            out["plan_error"] = "your plan could not be read: %s" % e
        out.update({
            "plan": me.get("plan"),
            "plan_caps": me.get("caps") or {},
            # the plan's scale held to each platform's ceiling, as a run reaches it
            "scale": me.get("scale"),
            "generations": me.get("generations"),
            "mode": "hosted (app.syntheticcfo.com, your API key)",
            "notes": [
                "All seven modules run on all three platforms; the combined environments, "
                "multi-year arcs and the blind track open at Team.",
                "Your plan's limits apply as in the app: a request for a module, platform or "
                "feature the plan does not include is refused with a message naming what is "
                "missing; a num_rows outside the platform's range is first brought into it, and "
                "one then above the plan's scale (plan_caps.max_rows; scale holds it to each "
                "platform's ceiling) is refused with a message naming the limit; with no "
                "num_rows the world takes the volume tier's size (the standard tier's unless "
                "volume_tier names another), and when that is more than the plan allows it is "
                "sized at the plan's limit, which the platform says under warnings.",
                "The same seed and configuration on the same engine version regenerate a "
                "byte-identical package; on another engine version they can build a different "
                "dataset, and the package's certificate names the engine.",
                "No language model generates any financial data.",
            ]})
        return out
    if not _engine_present():
        return _no_engine()
    from engine.interpreter import ALLOWED, ROW_CAP, ORACLE_ROW_CAP, ROW_MIN, SIZE_BANDS
    from engine.entities import VOLUME_TIERS
    from engine.company_profile import REGIONS
    modinfo = {
        "P2P": "Procure-to-Pay: vendors, purchase orders, invoices, payments",
        "O2C": "Order-to-Cash: customers, sales orders, billing, receipts",
        "CE":  "Cash & Treasury: bank statements and reconciliation",
        "R2R": "Record-to-Report: the general ledger and close",
        "HR":  "Human Capital & Payroll (with payroll fraud)",
        "MM":  "Materials Management & Inventory (with inventory and costing fraud)",
        "FA":  "Fixed Assets (with PP&E fraud)",
    }
    return {
        "platforms": ALLOWED["platform"],
        "modules": [{"code": m, "description": modinfo.get(m, m)} for m in ALLOWED["modules"]],
        "industries": ALLOWED["industry"],
        "fraud_levels": ALLOWED["fraud_intensity"],
        "volume_tiers": {k: v for k, v in VOLUME_TIERS.items()},
        "size_bands": list(SIZE_BANDS),
        "jurisdictions": _jurisdiction_list(),
        "regions": sorted(REGIONS),
        "row_limits": {"min": ROW_MIN, "max_sap": ROW_CAP, "max_oracle": ORACLE_ROW_CAP},
        "mode": "local (the engine beside this server; no plan, allowance or metering applies)",
        "notes": [
            "All seven modules run on all three platforms.",
            "fraud_level 'none' produces a clean baseline with an empty answer key.",
            "Every dataset ships a four-layer package: executive brief, audit "
            "evidence (with a ground-truth fraud answer key), a SQLite database, "
            "and source workbooks that mirror the real ERP tables.",
            "The same seed and configuration on the same engine version regenerate a "
            "byte-identical package; on another engine version they can build a different "
            "dataset, and the package's certificate names the engine.",
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
# every field the platform's job request takes (app.JobRequest), so a stored
# configuration handed back under reproduce asks for the same world
_ALLOWED_KEYS = {"platform", "modules", "industry", "jurisdiction", "region",
                 "fraud_intensity", "num_rows", "volume_tier", "fiscal_year",
                 "num_years", "seed", "company_code", "size_band",
                 "entropy_level", "blind_track", "foreign_currency_share",
                 "evaluation_mode"}


def generate_dataset(params):
    """Validate the request through the engine's own interpreter, run the
    deterministic engine, and return a summary plus the paths a caller needs.
    Multi-year requests (num_years > 1) run the fraud-arc path. With an API key
    set, the same request runs on the hosted platform instead."""
    if _hosted():
        return _generate_hosted(params)
    if not _engine_present():
        return _no_engine()
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

    # every entry point writes the package under jobs_base/job_id, whatever its
    # shape: a single world's four layers, an arc's FY<year>/ legs plus
    # CONSOLIDATED/, a group's SUB_*/ legs plus GROUP_CONSOLIDATION/, a pair's
    # SAP_ECC/ and SAP_S4HANA/ (the tool used to hand back the parent directory
    # for an arc, with no years and an empty summary)
    pkg_dir = os.path.join(base, job_id)

    out = {
        "ok": True,
        "job_id": job_id,
        "seed": res.get("seed"),
        "config": {k: cfg[k] for k in ("platform", "modules", "industry", "fraud_intensity",
                                       "fiscal_year") if k in cfg},
        "package_dir": pkg_dir,
        "zip_path": res.get("zip_path"),
        "warnings": warnings,
        "reproduce": {"note": "call reproduce with this exact config and seed: on the same engine "
                              "version it gives a byte-identical package", "seed": res.get("seed"),
                      "config": {k: cfg.get(k) for k in _ALLOWED_KEYS if cfg.get(k) is not None}},
    }
    out.update(_shape_info(pkg_dir))
    out["summary"] = _summarise_package(pkg_dir)
    return out


def _shape_info(pkg):
    """The package's layout in words: its shape and the years of an arc (the
    legs themselves are listed by _summarise_package, one entry per leg)."""
    L = _layout()
    shp = L.shape(pkg)
    info = {"shape": shp, "shape_name": L.SHAPE_NAMES.get(shp, shp)}
    if shp in (L.ARC, L.GROUP_ARC):
        info["arc_years"] = sorted(os.path.basename(y) for y in glob.glob(os.path.join(pkg, "FY*"))
                                   if os.path.isdir(y))
    return info


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
    """The ONE database that represents the package (engine.layout.package_db)."""
    return _layout().package_db(pkg)


def _db_counts(db):
    con = _connect(db)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        counts = {}
        for t in tables:
            try:
                counts[t] = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            except Exception:
                counts[t] = None
        return counts
    finally:
        con.close()


def _summarise_package(pkg, deep=False):
    L = _layout()
    shp = L.shape(pkg)
    s = {"package_dir": pkg,
         "layers": sorted(d for d in os.listdir(pkg) if os.path.isdir(os.path.join(pkg, d)))}
    s.update(_shape_info(pkg))
    db = _find_db(pkg)
    if db:
        counts = _db_counts(db)
        s["database"] = _rel(db, pkg)
        s["database_scope"] = _DB_SCOPE.get(shp, "")
        s["tables"] = counts if deep else {"count": len(counts),
                                           "total_rows": sum(v or 0 for v in counts.values())}
    # the legs of a multi-leg archive, each with its own database and key
    legs = L.legs(pkg)
    if legs:
        s["legs"] = []
        for label, d in legs:
            ldb = L.package_db(d)
            entry = {"leg": label, "path": _rel(d, pkg),
                     "database": _rel(ldb, pkg) if ldb else None}
            if ldb:
                lc = _db_counts(ldb)
                entry["tables"] = len(lc)
                entry["total_rows"] = sum(v or 0 for v in lc.values())
            entry["answer_key_rows"] = len(_labels().load_answer_key(d))
            # the leg's own sealed plants, when it carries the blind track (its
            # facts' count; never a sealed row)
            lsk = _sealed_key(d)
            if lsk:
                entry["sealed_key_rows"] = lsk.get("rows")
            wp = glob.glob(os.path.join(d, "01_Audit_Evidence", "Statement_Workpaper.csv"))
            if wp:
                entry["financial_statements"] = _statement_headline(wp[0])
            # every leg is a complete world and ships its own filing (the
            # summary used to look at the archive root only, where none sits,
            # and reported no filing for any arc, group or pair)
            tf = _tax_filing(d)
            if tf:
                entry["tax_filing"] = _rel(tf, pkg)
            s["legs"].append(entry)
    # answer key
    ak = _answer_key_summary(pkg)
    if ak is not None:
        s["fraud_answer_key"] = ak
    # statements headline
    wp = glob.glob(os.path.join(pkg, "01_Audit_Evidence", "Statement_Workpaper.csv"))
    if wp:
        s["financial_statements"] = _statement_headline(wp[0])
    # tax filing: a single world's own; on a multi-leg package every leg's,
    # each under its leg (the legs entries carry the same paths)
    tf = _tax_filing(pkg)
    if tf:
        s["tax_filing"] = _rel(tf, pkg)
    elif legs:
        filings = [e["tax_filing"] for e in s.get("legs", []) if e.get("tax_filing")]
        if filings:
            s["tax_filings"] = filings
    # the archive-level certificate of a multi-leg package
    cert = L.archive_certificate(pkg)
    if cert:
        s["archive_certificate"] = _rel(cert, pkg)
    # the package facts (engine/package_facts.py): the record the documents were
    # written from, when the engine is beside us to read it
    facts, source = _facts_for(pkg)
    if facts is not None:
        s["facts"] = _facts_brief(facts)
        s["facts_source"] = source
    elif source:
        s["facts_source"] = source
    return s


def _facts_for(pkg):
    """(facts, source): the persisted facts of a world or an archive, else the
    facts computed after the fact from the package's own files, else (None,
    why). Hosted mode ships no engine, so it says so instead."""
    try:
        from engine import package_facts as PF
    except Exception:
        return None, ("the package facts are read with the engine, which a hosted-mode server does not "
                      "have; the package's own documents were written from them")
    try:
        f, source = PF.load_or_compute(pkg)
        if f is not None and source == "persisted":
            source = "persisted (job_dir/_facts.json, the record the package documents were written from)"
        return f, source
    except Exception as e:
        return None, "the package facts could not be read: %s" % e.__class__.__name__


def _facts_brief(f):
    """The facts a caller wants at a glance, from a world's or an archive's facts."""
    labels = (f.get("labels") or (f.get("answer_key") or {}).get("labels") or {})
    brief = {
        "engine_version": f.get("engine_version"), "seed": f.get("seed"),
        "platform": f.get("platform"), "platform_display": f.get("platform_display"),
        "modules": f.get("modules"), "fraud_intensity": f.get("fraud_intensity"),
        "labelled_records": labels.get("total"),
        "controls": [{"id": c.get("id"), "name": c.get("name"), "labelled": c.get("labelled"),
                      "status": c.get("status")} for c in (f.get("controls") or [])],
    }
    if f.get("kind") == "archive":
        brief.update({"shape": f.get("shape"), "fiscal_years": f.get("fiscal_years"),
                      "legs": [e.get("label") for e in (f.get("legs") or [])]})
    else:
        brief.update({"fiscal_year": f.get("fiscal_year"), "entity": f.get("entity"),
                      "table_count": f.get("table_count"), "row_total": f.get("row_total"),
                      "evaluation_mode": (f.get("evaluation_mode") or {}).get("applied")})
    return brief


# ---------------------------------------------------------------------------
# the blind track's sealed key
# ---------------------------------------------------------------------------
# A package built with the blind evaluation track carries further planted rows
# whose key is sealed on the platform (job_dir/_sealed/adversary_truth.db,
# which no packager ships). Ground_Truth_Labels.csv does not list them, and
# these tools presented that key as the whole truth ("the package's one answer
# key") on a blind package, never naming the sealed plants the whitepaper and
# the lab card count, so a score computed here against the key charged every
# sealed plant a detector found as a false positive. They now say, from the
# package facts, that the sealed key exists, how many rows it holds and how the
# app's scoring treats it. They never open the sealed key and never return a
# sealed row.
_SCORE_ROUTE = "POST /api/v1/jobs/<job_id>/score"


def _cert_config(path):
    """The Configuration JSON a certificate records, or {}."""
    import re
    try:
        txt = open(path, encoding="utf-8").read()
    except OSError:
        return {}
    m = re.search(r"^Configuration\s*:\s*(\{.*\})\s*$", txt, re.M)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except ValueError:
        return {}


def _certified_blind(pkg):
    """Whether the package's certificates record the blind track: what hosted
    mode can read, since no package facts travel with a downloaded package."""
    L = _layout()
    paths = [os.path.join(pkg, "00_Executive_Brief", "Reproducibility_Certificate.txt")]
    arch = L.archive_certificate(pkg)
    if arch:
        paths.append(arch)
    paths += [os.path.join(d, "00_Executive_Brief", "Reproducibility_Certificate.txt") for _l, d in L.legs(pkg)]
    for p in paths:
        if os.path.isfile(p):
            try:
                if int(_cert_config(p).get("blind_track") or 0):
                    return True
            except (TypeError, ValueError):
                continue
    return False


def _sealed_key(pkg, leg=None):
    """The blind track's sealed key as the package facts record it (the
    world's, or every leg's), or None when the package was built without it.
    Its existence, where it is held, its row count and how the app's scoring
    treats it: never a sealed row. `leg` narrows the count to one leg."""
    facts, _src = _facts_for(pkg)
    on, rows, per_leg, in_db = False, None, {}, False
    if facts is not None and facts.get("kind") == "archive":
        on = bool(facts.get("blind_track"))
        try:
            from engine import package_facts as PF
            lf = PF.leg_facts(pkg)
        except Exception:
            lf = {}
        for label, f in lf.items():
            bt = f.get("blind_track") or {}
            if bt.get("on"):
                on = True
                in_db = in_db or bool(bt.get("in_database"))
                if bt.get("sealed_rows") is not None:
                    per_leg[label] = int(bt["sealed_rows"])
        if per_leg:
            rows = (per_leg.get(str(leg).strip().upper(), 0) if leg else sum(per_leg.values()))
    elif facts is not None:
        bt = facts.get("blind_track") or {}
        on, in_db = bool(bt.get("on")), bool(bt.get("in_database"))
        if bt.get("sealed_rows") is not None:
            rows = int(bt["sealed_rows"])
    else:
        on = _certified_blind(pkg)
    if not on:
        return None
    if SERVER_SIDE.get():
        rows, per_leg = None, {}
    n = ("%s " % format(rows, ",")) if rows is not None else ""
    out = {
        "exists": True,
        "in_this_answer_key": False,
        "held": ("in this package's own database, table ADVERSARY_TRUTH" if in_db else
                 "server-side, in the _sealed/ sidecar of the job that generated the package (the "
                 "platform's, for a job run there): the package zip and the lab export never carry it, "
                 "and these tools never open it"),
        "rows": rows,
        "rows_source": ("the package facts (blind_track.sealed_rows)" if rows is not None else
                        "not stated here: the package facts that record it stay server-side with the "
                        "sealed key (the certificate's configuration records blind_track)"),
        "scoring": ("The app's scoring route (%s) grades a submission against this answer key, where a flag "
                    "on a sealed plant names no labelled record and counts as a false positive, and grades "
                    "the same submission against the sealed key too, every year and leg merged, in the "
                    "blind_track block of its result: precision, recall and F1 over the sealed plants, a flag "
                    "on a record this answer key labels set aside rather than counted as a blind false "
                    "positive, and a discipline score for flags on the noise register when the package has "
                    "one." % _SCORE_ROUTE),
        "local_scoring": ("A score computed against this answer key alone charges every sealed plant a "
                          "detector finds as a false positive: the %sblind-track rows are planted fraud "
                          "this key does not list." % n),
    }
    if per_leg and not leg:
        out["rows_by_leg"] = per_leg
    return out


def _with_sealed(scope, sk):
    """A key scope sentence that names the sealed key beside it."""
    if not sk:
        return scope
    n = ("%s " % format(sk["rows"], ",")) if sk.get("rows") is not None else ""
    return ("%s, for the standard fraud catalogue; the blind track's %splanted rows are not in it: their key "
            "is sealed (see sealed_key)" % (scope, n))


def _answer_key_summary(pkg):
    LB = _labels()
    L = _layout()
    files = LB.answer_key_files(pkg)
    if not files:
        return None
    rows = LB.load_answer_key(pkg)
    shp = L.shape(pkg)
    by_vector = Counter(r.get("fraud_vector", "") for r in rows)
    by_control = Counter(r.get("control_id", "") for r in rows)
    by_module = Counter(r.get("module", "") for r in rows)
    sk = _sealed_key(pkg)
    out = {
        "total_labelled_records": len(rows),
        "scope": _with_sealed(_KEY_SCOPE.get(shp, ""), sk),
        "files": [_rel(p, pkg) for p in files],
        "by_module": dict(by_module),
        "by_fraud_vector": dict(by_vector),
        "by_control": dict(by_control),
        "note": ("clean baseline" if not rows else
                 "each row is one injected fraud record; use query_answer_key for the rows") if not sk else
                ("no standard-catalogue fraud is labelled (a clean baseline for the catalogue); the blind "
                 "track's plants are under the sealed key" if not rows else
                 "each row is one injected standard-catalogue fraud record; use query_answer_key for the "
                 "rows; the blind track's plants are under the sealed key"),
    }
    if sk:
        out["sealed_key"] = sk
    ents = Counter(r.get("entity", "") for r in rows if r.get("entity"))
    if ents:
        out["by_entity"] = dict(ents)
    years = Counter(r.get("fiscal_year", "") for r in rows if r.get("fiscal_year"))
    if years:
        out["by_fiscal_year"] = dict(years)
    if shp == L.PAIR:
        out["by_leg"] = {label: len(LB.load_answer_key(d)) for label, d in L.legs(pkg)}
    return out


def _tax_filing(d):
    """A world's Tax_Filing_Summary.csv (its audit evidence), or None."""
    p = os.path.join(d, "01_Audit_Evidence", "Tax_Filing_Summary.csv")
    return p if os.path.isfile(p) else None


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
def query_answer_key(package_dir, module=None, control_id=None, limit=200, leg=None, offset=0):
    """Return the ground-truth fraud labels as structured rows, so an agent
    building or scoring a fraud detector can grade against the truth. This is the
    thing a generic synthetic-data tool does not have.

    One answer key per package (engine.labels): every year of an arc once, a
    group's merged key once. A migration pair ships one key per leg: with no
    leg named, both legs' rows come back tagged with their leg (the way the lab
    export ships them); leg='SAP_ECC' or 'SAP_S4HANA' returns that leg's alone.
    On any multi-leg package a leg may be named to read that leg's own key."""
    L, LB = _layout(), _labels()
    pkg = package_dir or ""
    if not pkg or not os.path.isdir(pkg):
        return {"ok": False, "error": "package_dir not found: %r" % package_dir}
    shp = L.shape(pkg)
    scope = _KEY_SCOPE.get(shp, "")
    if leg:
        d = _leg_dir(pkg, leg)
        if not d:
            return {"ok": False, "error": "unknown leg %r" % leg,
                    "available_legs": [label for label, _d in L.legs(pkg)]}
        rows = [dict(r, leg=str(leg).strip().upper()) for r in LB.load_answer_key(d)]
        scope = "the %s leg's own answer key" % str(leg).strip().upper()
    elif shp == L.PAIR:
        rows = []
        for label, d in L.legs(pkg):
            rows += [dict(r, leg=label) for r in LB.load_answer_key(d)]
    else:
        if not LB.answer_key_files(pkg):
            return {"ok": False, "error": "no Ground_Truth_Labels.csv in this package"}
        rows = LB.load_answer_key(pkg)
    if module:
        rows = [r for r in rows if (r.get("module") or "").upper() == str(module).upper()]
    if control_id:
        rows = [r for r in rows if (r.get("control_id") or "").upper() == str(control_id).upper()]
    total = len(rows)
    lim = max(1, min(int(limit or 200), 2000))
    try:
        off = max(0, int(offset or 0))
    except (TypeError, ValueError):
        off = 0
    cols = ["module", "control_id", "fraud_vector", "source_table", "record_id", "detail"]
    for extra in ("entity", "fiscal_year", "leg"):
        if any(r.get(extra) for r in rows):
            cols.append(extra)
    page = rows[off:off + lim]
    out = {"ok": True, "total_matching": total, "offset": off, "returned": len(page),
           "scope": scope, "labels": page, "columns": cols}
    # a key larger than one page is read page by page (since 1.15.2 the
    # answer key of a large package is no longer capped at its first 2,000)
    if off + len(page) < total:
        out["next_offset"] = off + len(page)
    # a blind-track package: the rows above are the standard catalogue's; the
    # sealed key is named, counted and explained, and none of its rows returned
    sk = _sealed_key(pkg, leg)
    if sk:
        out["scope"] = _with_sealed(scope, sk)
        out["sealed_key"] = sk
    return out


# ---------------------------------------------------------------------------
# read_table
# ---------------------------------------------------------------------------
# the comparisons read_table's filter accepts, each a parameterised SQL operator
_WHERE_OPS = {"equals": "=", "not_equals": "<>", "lt": "<", "lte": "<=", "gt": ">", "gte": ">=",
              "like": "LIKE", "in": "IN"}


def _where_terms(where):
    """The filter read_table takes, as a list of {"column", <op>: value}
    conditions joined by AND: one condition as a dict ({"column": "KUNNR",
    "equals": "100045"}, the form before 1.15.2) or several as a list. The ops
    are _WHERE_OPS; "in" takes a list. Returns [(column, op, value)] or raises
    ValueError naming what it could not read."""
    if not where:
        return []
    items = where if isinstance(where, list) else [where]
    out = []
    for w in items:
        if not isinstance(w, dict) or not w.get("column"):
            raise ValueError("each condition needs a column, e.g. {\"column\": \"KUNNR\", \"equals\": \"100045\"}")
        ops = [k for k in w if k in _WHERE_OPS]
        if len(ops) != 1:
            raise ValueError("each condition takes exactly one of: " + ", ".join(_WHERE_OPS))
        v = w[ops[0]]
        if ops[0] == "in" and not (isinstance(v, list) and v and len(v) <= 1000):
            raise ValueError("\"in\" takes a list of 1 to 1,000 values")
        out.append((str(w["column"]), ops[0], v))
    return out


def read_table(package_dir, table, limit=50, where=None, leg=None, offset=0):
    """Read rows from a named table in the generated database, so the caller can
    actually use the data inline. `where` is an optional filter: one condition
    {"column": "...", "equals": "..."} or a list of them joined by AND, each
    with one comparison of _WHERE_OPS (equals, not_equals, lt, lte, gt, gte,
    like, in). total_rows counts the rows the filter matches (table_rows the
    whole table), and `offset` pages through them in the table's own row
    order. The database is the one engine.layout picks for the package (a
    merged one on an arc or a group); `leg` names a leg of a multi-leg
    package to read that leg's own database."""
    L = _layout()
    pkg = package_dir or ""
    if not pkg or not os.path.isdir(pkg):
        return {"ok": False, "error": "package_dir not found: %r" % package_dir}
    if leg:
        d = _leg_dir(pkg, leg)
        if not d:
            return {"ok": False, "error": "unknown leg %r" % leg,
                    "available_legs": [label for label, _d in L.legs(pkg)]}
        db = L.package_db(d)
    else:
        db = _find_db(pkg)
    if not db:
        return {"ok": False, "error": "no database found in this package"}
    con = _connect(db)
    con.row_factory = sqlite3.Row
    valid = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    if table not in valid:
        con.close()
        out = {"ok": False, "error": "unknown table %r" % table,
               "database": _rel(db, pkg), "available_tables": sorted(valid)}
        legs = [label for label, _d in L.legs(pkg)]
        if legs:
            out["available_legs"] = legs
        return out
    lim = max(1, min(int(limit or 50), 500))
    try:
        off = max(0, int(offset or 0))
    except (TypeError, ValueError):
        off = 0
    try:
        terms = _where_terms(where)
    except ValueError as e:
        con.close()
        return {"ok": False, "error": "where: " + str(e)}
    cols = {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}
    clauses, args = [], []
    for col, op, v in terms:
        if col not in cols:
            con.close()
            return {"ok": False, "error": "unknown column %r" % col,
                    "columns": sorted(cols)}
        if op == "in":
            clauses.append(f'"{col}" IN ({", ".join("?" * len(v))})')
            args.extend(v)
        else:
            clauses.append(f'"{col}" {_WHERE_OPS[op]} ?')
            args.append(v)
    cond = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f'SELECT * FROM "{table}"{cond} ORDER BY rowid LIMIT {lim} OFFSET {off}'
    try:
        out = [dict(r) for r in con.execute(sql, args)]
    except sqlite3.OperationalError:
        # a view has no rowid: its own order
        out = [dict(r) for r in con.execute(f'SELECT * FROM "{table}"{cond} LIMIT {lim} OFFSET {off}', args)]
    matching = con.execute(f'SELECT COUNT(*) FROM "{table}"{cond}', args).fetchone()[0]
    table_rows = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] if clauses else matching
    con.close()
    res = {"ok": True, "table": table, "database": _rel(db, pkg),
           "database_scope": ("the %s leg's own database" % str(leg).strip().upper()) if leg
                             else _DB_SCOPE.get(L.shape(pkg), ""),
           "total_rows": matching, "table_rows": table_rows, "offset": off,
           "returned": len(out), "rows": out}
    if off + len(out) < matching:
        res["next_offset"] = off + len(out)
    return res


# ---------------------------------------------------------------------------
# reproduce  (prove determinism)
# ---------------------------------------------------------------------------
def reproduce(config=None, seed=None, job_id=None):
    """Build a package again and report the engine version and the SHA-256 file
    hashes its certificate records, so a caller can check reproduction: the same
    seed and configuration on the same engine version give the same hashes, and
    on another engine version they can build a different dataset.

    job_id (hosted mode): the platform's Reproduce of that job, free on the
    engine version it ran on (the same files, for a job that completed) and, on
    another engine or one the platform has no record of, a new dataset that
    counts as one generation on a metered plan; the platform's note says which.
    config and seed: a new generation of exactly that configuration (under your
    plan in hosted mode)."""
    if job_id:
        if not _hosted():
            return {"ok": False, "error": ("job_id names a job on the platform: set SCFO_API_KEY to use "
                                           "the platform's Reproduce, or pass config and seed")}
        src = _client().job(str(job_id))
        job = src.reproduce(wait=True, quiet=True)
        res = _hosted_result(job)
        res["reproduced_from"] = str(job_id)
        for k in ("engine", "original_engine", "metered"):
            if k in job.accepted:
                res[k] = job.accepted[k]
    else:
        if config is None or seed is None:
            return {"ok": False, "error": "pass the job_id of an earlier generation, or both config and seed"}
        params = dict(config or {})
        params["seed"] = seed
        res = generate_dataset(params)
    if not res.get("ok"):
        return res
    pkg = res["package_dir"]
    res.update(certificate_summary(pkg))
    return res


def certificate_summary(pkg):
    """The reproducibility certificate of a package in a few fields: where it
    is, the engine version it names and its first SHA-256 file hash lines.
    A single world's own certificate, else the archive-level certificate a
    multi-leg package writes at its root (engine.layout.archive_certificate),
    which digests the archive-level artifacts and every leg certificate."""
    import re
    out = {}
    cert = glob.glob(os.path.join(pkg, "00_Executive_Brief", "Reproducibility_Certificate.txt"))
    cert_path = cert[0] if cert else _layout().archive_certificate(pkg)
    cert_hashes = None
    if cert_path:
        txt = open(cert_path, encoding="utf-8").read()
        cert_hashes = [ln.strip() for ln in txt.splitlines()
                       if len(ln.strip().split()) == 2 and len(ln.strip().split()[0]) == 64]
        m = re.search(r"^Engine version\s*:\s*(\S+)", txt, re.M)
        if m:
            out["engine_version"] = m.group(1)
    out["reproducibility_certificate"] = _rel(cert_path, pkg) if cert_path else None
    out["file_hashes"] = cert_hashes[:12] if cert_hashes else None
    out["note"] = ("On the engine version named here (engine_version), the same seed and configuration "
                   "give these same hashes: compare them with an earlier run's certificate, which names "
                   "its own engine. On another engine version the same seed can build a different "
                   "dataset.")
    return out


# ---------------------------------------------------------------------------
# get_deliverable  (the documents a person reads, in the chat)
# ---------------------------------------------------------------------------
# The read tools above serve an analyst (tables, the answer key's rows); this one
# serves a person who wants to read what the package says: the executive brief,
# the forensic audit report, the financial statements, the control results, one
# sheet of a source workbook. Everything is read from files the package already
# holds, at request time, and nothing is written; a PDF is returned as a preview
# of its extracted text, and every answer names the real files behind it
# ("files": name, path inside the package, type and size), which the hosted
# connector turns into short-lived download links. Only the package's standard
# folders are read, and only files a listing of them names, so neither the
# sealed key (job_dir/_sealed/) nor the facts sidecar is ever reachable here.
TEXT_CAP = 20000          # characters of text per answer at most; ask with offset for more
TABLE_DEFAULT = 50
TABLE_MAX = 200
CELL_CAP = 500            # characters per table cell

_BRIEF = "00_Executive_Brief"
_EVIDENCE = "01_Audit_Evidence"
_SOURCE = "03_Source_Data"

# kind -> (title, folder, file name), for the documents that are one file each
_DELIVERABLE_FILES = {
    "brief": ("Executive brief (the whitepaper, with the reproducibility certificate)", _BRIEF, "Whitepaper.pdf"),
    "certificate": ("Reproducibility certificate", _BRIEF, "Reproducibility_Certificate.txt"),
    "financial_statements": ("Financial statements (P&L, balance sheet, cash flow)", _BRIEF,
                             "Financial_Statements.pdf"),
    "audit_report": ("Forensic audit report", _EVIDENCE, "Forensic_Audit_Report.pdf"),
    "realism_report": ("Statistical realism report", _EVIDENCE, "Statistical_Realism_Report.pdf"),
    "control_results": ("Control results (the SOX control matrix)", _EVIDENCE, "SOX_Control_Matrix.csv"),
    "tax_filing": ("Tax filing summary, by period", _EVIDENCE, "Tax_Filing_Summary.csv"),
}
# files that go with a document: downloaded beside it, never read into the reply
_COMPANION_FILES = {
    "brief": ((_BRIEF, "Reproducibility_Certificate.txt"),),
    "financial_statements": ((_EVIDENCE, "Statement_Workpaper.csv"),),
}
DELIVERABLE_KINDS = ("list", "brief", "audit_report", "answer_key_summary", "financial_statements",
                     "control_results", "tax_filing", "realism_report", "manifest", "certificate", "workbook",
                     "archive_brief")
_WHAT = {"financial_statements": "financial statements", "audit_report": "forensic audit report",
         "brief": "executive brief", "control_results": "control matrix", "tax_filing": "tax filing",
         "realism_report": "realism report", "certificate": "reproducibility certificate",
         "answer_key_summary": "answer key", "manifest": "module manifests", "workbook": "source workbooks",
         "archive_brief": "archive brief (a single world has none; its story is in the executive brief)"}
FILE_TYPES = {".pdf": "application/pdf", ".csv": "text/csv", ".txt": "text/plain",
              ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


def _dv_err(msg, **extra):
    out = {"ok": False, "error": msg}
    out.update(extra)
    return out


def _file_entry(pkg, path):
    """What a client needs to offer a file: its name, its path inside the
    package, its type and its size. None when it is not a file we serve."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in FILE_TYPES or not os.path.isfile(path):
        return None
    return {"name": os.path.basename(path), "path": _rel(path, pkg), "type": FILE_TYPES[ext],
            "size_bytes": os.path.getsize(path)}


def _files(pkg, *paths):
    out = []
    for p in paths:
        e = _file_entry(pkg, p) if p else None
        if e and e not in out:
            out.append(e)
    return out


def _pdf_text(path):
    """The text of a PDF the package ships, page by page, or (None, why)."""
    try:
        from pypdf import PdfReader
    except Exception:
        PdfReader = None
    pages = []
    if PdfReader is not None:
        try:
            for i, page in enumerate(PdfReader(path).pages, 1):
                pages.append("[page %d]\n%s" % (i, (page.extract_text() or "").strip()))
        except Exception as e:
            return None, "the PDF could not be read (%s)" % e.__class__.__name__
    else:
        try:
            import fitz
        except Exception:
            return None, ("reading a PDF's text needs the pypdf package (pip install pypdf); the file itself "
                          "is in the package")
        try:
            with fitz.open(path) as doc:
                for i, page in enumerate(doc, 1):
                    pages.append("[page %d]\n%s" % (i, (page.get_text() or "").strip()))
        except Exception as e:
            return None, "the PDF could not be read (%s)" % e.__class__.__name__
    text = "\n\n".join(pages).strip()
    if not text.replace("[page", "").strip(" ]0123456789\n"):
        return None, "this PDF has no extractable text"
    return text, None


# where a part may end, best first: before a page, a section heading, a blank
# line, a line end, a sentence end. A cut is taken only in the window's second
# half, so a part is never shorter than half the cap.
_BREAKS = ("\n[page ", "\n\n[", "\n\n", "\n", ". ", "; ", ", ", " ")


def _cut(text, start, cap):
    end = start + cap
    if end >= len(text):
        return len(text)
    window = text[start:end]
    floor = cap // 2
    for mark in _BREAKS:
        i = window.rfind(mark)
        if i >= floor:
            # the line ends close this part; a page or heading mark starts the
            # next one; a sentence or clause mark stays with the part it ends
            lead = len(mark) - len(mark.lstrip("\n"))
            return start + i + (lead if lead else len(mark))
    return end


def _cap_text(text, offset):
    try:
        offset = max(0, int(offset or 0))
    except (TypeError, ValueError):
        offset = 0
    total = len(text)
    offset = min(offset, total)
    end = _cut(text, offset, TEXT_CAP)
    chunk = text[offset:end]
    out = {"format": "text", "text": chunk, "total_chars": total, "offset": offset,
           "returned_chars": len(chunk), "truncated": end < total}
    if out["truncated"]:
        out["next_offset"] = end
        out["truncated_note"] = ("This is characters %d to %d of %d, cut at a paragraph or sentence. For the "
                                 "next part, ask again with offset %d; or download the file."
                                 % (offset + 1, end, total, end))
    elif offset:
        out["final_note"] = "This is the last part (characters %d to %d of %d)." % (offset + 1, end, total)
    return out


def _cell(v):
    if v is None:
        return None
    if isinstance(v, (int, float, bool)):
        return v
    if hasattr(v, "isoformat"):
        return v.isoformat()
    s = str(v)
    return s if len(s) <= CELL_CAP else s[:CELL_CAP] + "..."


def _limits(limit, offset):
    try:
        lim = int(limit) if limit is not None else TABLE_DEFAULT
    except (TypeError, ValueError):
        lim = TABLE_DEFAULT
    try:
        off = max(0, int(offset or 0))
    except (TypeError, ValueError):
        off = 0
    return max(1, min(lim, TABLE_MAX)), off


def _table(header, rows_iter, limit, offset):
    lim, off = _limits(limit, offset)
    rows, total = [], 0
    for r in rows_iter:
        if off <= total < off + lim:
            rows.append([_cell(v) for v in r])
        total += 1
    out = {"format": "table", "columns": [_cell(h) for h in header], "rows": rows,
           "total_rows": total, "offset": off, "returned": len(rows)}
    if off + len(rows) < total:
        out["next_offset"] = off + len(rows)
    return out


def _csv_table(path, limit, offset):
    with open(path, newline="", encoding="utf-8") as f:
        rd = csv.reader(f)
        header = next(rd, [])
        return _table(header, rd, limit, offset)


def _workbooks(d):
    src = os.path.join(d, _SOURCE)
    if not os.path.isdir(src):
        return []
    return sorted(f for f in os.listdir(src)
                  if f.lower().endswith((".xlsx", ".csv")) and not f.startswith((".", "~", "_"))
                  and os.path.isfile(os.path.join(src, f)))


def _manifests(d):
    ev = os.path.join(d, _EVIDENCE)
    if not os.path.isdir(ev):
        return []
    return sorted(f[len("Manifest_"):-len(".pdf")] for f in os.listdir(ev)
                  if f.startswith("Manifest_") and f.endswith(".pdf"))


def _archive_cert(pkg):
    try:
        return _layout().archive_certificate(pkg)
    except Exception:
        return None


def _archive_brief(pkg):
    try:
        return _layout().archive_brief(pkg)
    except Exception:
        return None


def _level_docs(pkg, d, leg_label):
    """The documents at one level (the package root, or one leg), each with
    its files."""
    docs = []
    tag = {"leg": leg_label} if leg_label else {}
    for kind, (title, folder, name) in _DELIVERABLE_FILES.items():
        p = os.path.join(d, folder, name)
        if os.path.isfile(p):
            comp = [os.path.join(d, f, n) for f, n in _COMPANION_FILES.get(kind, ())]
            docs.append(dict(tag, kind=kind, title=title, files=_files(pkg, p, *comp)))
    keys = _labels().answer_key_files(d) if (leg_label or not _legs(pkg)) else []
    if keys:
        docs.append(dict(tag, kind="answer_key_summary", title="Answer-key summary (labelled records by "
                         "module, control and vector)", files=_files(pkg, *keys)))
    for m in _manifests(d):
        docs.append(dict(tag, kind="manifest", module=m, title="Module manifest, %s" % m,
                         files=_files(pkg, os.path.join(d, _EVIDENCE, "Manifest_%s.pdf" % m))))
    for wb in _workbooks(d):
        entry = dict(tag, kind="workbook", workbook=wb, title="Source workbook %s" % wb,
                     files=_files(pkg, os.path.join(d, _SOURCE, wb)))
        if wb.lower().endswith(".xlsx"):
            try:
                from openpyxl import load_workbook
                w = load_workbook(os.path.join(d, _SOURCE, wb), read_only=True)
                entry["sheets"] = list(w.sheetnames)
                w.close()
            except Exception:
                pass
        docs.append(entry)
    return docs


def _root_docs(pkg):
    """The documents a multi-leg package keeps at its root."""
    docs = []
    cert = _archive_cert(pkg)
    if cert:
        docs.append({"kind": "certificate", "title": "Archive reproducibility certificate (every leg)",
                     "files": _files(pkg, cert)})
    brief = _archive_brief(pkg)
    if brief:
        docs.append({"kind": "archive_brief", "title": "Archive brief (%s)" % os.path.basename(brief),
                     "files": _files(pkg, brief)})
    keys = _labels().answer_key_files(pkg)
    if keys:
        docs.append({"kind": "answer_key_summary", "title": "Answer-key summary of the whole archive",
                     "files": _files(pkg, *keys)})
    return docs


def _all_docs(pkg, leg):
    """Every document the request can reach: one leg's when leg is named; else
    the root's and every leg's, each leg's tagged with its label."""
    if leg:
        d = _leg_dir(pkg, leg)
        return _level_docs(pkg, d, str(leg).strip().upper()) if d else []
    legs = _legs(pkg)
    if not legs:
        return _level_docs(pkg, pkg, None)
    docs = _root_docs(pkg) + _level_docs(pkg, pkg, None)
    for label, d in legs:
        docs.extend(_level_docs(pkg, d, label))
    return docs


def _available(pkg, leg):
    """The documents there are, each as the arguments that read it."""
    out = []
    for doc in _all_docs(pkg, leg):
        a = {"kind": doc["kind"]}
        for k in ("workbook", "module", "leg"):
            if doc.get(k):
                a[k] = doc[k]
        if a not in out:
            out.append(a)
    return out


def _args_text(a):
    return ", ".join("%s=%s" % (k, a[k]) for k in ("kind", "workbook", "module", "leg") if a.get(k))


def _missing(pkg, leg, what):
    av = _available(pkg, leg)
    msg = "This package has no %s%s." % (what, " in leg %s" % str(leg).strip().upper() if leg else "")
    if av:
        msg += " Available: %s." % "; ".join(_args_text(a) for a in av)
    else:
        msg += " It holds no readable document."
    return _dv_err(msg, available=av)


def _list_deliverables(pkg, leg):
    docs = _all_docs(pkg, leg)
    out = {"ok": True, "kind": "list", "documents": docs}
    legs = [label for label, _d in _legs(pkg)]
    if legs and not leg:
        out["legs"] = legs
        out["legs_note"] = ("This package has legs: %s. The documents of each are listed with their leg; name "
                            "one with leg to read it. With no leg, a document is read from the package root if "
                            "it is there, else from the last leg, %s." % (", ".join(legs), legs[-1]))
    if leg and not _leg_dir(pkg, leg):
        return _dv_err("unknown leg %r" % leg, available_legs=legs)
    if not docs:
        out["note"] = "No readable document sits in this package."
    return out


def _locate(pkg, leg, has):
    """Where a document is read from: (dir, leg label or None, note) or None.
    A named leg is read from that leg. Otherwise the package root, then the
    legs from the last back, the first that has it (has(dir) says)."""
    if leg:
        d = _leg_dir(pkg, leg)
        return (d, str(leg).strip().upper(), None) if d and has(d) else None
    legs = _legs(pkg)
    if has(pkg):
        return pkg, None, None
    for label, d in reversed(legs):
        if has(d):
            others = [l for l, _d in legs if l != label]
            note = ("Read from leg %s, the last of this package's legs that has it. The other legs: %s; name "
                    "one with leg." % (label, ", ".join(others))) if others else None
            return d, label, note
    return None


def get_deliverable(package_dir, kind=None, workbook=None, sheet=None, module=None, limit=None,
                    offset=None, leg=None):
    """One of the package's documents: a preview of its text, or a table, with
    the files it comes from. kind 'list' (the default) says which documents
    this package holds."""
    pkg = package_dir or ""
    if not pkg or not os.path.isdir(pkg):
        return _dv_err("package_dir not found: %r" % package_dir)
    kind = (kind or "list").strip().lower()
    if leg and not _leg_dir(pkg, leg):
        return _dv_err("unknown leg %r" % leg, available_legs=[l for l, _d in _legs(pkg)])
    if kind not in DELIVERABLE_KINDS:
        av = _available(pkg, leg)
        return _dv_err("unknown kind %r. Available here: %s." % (kind, "; ".join(_args_text(a) for a in av)
                                                                  or "nothing"),
                       kinds=list(DELIVERABLE_KINDS), available=av)
    if kind == "list":
        return _list_deliverables(pkg, leg)
    multi = bool(_legs(pkg))

    def done(base, d, label, note):
        if label:
            base["leg"] = label
        if note:
            base["leg_note"] = note
        return base

    if kind == "archive_brief":
        p = _archive_brief(pkg) if not leg else None
        if not p:
            return _missing(pkg, leg, _WHAT[kind])
        base = {"ok": True, "kind": kind, "title": "Archive brief", "source": _rel(p, pkg),
                "files": _files(pkg, p)}
        base.update(_cap_text(open(p, encoding="utf-8").read(), offset))
        return base

    if kind == "answer_key_summary":
        if multi and not leg:
            d, label, note = pkg, None, None
        else:
            loc = _locate(pkg, leg, lambda x: bool(_labels().answer_key_files(x)))
            if loc is None:
                return _missing(pkg, leg, _WHAT[kind])
            d, label, note = loc
        ak = _answer_key_summary(d)
        if ak is None:
            return _missing(pkg, leg, _WHAT[kind])
        keys = _labels().answer_key_files(d)
        base = {"ok": True, "kind": kind, "title": "Answer-key summary" + (" of the whole archive"
                                                                         if multi and not leg else ""),
                "source": _rel(keys[0], pkg) if keys else "%s/Ground_Truth_Labels.csv" % _EVIDENCE,
                "format": "summary", "summary": ak, "files": _files(pkg, *keys)}
        sk = _sealed_key(d, None)
        if sk:
            base["sealed_key"] = {k: sk[k] for k in ("exists", "held", "scoring") if k in sk}
        return done(base, d, label, note)

    if kind == "certificate" and multi and not leg and _archive_cert(pkg):
        p = _archive_cert(pkg)
        base = {"ok": True, "kind": kind, "title": "Archive reproducibility certificate (every leg)",
                "source": _rel(p, pkg), "files": _files(pkg, p),
                "leg_note": ("The archive's own certificate, at the package root. Each leg's certificate: name "
                             "the leg (%s)." % ", ".join(l for l, _d in _legs(pkg)))}
        base.update(_cap_text(open(p, encoding="utf-8").read(), offset))
        return base

    if kind == "workbook":
        loc = _locate(pkg, leg, lambda x: bool(_workbooks(x)))
        if loc is None:
            return _missing(pkg, leg, _WHAT[kind])
        d, label, note = loc
        books = _workbooks(d)
        if not workbook:
            return done(_dv_err("Name the workbook: one of %s." % ", ".join(books), workbooks=books), d, label,
                        note)
        if workbook not in books:
            return done(_dv_err("No such workbook %r. The workbooks: %s." % (workbook, ", ".join(books)),
                                workbooks=books), d, label, note)
        path = os.path.join(d, _SOURCE, workbook)
        base = {"ok": True, "kind": kind, "title": "Source workbook %s" % workbook,
                "source": _rel(path, pkg), "files": _files(pkg, path)}
        if workbook.lower().endswith(".csv"):
            base.update(_csv_table(path, limit, offset))
            return done(base, d, label, note)
        from openpyxl import load_workbook
        w = load_workbook(path, read_only=True, data_only=True)
        try:
            names = list(w.sheetnames)
            if not sheet:
                return done(_dv_err("Name the sheet: one of %s." % ", ".join(names), workbook=workbook,
                                    sheets=names, files=base["files"]), d, label, note)
            if sheet not in names:
                return done(_dv_err("No such sheet %r in %s. The sheets: %s." % (sheet, workbook,
                                                                               ", ".join(names)),
                                    sheets=names, files=base["files"]), d, label, note)
            it = w[sheet].iter_rows(values_only=True)
            header = next(it, ()) or ()
            base["sheet"] = sheet
            base.update(_table(header, it, limit, offset))
        finally:
            w.close()
        return done(base, d, label, note)

    if kind == "manifest":
        loc = _locate(pkg, leg, lambda x: bool(_manifests(x)))
        if loc is None:
            return _missing(pkg, leg, _WHAT[kind])
        d, label, note = loc
        mods = _manifests(d)
        if not module:
            return done(_dv_err("Name the module: one of %s." % ", ".join(mods), modules=mods), d, label, note)
        m = str(module).strip().upper()
        if m not in mods:
            return done(_dv_err("No manifest for module %r. The modules: %s." % (module, ", ".join(mods)),
                                modules=mods), d, label, note)
        path = os.path.join(d, _EVIDENCE, "Manifest_%s.pdf" % m)
        text, why = _pdf_text(path)
        if text is None:
            return _dv_err("%s: %s" % (_rel(path, pkg), why), files=_files(pkg, path))
        base = {"ok": True, "kind": kind, "title": "Module manifest, %s" % m, "source": _rel(path, pkg),
                "files": _files(pkg, path)}
        base.update(_cap_text(text, offset))
        return done(base, d, label, note)

    title, folder, name = _DELIVERABLE_FILES[kind]
    loc = _locate(pkg, leg, lambda x: os.path.isfile(os.path.join(x, folder, name)))
    if loc is None:
        return _missing(pkg, leg, _WHAT[kind])
    d, label, note = loc
    path = os.path.join(d, folder, name)
    comp = [os.path.join(d, f, n) for f, n in _COMPANION_FILES.get(kind, ())]
    base = {"ok": True, "kind": kind, "title": title, "source": _rel(path, pkg),
            "files": _files(pkg, path, *comp)}
    if name.endswith(".csv"):
        base.update(_csv_table(path, limit, offset))
        if kind == "control_results":
            base["source_note"] = ("Read from the package's SOX control matrix (one row per control, with its "
                                   "status and exception count).")
        return done(base, d, label, note)
    if name.endswith(".txt"):
        base.update(_cap_text(open(path, encoding="utf-8").read(), offset))
        return done(base, d, label, note)
    text, why = _pdf_text(path)
    if text is None:
        return _dv_err("%s: %s" % (_rel(path, pkg), why), files=base["files"])
    if kind == "brief":
        cert = os.path.join(d, _BRIEF, "Reproducibility_Certificate.txt")
        if os.path.isfile(cert):
            text += "\n\n[Reproducibility_Certificate.txt]\n" + open(cert, encoding="utf-8").read()
            base["sources"] = [_rel(path, pkg), _rel(cert, pkg)]
    base.update(_cap_text(text, offset))
    if kind == "audit_report":
        ak = _answer_key_summary(d)
        if ak is not None:
            base["answer_key_summary"] = ak
    if kind == "financial_statements":
        wp = os.path.join(d, _EVIDENCE, "Statement_Workpaper.csv")
        if os.path.isfile(wp) and not offset:
            t = _csv_table(wp, limit, None)
            base["workpaper"] = dict(t, source=_rel(wp, pkg))
    return done(base, d, label, note)


# The parts of a payload that are the document itself. In a tool result they
# appear once, in the readable text block; the structured result carries the
# rest (title, paging, files and links), so no client is sent the text twice.
_BODY_KEYS = ("text", "rows", "columns", "summary", "workpaper", "answer_key_summary")


def deliverable_meta(payload):
    """The payload without the document's body: what the structured result of
    a get_deliverable call carries."""
    if not isinstance(payload, dict):
        return payload
    out = {k: v for k, v in payload.items() if k not in _BODY_KEYS}
    if any(k in payload for k in _BODY_KEYS):
        out["body"] = "in the text content of this result"
    return out


def _size(n):
    n = int(n or 0)
    if n < 1024:
        return "%d bytes" % n
    if n < 1024 * 1024:
        return "%.0f KB" % (n / 1024.0)
    return "%.1f MB" % (n / (1024.0 * 1024.0))


def _file_lines(files):
    out = []
    for f in files or []:
        line = "- %s (%s, %s)" % (f.get("name"), f.get("type"), _size(f.get("size_bytes")))
        if f.get("url"):
            line += ": %s" % f["url"]
        elif f.get("local_path"):
            line += ": %s" % f["local_path"]
        else:
            line += ": %s in the package" % f.get("path")
        out.append(line)
    return out


def deliverable_text(payload):
    """A deliverable as readable text: the document, or a table in Markdown, with
    its title, its files and how to get more. The one place a get_deliverable
    result carries the document's words."""
    if not isinstance(payload, dict):
        return str(payload)
    if not payload.get("ok"):
        lines = ["Could not read the deliverable: %s" % payload.get("error", "")]
        if payload.get("files"):
            lines.append("")
            lines.append("The file:")
            lines.extend(_file_lines(payload["files"]))
        if payload.get("open_in_app"):
            lines.append("")
            lines.append("Open the package in the app: %s" % payload["open_in_app"])
        return "\n".join(lines)
    lines = []
    if payload.get("kind") == "list":
        lines.append("Documents in this package:")
        for doc in payload.get("documents") or []:
            extra = ", ".join("%s=%s" % (k, doc[k]) for k in ("workbook", "module", "leg") if doc.get(k))
            lines.append("- %s: kind=%s%s" % (doc["title"], doc["kind"], (", " + extra) if extra else ""))
            for fl in _file_lines(doc.get("files")):
                lines.append("  " + fl)
        if payload.get("legs_note"):
            lines.append("")
            lines.append(payload["legs_note"])
    else:
        lines.append("# %s" % payload.get("title", payload.get("kind")))
        if payload.get("leg"):
            lines.append("Leg: %s" % payload["leg"])
        if payload.get("leg_note"):
            lines.append(payload["leg_note"])
        if payload.get("files"):
            lines.append("")
            lines.append("Download:" if any(f.get("url") for f in payload["files"]) else "Files:")
            lines.extend(_file_lines(payload["files"]))
            if payload.get("links_note"):
                lines.append(payload["links_note"])
        lines.append("")
        if payload.get("format") == "text":
            lines.append("## Preview" if payload.get("files") else "## Text")
            lines.append("")
            lines.append(payload.get("text", ""))
            if payload.get("truncated_note"):
                lines.append("")
                lines.append(payload["truncated_note"])
            elif payload.get("final_note"):
                lines.append("")
                lines.append(payload["final_note"])
        elif payload.get("format") == "table":
            if payload.get("sheet"):
                lines.append("Sheet: %s" % payload["sheet"])
            lines.append(_md_table(payload))
        elif payload.get("format") == "summary":
            lines.append(json.dumps(payload.get("summary"), indent=2, default=str))
        if payload.get("workpaper"):
            lines.append("")
            lines.append("## Statement workpaper")
            lines.append(_md_table(payload["workpaper"]))
        if payload.get("answer_key_summary"):
            lines.append("")
            lines.append("## Answer-key summary")
            lines.append(json.dumps(payload["answer_key_summary"], indent=2, default=str))
    if payload.get("open_in_app"):
        lines.append("")
        lines.append("Open the whole package in the app: %s" % payload["open_in_app"])
    return "\n".join(lines)


def _md_table(t):
    def esc(v):
        s = "" if v is None else str(v)
        s = s.replace("|", "\\|").replace("\n", " ")
        return s if len(s) <= 60 else s[:57] + "..."
    cols = t.get("columns") or []
    out = ["", "| " + " | ".join(esc(c) for c in cols) + " |", "|" + "---|" * len(cols)]
    for r in t.get("rows") or []:
        out.append("| " + " | ".join(esc(v) for v in r) + " |")
    out.append("")
    out.append("Rows %d to %d of %d." % (t.get("offset", 0) + 1 if t.get("returned") else 0,
                                         t.get("offset", 0) + t.get("returned", 0), t.get("total_rows", 0)))
    if t.get("next_offset") is not None:
        out.append("Ask again with offset %d for more." % t["next_offset"])
    return "\n".join(out)
