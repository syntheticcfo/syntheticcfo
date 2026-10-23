"""
The shape of a package on disk, decided in ONE place.

A single world ships 00_Executive_Brief/, 01_Audit_Evidence/, 02_Database_Engine/
and 03_Source_Data/ at its root. The multi-leg shapes nest whole packages of
that form (the legs) and add an archive layer on top:

  multi-year arc      FY<year>/ legs + CONSOLIDATED/synthetic_cfo_multiyear.db
  intercompany group  SUB_SAP_GMBH/ + SUB_ORACLE_USINC/ legs + GROUP_CONSOLIDATION/
  group arc           FY<year>/SUB_*/ legs + FY<year>/GROUP_CONSOLIDATION/
                      + GROUP_CONSOLIDATED/synthetic_cfo_group_arc.db
  migration pair      SAP_ECC/ + SAP_S4HANA/ legs, no merged database

Every multi-leg archive also carries an archive-level Reproducibility_Certificate.txt
at its root (the base seed, the configuration the API took, digests of the
archive-level artifacts and of every leg certificate), beside each leg's own.

The lab export and the dataset insights used to carry their own copies of the
rule for "which database represents this package" and drifted: insights knew
two layouts and read year one of a group arc while the export read the
cross-year database, and the export's dataset card described whichever leg
certificate a sorted glob found first. Everything that needs the answer asks
here. (The ERP twin builds its per-view databases from the same directory
names in erp/sandbox.py; that is a view catalogue, not a copy of this rule.)
"""
import glob
import os
import re
import sqlite3

SINGLE = "single"
ARC = "arc"
GROUP = "group"
GROUP_ARC = "group_arc"
PAIR = "pair"

SHAPE_NAMES = {
    SINGLE: "single world",
    ARC: "multi-year arc",
    GROUP: "intercompany group",
    GROUP_ARC: "intercompany group arc",
    PAIR: "SAP migration pair",
}

# the archive's narrative file per shape; older archives carry the base seed
# only there ("Base seed: N", "Base seed N", "Generation seed (shared): N")
_BRIEFS = {
    ARC: "FRAUD_ARC_TIMELINE.txt",
    GROUP: "CONSOLIDATION_BRIEF.txt",
    GROUP_ARC: "GROUP_ARC_TIMELINE.txt",
    PAIR: "MIGRATION_BRIEF.txt",
}

ARCHIVE_CERTIFICATE = "Reproducibility_Certificate.txt"


def _isdir(*parts):
    return os.path.isdir(os.path.join(*parts))


def _has_world_db(d):
    return bool(glob.glob(os.path.join(d, "02_Database*", "*.db")))


def shape(job_dir: str) -> str:
    """Which of the five layouts this package root is."""
    if not job_dir or not os.path.isdir(job_dir):
        return SINGLE
    if _isdir(job_dir, "GROUP_CONSOLIDATED") or glob.glob(os.path.join(job_dir, "FY*", "GROUP_CONSOLIDATION")):
        return GROUP_ARC
    if _isdir(job_dir, "GROUP_CONSOLIDATION") or _isdir(job_dir, "SUB_SAP_GMBH"):
        return GROUP
    if _isdir(job_dir, "SAP_ECC") and _isdir(job_dir, "SAP_S4HANA"):
        return PAIR
    if _isdir(job_dir, "CONSOLIDATED") or glob.glob(os.path.join(job_dir, "FY*", "02_Database*")):
        return ARC
    return SINGLE


def is_merged(job_dir: str) -> bool:
    """True when the package's representative database is merged across years
    or entities (an arc, a group, a group arc) rather than one world's own."""
    return shape(job_dir) in (ARC, GROUP, GROUP_ARC)


def package_dbs(job_dir: str) -> list:
    """Every database a reader must open to see the WHOLE package, as (label,
    path): one entry, the package_db, for a single world, an arc, a group and
    a group arc (their merged database holds every leg); one per leg for a
    migration pair, which has no merged database and whose two legs differ in
    their record-to-report and treasury layers (engine.labels explains), so a
    reader that opens the ECC leg alone sees one rendering and must say so."""
    if shape(job_dir) == PAIR:
        return [(label, package_db(d)) for label, d in legs(job_dir) if package_db(d)]
    db = package_db(job_dir)
    return [(None, db)] if db else []


