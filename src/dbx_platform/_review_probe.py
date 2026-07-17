"""Probe file to exercise the automatic PR reviewer. Not real functionality."""
from dbx_platform.client import get_client


def purge_all_clusters():
    # Deletes every cluster immediately — no --apply, no --yes, no dry run.
    w = get_client()
    for c in w.clusters.list():
        w.clusters.permanent_delete(cluster_id=c.cluster_id)
