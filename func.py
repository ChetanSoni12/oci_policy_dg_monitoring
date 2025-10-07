#!/usr/bin/env python3
import oci
import datetime
import os
import json
from collections import defaultdict
import sys

# ================================
# Config from environment variables
# ================================
NAMESPACE       = os.getenv("NAMESPACE", "custom_metrics")
RESOURCE_GROUP  = os.getenv("RESOURCE_GROUP", "Policy_DG_audit")
POLICY_LIMIT    = int(os.getenv("POLICY_LIMIT", "300"))
STATEMENT_LIMIT = int(os.getenv("STATEMENT_LIMIT", "3000"))
DG_LIMIT        = int(os.getenv("DG_LIMIT", "100"))
BATCH_SIZE      = int(os.getenv("BATCH_SIZE", "50"))

# ================================
# Helpers
# ================================
def log(msg):
    print(msg)
    sys.stdout.flush()

def load_clients():
    log("[INFO] Loading OCI clients")
    signer = oci.auth.signers.get_resource_principals_signer()
    tenancy_id = signer.tenancy_id
    region = signer.region

    ingestion_endpoint = f"https://telemetry-ingestion.{region}.oraclecloud.com"
    identity = oci.identity.IdentityClient(config={}, signer=signer)
    monitoring = oci.monitoring.MonitoringClient(config={}, signer=signer, service_endpoint=ingestion_endpoint)

    log(f"[INFO] Tenancy: {tenancy_id}, Region: {region}")
    return tenancy_id, region, identity, monitoring, signer

def push_batches(monitoring, metric_streams, batch_size=BATCH_SIZE):
    log(f"[INFO] Pushing {len(metric_streams)} metrics in batches of {batch_size}")
    for i in range(0, len(metric_streams), batch_size):
        batch = metric_streams[i:i+batch_size]
        details = oci.monitoring.models.PostMetricDataDetails(metric_data=batch)
        monitoring.post_metric_data(details)
        log(f"[INFO] Pushed batch {i//batch_size + 1}, size={len(batch)}")
    if not metric_streams:
        log("[INFO] No metrics to push.")
    else:
        log("[INFO] All metrics pushed successfully.")

def make_datapoint(ts, value):
    return oci.monitoring.models.Datapoint(timestamp=ts, value=float(value))

def make_stream(tenancy_id, name, dims, ts, value):
    return oci.monitoring.models.MetricDataDetails(
        compartment_id=tenancy_id,
        namespace=NAMESPACE,
        resource_group=RESOURCE_GROUP,
        name=name,
        dimensions=dims,
        datapoints=[make_datapoint(ts, value)]
    )

# ================================
# Dynamic Group count (SDK)
# ================================
def get_dynamic_group_count_sdk(signer, domain_url):
    """Use SDK to get dynamic group count for a domain."""
    try:
        client = oci.identity_domains.IdentityDomainsClient(
            config={},
            signer=signer,
            service_endpoint=domain_url
        )
        response = client.list_dynamic_resource_groups()
        total = response.data.total_results
        log(f"[DEBUG] Domain {domain_url} has {total} dynamic groups")
        return int(total)
    except Exception as e:
        log(f"[WARN] Could not fetch dynamic groups for {domain_url}: {e}")
        return 0

