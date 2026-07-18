import { get, post } from '@/utils/request'

const BILLING_PREFIX = '/api/v1/billing'

export interface ApiResponse<T> {
  success: boolean
  message?: string
  data?: T
}

export type BillingCycle = 'monthly' | 'quarterly' | 'yearly'

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
  amount: string
  validity_days: number
}

export interface BillingCatalog {
  plans: BillingPlan[]
  ai_quota_packages: AIQuotaPackage[]
}

export interface BillingOrder {
  order_no: string
  product_type: 'plan' | 'ai_quota_package'
  product_code: string
  product_name: string
  amount: string
  currency: string
  status: 'pending' | 'paid' | 'expired' | 'failed' | 'cancelled'
  payment_channel?: string
  payment_ready?: boolean
  qr_code?: string | null
  payment_expires_at?: string | null
  entitlement_status: 'pending' | 'granted' | 'failed'
  paid_at?: string | null
  duplicate?: boolean
  created_at?: string | null
}

export interface AIUsageSummary {
  effective_replies: number
  remaining_quota: number | null
}

export const getBillingCatalog = () =>
  get<ApiResponse<BillingCatalog>>(`${BILLING_PREFIX}/catalog`)

export const getBillingPaymentReadiness = () =>
  get<ApiResponse<{ payment_ready: boolean }>>(`${BILLING_PREFIX}/payment-readiness`)

export const createBillingOrder = (
  productType: 'plan' | 'ai_quota_package',
  productId: number,
  requestKey: string,
) => post<ApiResponse<BillingOrder>>(`${BILLING_PREFIX}/orders`, {
  product_type: productType,
  product_id: productId,
  request_key: requestKey,
})

export const createBillingPayment = (orderNo: string) =>
  post<ApiResponse<BillingOrder>>(`${BILLING_PREFIX}/orders/${orderNo}/pay`)

export const getBillingOrder = (orderNo: string) =>
  get<ApiResponse<BillingOrder>>(`${BILLING_PREFIX}/orders/${orderNo}`)

export const getMyAIUsage = () =>
  get<ApiResponse<AIUsageSummary>>('/api/v1/ai-usage')
