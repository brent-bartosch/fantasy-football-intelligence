"""Renderers for the decision artifacts + the briefing's health section.

Layer 4 (ARCHITECTURE §2): renders precomputed state, never kicks off a
simulation or an ingest. Deliberately empty of re-exports — importing
`ffi.reports` must not drag in every renderer.
"""
