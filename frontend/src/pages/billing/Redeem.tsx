/**
 * 兑换码兑换页
 *
 * 功能：
 * 1. 输入兑换码，自动生成幂等键，防重复提交；
 * 2. 显示兑换成功/失败/过期/禁用/已使用等结果；
 * 3. 浏览器日志与错误提示均不输出/回显完整兑换码；
 * 4. 升级场景后端返回 needs_confirmation，前端弹窗提示
 *    “旧套餐剩余时间将不折算、不退补，确认继续兑换吗？”，
 *    用户取消则不发起兑换请求；
 * 5. 兑换完成后跳转套餐页刷新套餐/额度/权益展示。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, CheckCircle, Loader2, Ticket } from 'lucide-react'
import { redeem, type RedeemResult } from '@/api/redemption'
import {
  getBillingCatalog,
  getMyAIUsage,
  getMyEntitlements,
  type AIUsageSummary,
  type BillingCatalog,
  type UserEntitlements,
} from '@/api/billing'
import { useUIStore } from '@/store/uiStore'
import { getApiErrorMessage } from '@/utils/request'
import { ConfirmModal } from '@/components/common/ConfirmModal'

/** 生成幂等键（UUID 优先，回退随机串） */
const makeIdempotencyKey = () =>
  typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `redeem-${Date.now()}-${Math.random().toString(36).slice(2)}`

