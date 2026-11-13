"""SEHAT AI demo kit (docs/07, Phase 7). Run from `backend/` (or through `make demo`, `make demo-seed`, `make demo-check`):

    python -m demo_kit run     # start the offline demo stack on a scratch database, seed it, check it; Ctrl-C stops it
    python -m demo_kit seed    # seed a running backend with the synthetic demo cases (refuses a non-empty database)
    python -m demo_kit check   # the pre-demo check against a running stack

Everything here is synthetic and talks only to this computer (127.0.0.1). The demo database is a scratch file outside
the repository, never `sehat.db`. The backend reads the developer `.env` itself for everything the kit does not set;
the kit never reads or prints it.
"""
