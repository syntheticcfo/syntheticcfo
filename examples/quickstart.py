"""Generate one world, download it, and join the answer key onto a table.

    pip install syntheticcfo pandas
    SCFO_API_KEY=scfo_... python quickstart.py
"""
import os
import zipfile

import pandas as pd
from syntheticcfo import Client

c = Client()                                        # reads SCFO_API_KEY
job = c.generate(platform="SAP_ECC", industry="MANUFACTURING", fraud="medium",
                 rows=600, modules=["P2P", "O2C"], jurisdiction="DE", seed=42,
                 wait=True)

lab_zip = job.lab_export("out/")
with zipfile.ZipFile(lab_zip) as z:
    z.extractall("out/lab")

# Document numbers are strings. A numeric parse drops leading zeros and the
# join silently fails.
bkpf = pd.read_json("out/lab/tables/P2P_BKPF.jsonl", lines=True, dtype={"BELNR": str})
key = pd.read_json("out/lab/answer_key.jsonl", lines=True, dtype={"record_id": str})

labelled = bkpf.merge(key[key.source_table == "P2P_BKPF"],
                      left_on="BELNR", right_on="record_id", how="left")
print(labelled["fraud_vector"].value_counts(dropna=False))
