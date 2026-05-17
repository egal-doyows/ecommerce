"""
Market-basket analysis (Apriori) for upsell suggestions.

Output: top rules ranked by lift. POS reads "for antecedent X, the top
consequent is Y with lift L" to suggest add-ons after the first item is added.

Falls back to raw co-occurrence ranking when there's not enough data
or mlxtend isn't installed.
"""

import logging
from collections import defaultdict

from django.db import transaction
from django.utils import timezone

from menu.models import Order, OrderItem
from ml import fallbacks, gates
from ml.models import BasketRule
from ml.trainers._runner import model_run

logger = logging.getLogger(__name__)

MIN_SUPPORT = 0.005      # rule must appear in ≥0.5% of orders
MIN_CONFIDENCE = 0.10
MIN_LIFT = 1.05
TOP_N = 200


def _try_import_apriori():
    try:
        from mlxtend.frequent_patterns import apriori, association_rules  # noqa: WPS433
        return apriori, association_rules
    except Exception as e:
        logger.warning('mlxtend not available (%s) — using co-occurrence baseline', e)
        return None, None


def _baskets():
    """Build {order_id: set(menu_item_id)} for all paid orders."""
    baskets = defaultdict(set)
    rows = OrderItem.objects.filter(order__status='paid').values('order_id', 'menu_item_id')
    for r in rows:
        baskets[r['order_id']].add(r['menu_item_id'])
    return {oid: items for oid, items in baskets.items() if len(items) >= 2}


def train():
    with model_run('basket') as run:
        ready, info = gates.basket_ready()
        run.rows_used = info['multi_item_orders']

        baskets = _baskets()
        if not ready or not baskets:
            rows = fallbacks.basket_baseline()
            _replace_rules(rows, source='baseline')
            run.rows_written = len(rows)
            run.status = 'skipped'
            run.metric_name = 'baseline_only'
            run.error = (
                f"Not enough multi-item orders: "
                f"{info['multi_item_orders']}/{info['need_multi']}"
            )
            return

        apriori, association_rules = _try_import_apriori()
        if apriori is None:
            rows = fallbacks.basket_baseline()
            _replace_rules(rows, source='baseline')
            run.rows_written = len(rows)
            run.status = 'skipped'
            run.metric_name = 'baseline_only'
            run.error = 'mlxtend not installed.'
            return

        import pandas as pd
        from mlxtend.preprocessing import TransactionEncoder

        transactions = [sorted(s) for s in baskets.values()]
        te = TransactionEncoder()
        arr = te.fit(transactions).transform(transactions)
        df = pd.DataFrame(arr, columns=te.columns_)

        freq = apriori(df, min_support=MIN_SUPPORT, use_colnames=True, max_len=2)
        if freq.empty:
            run.status = 'ok'
            run.metric_name = 'rules'
            run.metric_value = 0
            return

        rules = association_rules(freq, metric='lift', min_threshold=MIN_LIFT)
        rules = rules[
            (rules['confidence'] >= MIN_CONFIDENCE)
            & (rules['antecedents'].apply(len) == 1)
            & (rules['consequents'].apply(len) == 1)
        ].sort_values('lift', ascending=False).head(TOP_N)

        out = []
        n_orders = len(baskets)
        for _, r in rules.iterrows():
            (ant,) = tuple(r['antecedents'])
            (con,) = tuple(r['consequents'])
            out.append({
                'antecedent_id': int(ant),
                'consequent_id': int(con),
                'support': float(r['support']),
                'confidence': float(r['confidence']),
                'lift': float(r['lift']),
                'n_orders': n_orders,
            })
        _replace_rules(out, source='ml')
        run.rows_written = len(out)
        run.metric_name = 'mean_lift'
        run.metric_value = float(sum(r['lift'] for r in out) / max(1, len(out)))


@transaction.atomic
def _replace_rules(rows, source):
    BasketRule.objects.all().delete()
    objs = [
        BasketRule(
            antecedent_id=r['antecedent_id'],
            consequent_id=r['consequent_id'],
            support=r['support'],
            confidence=r['confidence'],
            lift=r['lift'],
            n_orders=r['n_orders'],
            source=source,
        )
        for r in rows
    ]
    BasketRule.objects.bulk_create(objs, batch_size=500)
