from .models import Branch, UserBranch


class BranchMiddleware:
    """Attach the current branch to every request as request.branch."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.branch = None
        request.user_branches = []

        if request.user.is_authenticated:
            is_overall = (
                request.user.is_superuser
                or request.user.groups.filter(name__in=['Owner', 'Overall Manager']).exists()
            )

            # Gather all branches this user can access
            assignments = list(
                UserBranch.objects.filter(user=request.user)
                .select_related('branch')
                .order_by('-is_primary', 'branch__name')
            )

            if is_overall:
                # Overall Managers / superusers can access every active branch
                request.user_branches = list(Branch.objects.filter(is_active=True))
            else:
                request.user_branches = [a.branch for a in assignments if a.branch.is_active]

            # Resolve active branch from session
            branch_id = request.session.get('branch_id')
            # Clean up stale 'all' value from previous implementation
            if branch_id == 'all':
                del request.session['branch_id']
                branch_id = None
            if branch_id:
                for b in request.user_branches:
                    if b.pk == branch_id:
                        request.branch = b
                        break

            # Fall back to primary assignment
            if not request.branch and assignments:
                primary = next((a for a in assignments if a.is_primary), assignments[0])
                if primary.branch.is_active:
                    request.branch = primary.branch
                    request.session['branch_id'] = primary.branch.pk

            # Overall users with no assignment: fall back to first active branch
            if not request.branch and is_overall:
                first = Branch.objects.filter(is_active=True).first()
                if first:
                    request.branch = first
                    request.session['branch_id'] = first.pk

        return self.get_response(request)
