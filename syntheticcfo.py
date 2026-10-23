"""synthetic cfo - Python client.

Generate forensic-grade synthetic ERP data (SAP ECC, SAP S/4HANA, Oracle Cloud)
from a script or notebook. One file, standard library only - copy it anywhere.
The client is MIT licensed; the data it generates is governed by your plan and
the Terms at https://app.syntheticcfo.com/terms.

No code needed - the command line does the whole thing:

    pip install syntheticcfo
    syntheticcfo generate --key scfo_YOURKEY --platform oracle --fraud high --rows 600

That one line generates the world, waits, and saves the package zip into the
folder you are standing in (change it with --out). Add --lab for the JSONL lab
export. If "syntheticcfo" is not recognized on your machine,
"python -m syntheticcfo generate ..." does exactly the same thing. The other
commands: download, reproduce, score, me and jobs (syntheticcfo --help).

From code:

    from syntheticcfo import Client

    c = Client(api_key="scfo_...")          # or set the SCFO_API_KEY env var
    job = c.generate(platform="ORACLE", industry="RETAIL", fraud="high",
                     rows=600, modules=["P2P", "O2C"], jurisdiction="ZA",
                     seed=42, wait=True)
    package = job.download("out/")           # four-layer audit package (zip)
    lab = job.lab_export("out/")             # JSONL tables + answer key + dataset card
    result = job.score(flagged)              # your detector's record ids, graded

Create an API key on your account page (Account -> API access) at
https://app.syntheticcfo.com. A free account works and needs no card:
https://app.syntheticcfo.com/?signup=1. The key carries your plan and monthly
allowance; it cannot manage the account itself.

The same seed and configuration on the same engine version give byte-identical
files: the reproducibility certificate in each package names the engine version
and lists the SHA-256 of its files, and on another engine version the same seed
can build a different dataset. No language model ever generates the numbers.
"""

import json as _json
import os as _os
import re as _re
import ssl as _ssl
import time as _time
import urllib.error as _uerr
import urllib.request as _ureq
from pathlib import Path as _Path

__version__ = "0.2.2"
__all__ = ["Client", "Job", "SyntheticCfoError", "main"]

_DEFAULT_BASE = "https://app.syntheticcfo.com"

# The vocabulary the platform validates against (the engine's own lists). The
# platform is the authority and names the allowed values when it refuses one;
# these are listed for discoverability, and the platform's release checks hold
# them to the engine's lists.
PLATFORMS = ["SAP_ECC", "SAP_S4HANA", "ORACLE"]
# the two combined environments, on the Team plan and above
COMBINED_ENVIRONMENTS = ["SAP_MIGRATION_PAIR", "INTERCOMPANY_GROUP"]
MODULES = ["P2P", "O2C", "CE", "R2R", "HR", "MM", "FA"]
INDUSTRIES = ["TMT", "PHARMA", "MANUFACTURING", "FINANCIAL_SERVICES", "RETAIL"]
FRAUD_LEVELS = ["none", "low", "medium", "high"]
VOLUME_TIERS = ["proof_of_concept", "standard", "professional", "enterprise"]
SIZE_BANDS = ["startup", "smb", "mid_market", "enterprise", "global"]
JURISDICTIONS = ["AU", "CA", "CH", "DE", "GB", "IE", "IN", "JP", "US", "ZA"]
REGIONS = ["AMERICAS", "APAC", "EMEA", "GLOBAL"]

_PLATFORM_ALIASES = {
    "sap": "SAP_ECC", "ecc": "SAP_ECC", "sap ecc": "SAP_ECC", "sap_ecc": "SAP_ECC",
    "s4": "SAP_S4HANA", "s4hana": "SAP_S4HANA", "s/4hana": "SAP_S4HANA",
    "sap_s4hana": "SAP_S4HANA", "hana": "SAP_S4HANA",
    "oracle": "ORACLE", "oracle cloud": "ORACLE", "oracle_cloud": "ORACLE",
    "fusion": "ORACLE",
    "migration pair": "SAP_MIGRATION_PAIR", "sap_migration_pair": "SAP_MIGRATION_PAIR",
    "intercompany group": "INTERCOMPANY_GROUP", "intercompany_group": "INTERCOMPANY_GROUP",
}


