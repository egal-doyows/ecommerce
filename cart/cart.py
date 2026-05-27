import copy
from decimal import Decimal

from menu.models import MenuItem


def _make_key(product_id, option_ids):
    """
    Cart line key. Items with different accompaniments are separate lines, so
    the key folds in the chosen option ids:
        "12:"      → product 12, no accompaniments
        "12:3-7"   → product 12 with options 3 and 7
    """
    if option_ids:
        joined = '-'.join(str(o) for o in sorted(int(i) for i in option_ids))
        return f"{product_id}:{joined}"
    return f"{product_id}:"


class Cart():
    def __init__(self, request):
        self.session = request.session
        cart = self.session.get('session_key')

        if 'session_key' not in request.session:
            cart = self.session['session_key'] = {}

        self.cart = cart

    def add(self, product, product_qty, options=None):
        """
        Add a line. `options` is a list of resolved accompaniment dicts:
            {'id': int, 'group_name': str, 'label': str, 'delta': Decimal|str}
        The stored price is all-in: base price + sum of option deltas.
        """
        options = options or []
        option_ids = [o['id'] for o in options]
        key = _make_key(product.id, option_ids)

        if key in self.cart:
            self.cart[key]['qty'] = product_qty
        else:
            delta = sum(Decimal(str(o['delta'])) for o in options)
            self.cart[key] = {
                'price': str(product.price + delta),
                'qty': product_qty,
                'product_id': int(product.id),
                'options': [
                    {
                        'id': int(o['id']),
                        'group_name': o.get('group_name', ''),
                        'label': o['label'],
                        'delta': str(o['delta']),
                    }
                    for o in options
                ],
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
        for key, item in self.cart.items():
            pid = item.get('product_id')
            if pid is None:
                try:
                    pid = int(key)
                except ValueError:
                    continue
            product_ids.add(int(pid))

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
                item.setdefault('options', [])
                yield item

    def get_total(self):
        return sum(Decimal(item['price']) * item['qty'] for item in self.cart.values())
