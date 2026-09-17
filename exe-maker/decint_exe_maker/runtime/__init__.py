"""DECINT runtime — the licensing layer that ships inside every built EXE.

Everything in this package must stay dependency-free (stdlib only) and small:
it is copied verbatim into the customer's binary as ``decint_rt``. The vendor
side of the tool imports the same modules, so there is exactly one copy of the
license format and one copy of the RSA code.
"""
