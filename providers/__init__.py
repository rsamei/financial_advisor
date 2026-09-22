"""Data-source modules. Each exposes CONTRACT and fetch(args, transport=None).

Registration happens in tools/market_retrieve.py, which is the only place that decides which
provider runs. Importing a module here does not make it callable from a command: the registry
does, and the registry is a hard-coded table.
"""
