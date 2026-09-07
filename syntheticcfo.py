"""synthetic cfo - Python client.

Generate forensic-grade synthetic ERP data (SAP ECC, SAP S/4HANA, Oracle Cloud)
from a script or notebook. One file, standard library only - copy it anywhere.

No code needed - the command line does the whole thing:

    pip install syntheticcfo
    syntheticcfo generate --key scfo_YOURKEY --platform oracle --fraud high --rows 600

(pip install syntheticcfo from the package index is the earlier 0.1.0 until 0.2.0
is published there: same client, no command line.)

That one line generates the world, waits, and saves the package zip into the
folder you are standing in (change it with --out). Add --lab for the JSONL lab
export. If "syntheticcfo" is not recognized on your machine,
"python -m syntheticcfo generate ..." does exactly the same thing.

From code:

    from syntheticcfo import Client

    c = Client(api_key="scfo_...")          # or set the SCFO_API_KEY env var
    job = c.generate(platform="ORACLE", industry="RETAIL", fraud="high",
                     rows=600, modules=["P2P", "O2C"], jurisdiction="ZA",
                     seed=42, wait=True)
    package = job.download("out/")           # four-layer audit package (zip)
    lab = job.lab_export("out/")             # JSONL tables + answer key + dataset card

Create an API key on your account page (Account -> API access) at
https://app.syntheticcfo.com. The key carries your plan and monthly allowance;
it cannot manage the account itself.

Every dataset is deterministic: the same seed and configuration reproduce the
package byte for byte, and the reproducibility certificate in the package
proves it. No language model ever generates the numbers.
"""

import json as _json
import os as _os
import re as _re
import ssl as _ssl
import time as _time
import urllib.error as _uerr
import urllib.request as _ureq
from pathlib import Path as _Path

__version__ = "0.2.1"
__all__ = ["Client", "Job", "SyntheticCfoError", "main"]

_DEFAULT_BASE = "https://app.syntheticcfo.com"

# The vocabulary the server validates against (engine/interpreter.py ALLOWED).
# The server is the authority; these are listed for discoverability.
PLATFORMS = ["SAP_ECC", "SAP_S4HANA", "ORACLE"]
MODULES = ["P2P", "O2C", "CE", "R2R", "HR", "MM", "FA"]
INDUSTRIES = ["TMT", "PHARMA", "MANUFACTURING", "FINANCIAL_SERVICES", "RETAIL"]
FRAUD_LEVELS = ["none", "low", "medium", "high"]

