"""Licensing endpoints: activation, heartbeat, and admin management."""

from __future__ import annotations

import logging

from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts import tokens
from apps.authz.context import PrincipalKind
from apps.authz.drf import (
    HasPermission,
    IsAuthenticatedPrincipal,
    PublicEndpointMixin,
    auth_context,
)
from apps.core.exceptions import AppError, NotFoundError
from apps.core.serializers import DetailSerializer as SimpleResultSerializer

from . import services
from .models import Device, DeviceStatus, License, LicenseEvent, LicenseStatus
from .serializers import (
    ActivationRequestSerializer,
    ActivationResponseSerializer,
    DeviceSerializer,
    DeviceTokenRequestSerializer,
    HeartbeatRequestSerializer,
    HeartbeatResponseSerializer,
    IssuedLicenseSerializer,
    IssueLicenseSerializer,
    LicenseEventSerializer,
    LicenseSerializer,
    RenewLicenseSerializer,
)

logger = logging.getLogger(__name__)


def _client_ip(request: Request) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _validated(serializer_class, request: Request) -> dict:
    serializer = serializer_class(data=request.data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def _acting_user(request: Request):
    principal = auth_context(request)
    if principal.user_id is None:
        return None
    from apps.accounts.models import User

    return User.objects.filter(id=principal.user_id).first()


def _org_licenses(request: Request):
    """Every licence query is scoped to the caller's organization (threat I1)."""
    return License.objects.filter(organization_id=auth_context(request).require_organization())


# ── device-facing ────────────────────────────────────────────────────────────


class ActivateView(PublicEndpointMixin, APIView):
    """
    The activation handshake. Public by necessity — the device has no
    credentials yet — so it is rate-limited hard (5/hour/IP).
    """

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "activation"

    @extend_schema(
        summary="Activate a device against a licence key",
        request=ActivationRequestSerializer,
        responses={201: ActivationResponseSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _validated(ActivationRequestSerializer, request)

        activation = services.activate(
            license_key=data["license_key"],
            device_name=data["device_name"],
            mode=data.get("mode", "POS"),
            platform=data.get("platform", ""),
            app_version=data.get("app_version", ""),
            fingerprint=data.get("fingerprint", ""),
            ip_address=_client_ip(request),
        )
        device = activation.device

        return Response(
            {
                "device_id": str(device.id),
                # Returned exactly once. The client stores it in the Windows
                # Credential Manager; the server keeps only an Argon2id hash.
                "device_secret": activation.device_secret,
                "offline_token": activation.offline_token,
                "branch_id": str(device.branch_id),
                "branch_name": device.branch.name_ar,
                "device_name": device.device_name,
                "mode": device.mode,
                "license_status": device.license.status,
                "license_expires_at": device.license.expires_at,
            },
            status=status.HTTP_201_CREATED,
        )


class DeviceTokenView(PublicEndpointMixin, APIView):
    """Exchange the long-lived device secret for a short-lived access token."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    @extend_schema(
        summary="Obtain a device access token",
        request=DeviceTokenRequestSerializer,
        responses={200: None},
    )
    def post(self, request: Request) -> Response:
        data = _validated(DeviceTokenRequestSerializer, request)
        device = services.authenticate_device(
            device_id=data["device_id"],
            device_secret=data["device_secret"],
            ip_address=_client_ip(request),
        )

        state = services.evaluate_state(device.license)

        # A device token names no human — `user=None`. It can drain the outbox
        # and pull the catalog at 3am, but cannot take money, because there
        # would be nobody to name in the audit log. See PrincipalKind.
        pair = tokens.issue_pair(
            user=None,
            kind=PrincipalKind.DEVICE,
            organization_id=device.license.organization_id,
            branch_id=device.branch_id,
            device_id=device.id,
            ip_address=_client_ip(request),
        )
        pair.pop("family_id", None)
        pair["license_stage"] = state.stage.value
        pair["can_open_new_orders"] = state.can_open_new_orders
        return Response(pair)


class HeartbeatView(APIView):
    """
    Periodic liveness + licence status, and how a fresh offline token reaches
    the device. A terminal that is online daily slides its grace window forward
    without ever noticing the mechanism.
    """

    permission_classes = [IsAuthenticatedPrincipal]
    required_permission = ""

    @extend_schema(
        summary="Device heartbeat",
        request=HeartbeatRequestSerializer,
        responses={200: HeartbeatResponseSerializer},
    )
    def post(self, request: Request) -> Response:
        from django.conf import settings as dj_settings

        principal = auth_context(request)
        if principal.device_id is None:
            raise AppError("هذا الإجراء متاح من جهاز مفعّل فقط", code="DEVICE_REQUIRED")

        data = _validated(HeartbeatRequestSerializer, request)
        device = (
            Device.objects.select_related("license", "branch")
            .filter(id=principal.device_id)
            .first()
        )
        if device is None:
            raise NotFoundError("الجهاز غير موجود", code="DEVICE_NOT_FOUND")

        if device.status == DeviceStatus.REVOKED:
            services._record(
                device.license,
                LicenseEvent.Event.HEARTBEAT_DENIED,
                device=device,
                ip_address=_client_ip(request),
                detail={"reason": "DEVICE_REVOKED"},
            )
            raise services.DeviceRevoked()

        device.last_seen_at = timezone.now()
        device.last_ip = _client_ip(request)
        if version := data.get("app_version"):
            device.app_version = version
        device.save(update_fields=["last_seen_at", "last_ip", "app_version"])

        state = services.evaluate_state(device.license)

        return Response(
            {
                "server_time": timezone.now(),
                "license_status": device.license.status,
                "stage": state.stage.value,
                "can_open_new_orders": state.can_open_new_orders,
                "can_close_open_orders": state.can_close_open_orders,
                "days_until_expiry": state.days_until_expiry,
                "message_ar": state.message_ar,
                "offline_token": services.issue_offline_token(device.license, device),
                "min_supported_client_version": dj_settings.MIN_SUPPORTED_CLIENT_VERSION,
                "latest_version": dj_settings.APP_VERSION,
            }
        )


class InvoiceBlockView(APIView):
    """Reserve the next disjoint invoice-number range for this device (C9)."""

    permission_classes = [IsAuthenticatedPrincipal]
    required_permission = ""

    @extend_schema(summary="Allocate an invoice number block", request=None, responses={201: None})
    def post(self, request: Request) -> Response:
        principal = auth_context(request)
        if principal.device_id is None:
            raise AppError("هذا الإجراء متاح من جهاز مفعّل فقط", code="DEVICE_REQUIRED")

        device = Device.objects.filter(id=principal.device_id).select_related("branch").first()
        if device is None:
            raise NotFoundError("الجهاز غير موجود", code="DEVICE_NOT_FOUND")

        block = services.allocate_invoice_block(device)
        return Response(
            {
                "block_id": str(block.id),
                "range_start": block.range_start,
                "range_end": block.range_end,
                "size": block.size,
            },
            status=status.HTTP_201_CREATED,
        )


# ── admin-facing ─────────────────────────────────────────────────────────────


class LicenseListView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permissions = {"GET": "licenses.view", "POST": "licenses.manage"}

    @extend_schema(summary="List licences", responses={200: LicenseSerializer(many=True)})
    def get(self, request: Request) -> Response:
        licenses = _org_licenses(request).select_related("branch").order_by("-created_at")
        return Response(LicenseSerializer(licenses, many=True).data)

    @extend_schema(
        summary="Issue a licence",
        request=IssueLicenseSerializer,
        responses={201: IssuedLicenseSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _validated(IssueLicenseSerializer, request)
        principal = auth_context(request)

        from apps.organizations.models import Branch, Organization

        organization = Organization.objects.get(id=principal.require_organization())
        branch = None
        if branch_id := data.get("branch_id"):
            branch = Branch.objects.filter(id=branch_id, organization=organization).first()
            if branch is None:
                raise NotFoundError("الفرع غير موجود", code="BRANCH_NOT_FOUND")

        issued = services.issue_license(
            organization=organization,
            branch=branch,
            license_type=data["license_type"],
            max_devices=data.get("max_devices", 8),
            expires_at=data.get("expires_at"),
            notes=data.get("notes", ""),
            actor=_acting_user(request),
            ip_address=_client_ip(request),
        )

        payload = LicenseSerializer(issued.license).data
        # The ONLY time the plaintext key is ever returned. Lost keys are
        # regenerated, not recovered — which is why a stolen database yields
        # nothing usable.
        payload["license_key"] = issued.plaintext_key
        payload["warning_ar"] = "احفظ هذا المفتاح الآن — لن يظهر مرة أخرى."
        return Response(payload, status=status.HTTP_201_CREATED)


class LicenseDetailView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permissions = {"GET": "licenses.view", "default": "licenses.manage"}

    def _get(self, request: Request, pk):
        license_obj = _org_licenses(request).filter(id=pk).select_related("branch").first()
        if license_obj is None:
            raise NotFoundError("الترخيص غير موجود", code="LICENSE_NOT_FOUND")
        return license_obj

    @extend_schema(summary="Licence detail", responses={200: LicenseSerializer})
    def get(self, request: Request, pk) -> Response:
        return Response(LicenseSerializer(self._get(request, pk)).data)


class LicenseActionView(APIView):
    """suspend · resume · revoke · renew · regenerate-key"""

    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "licenses.manage"

    ACTIONS = {"suspend", "resume", "revoke", "renew", "regenerate-key"}

    @extend_schema(
        # Explicit: this shares the POST verb and a path prefix with the issue
        # endpoint, and the auto-generated ids collide.
        operation_id="licensing_license_action",
        summary="Act on a licence (suspend · resume · revoke · renew · regenerate-key)",
        request=RenewLicenseSerializer,
        responses={200: LicenseSerializer},
    )
    def post(self, request: Request, pk, action: str) -> Response:
        if action not in self.ACTIONS:
            raise NotFoundError("إجراء غير معروف", code="UNKNOWN_ACTION")

        license_obj = _org_licenses(request).filter(id=pk).first()
        if license_obj is None:
            raise NotFoundError("الترخيص غير موجود", code="LICENSE_NOT_FOUND")

        actor = _acting_user(request)
        ip = _client_ip(request)
        handler = getattr(self, f"_{action.replace('-', '_')}")
        result = handler(request, license_obj, actor, ip)
        return Response(result)

    def _suspend(self, request, license_obj, actor, ip):
        license_obj.status = LicenseStatus.SUSPENDED
        license_obj.save(update_fields=["status", "updated_at"])
        services._record(license_obj, LicenseEvent.Event.SUSPENDED, actor=actor, ip_address=ip)
        return LicenseSerializer(license_obj).data

    def _resume(self, request, license_obj, actor, ip):
        license_obj.status = LicenseStatus.ACTIVE
        license_obj.save(update_fields=["status", "updated_at"])
        services._record(license_obj, LicenseEvent.Event.RESUMED, actor=actor, ip_address=ip)
        return LicenseSerializer(license_obj).data

    def _revoke(self, request, license_obj, actor, ip):
        """Irreversible. Every device dies immediately."""
        license_obj.status = LicenseStatus.REVOKED
        license_obj.save(update_fields=["status", "updated_at"])
        killed = license_obj.devices.exclude(status=DeviceStatus.REVOKED).update(
            status=DeviceStatus.REVOKED
        )
        services._record(
            license_obj,
            LicenseEvent.Event.REVOKED,
            actor=actor,
            ip_address=ip,
            detail={"devices_revoked": killed},
        )
        return LicenseSerializer(license_obj).data

    def _renew(self, request, license_obj, actor, ip):
        data = _validated(RenewLicenseSerializer, request)
        previous = license_obj.expires_at
        license_obj.expires_at = data["expires_at"]
        if license_obj.status in (LicenseStatus.EXPIRED, LicenseStatus.PENDING):
            license_obj.status = LicenseStatus.ACTIVE
        license_obj.save(update_fields=["expires_at", "status", "updated_at"])
        services._record(
            license_obj,
            LicenseEvent.Event.RENEWED,
            actor=actor,
            ip_address=ip,
            detail={
                "from": previous.isoformat() if previous else None,
                "to": license_obj.expires_at.isoformat(),
            },
        )
        return LicenseSerializer(license_obj).data

    def _regenerate_key(self, request, license_obj, actor, ip):
        issued = services.regenerate_key(license_obj, actor=actor, ip_address=ip)
        payload = LicenseSerializer(issued.license).data
        payload["license_key"] = issued.plaintext_key
        payload["warning_ar"] = "احفظ هذا المفتاح الآن — لن يظهر مرة أخرى."
        return payload


class LicenseEventListView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "licenses.view"

    @extend_schema(summary="Licence event log", responses={200: LicenseEventSerializer(many=True)})
    def get(self, request: Request, pk) -> Response:
        license_obj = _org_licenses(request).filter(id=pk).first()
        if license_obj is None:
            raise NotFoundError("الترخيص غير موجود", code="LICENSE_NOT_FOUND")
        events = license_obj.events.select_related("actor", "device")[:200]
        return Response(LicenseEventSerializer(events, many=True).data)


class DeviceListView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "devices.view"

    @extend_schema(summary="List activated devices", responses={200: DeviceSerializer(many=True)})
    def get(self, request: Request) -> Response:
        devices = Device.objects.filter(
            license__organization_id=auth_context(request).require_organization()
        ).select_related("license", "branch")
        return Response(DeviceSerializer(devices, many=True).data)


class DeviceActionView(APIView):
    """revoke · reset · suspend · resume"""

    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "devices.manage"

    @extend_schema(summary="Act on a device", request=None, responses={200: SimpleResultSerializer})
    def post(self, request: Request, pk, action: str) -> Response:
        device = (
            Device.objects.filter(
                id=pk, license__organization_id=auth_context(request).require_organization()
            )
            .select_related("license")
            .first()
        )
        if device is None:
            raise NotFoundError("الجهاز غير موجود", code="DEVICE_NOT_FOUND")

        actor = _acting_user(request)
        ip = _client_ip(request)

        if action == "revoke":
            device.status = DeviceStatus.REVOKED
            device.save(update_fields=["status", "updated_at"])
            services._record(
                device.license,
                LicenseEvent.Event.DEVICE_REVOKED,
                device=device,
                actor=actor,
                ip_address=ip,
            )
            detail = "تم إلغاء تفعيل الجهاز"
        elif action == "reset":
            # Frees the seat. The device must activate again to come back.
            device.delete()
            services._record(
                device.license,
                LicenseEvent.Event.DEVICE_RESET,
                actor=actor,
                ip_address=ip,
                detail={"device_name": device.device_name},
            )
            detail = "تم تحرير المقعد — يمكن تفعيل جهاز آخر"
        elif action == "suspend":
            device.status = DeviceStatus.SUSPENDED
            device.save(update_fields=["status", "updated_at"])
            detail = "تم إيقاف الجهاز مؤقتاً"
        elif action == "resume":
            device.status = DeviceStatus.ACTIVE
            device.save(update_fields=["status", "updated_at"])
            detail = "تم إعادة تفعيل الجهاز"
        elif action == "unlock":
            # Five wrong PINs lock the TERMINAL for fifteen minutes. That is the
            # control which makes a four-digit PIN defensible, and it is staying.
            #
            # What was missing is a way out. Until this existed the only remedies
            # were waiting out the window with a queue at the counter, or a shell
            # on the server — so a mistyped PIN during a rush closed a till and
            # the manager standing next to it could do nothing about it.
            #
            # Deliberately does NOT clear per-user PIN counters for the whole
            # branch: those belong to the step-up approval path, where a wrong PIN
            # really is that person's failed attempt, and wiping them here would
            # turn one button into a way to reset everybody's rate limit.
            from apps.accounts.services import clear_failures

            clear_failures(f"terminal:{device.id}")
            services._record(
                device.license,
                LicenseEvent.Event.DEVICE_UNLOCKED,
                device=device,
                actor=actor,
                ip_address=ip,
            )
            detail = "تم فتح الجهاز — يمكن تسجيل الدخول بالرمز الآن"
        else:
            raise NotFoundError("إجراء غير معروف", code="UNKNOWN_ACTION")

        return Response({"detail": detail})
