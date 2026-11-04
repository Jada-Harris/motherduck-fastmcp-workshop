"""Your MCP server for San Francisco 311 data.

FastMCP Gateway runs this file. Its entrypoint setting is `server.py:mcp`.
"""

from collections import Counter, defaultdict
from statistics import median
from typing import Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from sf311.data import BY_ID, LOCAL_NAMES, NEIGHBORHOODS, REQUESTS, SERVICES, Request, closest

MAX_ROWS = 50
MIN_CLOSED_FOR_MEDIAN = 10

GROUPS = {
    "neighborhood": lambda r: r.neighborhood,
    "service": lambda r: r.service,
    "agency": lambda r: r.agency,
    "source": lambda r: r.source,
    "weekday": lambda r: r.opened.strftime("%A"),
    "status": lambda r: r.status,
}

mcp = FastMCP("sf-311")


@mcp.tool(annotations={"readOnlyHint": True})
def dataset_info() -> dict:
    """Describe the 311 dataset this server answers questions about.

    Call this first to learn the date range and the exact service and
    neighborhood names that other tools accept.
    """
    return {
        "description": "San Francisco 311 service requests filed in one week.",
        "first_opened": min(request.opened for request in REQUESTS).isoformat(),
        "last_opened": max(request.opened for request in REQUESTS).isoformat(),
        "requests": len(REQUESTS),
        "services": SERVICES,
        "neighborhoods": NEIGHBORHOODS,
        "local_neighborhood_names": len(LOCAL_NAMES),
    }


def _check_name(kind: str, value: str | None, choices: list[str]) -> None:
    if value is None or value in choices:
        return
    suggestions = closest(value, choices)
    hint = f"Did you mean: {', '.join(suggestions)}?" if suggestions else "Call dataset_info for valid names."
    raise ToolError(f"Unknown {kind} {value!r}. {hint}")


def _matching(service: str | None, neighborhood: str | None, status: str | None = None) -> list[Request]:
    _check_name("service", service, SERVICES)
    _check_name("neighborhood", neighborhood, NEIGHBORHOODS)
    return [
        r
        for r in REQUESTS
        if (service is None or r.service == service)
        and (neighborhood is None or r.neighborhood == neighborhood)
        and (status is None or r.status == status)
    ]


@mcp.tool(annotations={"readOnlyHint": True})
def search_requests(
    service: str | None = None,
    neighborhood: str | None = None,
    status: Literal["Open", "Closed"] | None = None,
    limit: int = 20,
) -> list[dict]:
    """List individual 311 requests, newest first, as full records.

    Use this to look at examples or details (address, subtype, agency, times).
    Returns at most 50 rows. Do NOT use it to count or rank: use
    count_requests for totals, and median_close_hours for response times.
    Use get_request when you already have a request_id.
    `service` and `neighborhood` must match dataset_info names exactly.
    """
    if not 1 <= limit <= MAX_ROWS:
        raise ToolError(f"limit must be between 1 and {MAX_ROWS}; got {limit}. For totals use count_requests.")
    rows = sorted(_matching(service, neighborhood, status), key=lambda r: r.opened, reverse=True)
    return [r.as_dict() for r in rows[:limit]]


@mcp.tool(annotations={"readOnlyHint": True})
def count_requests(
    service: str | None = None,
    neighborhood: str | None = None,
    group_by: Literal["neighborhood", "service", "agency", "source", "weekday", "status"] | None = None,
) -> int | dict[str, int]:
    """Count 311 requests, optionally filtered and grouped.

    Without group_by, returns one number. With group_by, returns a dict of
    group -> count, largest first (at most 50 groups), so the first key is the
    top group. Use this for "which neighborhood/service has the most..."
    questions, e.g. service="Sidewalk and Curb", group_by="neighborhood".
    Requests with no neighborhood are left out of neighborhood groups.
    For response times use median_close_hours; for example records use
    search_requests. Names must match dataset_info exactly.
    """
    rows = _matching(service, neighborhood)
    if group_by is None:
        return len(rows)
    if group_by not in GROUPS:
        raise ToolError(f"Unknown group_by {group_by!r}. Try one of: {', '.join(GROUPS)}.")
    key = GROUPS[group_by]
    counts = Counter(key(r) for r in rows if key(r))
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return dict(ranked[:MAX_ROWS])


@mcp.tool(annotations={"readOnlyHint": True})
def median_close_hours(
    service: str | None = None,
    group_by: Literal["neighborhood", "agency"] | None = None,
) -> float | None | dict[str, float]:
    """Median hours from opening to closing, over closed requests only.

    Without group_by, returns one number (null if nothing has closed). With
    group_by, returns group -> hours, fastest first, leaving out groups with
    fewer than 10 closed requests. Use this for "how fast" questions. For
    volumes use count_requests; for individual cases use search_requests.
    `service` must match a dataset_info name exactly.
    """
    _check_name("service", service, SERVICES)
    if group_by is not None and group_by not in ("neighborhood", "agency"):
        raise ToolError(f"Unknown group_by {group_by!r}. Try 'neighborhood' or 'agency'.")
    rows = [r for r in REQUESTS if r.hours_to_close is not None and (service is None or r.service == service)]
    if group_by is None:
        return round(median(r.hours_to_close for r in rows), 1) if rows else None
    key = GROUPS[group_by]
    hours: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if key(r):
            hours[key(r)].append(r.hours_to_close)
    medians = {g: round(median(v), 1) for g, v in hours.items() if len(v) >= MIN_CLOSED_FOR_MEDIAN}
    return dict(sorted(medians.items(), key=lambda item: (item[1], item[0]))[:MAX_ROWS])


@mcp.tool(annotations={"readOnlyHint": True})
def get_request(request_id: str) -> dict:
    """Return one 311 request by its request_id, with all fields.

    Use this when you already have an id (for example from search_requests).
    To find requests by service or place use search_requests; for totals
    use count_requests.
    """
    request = BY_ID.get(request_id)
    if request is None:
        raise ToolError(f"No request with id {request_id!r}. Use search_requests to find valid request_ids.")
    return request.as_dict()


if __name__ == "__main__":
    mcp.run()