_PLATFORM_ALIASES = {
    "sap": "SAP_ECC", "ecc": "SAP_ECC", "sap ecc": "SAP_ECC", "sap_ecc": "SAP_ECC",
    "s4": "SAP_S4HANA", "s4hana": "SAP_S4HANA", "s/4hana": "SAP_S4HANA",
    "sap_s4hana": "SAP_S4HANA", "hana": "SAP_S4HANA",
    "oracle": "ORACLE", "oracle cloud": "ORACLE", "oracle_cloud": "ORACLE",
    "fusion": "ORACLE",
}


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
        """Your account: email, plan, monthly generations, capability caps."""
        return self._req("GET", "/api/v1/auth/me")

    # -- generation -------------------------------------------------------------
    def generate(self, platform="SAP_ECC", modules=None, rows=None, industry="TMT",
                 fraud="medium", jurisdiction=None, region=None, seed=None,
                 years=1, entropy=0, blind=False, evaluation_mode=False,
                 size_band=None, fx_share=0.0, fiscal_year=2025,
                 wait=False, poll_interval=3.0, quiet=False):
        """Start a generation and return the Job.

        platform   SAP_ECC | SAP_S4HANA | ORACLE (aliases like "oracle" work)
        modules    subset of P2P O2C CE R2R HR MM FA (default: the core four)
        rows       target rows per major table (plan caps apply, honestly clamped)
        fraud      none | low | medium | high
        seed       any integer; the same seed + config reproduces byte-identically
        evaluation_mode  withhold ground-truth marker columns from the shipped
                   database so a model cannot read the answer key (the labels
                   still ship in 01_Audit_Evidence)
        wait       block until the package is built, then return the Job
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
            # a blind track is only honest with the marker columns withheld, so it
            # switches evaluation mode on (the app and the API do the same)
            "evaluation_mode": bool(evaluation_mode) or bool(blind),
        }
        d = self._req("POST", "/api/v1/jobs", body)
        job = Job(self, d["job_id"])
        if wait:
            job.wait(poll_interval=poll_interval, quiet=quiet)
        return job

    def job(self, job_id):
        """Attach to an existing job by id."""
        j = Job(self, job_id)
        j.refresh()
        return j

    def jobs(self):
        """Your generation history, newest first (list of dicts)."""
        d = self._req("GET", "/api/v1/jobs")
        return d.get("jobs", d) if isinstance(d, dict) else d


class Job:
    """One generation. Poll it, then download the package or the lab export."""

    def __init__(self, client, job_id):
        self._c = client
        self.id = job_id
        self._d = {}

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

    def cancel(self):
        """Stop a running generation. The credit is refunded on metered plans."""
        return self._c._req("POST", "/api/v1/jobs/%s/cancel" % self.id)

    def __repr__(self):
        return "Job(%r, status=%r, progress=%s)" % (self.id, self.status,
                                                    self.progress)


# -- command line ---------------------------------------------------------------
def _cli_client(args):
    key = (getattr(args, "key", None) or "").strip() or None
    try:
        return Client(api_key=key)
    except SyntheticCfoError:
        raise SyntheticCfoError(
            "No API key found. Create one at https://app.syntheticcfo.com "
            "(sign in, open the menu, click your email for Account, then the "
            "API access card, Create key), then either add --key scfo_... to "
            "this command or set the SCFO_API_KEY environment variable. "
            "Step-by-step guide: https://app.syntheticcfo.com/evaluate")


def _cli_generate(args):
    c = _cli_client(args)
    mods = None
    if args.modules:
        mods = [m.strip().upper() for chunk in args.modules
                for m in chunk.split(",") if m.strip()]
    job = c.generate(platform=args.platform, modules=mods, rows=args.rows,
                     industry=args.industry, fraud=args.fraud,
                     jurisdiction=args.jurisdiction, seed=args.seed,
                     years=args.years, evaluation_mode=args.evaluation_mode,
                     wait=True)
    saved = job.download(args.out)
    print("Saved the audit package: %s" % saved)
    if args.lab:
        lab = job.lab_export(args.out)
        print("Saved the lab export:  %s" % lab)
    print("Seed %s regenerates this world byte-identically." % job.seed)


def main(argv=None):
    """Entry point for the `syntheticcfo` command (and python -m syntheticcfo)."""
    import argparse
    import sys

    p = argparse.ArgumentParser(
        prog="syntheticcfo",
        description="Generate forensic-grade synthetic ERP data from one command. "
                    "Get an API key at https://app.syntheticcfo.com (Account, "
                    "API access); full walkthrough at "
                    "https://app.syntheticcfo.com/evaluate")
    sub = p.add_subparsers(dest="cmd")

    g = sub.add_parser("generate",
                       help="generate a world, wait, and save the package zip here")
    g.add_argument("--platform", default="SAP_ECC",
                   help="SAP_ECC, SAP_S4HANA or ORACLE (plain words like 'oracle' work)")
    g.add_argument("--modules", nargs="*", default=None, metavar="M",
                   help="subset of %s (default: the core four)" % " ".join(MODULES))
    g.add_argument("--rows", type=int, default=None,
                   help="target rows per major table (plan caps apply)")
    g.add_argument("--industry", default="TMT",
                   help="one of %s" % ", ".join(INDUSTRIES))
    g.add_argument("--fraud", default="medium", choices=FRAUD_LEVELS)
    g.add_argument("--jurisdiction", default=None,
                   help="country code, e.g. US, DE, ZA, JP")
    g.add_argument("--seed", type=int, default=None,
                   help="any integer; same seed + config = byte-identical world")
    g.add_argument("--years", type=int, default=1,
                   help="1 for a single year, 2-3 for a multi-year arc")
    g.add_argument("--evaluation-mode", action="store_true", dest="evaluation_mode",
                   help="withhold ground-truth marker columns from the shipped data")
    g.add_argument("--lab", action="store_true",
                   help="also save the lab export (JSONL per table + answer key)")
    g.add_argument("--out", default=".",
                   help="folder or file path for the downloads (default: right here)")
    g.add_argument("--key", default=None, help="API key (else SCFO_API_KEY env var)")

    m = sub.add_parser("me", help="show the account this key belongs to")
    m.add_argument("--key", default=None)

    j = sub.add_parser("jobs", help="list your recent generations")
    j.add_argument("--key", default=None)

    args = p.parse_args(argv)
    if not args.cmd:
        p.print_help()
        return 0
    try:
        if args.cmd == "generate":
            _cli_generate(args)
        elif args.cmd == "me":
            print(_json.dumps(_cli_client(args).me(), indent=2))
        elif args.cmd == "jobs":
            for row in _cli_client(args).jobs():
                print("%s  %-9s %s" % (str(row.get("job_id", ""))[:12],
                                       row.get("status", ""),
                                       row.get("created_at", "")))
        return 0
    except SyntheticCfoError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("stopped. The job keeps building; reattach with the jobs command.",
              file=sys.stderr)
        return 130


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())