# The column names the platform's scorer reads as the record id of a CSV: any
# name with "record" in it, or one of these.
_ID_HEADERS = ("id", "belnr", "doc_id", "document", "invoice_id")


def _submission_text(text):
    """The text a score request sends. A one-column CSV of record ids opens
    with its header line (record_id); a platform that reads a first line as
    column names only when it holds a comma, as the engine's parser does,
    grades that header as a flagged record, one false positive in every score
    made from such a file. The lone header line is left out here, whatever the
    platform does with it; any other text is sent as it is given."""
    text = str(text)
    lines = text.lstrip("\ufeff").splitlines()
    first = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    if first is None or "," in lines[first]:
        return text
    head = lines[first].strip().strip("\"'").strip().lower()
    if "record" in head or head in _ID_HEADERS:
        return "\n".join(lines[:first] + lines[first + 1:]) + "\n"
    return text


class SyntheticCfoError(Exception):
    """An error answered by the platform. .status carries the HTTP code."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class Client:
    """A synthetic cfo account, spoken to over its API key.

    api_key   your key from Account -> API access (falls back to SCFO_API_KEY)
    base_url  the platform (defaults to the hosted app)
    timeout   per-request timeout in seconds
    """

    def __init__(self, api_key=None, base_url=None, timeout=120):
        self.api_key = (api_key or _os.environ.get("SCFO_API_KEY", "")).strip()
        if not self.api_key:
            raise SyntheticCfoError(
                "No API key. Pass Client(api_key=...) or set SCFO_API_KEY. "
                "Create one at https://app.syntheticcfo.com (Account -> API access).")
        self.base_url = (base_url or _os.environ.get("SCFO_BASE_URL", "") or
                         _DEFAULT_BASE).rstrip("/")
        self.timeout = timeout

    # -- plumbing ---------------------------------------------------------------
    def _req(self, method, path, body=None):
        url = self.base_url + path
        data = _json.dumps(body).encode() if body is not None else None
        req = _ureq.Request(url, data=data, method=method)
        req.add_header("Authorization", "Bearer " + self.api_key)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "syntheticcfo-python/" + __version__)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with _ureq.urlopen(req, timeout=self.timeout,
                               context=_ssl.create_default_context()) as r:
                return _json.loads(r.read().decode() or "{}")
        except _uerr.HTTPError as e:
            raise SyntheticCfoError(self._err_message(e), status=e.code) from None
        except _uerr.URLError as e:
            raise SyntheticCfoError(
                "Cannot reach %s (%s). Check your connection." % (self.base_url,
                                                                  e.reason)) from None

    def _err_message(self, e):
        try:
            detail = _json.loads(e.read().decode()).get("detail", "")
        except Exception:
            detail = ""
        if isinstance(detail, list):
            # a validation refusal names its fields as a list of objects
            parts = []
            for d in detail:
                if isinstance(d, dict):
                    loc = ".".join(str(x) for x in (d.get("loc") or []) if x not in ("body", "query"))
                    parts.append(("%s: %s" % (loc, d.get("msg", ""))) if loc else str(d.get("msg", "")))
                else:
                    parts.append(str(d))
            detail = "; ".join(p for p in parts if p)
        elif not isinstance(detail, str):
            detail = _json.dumps(detail)
        if e.code == 401:
            return detail or ("Not authorised. Check your API key, or create a new one "
                              "at Account -> API access.")
        if e.code == 402:
            return (detail or "Your plan does not cover this request.") + \
                   " Upgrade at https://app.syntheticcfo.com/request-access."
        return detail or ("Request failed with HTTP %d." % e.code)

    def _save(self, path, dest, fallback_name):
        """Stream an authenticated download to dest (a directory or file path)."""
        url = self.base_url + path
        req = _ureq.Request(url)
        req.add_header("Authorization", "Bearer " + self.api_key)
        req.add_header("User-Agent", "syntheticcfo-python/" + __version__)
        try:
            r = _ureq.urlopen(req, timeout=max(self.timeout, 600),
                              context=_ssl.create_default_context())
        except _uerr.HTTPError as e:
            raise SyntheticCfoError(self._err_message(e), status=e.code) from None
        except _uerr.URLError as e:
            raise SyntheticCfoError(
                "Cannot reach %s (%s). Check your connection." % (self.base_url,
                                                                  e.reason)) from None
        with r:
            name = fallback_name
            cd = r.headers.get("Content-Disposition", "")
            m = _re.search(r'filename="?([^";]+)"?', cd)
            if m:
                name = _Path(m.group(1)).name
            dest = _Path(dest)
            out = dest / name if (dest.is_dir() or not dest.suffix) else dest
            out.parent.mkdir(parents=True, exist_ok=True)
            with open(out, "wb") as f:
                while True:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
        return out

    # -- account ----------------------------------------------------------------
    def me(self):
        """Your account: email, plan, this month's generations, and your caps
        (caps["max_rows"] is your plan's scale; "scale" holds it to each
        platform's ceiling)."""
        return self._req("GET", "/api/v1/auth/me")

    # -- generation -------------------------------------------------------------
    def generate(self, platform="SAP_ECC", modules=None, rows=None, industry="TMT",
                 fraud="medium", jurisdiction=None, region=None, seed=None,
                 years=1, entropy=0, blind=False, evaluation_mode=None,
                 size_band=None, fx_share=0.0, fiscal_year=2025,
                 volume_tier=None, company_code=None,
                 wait=False, poll_interval=3.0, quiet=False):
        """Start a generation and return the Job.

        platform   SAP_ECC | SAP_S4HANA | ORACLE (aliases like "oracle" work),
                   or a combined environment, SAP_MIGRATION_PAIR or
                   INTERCOMPANY_GROUP, on the Team plan and above
        modules    subset of P2P O2C CE R2R HR MM FA (default: the core four)
        rows       the scale: base transactions per cycle, the setting the
                   plans call rows (a generation parameter, not a count of the
                   rows the tables end up holding). Up to your plan's limit:
                   me() shows it as caps["max_rows"], and as "scale" held to
                   each platform's ceiling. A value outside the platform's range
                   is first brought into it, without a note; a value then above
                   your plan's limit is refused with a 402 that names the
                   limit. Left out, the world takes the volume tier's size (the
                   standard tier's unless volume_tier names another); when that
                   size is more than your plan allows (on the free plan the
                   standard tier's is), it is sized at your plan's limit
                   instead and the platform says so in a note (job.notes,
                   printed unless quiet)
        volume_tier  proof_of_concept | standard | professional | enterprise:
                   the preset scale used when rows is left out
        fraud      none | low | medium | high
        jurisdiction  a country code (one of JURISDICTIONS); region instead
                   mixes several countries (one of REGIONS)
        seed       any integer; the same seed and configuration on the same
                   engine version give byte-identical files (on another engine
                   version they can build a different dataset)
        years      1 for a single year; 2 to 5 for a multi-year arc on the Team
                   plan and above (3 at most on the intercompany group; the
                   migration pair is one year)
        evaluation_mode  withhold the ground-truth marker columns from the
                   shipped database and workbooks and move the annotation layer
                   out, so no column names a scheme on its own rows (the answer
                   key still ships in 01_Audit_Evidence). Left out, it is on
                   with the blind track and off otherwise
        blind      add the blind evaluation track (Team plan and above): extra
                   schemes whose key stays sealed on the platform and is scored
                   there; it switches evaluation_mode on unless
                   evaluation_mode=False is given, as the platform does
        company_code  the SAP company code on every document (one to four
                   letters or digits; left out, the platform's default, 1000);
                   the intercompany group keeps its subsidiaries' own codes
        wait       block until the package is built, then return the Job
        quiet      print nothing (the notes are still on job.notes)
        """
        plat = _PLATFORM_ALIASES.get(str(platform).strip().lower(), platform)
        body = {
            "platform": plat,
            "modules": list(modules) if modules else ["P2P", "O2C", "CE", "R2R"],
            "num_rows": int(rows) if rows else None,
            "industry": str(industry).upper(),
            "fraud_intensity": str(fraud).lower(),
            "fiscal_year": int(fiscal_year),
            "seed": int(seed) if seed is not None else None,
            "num_years": int(years),
            "jurisdiction": jurisdiction,
            "region": region,
            "size_band": size_band,
            "entropy_level": int(entropy),
            "blind_track": 1 if blind else 0,
            "foreign_currency_share": float(fx_share),
            # a blind track is only honest with the marker columns withheld, so
            # it switches evaluation mode on unless the caller said otherwise,
            # as the app and the API do. Until 20 October 2026 the client sent
            # True whenever blind was set, so a stored configuration with the
            # blind track and evaluation mode off could not be asked for again.
            "evaluation_mode": bool(blind) if evaluation_mode is None else bool(evaluation_mode),
        }
        # sent only when given: the platform's own defaults apply otherwise (a
        # null would be refused, where an absent field takes the default)
        if volume_tier is not None:
            body["volume_tier"] = str(volume_tier)
        if company_code is not None:
            body["company_code"] = str(company_code)
        d = self._req("POST", "/api/v1/jobs", body)
        job = Job(self, d["job_id"], accepted=d)
        if not quiet:
            for note in job.notes:
                print(note, flush=True)
        if wait:
            job.wait(poll_interval=poll_interval, quiet=quiet)
        return job

    def job(self, job_id):
        """Attach to an existing job by id."""
        j = Job(self, job_id)
        j.refresh()
        return j

    def jobs(self):
        """Your generation history, newest first (list of dicts). Each carries
        the engine it ran on and what Reproduce would do for it."""
        d = self._req("GET", "/api/v1/jobs")
        return d.get("jobs", d) if isinstance(d, dict) else d


class Job:
    """One generation. Poll it, download the package or the lab export, score a
    detector against its answer key, or reproduce it."""

    def __init__(self, client, job_id, accepted=None):
        self._c = client
        self.id = job_id
        self._d = {}
        # What the platform answered when it accepted the request, and the
        # notes in it: the scale it chose when no row count was given, that
        # one generation is left this month (the note comes with the request
        # that leaves one), or what a Reproduce builds and whether it counts
        # as a generation.
        self.accepted = dict(accepted or {})
        self.notes = [str(self.accepted[k]) for k in ("size_note", "quota_note", "note")
                      if self.accepted.get(k)]

    def refresh(self):
        self._d = self._c._req("GET", "/api/v1/jobs/" + self.id)
        return self

    @property
    def status(self):
        return self._d.get("status")

    @property
    def progress(self):
        return self._d.get("progress") or 0

    @property
    def message(self):
        return self._d.get("message") or ""

    @property
    def error(self):
        return self._d.get("error") or ""

    @property
    def seed(self):
        return self._d.get("seed")

    @property
    def engine(self):
        """The engine version this job ran on, as the platform records it, or
        None where it does not say."""
        return self._d.get("engine")

    @property
    def reproduce_note(self):
        """The platform's own sentence on what reproduce() would do for this
        job, said before it is called: whether it is free (on the engine the
        job ran on) or builds a new dataset that counts as a generation (on
        another engine, or when the platform has no record of the job's
        engine)."""
        return self._d.get("reproduce_note") or ""

    @property
    def config(self):
        """The exact configuration the platform stored for this job (what
        reproduce() runs again), or {}."""
        raw = self._d.get("config_json")
        if isinstance(raw, dict):
            return dict(raw)
        try:
            cfg = _json.loads(raw or "{}")
        except ValueError:
            return {}
        return cfg if isinstance(cfg, dict) else {}

    def wait(self, timeout=3600, poll_interval=3.0, quiet=False):
        """Poll until the job completes. Raises on failure or cancellation."""
        start = _time.monotonic()
        last = ""
        while True:
            self.refresh()
            st = self.status
            if not quiet:
                line = "[%s] %s%% %s" % (st, self.progress, self.message)
                if line != last:
                    print(line, flush=True)
                    last = line
            if st == "complete":
                return self
            if st in ("failed", "cancelled"):
                raise SyntheticCfoError(self.error or ("Job %s." % st))
            if _time.monotonic() - start > timeout:
                raise SyntheticCfoError(
                    "Timed out after %ds; the job is still %s. Attach again later "
                    "with client.job(%r)." % (timeout, st, self.id))
            _time.sleep(poll_interval)

    def download(self, dest="."):
        """Save the four-layer audit package zip. Returns the saved Path."""
        return self._c._save("/api/v1/jobs/%s/download" % self.id, dest,
                             "synthetic_cfo_%s.zip" % self.id[:8])

    def lab_export(self, dest="."):
        """Save the lab export zip: one JSONL per table, answer_key.jsonl,
        DATASET_CARD.md, the reproducibility certificate and sha256 checksums."""
        return self._c._save("/api/v1/jobs/%s/lab-export" % self.id, dest,
                             "synthetic_cfo_lab_%s.zip" % self.id[:8])

    def insights(self):
        """The dataset's insight summary (modules, fraud counts, key tables)."""
        return self._c._req("GET", "/api/v1/jobs/%s/insights" % self.id)

    def score(self, record_ids=None, text=None, controls=None, leg=None):
        """Grade a detector's flagged records against this job's answer key (the
        job must be complete). Returns precision, recall, F1, the grade, the
        counts and recall per scheme; a blind-track package adds its sealed score.

        record_ids  the flagged ids as the tables spell them: a document number,
                    or a composite such as "4500000123/10" (a purchase-order
                    line, which also matches on the purchase order alone, never
                    on the line number alone). On a multi-year arc,
                    "FY2024:4500000123" or {"record_id": ..., "fiscal_year":
                    2024} grades a flag inside that year; a bare id counts in
                    every year that labels it
        text        instead of record_ids: ids separated by newlines, commas or
                    spaces, or a CSV with a record_id column (a fiscal_year
                    column beside it names the year on a multi-year arc). A
                    file of that one column works too: its header line is
                    left out of what is sent, so the word record_id is never
                    graded as a flag
        controls    grade only these control ids (e.g. ["C-01", "C-05"])
        leg         on a migration pair, the leg your detector ran on
                    (SAP_ECC unless named)
        """
        body = {}
        if record_ids is not None:
            body["record_ids"] = list(record_ids)
        if text is not None:
            body["text"] = _submission_text(text)
        if not body.get("record_ids") and not (body.get("text") or "").strip():
            raise SyntheticCfoError("Nothing to score: pass record_ids or text.")
        if controls:
            body["controls"] = list(controls)
        if leg:
            body["leg"] = str(leg)
        return self._c._req("POST", "/api/v1/jobs/%s/score" % self.id, body)

    def reproduce(self, wait=False, poll_interval=3.0, quiet=False):
        """Run this job again, as a new job, with the seed and configuration the
        platform stored for it. On the engine version the job ran on, that uses
        no generation and, for a job that completed, gives the same files; on
        another engine, or when the platform has no record of the job's engine,
        the same seed and configuration build a new dataset (its certificate
        names the engine), which counts as one generation on a metered plan and
        is refused, like any generation, once the month's are spent. The new
        job's notes say which (printed unless quiet); reproduce_note says it
        before you call it. A job from before the platform stored
        configurations cannot be reproduced: the platform refuses it and says
        why."""
        d = self._c._req("POST", "/api/v1/jobs/%s/reproduce" % self.id)
        job = Job(self._c, d["job_id"], accepted=d)
        if not quiet:
            for note in job.notes:
                print(note, flush=True)
        if wait:
            job.wait(poll_interval=poll_interval, quiet=quiet)
        return job

    def cancel(self):
        """Stop a running generation. On the free plan a cancelled generation
        still counts toward the month; a failed one is refunded."""
        return self._c._req("POST", "/api/v1/jobs/%s/cancel" % self.id)

    def __repr__(self):
        return "Job(%r, status=%r, progress=%s)" % (self.id, self.status,
                                                    self.progress)


# -- command line ---------------------------------------------------------------
# the job the command line is waiting on, so a Ctrl-C can say how to get it
_CLI_STATE = {}


def _cli_client(args):
    key = (getattr(args, "key", None) or "").strip() or None
    try:
        return Client(api_key=key)
    except SyntheticCfoError:
        raise SyntheticCfoError(
            "No API key found. Create one at https://app.syntheticcfo.com "
            "(sign in, open the menu, click your email for Account, then the "
            "API access card, Create key; no account yet? A free one needs no "
            "card: https://app.syntheticcfo.com/?signup=1), then either add "
            "--key scfo_... to this command or set the SCFO_API_KEY "
            "environment variable. "
            "Step-by-step guide: https://app.syntheticcfo.com/evaluate")


def _certificate_engine(zip_path):
    """The engine version the package's own certificate names (the shallowest
    Reproducibility_Certificate.txt in the zip), or None."""
    import zipfile
    try:
        with zipfile.ZipFile(str(zip_path)) as z:
            certs = sorted((n for n in z.namelist()
                            if n.endswith("Reproducibility_Certificate.txt")),
                           key=lambda n: (n.count("/"), n))
            if not certs:
                return None
            text = z.read(certs[0]).decode("utf-8", "replace")
    except (OSError, zipfile.BadZipFile, KeyError):
        return None
    m = _re.search(r"^Engine version\s*:\s*(\S+)", text, _re.M)
    return m.group(1) if m else None


def _cli_finish(job, args):
    """Wait for a job, save its package (and the lab export with --lab), and
    say what its seed rebuilds."""
    _CLI_STATE["job"] = job.id
    _CLI_STATE["built"] = False
    print("Job %s" % job.id, flush=True)
    job.wait()
    _CLI_STATE["built"] = True
    saved = job.download(args.out)
    print("Saved the audit package: %s" % saved)
    if getattr(args, "lab", False):
        lab = job.lab_export(args.out)
        print("Saved the lab export:  %s" % lab)
    _CLI_STATE.pop("job", None)
    engine = job.engine or _certificate_engine(saved)
    if job.seed is not None:
        print("Seed %s with the same configuration rebuilds these files on %s; "
              "another engine version can build a different dataset."
              % (job.seed, ("engine " + engine) if engine else "the same engine version"))


def _cli_generate(args):
    c = _cli_client(args)
    mods = None
    if args.modules:
        mods = [m.strip().upper() for chunk in args.modules
                for m in chunk.split(",") if m.strip()]
    _CLI_STATE["asked"] = True
    job = c.generate(platform=args.platform, modules=mods, rows=args.rows,
                     industry=args.industry, fraud=args.fraud,
                     jurisdiction=args.jurisdiction, seed=args.seed,
                     years=args.years, evaluation_mode=args.evaluation_mode,
                     wait=False)
    _cli_finish(job, args)


def _cli_download(args):
    c = _cli_client(args)
    _cli_finish(c.job(args.job_id), args)


def _cli_reproduce(args):
    c = _cli_client(args)
    _CLI_STATE["asked"] = True
    job = Job(c, args.job_id).reproduce(wait=False)
    _cli_finish(job, args)


def _cli_score(args):
    import sys
    c = _cli_client(args)
    if args.ids_file == "-":
        text = sys.stdin.read()
    else:
        with open(args.ids_file, encoding="utf-8") as fh:
            text = fh.read()
    res = Job(c, args.job_id).score(text=text, leg=args.leg)
    if args.json:
        print(_json.dumps(res, indent=2))
        return
    print("precision %.4f  recall %.4f  F1 %.4f  grade %s"
          % (res.get("precision", 0), res.get("recall", 0), res.get("f1", 0), res.get("grade", "")))
    print("%s flagged: %s named a planted record, %s named nothing in the key; "
          "%s of %s planted records found"
          % (res.get("submitted"), res.get("true_positives"), res.get("false_positives"),
             res.get("labels_detected"), res.get("ground_truth_records")))
    for v in res.get("per_vector") or []:
        print("  %-9s %-28s %s of %s" % (v.get("control", ""), v.get("vector", ""),
                                         v.get("detected"), v.get("total")))
    if res.get("blind_track"):
        print("The blind track's sealed score is in the full result: add --json.")
    if res.get("note"):
        print(res["note"])


def _cli_jobs(args):
    for row in _cli_client(args).jobs():
        line = "%s  %-9s %s" % (row.get("job_id", ""), row.get("status", ""),
                                row.get("created_at", ""))
        if row.get("engine"):
            line += "  engine %s" % row["engine"]
        print(line)
        if row.get("reproduce_note"):
            print("    %s" % row["reproduce_note"])


def main(argv=None):
    """Entry point for the `syntheticcfo` command (and python -m syntheticcfo)."""
    import argparse
    import sys

    _CLI_STATE.clear()
    p = argparse.ArgumentParser(
        prog="syntheticcfo",
        description="Generate forensic-grade synthetic ERP data from one command. "
                    "Get an API key at https://app.syntheticcfo.com (Account, "
                    "API access; a free account works: "
                    "https://app.syntheticcfo.com/?signup=1); full walkthrough at "
                    "https://app.syntheticcfo.com/evaluate")
    sub = p.add_subparsers(dest="cmd")

    def saves(sp):
        sp.add_argument("--lab", action="store_true",
                        help="also save the lab export (JSONL per table + answer key)")
        sp.add_argument("--out", default=".",
                        help="folder or file path for the downloads (default: right here)")
        sp.add_argument("--key", default=None, help="API key (else SCFO_API_KEY env var)")

    g = sub.add_parser("generate",
                       help="generate a world, wait, and save the package zip here")
    g.add_argument("--platform", default="SAP_ECC",
                   help="SAP_ECC, SAP_S4HANA or ORACLE (plain words like 'oracle' "
                        "work), or a combined environment, SAP_MIGRATION_PAIR or "
                        "INTERCOMPANY_GROUP, on the Team plan and above")
    g.add_argument("--modules", nargs="*", default=None, metavar="M",
                   help="subset of %s (default: the core four)" % " ".join(MODULES))
    g.add_argument("--rows", type=int, default=None,
                   help="the scale: base transactions per cycle, the setting the "
                        "plans call rows (not a count of the rows the tables end "
                        "up holding), up to your plan's limit ('syntheticcfo me' "
                        "shows it). A value outside the platform's range is first "
                        "brought into it; a value then above your plan's limit is "
                        "refused with a message naming the limit. Left out, the "
                        "world takes the standard volume; when your plan allows "
                        "less, as the free plan does, it is sized at your plan's "
                        "limit instead and the platform's note saying so is printed")
    g.add_argument("--industry", default="TMT",
                   help="one of %s" % ", ".join(INDUSTRIES))
    g.add_argument("--fraud", default="medium", choices=FRAUD_LEVELS)
    g.add_argument("--jurisdiction", default=None,
                   help="a country code, one of %s" % ", ".join(JURISDICTIONS))
    g.add_argument("--seed", type=int, default=None,
                   help="any integer; the same seed and configuration on the same "
                        "engine version give byte-identical files (on another "
                        "engine version they can build a different dataset). Left "
                        "out, a fresh one is drawn and printed at the end")
    g.add_argument("--years", type=int, default=1,
                   help="1 for a single year; 2 to 5 for a multi-year arc on the "
                        "Team plan and above (3 at most on the intercompany group; "
                        "the migration pair is one year)")
    g.add_argument("--evaluation-mode", action="store_true", dest="evaluation_mode",
                   help="withhold the ground-truth marker columns and move the "
                        "annotations out of the shipped data (the answer key "
                        "still ships)")
    saves(g)

    d = sub.add_parser("download",
                       help="wait for a job if it is still building, then save its package")
    d.add_argument("job_id", help="the job's id, as 'syntheticcfo jobs' lists it")
    saves(d)

    r = sub.add_parser("reproduce",
                       help="run a job again with its seed and configuration, then save "
                            "the new package (free on the engine the job ran on; on "
                            "another engine a new dataset that counts as one "
                            "generation on a metered plan; 'syntheticcfo jobs' says "
                            "which)")
    r.add_argument("job_id", help="the job's id, as 'syntheticcfo jobs' lists it")
    saves(r)

    s = sub.add_parser("score",
                       help="grade your detector's flagged record ids against a job's answer key")
    s.add_argument("job_id", help="the job's id, as 'syntheticcfo jobs' lists it")
    s.add_argument("ids_file",
                   help="a file of record ids (one per line, or separated by commas or "
                        "spaces; FY2024:<id> names a year on a multi-year arc), or a CSV "
                        "with a record_id column (a file of that one column works too); "
                        "- reads standard input")
    s.add_argument("--leg", default=None,
                   help="on a migration pair, the leg your detector ran on (SAP_ECC "
                        "unless named)")
    s.add_argument("--json", action="store_true", help="print the full result as JSON")
    s.add_argument("--key", default=None, help="API key (else SCFO_API_KEY env var)")

    m = sub.add_parser("me", help="show the account this key belongs to: plan, "
                                  "this month's generations, limits")
    m.add_argument("--key", default=None)

    j = sub.add_parser("jobs", help="list your recent generations, with the engine each "
                                    "ran on and what Reproduce would do")
    j.add_argument("--key", default=None)

    args = p.parse_args(argv)
    if not args.cmd:
        p.print_help()
        return 0
    try:
        if args.cmd == "generate":
            _cli_generate(args)
        elif args.cmd == "download":
            _cli_download(args)
        elif args.cmd == "reproduce":
            _cli_reproduce(args)
        elif args.cmd == "score":
            _cli_score(args)
        elif args.cmd == "me":
            print(_json.dumps(_cli_client(args).me(), indent=2))
        elif args.cmd == "jobs":
            _cli_jobs(args)
        return 0
    except SyntheticCfoError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    except OSError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        jid = _CLI_STATE.get("job")
        if jid and _CLI_STATE.get("built"):
            print("stopped. Job %s is built; save it with: syntheticcfo download %s"
                  % (jid, jid), file=sys.stderr)
        elif jid:
            print("stopped waiting. Job %s keeps building on the platform; when it is "
                  "done, save it with: syntheticcfo download %s" % (jid, jid),
                  file=sys.stderr)
        elif _CLI_STATE.get("asked"):
            print("stopped. If the platform accepted the request, 'syntheticcfo jobs' "
                  "lists it, and 'syntheticcfo download <job id>' saves it.",
                  file=sys.stderr)
        else:
            print("stopped.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())
