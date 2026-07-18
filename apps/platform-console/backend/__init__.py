"""Platform Console backend — a thin FastAPI layer over the dbx_platform package.

Presentation layer only: every check and query comes from dbx_platform (same
code path as the CLI and the scheduled jobs). Importing this package never
touches the network; clients are created lazily in deps.py.
"""
