"""
pricing.py — subscription plans and coupons (pricing.json), shared by both
backends and the landing page. Coupon codes stay server-side: the public
listing never includes them; a code is only confirmed through check_coupon().
"""
import json
import os

PRICING_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pricing.json')


def load():
    with open(PRICING_FILE, encoding='utf-8') as f:
        return json.load(f)


def public():
    """Plans for the landing page / signup — without the coupon codes."""
    data = load()
    return {'currency': data.get('currency', '₪'), 'plans': data['plans']}


def plan_ids():
    return [p['id'] for p in load()['plans']]


def normalize_plan(plan_id):
    ids = plan_ids()
    return plan_id if plan_id in ids else None


def check_coupon(code, plan_id=None):
    """{'code', 'plan', 'price'} for a valid code (case-insensitive), else None.
    A code tied to a plan only applies to that plan."""
    code = (code or '').strip().upper()
    c = {k.upper(): v for k, v in load().get('coupons', {}).items()}.get(code)
    if not c or (plan_id and c.get('plan') and c['plan'] != plan_id):
        return None
    return {'code': code, 'plan': c.get('plan'), 'price': c.get('price')}


def price_for(plan_id, coupon_code=None):
    """Monthly price for a plan, after a valid coupon."""
    plan = next((p for p in load()['plans'] if p['id'] == plan_id), None)
    if not plan:
        return None
    c = check_coupon(coupon_code, plan_id) if coupon_code else None
    return c['price'] if c else plan['price']
