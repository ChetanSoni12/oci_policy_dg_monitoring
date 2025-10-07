######### Code to create OCI IAM dashboards for Vizualization of the data added in OCI IAM custom metrics #########

#!/usr/bin/env python3
import uuid
import json
import oci
from oci.util import to_dict

# ----------------------------
# OCI Config (Cloud Shell)
# ----------------------------
# Load OCI configuration from default profile. Update if using a custom profile.
try:
    # print("[DEBUG] Loading OCI config...")
    config = oci.config.from_file("~/.oci/config", "DEFAULT")
    dashboard_client = oci.dashboard_service.DashboardClient(config)
    dashboard_group_client = oci.dashboard_service.DashboardGroupClient(config)
    # print("[DEBUG] Config and clients loaded successfully.")
except Exception as e:
    # print(f"[ERROR] Failed to load OCI config or initialize clients: {e}")
    exit(1)

# ----------------------------
# Variables
# ----------------------------
# Tenancy OCID from config
tenancy_id = config["tenancy"]

# Region used for all region-specific API calls
# Default is set to "us-ashburn-1". Change this if your tenancy home region is different.
region = config.get("region", "us-ashburn-1")

# ----------------------------
# Create Dashboard Group
# ----------------------------
try:
    # print("[DEBUG] Creating a new dashboard group...")
    create_group_details = oci.dashboard_service.models.CreateDashboardGroupDetails(
        display_name="IAM_Policy_DG_Group",  # Adjust this name if needed
        description="Dashboard group for IAM Policy and Dynamic Group monitoring",
        compartment_id=tenancy_id            # Required field. Creates in root compartment by default
    )

    group_response = dashboard_group_client.create_dashboard_group(create_group_details)
    dashboard_group_id = group_response.data.id
    # print(f"[DEBUG] Dashboard group created successfully: {dashboard_group_id}")

except Exception as e:
    # print(f"[ERROR] Failed to create dashboard group: {e}")
    exit(1)

# ----------------------------
# Widget definitions
# ----------------------------
widgets_info = [
    ("OCI IAM Policy - Current vs Limit", "oci_policies_total[${interval}].sum()", "GroupBar", 0, 0),
    ("OCI IAM Policy Statement - Current vs Limit", "oci_statements_total[${interval}].sum()", "GroupBar", 0, 9),
    ("Dynamic Group - Per Domain", "oci_dg_metrics[${interval}].max()", "Bar", 6, 0),
    ("Dynamic Group - Total", "oci_dg_total[${interval}].sum()", "GroupBar", 6, 9),
    ("OCI IAM Policy Statement - Top 10 Compartment", "oci_statements_top10[${interval}].sum()", "GroupBar", 12, 0),
    ("OCI IAM Policy - Top 10 Compartment", "oci_policies_top10[${interval}].sum()", "GroupBar", 12, 9),
    ("OCI IAM Policy - Per Compartment", "oci_policies_metrics[${interval}].sum()", "Bar", 18, 0),
    ("OCI IAM Policy Statement - Per Compartment", "oci_statements_metrics[${interval}].sum()", "Bar", 18, 9)
]

# ----------------------------
# Build widgets
# ----------------------------
widgets = []
for title, query, chart_type, top, left in widgets_info:
    widget = {
        "id": f"Monitoring_{uuid.uuid4()}",
        "title": title,
        "type": chart_type,
        "description": "",
        "layout": {
            "width": 9,
            "height": 6,
            "top": top,
            "left": left,
            "minH": 6,
            "minW": 9
        },
        "data": {
            "dataSource": "Monitoring",
            "api": {
                "type": "urlTemplate",
                "templateId": "monitoring",
                "variables": [
                    {
                        "compartmentId": {"value": tenancy_id},
                        "namespace": {"value": "custom_metrics"},
                        "resourceGroup": {"value": "Policy_DG_audit"},
                        "query": {"value": query},
                        "regionId": {"value": region}
                    }
                ]
            }
        }
    }
    widgets.append(widget)

# ----------------------------
# Create dashboard
# ----------------------------
try:
    # print("[DEBUG] Creating dashboard in group...")
    create_dashboard_details = oci.dashboard_service.models.CreateV1DashboardDetails(
        display_name="OCI_IAM_Policy_DG_Dashboard",
        description="OCI IAM Policy and Dynamic Group dashboard",
        dashboard_group_id=dashboard_group_id,
        schema_version="V1",
        widgets=widgets
    )

    response = dashboard_client.create_dashboard(create_dashboard_details)
    dashboard_id = response.data.id
    # print(f"[DEBUG] Dashboard created successfully: {dashboard_id}")

except Exception as e:
    # print(f"[ERROR] Failed to create dashboard: {e}")
    exit(1)

# ----------------------------
# Optional: Print final dashboard JSON
# Uncomment the following lines if you want to see the full JSON response
# ----------------------------
# print("\n--- Final Dashboard JSON ---")
# print(json.dumps(to_dict(response.data), indent=2))
