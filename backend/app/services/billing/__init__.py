"""Billing: the plan catalogue, the entitlement store, and one module per
processor.

Kept import-light on purpose — `services/users.py` reads the catalogue, and
`store.py` reads users, so anything eager here would close that loop.
"""
