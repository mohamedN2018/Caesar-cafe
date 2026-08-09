"""
The printer registry.

Before this, a printer was a string typed into each Desktop. Three terminals
meant three places to fix a typo, and nothing on the server could say what the
cafe owned. `branch.manage_printers` was a code in the catalogue that no route
declared.

What the tests defend, in order of how much a mistake costs:

  * a printer is reachable — a network printer with no host is refused at
    configuration time, not at print time in front of a customer;
  * exactly one default per kind, and choosing a new one MOVES the flag rather
    than failing;
  * deactivation, never deletion, because a queued job still names the row;
  * reading needs only `floor.view`, but changing needs `branch.manage_printers`.
"""

from __future__ import annotations

import pytest

from apps.kitchen.models import Station
from apps.printing.models import Printer

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner(make_user):
    return make_user(email="owner@caesar.test", role="SUPER_ADMIN")


@pytest.fixture
def client(authed, owner, branch):
    return authed(owner, branch=branch)


def payload(**overrides) -> dict:
    return {
        "name_ar": "طابعة الكاشير",
        "code": "CASHIER-1",
        "kind": "RECEIPT",
        "connection": "USB",
        "device_path": "/dev/usb/lp0",
        "paper_width_mm": 80,
        **overrides,
    }


# ── defining a printer ───────────────────────────────────────────────────────


class TestDefiningPrinters:
    def test_a_printer_is_created_for_the_branch(self, client, branch) -> None:
        response = client.post("/api/v1/printers/", payload(), format="json")

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["name_ar"] == "طابعة الكاشير"
        assert Printer.objects.get(pk=data["id"]).branch_id == branch.id

    def test_an_80mm_roll_reports_576_dots(self, client) -> None:
        """
        The Desktop rasterises Arabic to a bitmap and needs the width in dots.
        Getting it wrong crops every receipt down the right-hand side.
        """
        response = client.post("/api/v1/printers/", payload(), format="json")

        assert response.json()["data"]["dots"] == 576

    def test_a_58mm_roll_reports_384_dots(self, client) -> None:
        response = client.post("/api/v1/printers/", payload(paper_width_mm=58), format="json")

        assert response.json()["data"]["dots"] == 384

    def test_a_roll_that_does_not_exist_is_refused(self, client) -> None:
        """72mm is not a thermal roll anyone sells. Accepting it produces a
        printer that mis-renders every receipt with no obvious cause."""
        response = client.post("/api/v1/printers/", payload(paper_width_mm=72), format="json")

        assert response.status_code == 400

    def test_a_network_printer_without_a_host_is_refused(self, client) -> None:
        """
        Refused now, while somebody is looking at a form, rather than at print
        time when a cashier is looking at a machine that does nothing.
        """
        response = client.post(
            "/api/v1/printers/", payload(connection="NETWORK", host=""), format="json"
        )

        assert response.status_code == 400
        assert "host" in response.json()["errors"], "the form must know which field"

    def test_a_network_printer_with_a_host_is_accepted(self, client) -> None:
        response = client.post(
            "/api/v1/printers/",
            payload(connection="NETWORK", host="10.0.0.7"),
            format="json",
        )

        assert response.status_code == 201

    def test_two_printers_cannot_share_a_code_in_one_branch(self, client) -> None:
        client.post("/api/v1/printers/", payload(), format="json")
        response = client.post("/api/v1/printers/", payload(name_ar="أخرى"), format="json")

        assert response.status_code == 400


# ── the default ──────────────────────────────────────────────────────────────


class TestTheDefault:
    def test_choosing_a_new_default_moves_the_flag(self, client) -> None:
        """
        A uniqueness error here would be a correct database and a useless
        screen: the person pressing "make this the default" has already decided.
        """
        first = client.post("/api/v1/printers/", payload(is_default=True), format="json").json()[
            "data"
        ]
        second = client.post(
            "/api/v1/printers/",
            payload(code="CASHIER-2", name_ar="الثانية", is_default=True),
            format="json",
        )

        assert second.status_code == 201
        assert Printer.objects.get(pk=first["id"]).is_default is False

    def test_a_default_receipt_printer_does_not_disturb_the_kitchen(self, client) -> None:
        kitchen = client.post(
            "/api/v1/printers/",
            payload(code="KIT-1", kind="KITCHEN", is_default=True),
            format="json",
        ).json()["data"]
        client.post("/api/v1/printers/", payload(is_default=True), format="json")

        assert Printer.objects.get(pk=kitchen["id"]).is_default is True


# ── stations ─────────────────────────────────────────────────────────────────


