/**
 * 支付相关API
 *
 * 支付宝入口默认关闭：余额充值（createRecharge / getRechargeStatus）已停用，
 * 第一版改为兑换码购买。提现与结算记录接口保留。
 */
import { post, get } from '@/utils/request'

const PAYMENT_PREFIX = '/api/v1/payment'

export interface SettlementRecord {
  id: number
  alipay_id?: string
  payment_type?: 'alipay' | 'wechat'
  payment_qrcode?: string
  amount: string
  status: 'pending_review' | 'approved' | 'rejected' | 'paid'
  remark?: string
  reject_reason?: string
  created_at?: string
  updated_at?: string
}

export interface SettlementRecordListData {
  list: SettlementRecord[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

/** 创建提现申请 */
export const createWithdraw = async (amount: string): Promise<{
  success: boolean
  message?: string
  data?: {
    id: number
    amount: string
    status: string
    alipay_id: string
    balance: string
    created_at?: string
  }
}> => {
  return post(`${PAYMENT_PREFIX}/withdraw`, { amount })
}

/** 查询结算记录 */
export const getSettlementRecords = async (page: number = 1, pageSize: number = 20): Promise<{
  success: boolean
  message?: string
  data?: SettlementRecordListData
}> => {
  return get(`${PAYMENT_PREFIX}/settlement-records?page=${page}&page_size=${pageSize}`)
}