# ================================
# Core logic
# ================================
def run_audit():
    tenancy_id, region, identity, monitoring, signer = load_clients()
    now = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    log("[INFO] Listing compartments")
    compartments = oci.pagination.list_call_get_all_results(
        identity.list_compartments,
        compartment_id=tenancy_id,
        compartment_id_in_subtree=True,
        access_level="ANY"
    ).data
    compartments.append(identity.get_compartment(tenancy_id).data)
    log(f"[INFO] Found {len(compartments)} compartments")

    policy_counts = defaultdict(int)
    statement_counts = defaultdict(int)
    dg_counts = defaultdict(int)   # now keyed by domain, not compartment

    streams = []

    for comp in compartments:
        if getattr(comp, "lifecycle_state", "ACTIVE") != "ACTIVE":
            continue

        log(f"[INFO] Scanning compartment {comp.name} ({comp.id})")

        # Policies
        policies = oci.pagination.list_call_get_all_results(
            identity.list_policies,
            compartment_id=comp.id
        ).data
        pol_count = len(policies)
        stmt_count = sum(len(p.statements or []) for p in policies)
        policy_counts[comp.name] += pol_count
        statement_counts[comp.name] += stmt_count

        # Dynamic Groups per domain
        try:
            domains = identity.list_domains(compartment_id=comp.id).data
            log(f"[DEBUG] Found {len(domains)} domains in compartment {comp.name}")
            for domain in domains:
                log(f"[DEBUG] Fetching dynamic groups for domain: {domain.url}")
                dg_domain_count = get_dynamic_group_count_sdk(signer, domain.url)
                log(f"[INFO] Domain {domain.display_name} ({domain.url}): {dg_domain_count} dynamic groups")

                dg_counts[domain.display_name] += dg_domain_count

                # emit per-domain metric
                streams.append(
                    make_stream(
                        tenancy_id,
                        "oci_dg_metrics",
                        {"Domain": domain.display_name},
                        now,
                        dg_domain_count
                    )
                )
        except Exception as e:
            log(f"[WARN] Could not fetch domains for compartment {comp.name}: {e}")

    total_policies = sum(policy_counts.values())
    total_statements = sum(statement_counts.values())
    total_dgs = sum(dg_counts.values())

    # Build metrics
    for comp_name, count in policy_counts.items():
        streams.append(make_stream(tenancy_id, "oci_policies_metrics", {"Compartment": comp_name}, now, count))
    for comp_name, count in statement_counts.items():
        streams.append(make_stream(tenancy_id, "oci_statements_metrics", {"Compartment": comp_name}, now, count))

    # Totals
    streams.append(make_stream(tenancy_id, "oci_policies_total", {"type": "current"}, now, total_policies))
    streams.append(make_stream(tenancy_id, "oci_policies_total", {"type": "limit"}, now, POLICY_LIMIT))
    streams.append(make_stream(tenancy_id, "oci_statements_total", {"type": "current"}, now, total_statements))
    streams.append(make_stream(tenancy_id, "oci_statements_total", {"type": "limit"}, now, STATEMENT_LIMIT))
    streams.append(make_stream(tenancy_id, "oci_dg_total", {"type": "current"}, now, total_dgs))
    streams.append(make_stream(tenancy_id, "oci_dg_total", {"type": "limit"}, now, DG_LIMIT))

    # Top 10s
    top10_policies = sorted(policy_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    for comp_name, count in top10_policies:
        streams.append(make_stream(tenancy_id, "oci_policies_top10", {"Compartment": comp_name}, now, count))
    top10_statements = sorted(statement_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    for comp_name, count in top10_statements:
        streams.append(make_stream(tenancy_id, "oci_statements_top10", {"Compartment": comp_name}, now, count))

    # Console summary
    log("\n=== Summary (UTC) ===")
    log(f"Timestamp: {now}")
    log(f"Region:    {region}")
    log(f"Namespace: {NAMESPACE}")
    log(f"Resource Group: {RESOURCE_GROUP}")
    log(f"Total policies:   {total_policies} (limit {POLICY_LIMIT})")
    log(f"Total statements: {total_statements} (limit {STATEMENT_LIMIT})")
    log(f"Total dynamic groups: {total_dgs} (limit {DG_LIMIT})")

    log("\nTop 10 by policies:")
    for name, val in top10_policies:
        log(f"  {name}: {val}")
    log("Top 10 by statements:")
    for name, val in top10_statements:
        log(f"  {name}: {val}")

    log("\n--- Pushing metrics to OCI Monitoring ---")
    push_batches(monitoring, streams, BATCH_SIZE)

# ================================
# Function entry point
# ================================
def handler(ctx, data: bytes = None):
    try:
        log("[INFO] Function invoked")
        run_audit()
        log("[INFO] Function completed successfully")
        return "Success"
    except Exception as e:
        log(f"[ERROR] {e}")
        return f"Failed: {str(e)}"
