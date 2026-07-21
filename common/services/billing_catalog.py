from decimal import Decimal

# 套餐目录。
# - account_limit：1/3/8/30
# - enterprise：monthly_ai_quota=0 + ai_unlimited=True（无限次，仍计量与风控）
# - 免费版赠送的 100 次新用户额度由 user_service 注册流程发放，不在此声明。
PLAN_CATALOG = (
    {'code': 'free', 'name': '免费版', 'account_limit': 1, 'monthly_ai_quota': 0, 'ai_unlimited': False, 'is_free': True, 'sort_order': 0},
    {'code': 'standard', 'name': '标准版', 'account_limit': 3, 'monthly_ai_quota': 1000, 'ai_unlimited': False, 'is_free': False, 'sort_order': 10},
    {'code': 'merchant', 'name': '商家版', 'account_limit': 8, 'monthly_ai_quota': 5000, 'ai_unlimited': False, 'is_free': False, 'sort_order': 20},
    {'code': 'enterprise', 'name': '企业版', 'account_limit': 30, 'monthly_ai_quota': 0, 'ai_unlimited': True, 'is_free': False, 'sort_order': 30},
)

# 第一版只支持月卡和季卡，不开放年卡。年付记录由迁移脚本设为 disabled。
PLAN_PRICES = (
    ('standard', 'monthly', 1, Decimal('39.00')),
    ('standard', 'quarterly', 3, Decimal('99.00')),
    ('merchant', 'monthly', 1, Decimal('99.00')),
    ('merchant', 'quarterly', 3, Decimal('269.00')),
    ('enterprise', 'monthly', 1, Decimal('199.00')),
    ('enterprise', 'quarterly', 3, Decimal('539.00')),
)

# AI 加量包。轻量包(ai_light)与旧价格由迁移脚本设为 disabled。
# 常用包 1200 次 / 12.9 元 / 90 天
# 商家包 6000 次 / 39 元 / 180 天
# 无限包 79 元 / 30 天 / ai_unlimited=True（有效期内不限次，不改套餐等级与账号上限）
# 元组格式：(code, name, base_quota, bonus_quota, ai_unlimited, amount, validity_days, sort_order)
AI_QUOTA_PACKAGES = (
    ('ai_regular', '常用包', 1200, 0, False, Decimal('12.90'), 90, 10),
    ('ai_merchant', '商家包', 6000, 0, False, Decimal('39.00'), 180, 20),
    ('ai_unlimited', '无限包', 0, 0, True, Decimal('79.00'), 30, 30),
)

DEFAULT_FEATURE_FLAGS = {
    'free': ['keyword_reply', 'online_chat', 'manual_takeover', 'basic_auto_delivery'],
    'standard': ['keyword_reply', 'online_chat', 'manual_takeover', 'basic_auto_delivery', 'ai_reply', 'single_publish'],
    'merchant': ['keyword_reply', 'online_chat', 'manual_takeover', 'basic_auto_delivery', 'ai_reply', 'batch_publish', 'listing_monitor'],
    'enterprise': ['keyword_reply', 'online_chat', 'manual_takeover', 'basic_auto_delivery', 'ai_reply', 'batch_publish', 'listing_monitor', 'api_access'],
}

# 已废弃的旧套餐名称，仅供迁移/文档标记，不再作为业务依据。
DEPRECATED_PLAN_CODES = ('trial', 'basic', 'pro', 'team')

# 已废弃的旧加量包码，迁移脚本将其 enabled 置 0。
DEPRECATED_QUOTA_PACKAGE_CODES = ('ai_light', 'ai_large')

# AI 加量包码白名单（管理员生成兑换码时只能选这些）。
ALLOWED_QUOTA_PACKAGE_CODES = ('ai_regular', 'ai_merchant', 'ai_unlimited')
