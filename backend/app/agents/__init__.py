"""The eight JobPilot agents.

Each agent is a class with a single clear responsibility and typed I/O. They
share the DB session, candidate profile, and event log but run independently so
the orchestrator can enable/disable/rerun any one of them.
"""