export function Redeem() {
  const navigate = useNavigate()
  const addToast = useUIStore((state) => state.addToast)
  const [code, setCode] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState<RedeemResult | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  // 升级二次确认时复用同一幂等键，保证幂等
  const pendingKeyRef = useRef<string>('')
  // 权益展示：到期时间 / 账号上限 / AI 额度 / 无限权益 / 免费用户独立加量包剩余额度
  const [catalog, setCatalog] = useState<BillingCatalog | null>(null)
  const [usage, setUsage] = useState<AIUsageSummary | null>(null)
  const [entitlements, setEntitlements] = useState<UserEntitlements | null>(null)

  const loadEntitlements = useCallback(async () => {
    try {
      const [catalogRes, usageRes, entRes] = await Promise.all([
        getBillingCatalog(),
        getMyAIUsage(),
        getMyEntitlements(),
      ])
      setCatalog(catalogRes.data || null)
      setUsage(usageRes.data || null)
      setEntitlements(entRes.data || null)
    } catch {
      // 权益加载失败不阻塞兑换输入
    }
  }, [])

  useEffect(() => { void loadEntitlements() }, [loadEntitlements])

  // 兑换成功后刷新权益展示
  useEffect(() => {
    if (result) void loadEntitlements()
  }, [result, loadEntitlements])

  // 当前套餐是否享有无限权益：套餐本身无限，或权益来源套餐且额度标记无限
  const unlimitedActive = Boolean(
    catalog?.plans.find((p) => p.code === entitlements?.plan_code)?.ai_unlimited,
  )

  const handleSubmit = useCallback(async () => {
    const trimmed = code.trim()
    if (!trimmed || submitting) return
    if (trimmed.length < 8) {
      addToast({ type: 'error', message: '兑换码格式不正确' })
      return
    }
    // 每次提交生成新幂等键；升级二次确认时复用同一键
    const idempotencyKey = pendingKeyRef.current || makeIdempotencyKey()
    pendingKeyRef.current = idempotencyKey

    setSubmitting(true)
    try {
      const response = await redeem(trimmed, idempotencyKey, false)
      // 升级场景：后端只做只读预览，返回 needs_confirmation
      const data = response.data
      if (data && typeof data === 'object' && 'needs_confirmation' in data && data.needs_confirmation) {
        setConfirmOpen(true)
        return
      }
      if (response.success && data && 'record_id' in data) {
        setResult(data as RedeemResult)
        addToast({ type: 'success', message: response.message || '兑换成功' })
        pendingKeyRef.current = ''
      } else {
        addToast({ type: 'error', message: response.message || '兑换失败' })
      }
    } catch (error) {
      // 不在日志/提示中输出完整兑换码；错误文案由后端提供（通用，不含完整码）
      addToast({ type: 'error', message: getApiErrorMessage(error, '兑换失败，请检查兑换码是否正确') })
    } finally {
      setSubmitting(false)
    }
  }, [code, submitting, addToast, pendingKeyRef])

  /** 升级二次确认：复用同一幂等键以 confirm=true 真正兑换 */
  const handleConfirmUpgrade = useCallback(async () => {
    const trimmed = code.trim()
    const idempotencyKey = pendingKeyRef.current || makeIdempotencyKey()
    pendingKeyRef.current = idempotencyKey
    setSubmitting(true)
    try {
      const response = await redeem(trimmed, idempotencyKey, true)
      const data = response.data
      if (response.success && data && 'record_id' in data) {
        setResult(data as RedeemResult)
        addToast({ type: 'success', message: response.message || '兑换成功' })
        setConfirmOpen(false)
        pendingKeyRef.current = ''
      } else {
        addToast({ type: 'error', message: response.message || '兑换失败' })
      }
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '兑换失败，请检查兑换码是否正确') })
    } finally {
      setSubmitting(false)
    }
  }, [code, addToast, pendingKeyRef])

  return (
    <div className="space-y-6 pb-10">
      <div className="page-header flex-between flex-wrap gap-4">
        <div>
          <h1 className="page-title">兑换码兑换</h1>
          <p className="page-description">输入在链动小铺购买后获得的兑换码，核销后立即发放套餐或 AI 加量额度</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button onClick={() => navigate('/billing/store')} className="btn-ios-secondary">
            <ArrowLeft className="w-4 h-4" />前往商城
          </button>
          <button onClick={() => navigate('/billing')} className="btn-ios-secondary">
            <ArrowLeft className="w-4 h-4" />返回套餐页
          </button>
        </div>
      </div>

      <div className="vben-card">
        <div className="vben-card-body space-y-4">
          <div className="input-group">
            <label className="input-label">兑换码</label>
            <input
              type="text"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="请输入兑换码"
              className="input-ios"
              autoComplete="off"
              disabled={submitting}
              onKeyDown={(e) => { if (e.key === 'Enter') void handleSubmit() }}
            />
          </div>
          <button onClick={handleSubmit} disabled={submitting || !code.trim()} className="btn-ios-primary w-full">
            {submitting ? <><Loader2 className="w-4 h-4 animate-spin" />兑换中...</> : <><Ticket className="w-4 h-4" />立即兑换</>}
          </button>
          <p className="text-xs text-slate-400">请妥善保存兑换码，不要发送给其他人。兑换码核销后即失效。</p>
        </div>
      </div>

      {/* 当前权益展示：到期时间 / 账号上限 / AI 额度 / 无限权益 / 免费用户独立加量包剩余额度 */}
      <div className="vben-card">
        <div className="vben-card-header">
          <h2 className="vben-card-title">当前套餐与额度</h2>
        </div>
        <div className="vben-card-body grid grid-cols-2 gap-3 md:grid-cols-3">
          <EntitlementCell label="当前套餐" value={planLabel(entitlements?.plan_code)} />
          <EntitlementCell label="账号上限" value={entitlements ? String(entitlements.account_limit) : '-'} />
          <EntitlementCell
            label="到期时间"
            value={entitlements?.expires_at ? new Date(entitlements.expires_at).toLocaleDateString('zh-CN') : '长期'}
          />
          <EntitlementCell label="本月有效回复" value={formatQuota(usage?.effective_replies ?? 0)} />
          <EntitlementCell
            label="剩余可用额度"
            value={unlimitedActive ? '不限' : formatQuota(usage ? usage.remaining_quota : 0)}
          />
          <EntitlementCell label="无限权益" value={unlimitedActive ? '已开启' : '未开启'} />
        </div>
        <div className="vben-card-body pt-0 text-xs text-slate-400">
          {entitlements?.source === 'free_fallback'
            ? '当前为免费用户。若持有有效独立 AI 加量包，加量包额度可用于继续 AI 回复。'
            : '套餐内 AI 额度按自然月发放；额度不足可购买 AI 加量包。'}
        </div>
      </div>

      {result && (
        <div className="vben-card border-emerald-200 dark:border-emerald-900">
          <div className="vben-card-body flex items-start gap-3">
            <CheckCircle className="mt-0.5 h-6 w-6 text-emerald-500" />
            <div className="flex-1">
              <div className="font-semibold text-slate-900 dark:text-white">兑换成功</div>
              <div className="mt-1 text-sm text-slate-500">
                产品类型：{result.product_type === 'plan' ? '套餐' : 'AI 加量包'} · 动作：
                {actionLabel(result.action)}
              </div>
              <button onClick={() => navigate('/billing')} className="btn-ios-primary mt-4">
                查看我的套餐与额度
              </button>
            </div>
          </div>
        </div>
      )}

      <ConfirmModal
        isOpen={confirmOpen}
        title="升级确认"
        message="旧套餐剩余时间将不折算、不退补，确认继续兑换吗？"
        confirmText="确认兑换"
        cancelText="取消"
        type="warning"
        loading={submitting}
        onConfirm={() => void handleConfirmUpgrade()}
        onCancel={() => { setConfirmOpen(false); pendingKeyRef.current = '' }}
      />
    </div>
  )
}

function actionLabel(action: string): string {
  const mapping: Record<string, string> = {
    new: '开通套餐',
    renew: '续期',
    upgrade: '升级（旧套餐剩余时间不折算）',
    pending_downgrade: '待生效降级',
    package: '加量包发放',
    replay: '幂等返回',
  }
  return mapping[action] || action
}

const PLAN_NAMES: Record<string, string> = {
  free: '免费版',
  standard: '标准版',
  merchant: '商家版',
  enterprise: '企业版',
}

function planLabel(planCode?: string): string {
  if (!planCode) return '-'
  return PLAN_NAMES[planCode] || planCode
}

function formatQuota(value: number | null): string {
  return value === null ? '不限' : value.toLocaleString('zh-CN')
}

function EntitlementCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
      <div className="text-sm text-slate-500 dark:text-slate-400">{label}</div>
      <div className="mt-1 text-lg font-bold text-slate-900 dark:text-white">{value}</div>
    </div>
  )
}
