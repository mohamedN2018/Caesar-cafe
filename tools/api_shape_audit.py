"""
Every screen's GET, against what the server actually sends.

The floor screen read `payload.tables` from an endpoint that answers with a bare
array, and rendered an empty room on a full floor for as long as it existed. The
unit tests agreed with the mistake because they mocked the shape the CODE wanted.

So the only way to find the rest of that family is to ask the running server.
This calls each endpoint the SPA calls, and prints LIST or OBJ(keys) beside what
the source says it expects. A mismatch is a screen that is quietly blank.

Run it against a stack that is up:

    python tools/api_shape_audit.py [base-url]

Deliberately NOT a unit test. The mocks in a unit test are written by the same
hand as the code, so they agree with its mistakes; only the running server
disagrees. Keep the list current when a screen adds a GET.
"""

import json
import os
import sys
import urllib.error
import urllib.request

BASE = (
    sys.argv[1]
    if len(sys.argv) > 1
    else os.environ.get("API_BASE", "http://127.0.0.1:8080")
).rstrip("/") + "/api/v1"
EMAIL = os.environ.get("AUDIT_EMAIL", "admin@caesar.deplois.net")
PASSWORD = os.environ.get("AUDIT_PASSWORD", "admin")

# (path, what the frontend's generic says: "list" or "obj", where)
CALLS = [
    ("/auth/me/", "obj", "stores/auth.ts"),
    ("/system/info/", "obj", "views/auth/LoginView.vue"),
    ("/catalog/categories/", "list", "stores/pos.ts"),
    ("/catalog/products/", "list", "stores/pos.ts"),
    ("/catalog/modifier-groups/", "list", "stores/pos.ts"),
    ("/payments/methods/", "list", "stores/pos.ts"),
    ("/shifts/current/", "obj", "stores/pos.ts"),
    ("/audit/", "list", "views/audit/AuditLogView.vue"),
    ("/audit/actions/", "list", "views/audit/AuditLogView.vue"),
    ("/recipes/", "list", "views/catalog/RecipeView.vue"),
    ("/inventory/items/", "list", "views/catalog/RecipeView.vue"),
    ("/floor/areas/", "list", "views/floor/FloorPlanView.vue"),
    ("/floor/tables/", "list", "views/floor/FloorPlanView.vue"),
    ("/floor/status/", "list", "views/pos/PosTablesView.vue"),
    ("/hr/attendance/", "list", "views/hr/HrAttendanceView.vue"),
    ("/hr/roster/", "list", "views/hr/HrRosterView.vue"),
    ("/hr/patterns/", "list", "views/hr/HrRosterView.vue"),
    ("/staff/", "list", "views/hr/HrRosterView.vue"),
    ("/inventory/levels/", "list", "views/inventory/StockLevelView.vue"),
    ("/inventory/movements/", "list", "views/inventory/StockMovementView.vue"),
    ("/kids/areas/", "list", "views/kids/KidsBoardView.vue"),
    ("/kids/guardians/", "list", "views/kids/KidsGuardianView.vue"),
    ("/kids/children/", "list", "views/kids/KidsGuardianView.vue"),
    ("/kids/incidents/", "list", "views/kids/KidsIncidentView.vue"),
    ("/kids/sessions/", "list", "views/kids/KidsSessionListView.vue"),
    ("/kids/reports/?days=30", "obj", "views/kids/KidsSessionListView.vue"),
    ("/kids/tariffs/", "list", "views/kids/KidsTariffView.vue"),
    ("/kitchen/tickets/", "list", "views/kitchen/KitchenLiveView.vue"),
    ("/kitchen/stations/", "list", "views/kitchen/KitchenLiveView.vue"),
    ("/kitchen/performance/", "obj", "views/kitchen/KitchenLiveView.vue"),
    ("/licensing/devices/", "list", "views/licensing/DeviceListView.vue"),
    ("/licensing/licenses/", "list", "views/licensing/LicenseListView.vue"),
    (
        "/notifications/subscriptions/",
        "list",
        "views/notifications/NotificationsView.vue",
    ),
    ("/notifications/alerts/", "list", "views/notifications/NotificationsView.vue"),
    ("/ops/backups/", "obj", "views/ops/BackupView.vue"),
    ("/orders/", "list", "views/orders/OrderListView.vue"),
    ("/purchasing/purchase-orders/", "list", "views/purchasing/PurchaseOrderView.vue"),
    ("/purchasing/receipts/", "list", "views/purchasing/PurchaseOrderView.vue"),
    (
        "/purchasing/reorder-suggestions/",
        "list",
        "views/purchasing/PurchaseOrderView.vue",
    ),
    ("/suppliers/", "list", "views/purchasing/SupplierListView.vue"),
    ("/system/deleted/", "obj", "views/settings/DeletedItemsView.vue"),
    ("/printers/", "list", "views/settings/PrinterListView.vue"),
    ("/settings/schema/", "obj", "views/settings/SettingsView.vue"),
    ("/settings/", "obj", "views/settings/SettingsView.vue"),
    ("/shifts/", "list", "views/shifts/ShiftListView.vue"),
    ("/roles/", "list", "views/staff/StaffListView.vue"),
    ("/permissions/", "list", "views/staff/StaffListView.vue"),
    ("/sync/status/", "obj", "views/sync/SyncStatusView.vue"),
    ("/sync/conflicts/", "list", "views/sync/SyncStatusView.vue"),
    ("/notifications/vapid-key/", "obj", "modules/push/index.ts"),
]


def fetch(path: str, token: str):
    req = urllib.request.Request(
        BASE + path, headers={"Authorization": f"Bearer {token}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode()), r.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode() or "{}"
        try:
            return json.loads(raw), e.code
        except json.JSONDecodeError:
            # An HTML 404 means the path is wrong, which is itself a finding.
            return {"_raw": raw[:120]}, e.code
    except Exception as exc:  # noqa: BLE001
        return {"_error": str(exc)}, 0


def login() -> str:
    body = json.dumps({"email": EMAIL, "password": PASSWORD}).encode()
    req = urllib.request.Request(
        BASE + "/auth/login/", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())["data"]["access"]


def main() -> int:
    token = login()
    bad, errs = [], []

    for path, expected, where in CALLS:
        payload, status = fetch(path, token)
        if status != 200:
            code = (payload.get("error") or {}).get(
                "code", payload.get("_error", status)
            )
            errs.append((path, status, code, where))
            continue

        inner = payload.get("data", payload) if isinstance(payload, dict) else payload
        actual = "list" if isinstance(inner, list) else "obj"
        keys = list(inner)[:6] if isinstance(inner, dict) else f"len={len(inner)}"

        mark = "ok " if actual == expected else "MISMATCH"
        if actual != expected:
            bad.append((path, expected, actual, keys, where))
        print(f"{mark} {path:42} expected={expected:4} actual={actual:4} {keys}")

    print("\n" + "=" * 72)
    if bad:
        print(f"{len(bad)} SHAPE MISMATCH(ES) — each one is a screen rendering blank:")
        for path, exp, act, keys, where in bad:
            print(f"  {path}  wants {exp}, gets {act} {keys}   [{where}]")
    else:
        print("no shape mismatches")

    if errs:
        print(f"\n{len(errs)} endpoint(s) did not answer 200:")
        for path, status, code, where in errs:
            print(f"  {path}  {status} {code}   [{where}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
