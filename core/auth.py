"""Central authentication helpers.

Delegates to core.permissions for role checks — kept for backward compatibility.
"""

from core.permissions import has_full_access, full_access_required  # noqa: F401


def is_admin_role(user):
    """Return True if user is in the Owner group."""
    if not user.is_authenticated:
        return False
    return user.groups.filter(name='Owner').exists()
