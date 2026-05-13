from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.template.loader import render_to_string

from .cart import Cart
from menu.models import MenuItem, Table


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

        cart.add(product=product, product_qty=product_quantity)

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
