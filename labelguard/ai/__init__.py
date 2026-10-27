"""Optional, evidence-grounded AI assistance.

The language model only RE-STATES findings that the deterministic engine has already produced. It
cannot create, change, clear or approve a finding: nothing in this package imports the database or
the review workflow, and every response is validated against the evidence it was given before it
is shown. With no API key, or on any failure, a deterministic explanation is produced instead.
"""
