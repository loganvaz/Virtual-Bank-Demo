import logging
import uuid
from contextvars import ContextVar

_request_id: ContextVar = ContextVar("request_id", default="-")

REQUEST_ID_HEADER = "HTTP_X_REQUEST_ID"


def get_request_id():
    return _request_id.get()


class RequestIDMiddleware:
    """Attach a per-request correlation ID (honouring an inbound X-Request-ID)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.META.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        token = _request_id.set(request_id)
        request.request_id = request_id
        try:
            response = self.get_response(request)
        finally:
            _request_id.reset(token)
        response["X-Request-ID"] = request_id
        return response


class RequestIDFilter(logging.Filter):
    def filter(self, record):
        record.request_id = get_request_id()
        return True
