/**
 * 兑换码 API
 *
 * 功能：
 * 1. 客户兑换兑换码（支持升级二次确认）
 * 2. 管理员生成/查询/导出/禁用兑换码批次与单码
 * 3. 兑换审计查询（不含完整兑换码）
 * 4. 读取兑换码商城地址（redemption_store_url，统一从系统设置读取，不在前端硬编码）
 */
import { get, post } from '@/utils/request'
import type { ApiResponse } from '@/types'

const REDEMPTION_PREFIX = '/api/v1/redemption'
// 管理员接口挂在 /admin 下：/api/v1/admin/redemption/*
const ADMIN_PREFIX = '/api/v1/admin/redemption'

/** 兑换动作 */
export type RedeemAction = 'new' | 'renew' | 'upgrade' | 'pending_downgrade' | 'package' | 'replay'

/** 兑换结果 */
export interface RedeemResult {
  record_id: number
  batch_id: number
  product_type: 'plan' | 'ai_quota_package'
  product_code: string
  action: RedeemAction
  duplicate: boolean
}

/** 升级预览返回（needs_confirmation=true 时前端弹窗确认） */
export interface RedeemPreview {
  needs_confirmation: boolean
  action: RedeemAction
  code_last4?: string
  product_type?: string
  product_code?: string
}

/** 兑换码批次（列表视图，仅尾4位/状态/统计，不含完整码） */
export interface RedemptionBatch {
  batch_id: number
  batch_no: string
  product_type: 'plan' | 'ai_quota_package'
  product_code: string
  product_name: string
  billing_cycle: string | null
  duration_months: number | null
  validity_days: number | null
  ai_unlimited: boolean
  quantity: number
  generated_count: number
  used_count: number
  disabled: boolean
  disable_reason: string | null
  expires_at: string | null
  exported_at: string | null
  created_at: string | null
}

/** 创建批次返回（仅元数据，不含完整兑换码） */
export interface CreateBatchResult {
  batch_id: number
  batch_no: string
  product_type: 'plan' | 'ai_quota_package'
  product_code: string
}

/** 兑换审计记录（不含完整兑换码） */
export interface RedemptionAuditRecord {
  record_id: number
  batch_id: number
  code_id: number
  user_id: number
  product_type: string
  product_code: string
  created_at: string | null
}

/** 兑换码商城地址配置 */
export interface RedemptionStoreConfig {
  redemption_store_url: string
}

/**
 * 客户兑换兑换码。
 * 升级场景：confirm=false 时后端只做只读预览，返回 needs_confirmation=true；
 * 前端弹窗确认后以 confirm=true 再次提交。
 * 不在响应/异常/日志中回显完整兑换码（错误信息均为通用文案）。
 */
export const redeem = (code: string, idempotencyKey: string, confirm = false) =>
  post<ApiResponse<RedeemResult | RedeemPreview>>(`${REDEMPTION_PREFIX}/redeem`, {
    code,
    idempotency_key: idempotencyKey,
    confirm,
  })

/** 管理员：生成并提交兑换码批次，仅返回元数据 */
export const createBatch = (data: {
  product_type: 'plan' | 'ai_quota_package'
  product_code: string
  billing_cycle?: string | null
  count: number
  expires_at?: string | null
}) => post<ApiResponse<CreateBatchResult>>(`${ADMIN_PREFIX}/batches`, data)

/** 管理员：查询批次列表（不含完整码） */
export const listBatches = (limit = 100) =>
  get<ApiResponse<RedemptionBatch[]>>(`${ADMIN_PREFIX}/batches?limit=${limit}`)

/** 管理员：一次性导出批次完整兑换码（返回文件下载，仅成功一次） */
export const exportBatch = (batchId: number) =>
  post(`${ADMIN_PREFIX}/batches/${batchId}/export`, undefined, {
    responseType: 'blob',
  })

/** 管理员：禁用批次 */
export const disableBatch = (batchId: number, reason?: string) => {
  const query = reason ? `?reason=${encodeURIComponent(reason)}` : ''
  return post<ApiResponse>(`${ADMIN_PREFIX}/batches/${batchId}/disable${query}`)
}

/** 管理员：禁用单码 */
export const disableCode = (codeId: number, reason?: string) => {
  const query = reason ? `?reason=${encodeURIComponent(reason)}` : ''
  return post<ApiResponse>(`${ADMIN_PREFIX}/codes/${codeId}/disable${query}`)
}

/** 管理员：兑换审计（不含完整兑换码） */
export const audit = (params?: { batch_id?: number; limit?: number }) => {
  const query = new URLSearchParams()
  if (params?.batch_id) query.append('batch_id', String(params.batch_id))
  query.append('limit', String(params?.limit ?? 100))
  return get<ApiResponse<RedemptionAuditRecord[]>>(`${ADMIN_PREFIX}/audit?${query.toString()}`)
}

/**
 * 读取兑换码商城地址（redemption_store_url）。
 * 统一从系统设置读取，不在前端组件中硬编码商城 URL。
 * 登录用户可读取；未登录或配置缺失/读取失败时返回空串，由页面渲染
 * 安全降级提示（“商城地址未配置”），绝不渲染任意来源的硬编码地址。
 */
export const getRedemptionStoreUrl = () =>
  get<Record<string, string>>('/api/v1/system-settings').then((settings) => {
    const url = settings?.redemption_store_url
    return typeof url === 'string' && url.length > 0 ? url : ''
  })
