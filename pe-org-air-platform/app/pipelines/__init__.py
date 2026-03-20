"""
Pipelines — PE Org-AI-R Platform
app/pipelines/

Pipeline modules contain pure business logic: external API integrations,
data extraction, and scoring algorithms. They have no knowledge of HTTP,
FastAPI, or the request/response cycle.

Calling convention:
  Routers → Services → Pipelines
  (Pipelines are never called directly by routers)

Each pipeline exposes a stable public interface (usually a single class
or async runner function). Services own the lifecycle (singleton setup,
S3 key construction, Snowflake writes after pipeline output).
"""