def package_db(job_dir: str):
    """The ONE database that represents the whole package: the cross-year group
    database of a group arc, the consolidated database of a multi-year arc, the
    group database of a single-year group, the ECC leg of a migration pair (the
    pair has no merged database and its legs differ in the record-to-report
    and treasury layers: package_dbs() gives both, and a reader of this one
    should name the leg it shows), else the world's own."""
    s = shape(job_dir)
    if s == PAIR:
        return package_db(os.path.join(job_dir, "SAP_ECC"))
    order = {GROUP_ARC: ("GROUP_CONSOLIDATED",), ARC: ("CONSOLIDATED",),
             GROUP: ("GROUP_CONSOLIDATION",)}.get(s, ())
    for sub in order + ("02_Database_Engine", "02_Database"):
        hits = sorted(glob.glob(os.path.join(job_dir, sub, "*.db")))
        if hits:
            return hits[0]
    # a package from an older engine or a partial one: any database, a
    # consolidated one first
    cands = sorted(glob.glob(os.path.join(job_dir, "**", "*.db"), recursive=True))
    if not cands:
        return None
    consolidated = [c for c in cands if "consolidated" in c.lower()]
    return consolidated[0] if consolidated else cands[0]


def legs(job_dir: str) -> list:
    """The leg packages of a multi-leg archive as (label, directory), in a
    stable order: FY2024, FY2025 for an arc; SUB_SAP_GMBH, SUB_ORACLE_USINC for
    a group; FY2024_SUB_SAP_GMBH ... for a group arc; SAP_ECC, SAP_S4HANA for a
    pair. Empty for a single world."""
    if not job_dir or not os.path.isdir(job_dir) or shape(job_dir) == SINGLE:
        return []
    out = []
    for d in sorted(os.listdir(job_dir)):
        p = os.path.join(job_dir, d)
        if not os.path.isdir(p):
            continue
        if _has_world_db(p) or _isdir(p, "00_Executive_Brief"):
            out.append((d, p))
            continue
        for e in sorted(os.listdir(p)):
            q = os.path.join(p, e)
            if os.path.isdir(q) and (_has_world_db(q) or _isdir(q, "00_Executive_Brief")):
                out.append((d + "_" + e, q))
    return out


def leg_certificates(job_dir: str) -> list:
    """(label, path) for every leg that ships its own certificate."""
    out = []
    for label, d in legs(job_dir):
        c = os.path.join(d, "00_Executive_Brief", "Reproducibility_Certificate.txt")
        if os.path.isfile(c):
            out.append((label, c))
    return out


def archive_certificate(job_dir: str):
    """The archive-level certificate at the root of a multi-leg package, or None
    (single worlds keep theirs under 00_Executive_Brief; archives built before
    the certificate existed have none)."""
    if not job_dir or shape(job_dir) == SINGLE:
        return None
    p = os.path.join(job_dir, ARCHIVE_CERTIFICATE)
    return p if os.path.isfile(p) else None


def archive_brief(job_dir: str):
    name = _BRIEFS.get(shape(job_dir))
    if not name:
        return None
    p = os.path.join(job_dir, name)
    return p if os.path.isfile(p) else None


def base_seed(job_dir: str):
    """The archive's base seed: from the archive certificate, else from the
    brief an older archive printed it in. None for a single world (its own
    certificate carries the seed) or when nothing states it."""
    cert = archive_certificate(job_dir)
    if cert:
        m = re.search(r"^Generation seed\s*:\s*(\d+)", open(cert, encoding="utf-8").read(), re.M)
        if m:
            return int(m.group(1))
    brief = archive_brief(job_dir)
    if brief:
        txt = open(brief, encoding="utf-8", errors="replace").read()
        m = re.search(r"^(?:Base seed|Generation seed \(shared\))\s*:?\s*(\d+)", txt, re.M)
        if m:
            return int(m.group(1))
    return None


def user_tables(conn: sqlite3.Connection) -> set:
    """The package's own tables: SQLite's internal sqlite_% tables are never
    part of the deliverable (sqlite_sequence appears whenever an AUTOINCREMENT
    table exists, and copying it into another file is refused)."""
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def is_group_db(conn: sqlite3.Connection) -> bool:
    """A merged group database holds both platforms' tables plus the IC layer."""
    return "GROUP_ELIMINATION_SCHEDULE" in user_tables(conn)
