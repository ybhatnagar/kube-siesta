"""Shared analysis primitives used by every recommender head.

Stages 0–5 (prepare → periodicity → seasonality → active/idle → filter →
aggregate) + the interaction-graph helpers + config + state-store I/O.
Recommender heads (`recommenders/job`, `recommenders/maintenance`) build on
top of these; nothing in this package imports from a head.
"""
