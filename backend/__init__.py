"""Phase 3 backend: FastAPI server for the AI fighting arena.

Modules:
  settings_store - brandable/tunable settings (JSON file + env overlay)
  db             - SQLite persistence (fighters, battles, snapshots, hires)
  battle_runner  - runs engine battles in background threads, live snapshots
  app            - FastAPI routes + websocket
"""
