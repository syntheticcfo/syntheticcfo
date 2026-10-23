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
from collections import Counter

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
    con = sqlite3.connect(db)
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
def query_answer_key(package_dir, module=None, control_id=None, limit=200, leg=None):
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
    cols = ["module", "control_id", "fraud_vector", "source_table", "record_id", "detail"]
    for extra in ("entity", "fiscal_year", "leg"):
        if any(r.get(extra) for r in rows):
            cols.append(extra)
    out = {"ok": True, "total_matching": total, "returned": min(total, lim),
           "scope": scope, "labels": rows[:lim], "columns": cols}
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
def read_table(package_dir, table, limit=50, where=None, leg=None):
    """Read rows from a named table in the generated database, so the caller can
    actually use the data inline. `where` is an optional safe-ish filter on a
    single column: {"column": "...", "equals": "..."}. The database is the one
    engine.layout picks for the package (a merged one on an arc or a group);
    `leg` names a leg of a multi-leg package to read that leg's own database."""
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
    con = sqlite3.connect(db)
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
    return {"ok": True, "table": table, "database": _rel(db, pkg),
            "database_scope": ("the %s leg's own database" % str(leg).strip().upper()) if leg
                              else _DB_SCOPE.get(L.shape(pkg), ""),
            "total_rows": total, "returned": len(out), "rows": out}


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
    # surface the reproducibility certificate hash lines: a single world's own
    # certificate, else the archive-level certificate a multi-leg package writes
    # at its root (engine.layout.archive_certificate), which digests the
    # archive-level artifacts and every leg certificate
    pkg = res["package_dir"]
    cert = glob.glob(os.path.join(pkg, "00_Executive_Brief", "Reproducibility_Certificate.txt"))
    cert_path = cert[0] if cert else _layout().archive_certificate(pkg)
    cert_hashes = None
    if cert_path:
        import re
        txt = open(cert_path, encoding="utf-8").read()
        cert_hashes = [ln.strip() for ln in txt.splitlines()
                       if len(ln.strip().split()) == 2 and len(ln.strip().split()[0]) == 64]
        m = re.search(r"^Engine version\s*:\s*(\S+)", txt, re.M)
        if m:
            res["engine_version"] = m.group(1)
    res["reproducibility_certificate"] = _rel(cert_path, pkg) if cert_path else None
    res["file_hashes"] = cert_hashes[:12] if cert_hashes else None
    res["note"] = ("On the engine version named here (engine_version), the same seed and configuration "
                   "give these same hashes: compare them with an earlier run's certificate, which names "
                   "its own engine. On another engine version the same seed can build a different "
                   "dataset.")
    return res