class TestStations:
    @pytest.fixture
    def grill(self, branch, organization):
        return Station.objects.create(
            organization=organization, branch=branch, code="GRILL", name_ar="الشواية"
        )

    def test_a_kitchen_printer_can_be_assigned_to_stations(self, client, grill) -> None:
        response = client.post(
            "/api/v1/printers/",
            payload(code="KIT-1", kind="KITCHEN", stations=[str(grill.id)]),
            format="json",
        )

        assert response.status_code == 201
        assert response.json()["data"]["station_names"] == ["الشواية"]


# ── removal ──────────────────────────────────────────────────────────────────


class TestRemoval:
    def test_deleting_deactivates_and_keeps_the_row(self, client) -> None:
        """
        A queued job names this printer by id. A row that vanished would be a
        receipt that disappears without saying why.
        """
        created = client.post("/api/v1/printers/", payload(is_default=True), format="json")
        printer_id = created.json()["data"]["id"]

        assert client.delete(f"/api/v1/printers/{printer_id}/").status_code == 204

        printer = Printer.all_objects.get(pk=printer_id)
        assert printer.is_active is False
        assert printer.deactivated_at is not None

    def test_a_deactivated_printer_gives_up_being_the_default(self, client) -> None:
        """Otherwise it keeps the slot and nothing else can take it."""
        created = client.post("/api/v1/printers/", payload(is_default=True), format="json")
        printer_id = created.json()["data"]["id"]
        client.delete(f"/api/v1/printers/{printer_id}/")

        replacement = client.post(
            "/api/v1/printers/",
            payload(code="CASHIER-2", name_ar="البديلة", is_default=True),
            format="json",
        )

        assert replacement.status_code == 201


# ── who may touch it ─────────────────────────────────────────────────────────


class TestPermissions:
    @pytest.fixture
    def cashier_client(self, authed, make_user, branch):
        return authed(make_user(email="till@caesar.test", role="CASHIER"), branch=branch)

    def test_a_cashier_may_read_the_printers(self, client, cashier_client) -> None:
        """
        The terminal needs the list to route a ticket. Reading it is `floor.view`
        — the same thing that already lets a cashier see the room.
        """
        client.post("/api/v1/printers/", payload(), format="json")

        assert cashier_client.get("/api/v1/printers/").status_code == 200

    def test_a_cashier_may_not_change_them(self, cashier_client) -> None:
        response = cashier_client.post("/api/v1/printers/", payload(), format="json")

        assert response.status_code == 403

    def test_a_cashier_may_not_delete_one(self, client, cashier_client) -> None:
        printer_id = client.post("/api/v1/printers/", payload(), format="json").json()["data"]["id"]

        assert cashier_client.delete(f"/api/v1/printers/{printer_id}/").status_code == 403


# ── reaching the terminals ───────────────────────────────────────────────────


class TestSync:
    """
    `changelog.record` defers its append to `transaction.on_commit`, so these
    tests run the callbacks explicitly. A plain `django_db` test rolls back and
    the deferred write never fires — which would make every assertion here pass
    for the wrong reason if it were written the other way round.
    """

    @pytest.fixture
    def latest(self):
        from apps.sync.models import ChangeLog

        return lambda: ChangeLog.objects.filter(entity_type="printer").latest("seq")

    def test_a_new_printer_is_written_to_the_config_stream(
        self, client, latest, django_capture_on_commit_callbacks
    ) -> None:
        """
        The whole point of the registry: define it once, and every terminal has
        it at the next pull instead of somebody walking to three tills.
        """
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post("/api/v1/printers/", payload(), format="json")

        entry = latest()
        assert str(entry.entity_id) == response.json()["data"]["id"]
        assert entry.payload["code"] == "CASHIER-1"

    def test_the_payload_carries_the_stations_and_the_dots(
        self, client, branch, organization, latest, django_capture_on_commit_callbacks
    ):
        station = Station.objects.create(
            organization=organization, branch=branch, code="BAR", name_ar="البار"
        )

        with django_capture_on_commit_callbacks(execute=True):
            client.post(
                "/api/v1/printers/",
                payload(code="KIT-1", kind="KITCHEN", stations=[str(station.id)]),
                format="json",
            )

        entry = latest()
        assert entry.payload["station_ids"] == [str(station.id)]
        assert entry.payload["dots"] == 576

    def test_deactivating_reaches_the_terminals_too(
        self, client, latest, django_capture_on_commit_callbacks
    ) -> None:
        """A till that kept printing to a decommissioned machine is paper
        piling up somewhere nobody is standing."""
        printer_id = client.post("/api/v1/printers/", payload(), format="json").json()["data"]["id"]

        with django_capture_on_commit_callbacks(execute=True):
            client.delete(f"/api/v1/printers/{printer_id}/")

        entry = latest()
        assert entry.payload["is_active"] is False
