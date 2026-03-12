from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse

from .cart import Cart
from menu.models import MenuItem, Table


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

        cart_quantity = cart.__len__()
        response = JsonResponse({'qty': cart_quantity})
        return response


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

        cart_quantity = cart.__len__()
        cart_total = cart.get_total()

        response = JsonResponse({'qty': cart_quantity, 'total': str(cart_total)})
        return response


@login_required(login_url='waiter-login')
def cart_delete(request):
    cart = Cart(request)

    if request.POST.get('action') == 'post':
        cart_key = request.POST.get('cart_key', '')

        # Support legacy product_id param
        if not cart_key:
            cart_key = request.POST.get('product_id', '')

        cart.delete(key=cart_key)

        cart_quantity = cart.__len__()
        cart_total = cart.get_total()

        response = JsonResponse({'qty': cart_quantity, 'total': str(cart_total)})
        return response
