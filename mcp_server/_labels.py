"""
The answer key of a package, whatever its layout.

A single world ships 01_Audit_Evidence/Ground_Truth_Labels.csv. A multi-year
arc ships one per FY<year>/01_Audit_Evidence. An intercompany group ships a
MERGED key at the package root (both subsidiaries plus the intercompany
layer, with entity and fiscal_year columns) and, for a group arc, a merged
key per FY<year>/ as well as per-leg keys underneath. A migration pair ships
one key per leg (SAP_ECC/ and SAP_S4HANA/), each the key for that leg.
Reading every Ground_Truth_Labels.csv under a package therefore double or
triple counts a group, and reading the shallowest one grades an arc against
year one only.

This module is the one place that decides which files form ONE answer key,
so scoring, the vectors list, insights and the lab export can never disagree.

The migration pair. The two legs are built from one seed and one
configuration, and the procurement, sales, tax and master-data layers come
out row for row identical, so their labels are the same records; the
record-to-report journal layer and the bank world are generated for each
architecture separately (different generators consume different draws), so
their labels differ between the legs (audit: 284 labels on the ECC leg, 289
on the S/4HANA leg, 36 and 41 of them only in one leg). Grading the archive
against the ECC key alone therefore scored a perfect detector run on the
S/4HANA leg as grade B. The archive's one key is now the UNION of the two:
a label present in both legs appears once, and every row carries a "leg"
field naming the legs that hold it ("SAP_ECC", "SAP_S4HANA", or both joined
with "+"), so a reader that scopes by leg can, and one that does not still
sees every distinct label once.
"""
import csv
import glob
import os
import re

PAIR_LEGS = ("SAP_ECC", "SAP_S4HANA")
LEG_FIELD = "leg"


def _pair_key_files(job_dir):
    files = [os.path.join(job_dir, leg, "01_Audit_Evidence", "Ground_Truth_Labels.csv")
             for leg in PAIR_LEGS]
    if all(os.path.isfile(p) for p in files) and all(os.path.isdir(os.path.join(job_dir, l))
                                                    for l in PAIR_LEGS):
        return files
    return []


def answer_key_files(job_dir: str) -> list:
    """The files that together form exactly one answer key for this package
    (a migration pair: both legs' keys, merged by load_answer_key)."""
    if not job_dir or not os.path.isdir(job_dir):
        return []
    root = os.path.join(job_dir, "Ground_Truth_Labels.csv")
    if os.path.isfile(root):                       # group / group arc: merged at the root
        return [root]
    per_year = sorted(glob.glob(os.path.join(job_dir, "FY*", "Ground_Truth_Labels.csv")))
    if per_year:                                   # group arc without a root key
        return per_year
    per_year = sorted(glob.glob(os.path.join(job_dir, "FY*", "01_Audit_Evidence",
                                             "Ground_Truth_Labels.csv")))
    if per_year:                                   # multi-year arc: one key per year
        return per_year
    single = os.path.join(job_dir, "01_Audit_Evidence", "Ground_Truth_Labels.csv")
    if os.path.isfile(single):
        return [single]
    pair = _pair_key_files(job_dir)                # migration pair: both legs' keys
    if pair:
        return pair
    hits = []                                      # anything else: the shallowest key
    for r, _d, files in os.walk(job_dir):
        if "Ground_Truth_Labels.csv" in files:
            hits.append(os.path.join(r, "Ground_Truth_Labels.csv"))
    hits.sort(key=lambda p: (p.count(os.sep), p))
    return hits[:1]


def is_pair(job_dir: str) -> bool:
    return bool(job_dir and os.path.isdir(job_dir) and _pair_key_files(job_dir))


def _read(path, job_dir):
    m = re.search(r"FY(\d{4})", os.path.relpath(path, job_dir))
    rows = []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if m and not (r.get("fiscal_year") or "").strip():
                    r["fiscal_year"] = m.group(1)
                rows.append(r)
    except Exception:
        return []
    return rows


def load_answer_key(job_dir: str) -> list:
    """Every label row of the package, once. Arc rows gain a fiscal_year taken
    from their FY<year> folder when the file does not carry one. A migration
    pair's rows are the union of both legs' keys: a row present in both legs
    (the same six fields) once, every row carrying LEG_FIELD with the legs
    that hold it, in the ECC leg's order first."""
    files = answer_key_files(job_dir)
    if not files:
        return []
    if not is_pair(job_dir) or len(files) != 2:
        rows = []
        for p in files:
            rows.extend(_read(p, job_dir))
        return rows
    fields = ("module", "control_id", "fraud_vector", "source_table", "record_id", "detail")
    merged, index = [], {}
    for leg, p in zip(PAIR_LEGS, files):
        for r in _read(p, job_dir):
            key = tuple(str(r.get(k) or "") for k in fields)
            if key in index:
                have = merged[index[key]]
                if leg not in have[LEG_FIELD].split("+"):
                    have[LEG_FIELD] = have[LEG_FIELD] + "+" + leg
                continue
            r[LEG_FIELD] = leg
            index[key] = len(merged)
            merged.append(r)
    return merged
