import json
import re
import time
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_BASE_URL = "https://api.uala.com/api/v1"
CONFIG_URL = "https://pro.treatwell.es/config.js?v=1786621081"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36"
)
DETAIL_PARAMS = {
    "with_customer_favorite_venue_treatments": "true",
    "with_customer_favorite_treatment_categories": "true",
    "with_look": "true",
    "with_marketplace_details": "true",
}


class TreatwellAPIError(RuntimeError):
    pass


class TreatwellClient:
    def __init__(self, email, password, *, timeout=45, retries=5, api_key=""):
        self.email = email
        self.password = password
        self.timeout = timeout
        self.retries = retries
        self.api_key = api_key
        self.auth_token = ""
        self.venue_id = None
        self.venue_name = ""

    def login(self):
        if not self.api_key:
            config = self._request_absolute(
                CONFIG_URL,
                headers={"User-Agent": USER_AGENT, "Referer": "https://pro.treatwell.es/"},
            )
            match = re.search(r"UALA_API_KEY:'([^']+)'", config)
            if not match:
                raise TreatwellAPIError("No se pudo encontrar UALA_API_KEY en config.js")
            self.api_key = match.group(1)

        response = self._request(
            "POST",
            "/sessions.json",
            payload={"user": {"email": self.email, "password": self.password}},
            guest=True,
        )
        data = response.get("data") or {}
        self.auth_token = data.get("auth_token") or data.get("token") or ""
        venues = data.get("venues") or []
        self.venue_id = data.get("venue_id") or (venues[0].get("id") if venues else None)
        selected = next(
            (venue for venue in venues if venue.get("id") == self.venue_id),
            venues[0] if venues else {},
        )
        self.venue_name = selected.get("name") or ""
        if not self.auth_token or not self.venue_id:
            raise TreatwellAPIError("La sesion no devolvio auth_token o venue_id")
        return data

    def list_appointments(self, from_time, to_time):
        response = self._request(
            "GET",
            f"/venues/{self.venue_id}/appointments.json",
            params={"from_time": from_time, "to_time": to_time},
        )
        return (response.get("data") or {}).get("appointments") or []

    def appointment_detail(self, appointment_id):
        response = self._request(
            "GET",
            f"/venues/{self.venue_id}/appointments/{appointment_id}.json",
            params=DETAIL_PARAMS,
        )
        return response.get("data") or {}

    def _request(self, method, path, *, params=None, payload=None, guest=False):
        url = f"{API_BASE_URL}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        headers = {
            "Accept": "application/json",
            "Accept-Language": "es-ES",
            "Content-Type": "application/json",
            "X-Client-Auth": self.api_key,
            "Authorization": "none" if guest else f'Token token="{self.auth_token}"',
            "Origin": "https://pro.treatwell.es",
            "Referer": "https://pro.treatwell.es/",
            "User-Agent": USER_AGENT,
        }
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        raw = self._request_absolute(url, method=method, headers=headers, body=body)
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TreatwellAPIError("Treatwell devolvio una respuesta no JSON") from exc
        if not result.get("success", False):
            info = result.get("info") or "respuesta sin success=true"
            raise TreatwellAPIError(f"Treatwell rechazo la solicitud: {info}")
        return result

    def _request_absolute(self, url, *, method="GET", headers=None, body=None):
        request = Request(url, data=body, headers=headers or {}, method=method)
        last_error = None
        for attempt in range(self.retries + 1):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    return response.read().decode("utf-8")
            except HTTPError as exc:
                last_error = exc
                if exc.code not in {408, 429, 500, 502, 503, 504}:
                    break
            except (URLError, TimeoutError, OSError) as exc:
                last_error = exc
            if attempt < self.retries:
                time.sleep(min(30, 2**attempt))
        status = getattr(last_error, "code", "network")
        raise TreatwellAPIError(f"Error HTTP {status} al consultar Treatwell") from last_error


