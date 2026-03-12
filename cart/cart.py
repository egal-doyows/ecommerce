import copy
from decimal import Decimal

from menu.models import MenuItem


class Cart():
    def __init__(self, request):
        self.session = request.session
        cart = self.session.get('session_key')

        if 'session_key' not in request.session:
            cart = self.session['session_key'] = {}

        self.cart = cart

    def add(self, product, product_qty):
        product_id = str(product.id)

        if product_id in self.cart:
            self.cart[product_id]['qty'] = product_qty
        else:
            self.cart[product_id] = {
                'price': str(product.price),
                'qty': product_qty,
                'product_id': int(product_id),
            }

        self.session.modified = True

    def delete(self, key):
        key = str(key)

        if key in self.cart:
            del self.cart[key]
        self.session.modified = True

    def update(self, key, qty):
        key = str(key)

        if key in self.cart:
            self.cart[key]['qty'] = qty

        self.session.modified = True

    def clear(self):
        self.session['session_key'] = {}
        self.session.modified = True

    def __len__(self):
        return sum(item['qty'] for item in self.cart.values())

    def __iter__(self):
        product_ids = set()
        for key in self.cart.keys():
            try:
                product_ids.add(int(key))
            except ValueError:
                pass

        products = {p.id: p for p in MenuItem.objects.filter(id__in=product_ids)}

        cart = copy.deepcopy(self.cart)

        for key, item in cart.items():
            pid = item.get('product_id')
            if not pid:
                try:
                    pid = int(key)
                except ValueError:
                    continue

            if pid in products:
                item['product'] = products[pid]
                item['key'] = key
                item['price'] = Decimal(item['price'])
                item['total'] = item['price'] * item['qty']
                yield item

    def get_total(self):
        return sum(Decimal(item['price']) * item['qty'] for item in self.cart.values())
