from decimal import Decimal

PLAN_CATALOG = (
    {'code': 'free', 'name': '免费版', 'account_limit': 1, 'monthly_ai_quota': 0, 'is_free': True, 'sort_order': 0},
    {'code': 'standard', 'name': '标准版', 'account_limit': 2, 'monthly_ai_quota': 1000, 'is_free': False, 'sort_order': 10},
    {'code': 'merchant', 'name': '商家版', 'account_limit': 5, 'monthly_ai_quota': 5000, 'is_free': False, 'sort_order': 20},
    {'code': 'enterprise', 'name': '企业版', 'account_limit': 15, 'monthly_ai_quota': 15000, 'is_free': False, 'sort_order': 30},
)

PLAN_PRICES = (
    ('standard', 'monthly', 1, Decimal('39.00')),
    ('standard', 'quarterly', 3, Decimal('99.00')),
    ('standard', 'yearly', 12, Decimal('399.00')),
    ('merchant', 'monthly', 1, Decimal('99.00')),
    ('merchant', 'quarterly', 3, Decimal('269.00')),
    ('merchant', 'yearly', 12, Decimal('999.00')),
    ('enterprise', 'monthly', 1, Decimal('199.00')),
    ('enterprise', 'quarterly', 3, Decimal('539.00')),
    ('enterprise', 'yearly', 12, Decimal('1999.00')),
)

AI_QUOTA_PACKAGES = (
    ('ai_light', 'AI Light', 500, 100, Decimal('9.90'), 90, 10),
    ('ai_regular', 'AI Regular', 1000, 200, Decimal('16.90'), 90, 20),
    ('ai_merchant', 'AI Merchant', 5000, 1000, Decimal('59.00'), 180, 30),
    ('ai_large', 'AI Large', 10000, 2000, Decimal('99.00'), 180, 40),
)

DEFAULT_FEATURE_FLAGS = {
    'free': ['keyword_reply', 'online_chat', 'manual_takeover', 'basic_auto_delivery'],
    'standard': ['keyword_reply', 'online_chat', 'manual_takeover', 'basic_auto_delivery', 'ai_reply', 'single_publish'],
    'merchant': ['keyword_reply', 'online_chat', 'manual_takeover', 'basic_auto_delivery', 'ai_reply', 'batch_publish', 'listing_monitor'],
    'enterprise': ['keyword_reply', 'online_chat', 'manual_takeover', 'basic_auto_delivery', 'ai_reply', 'batch_publish', 'listing_monitor', 'api_access'],
}
