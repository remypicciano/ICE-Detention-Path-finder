"""Offline index pipeline.

Runs locally against Parquet files, produces R2 shards. The Worker never
imports this package; it only reads the finished objects.
"""
