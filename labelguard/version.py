"""The application version, defined in one place.

Kept in its own module (no imports) so that `app.py` can read it without depending on the package
`__init__`, and so there can be no circular import.
"""
__version__ = "0.1.0"