def normalize_appointment(stub, detail):
    appointment = detail.get("appointment") or stub
    customer = detail.get("customer") or {}
    service_data = appointment.get("data") or {}
    treatment = service_data.get("staff_member_treatment") or {}
    staff = service_data.get("staff_member") or {}

    start_at = _parse_datetime(appointment.get("time") or appointment.get("time_no_tz"))
    duration = _positive_int(
        appointment.get("custom_duration")
        or treatment.get("duration")
        or treatment.get("total_duration"),
        default=60,
    )
    end_at = start_at + timedelta(minutes=duration) if start_at else None
    price = _money(service_data.get("custom_price"))
    if price is None:
        price = _money(treatment.get("price")) or "0.00"

    customer_name = customer.get("full_name") or appointment.get("customer_full_name") or ""
    first_name = customer.get("first_name") or ""
    last_name = customer.get("last_name") or ""
    if not first_name and customer_name:
        first_name, _, last_name = customer_name.partition(" ")

    source = appointment.get("source_marketplace") or ("venue" if appointment.get("by_venue") else "")
    return {
        "external_source": "treatwell",
        "external_id": str(appointment.get("id") or stub.get("id") or ""),
        "parent_external_id": _string_or_empty(appointment.get("parent_id")),
        "client": {
            "treatwell_id": _string_or_empty(appointment.get("customer_id") or customer.get("id")),
            "external_id": _string_or_empty(customer.get("external_id")),
            "first_name": first_name,
            "last_name": last_name,
            "full_name": customer_name,
            "phone": customer.get("phone") or appointment.get("customer_phone_number") or "",
            "email": customer.get("email") or "",
        },
        "employee": {
            "treatwell_id": _string_or_empty(appointment.get("staff_member_id")),
            "first_name": staff.get("first_name") or "",
            "last_name": staff.get("last_name") or "",
            "full_name": " ".join(
                part for part in (staff.get("first_name"), staff.get("last_name")) if part
            ),
        },
        "service": {
            "treatwell_id": _string_or_empty(service_data.get("treatment_id")),
            "venue_treatment_id": _string_or_empty(treatment.get("venue_treatment_id")),
            "name": treatment.get("name") or treatment.get("short_name") or "",
        },
        "start_at": start_at.isoformat() if start_at else "",
        "end_at": end_at.isoformat() if end_at else "",
        "duration_minutes": duration,
        "price": price,
        "status": _booking_status(appointment.get("state")),
        "treatwell_status": appointment.get("state") or "",
        "source": source,
        "notes": appointment.get("notes") or "",
        "paid_online": bool(appointment.get("paid_online")),
        "paid_online_amount": _money(appointment.get("paid_online_amount")) or "0.00",
        "paid_online_at": appointment.get("paid_online_at") or "",
        "created_at": appointment.get("created_at") or "",
        "updated_at": appointment.get("updated_at") or "",
        "checked_in_at": appointment.get("checked_in_at") or "",
        "checked_out_at": appointment.get("checked_out_at") or "",
        "workstation_id": _string_or_empty(appointment.get("workstation_id")),
        "booking_token": appointment.get("booking_token") or "",
        "marketplace": appointment.get("appointment_marketplace_detail") or {},
    }


def _parse_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _positive_int(value, *, default=0):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _money(value):
    if value in (None, ""):
        return None
    try:
        return f"{Decimal(str(value)):.2f}"
    except (InvalidOperation, ValueError):
        return None


def _string_or_empty(value):
    return "" if value is None else str(value)


def _booking_status(value):
    return {
        "requested": "pending",
        "booked": "confirmed",
        "checked_in": "in_progress",
        "checked_out": "done",
        "missed": "no_show",
        "canceled": "cancelled",
        "cancelled": "cancelled",
        "deleted": "cancelled",
        "discarded": "cancelled",
    }.get((value or "").casefold(), "confirmed")
