"""
Scalable approval notification registry.

Any Django app can register a provider function that returns pending
approval info for the bell icon. Each provider is a callable:

    def provider(request) -> dict | None

Returned dict keys:
    label     (str)  – Human-readable group name, e.g. "Advance Requests"
    count     (int)  – Number of pending items
    url       (str)  – Link to the approval/list page
    icon      (str)  – FontAwesome class, e.g. "fa-solid fa-money-bill"
    priority  (int)  – Lower = shown first (optional, default 10)

Return None (or a dict with count=0) to suppress the entry.
"""

_registry = []


def register(provider_fn):
    """Register an approval provider function. Can be used as a decorator."""
    _registry.append(provider_fn)
    return provider_fn


def get_pending_approvals(request):
    """Collect all pending approval groups for the current request."""
    results = []
    for fn in _registry:
        try:
            info = fn(request)
        except Exception:
            continue
        if info and info.get('count', 0) > 0:
            info.setdefault('priority', 10)
            results.append(info)
    results.sort(key=lambda x: x['priority'])
    return results
