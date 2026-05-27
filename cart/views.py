from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.template.loader import render_to_string

from .cart import Cart
from menu.models import MenuItem, Table, AccompanimentOption


def resolve_options(product, option_ids):
    """
    Validate the chosen accompaniment options for a product and return them as
    snapshot dicts. Single-choice: each required group needs exactly one pick.
    Returns (options, error_message). On success error_message is None.
    """
    groups = list(product.accompaniment_groups.all())
    if not groups:
        return [], None

    group_ids = [g.id for g in groups]
    selected = list(
        AccompanimentOption.objects
        .filter(id__in=option_ids, group_id__in=group_ids, is_available=True)
        .select_related('group')
    )

    chosen_by_group = {}
    for opt in selected:
        chosen_by_group.setdefault(opt.group_id, []).append(opt)

    for g in groups:
        picks = chosen_by_group.get(g.id, [])
        if g.is_required and not picks:
            return None, f"Please choose: {g.name}"
        if len(picks) > 1:
            return None, f"Choose only one for: {g.name}"

    options = [
        {
            'id': opt.id,
            'group_name': opt.group.name,
            'label': opt.label,
            'delta': opt.price_delta,
        }
        for opt in selected
    ]
    return options, None


def _cart_response(request, cart):
    html = render_to_string('cart/_order_items.html', {'cart': cart}, request=request)
    return JsonResponse({
        'qty': cart.__len__(),
        'total': str(cart.get_total()),
        'html': html,
    })


@login_required(login_url='waiter-login')
def cart_summary(request):
    cart = Cart(request)
    tables = Table.objects.all()
    return render(request, 'cart/cart-summary.html', {'cart': cart, 'tables': tables})


@login_required(login_url='waiter-login')
def cart_add(request):
    cart = Cart(request)

    if request.POST.get('action') == 'post':
        product_id = int(request.POST.get('product_id'))
        product_quantity = int(request.POST.get('product_quantity'))
        product = get_object_or_404(MenuItem, id=product_id)

        options, error = resolve_options(product, request.POST.getlist('option_id'))
        if error:
            return JsonResponse({'error': error}, status=400)

        cart.add(product=product, product_qty=product_quantity, options=options)

        return _cart_response(request, cart)


@login_required(login_url='waiter-login')
def cart_update(request):
    cart = Cart(request)

    if request.POST.get('action') == 'post':
        cart_key = request.POST.get('cart_key', '')
        product_quantity = int(request.POST.get('product_quantity'))

        # Support legacy product_id param
        if not cart_key:
            cart_key = request.POST.get('product_id', '')

        cart.update(key=cart_key, qty=product_quantity)

        return _cart_response(request, cart)


@login_required(login_url='waiter-login')
def cart_delete(request):
    cart = Cart(request)

    if request.POST.get('action') == 'post':
        cart_key = request.POST.get('cart_key', '')

        # Support legacy product_id param
        if not cart_key:
            cart_key = request.POST.get('product_id', '')

        cart.delete(key=cart_key)

        return _cart_response(request, cart)
