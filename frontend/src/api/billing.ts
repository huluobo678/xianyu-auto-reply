import { get } from '@/utils/request'

const BILLING_PREFIX = '/api/v1/billing'

export interface ApiResponse<T> {
  success: boolean
  message?: string
  data?: T
}

// 第一版只支持月卡/季卡，不开放年付
export type BillingCycle = 'monthly' | 'quarterly'

export interface BillingPlanPrice {
  id: number
  billing_cycle: BillingCycle
  duration_months: number
  amount: string
  currency: string
}

export interface BillingPlan {
  id: number
  code: string
  name: string
  account_limit: number
  monthly_ai_quota: number
  ai_unlimited: boolean
  feature_flags: string[]
  is_free: boolean
  prices: BillingPlanPrice[]
}

export interface AIQuotaPackage {
  id: number
  code: string
  name: string
  base_quota: number
  bonus_quota: number
  total_quota: number
  ai_unlimited: boolean
  amount: string
  validity_days: number
}

export interface BillingCatalog {
  plans: BillingPlan[]
  ai_quota_packages: AIQuotaPackage[]
}

export interface AIUsageSummary {
  effective_replies: number
  remaining_quota: number | null
}

export interface UserEntitlements {
  plan_code: string
  account_limit: number
  monthly_ai_quota: number
  features: string[]
  expires_at: string | null
  source: 'subscription' | 'free_fallback'
}

// 支付宝入口默认关闭，第一版改为兑换码购买：
// 套餐支付 / 余额充值 / 广告付款及回调均不再通过支付宝发起，相关 API 已停用。
// catalog 与 entitlements 保留，用于展示套餐目录与当前权益。
export const getBillingCatalog = () =>
  get<ApiResponse<BillingCatalog>>(`${BILLING_PREFIX}/catalog`)

export const getMyAIUsage = () =>
  get<ApiResponse<AIUsageSummary>>('/api/v1/ai-usage')

export const getMyEntitlements = () =>
  get<ApiResponse<UserEntitlements>>(`${BILLING_PREFIX}/entitlements`)
