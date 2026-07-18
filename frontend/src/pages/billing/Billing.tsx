import { useCallback, useEffect, useMemo, useState } from 'react'
import { BadgeCheck, Bot, Check, Clock3, CreditCard, Gift, Loader2, PackagePlus, RefreshCw, ShieldCheck, Sparkles, X } from 'lucide-react'
import { QRCodeSVG } from 'qrcode.react'

import {
  createBillingOrder,
  createBillingPayment,
  getBillingCatalog,
  getBillingOrder,
  getBillingPaymentReadiness,
  getMyAIUsage,
  getMyEntitlements,
  type AIQuotaPackage,
  type AIUsageSummary,
  type BillingCatalog,
  type BillingCycle,
  type BillingOrder,
  type BillingPlan,
  type UserEntitlements,
} from '@/api/billing'
import { useUIStore } from '@/store/uiStore'
import { getApiErrorMessage } from '@/utils/request'

const cycleOptions: Array<{ value: BillingCycle; label: string; hint: string }> = [
  { value: 'monthly', label: '月付', hint: '灵活订阅' },
  { value: 'quarterly', label: '季付', hint: '连续3个月' },
  { value: 'yearly', label: '年付', hint: '长期更优惠' },
]

const featureLabels: Record<string, string> = {
  keyword_reply: '关键词文字和图片回复',
  online_chat: '在线聊天与人工接管',
  manual_takeover: '随时人工接管',
  basic_auto_delivery: '基础自动发货',
  ai_reply: 'AI 上下文回复',
  single_publish: '单品发布',
  batch_publish: '批量商品发布',
  listing_monitor: '商品监控',
  api_access: '开放 API 能力',
}

const planStyles: Record<string, { border: string; badge: string; button: string }> = {
  free: { border: 'border-slate-200 dark:border-slate-700', badge: 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200', button: 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400' },
  standard: { border: 'border-blue-200 dark:border-blue-800', badge: 'bg-blue-100 text-blue-700 dark:bg-blue-900/50 dark:text-blue-300', button: 'bg-blue-600 text-white hover:bg-blue-700' },
  merchant: { border: 'border-violet-400 ring-2 ring-violet-100 dark:ring-violet-900/40', badge: 'bg-violet-100 text-violet-700 dark:bg-violet-900/50 dark:text-violet-300', button: 'bg-violet-600 text-white hover:bg-violet-700' },
  enterprise: { border: 'border-amber-300 dark:border-amber-700', badge: 'bg-amber-100 text-amber-700 dark:bg-amber-900/50 dark:text-amber-300', button: 'bg-amber-500 text-white hover:bg-amber-600' },
}

const planNames: Record<string, string> = { free: '免费版', standard: '标准版', merchant: '商家版', enterprise: '企业版' }
const formatQuota = (value: number | null) => value === null ? '不限' : value.toLocaleString('zh-CN')
const makeRequestKey = () => typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
  ? crypto.randomUUID()
  : `billing-${Date.now()}-${Math.random().toString(36).slice(2)}`

export function Billing() {
  const addToast = useUIStore((state) => state.addToast)
  const [catalog, setCatalog] = useState<BillingCatalog | null>(null)
  const [usage, setUsage] = useState<AIUsageSummary | null>(null)
  const [entitlements, setEntitlements] = useState<UserEntitlements | null>(null)
  const [paymentReady, setPaymentReady] = useState(false)
  const [cycle, setCycle] = useState<BillingCycle>('monthly')
  const [loading, setLoading] = useState(true)
  const [buyingKey, setBuyingKey] = useState<string | null>(null)
  const [paymentOrder, setPaymentOrder] = useState<BillingOrder | null>(null)

  const loadPage = useCallback(async () => {
    setLoading(true)
    try {
      const [catalogResponse, readinessResponse, usageResponse, entitlementResponse] = await Promise.all([
        getBillingCatalog(), getBillingPaymentReadiness(), getMyAIUsage(), getMyEntitlements(),
      ])
      if (!catalogResponse.success || !catalogResponse.data) {
        throw new Error(catalogResponse.message || '套餐目录加载失败')
      }
      setCatalog(catalogResponse.data)
      setPaymentReady(Boolean(readinessResponse.data?.payment_ready))
      setUsage(usageResponse.data || null)
      setEntitlements(entitlementResponse.data || null)
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '套餐与额度加载失败') })
    } finally {
      setLoading(false)
    }
  }, [addToast])

  useEffect(() => { void loadPage() }, [loadPage])

  useEffect(() => {
    if (
      !paymentOrder
      || (paymentOrder.status === 'paid' && paymentOrder.entitlement_status === 'granted')
      || ['expired', 'failed', 'cancelled'].includes(paymentOrder.status)
    ) return undefined
    const timer = window.setInterval(async () => {
      try {
        const response = await getBillingOrder(paymentOrder.order_no)
        if (!response.data) return
        setPaymentOrder(response.data)
        if (response.data.status === 'paid' && response.data.entitlement_status === 'granted') {
          window.clearInterval(timer)
          addToast({ type: 'success', message: '支付成功，套餐或额度已到账' })
          const usageResponse = await getMyAIUsage()
          setUsage(usageResponse.data || null)
        }
      } catch {
        return
      }
    }, 2000)
    return () => window.clearInterval(timer)
  }, [addToast, paymentOrder?.entitlement_status, paymentOrder?.order_no, paymentOrder?.status])

  const sortedPlans = useMemo(() => [...(catalog?.plans || [])].sort((left, right) => left.id - right.id), [catalog])

  const startPayment = async (productType: 'plan' | 'ai_quota_package', productId: number, key: string) => {
    if (!paymentReady) {
      addToast({ type: 'warning', message: '在线支付暂未开通，请联系管理员' })
      return
    }
    setBuyingKey(key)
    try {
      const orderResponse = await createBillingOrder(productType, productId, makeRequestKey())
      if (!orderResponse.success || !orderResponse.data) throw new Error(orderResponse.message || '订单创建失败')
      const paymentResponse = await createBillingPayment(orderResponse.data.order_no)
      if (!paymentResponse.success || !paymentResponse.data?.qr_code) throw new Error(paymentResponse.message || '支付二维码创建失败')
      setPaymentOrder({ ...orderResponse.data, ...paymentResponse.data })
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '发起支付失败') })
    } finally {
      setBuyingKey(null)
    }
  }

  if (loading) {
    return <div className="flex min-h-[60vh] items-center justify-center text-slate-500"><Loader2 className="mr-3 h-6 w-6 animate-spin" />正在加载套餐与额度</div>
  }

  return (
    <div className="space-y-8 pb-10">
      <section className="overflow-hidden rounded-3xl bg-gradient-to-br from-slate-950 via-blue-950 to-violet-950 p-6 text-white shadow-xl md:p-9">
        <div className="flex flex-col gap-8 lg:flex-row lg:items-center lg:justify-between">
          <div className="max-w-2xl">
            <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/10 px-3 py-1 text-sm text-blue-100"><Sparkles className="h-4 w-4" />AI 有效回复按实际发送成功计费</div>
            <h1 className="text-3xl font-bold tracking-tight md:text-4xl">套餐与 AI 额度</h1>
            <p className="mt-3 leading-7 text-slate-300">失败、过滤、重复消息、人工接管和发送失败均不扣额度。免费基础功能长期可用，新用户赠送100次 AI 有效回复。</p>
          </div>
          <div className="grid min-w-[280px] grid-cols-2 gap-3 lg:min-w-[440px] lg:grid-cols-3">
<UsageCard label="当前套餐" value={planNames[entitlements?.plan_code || ''] || entitlements?.plan_code || '免费版'} />
            <UsageCard label="本月有效回复" value={formatQuota(usage?.effective_replies ?? 0)} />
            <UsageCard label="剩余可用额度" value={formatQuota(usage ? usage.remaining_quota : 0)} />
          </div>
        </div>
      </section>
      <section className={`flex flex-col gap-3 rounded-2xl border p-4 md:flex-row md:items-center md:justify-between ${paymentReady ? 'border-emerald-200 bg-emerald-50 dark:border-emerald-900 dark:bg-emerald-950/30' : 'border-amber-200 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/30'}`}>
        <div className="flex items-start gap-3">
          {paymentReady ? <ShieldCheck className="mt-0.5 h-5 w-5 text-emerald-600" /> : <Clock3 className="mt-0.5 h-5 w-5 text-amber-600" />}
          <div>
            <div className="font-semibold text-slate-900 dark:text-white">{paymentReady ? '在线支付已开通' : '在线支付暂未开通'}</div>
            <div className="mt-1 text-sm text-slate-600 dark:text-slate-300">{paymentReady ? '可使用支付宝扫码购买，支付成功后权益自动到账。' : '套餐和价格已展示，购买按钮暂时关闭；配置正式支付宝商户后即可启用。'}</div>
          </div>
        </div>
        <button onClick={() => void loadPage()} className="inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2 text-sm font-medium text-slate-700 hover:bg-white/70 dark:text-slate-200 dark:hover:bg-white/10"><RefreshCw className="h-4 w-4" />刷新状态</button>
      </section>

      <section>
        <div className="mb-6 flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div><h2 className="text-2xl font-bold text-slate-900 dark:text-white">选择套餐</h2><p className="mt-1 text-slate-500 dark:text-slate-400">季付固定3个月，套餐内 AI 额度按自然月发放。</p></div>
          <div className="inline-flex rounded-2xl bg-slate-100 p-1 dark:bg-slate-800">
            {cycleOptions.map((option) => (
              <button key={option.value} onClick={() => setCycle(option.value)} className={`rounded-xl px-4 py-2 text-sm transition ${cycle === option.value ? 'bg-white font-semibold text-slate-900 shadow-sm dark:bg-slate-700 dark:text-white' : 'text-slate-500 dark:text-slate-400'}`}>
                <span className="block">{option.label}</span><span className="block text-[11px] font-normal opacity-70">{option.hint}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-4">
          {sortedPlans.map((plan) => <PlanCard key={plan.id} plan={plan} cycle={cycle} paymentReady={paymentReady} buyingKey={buyingKey} onBuy={startPayment} />)}
        </div>
      </section>

      <section>
        <div className="mb-5"><h2 className="flex items-center gap-2 text-2xl font-bold text-slate-900 dark:text-white"><PackagePlus className="h-6 w-6 text-violet-600" />AI 加量包</h2><p className="mt-1 text-slate-500 dark:text-slate-400">套餐额度不足时单独购买，赠送次数已包含在总额度中。</p></div>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {(catalog?.ai_quota_packages || []).map((item) => <QuotaPackageCard key={item.id} item={item} paymentReady={paymentReady} buyingKey={buyingKey} onBuy={startPayment} />)}
        </div>
      </section>

      {paymentOrder && <PaymentModal order={paymentOrder} onClose={() => setPaymentOrder(null)} />}
    </div>
  )
}

function UsageCard({ label, value }: { label: string; value: string }) {
  return <div className="rounded-2xl border border-white/10 bg-white/10 p-4 backdrop-blur"><div className="text-sm text-slate-300">{label}</div><div className="mt-2 text-3xl font-bold">{value}</div></div>
}

function PlanCard({ plan, cycle, paymentReady, buyingKey, onBuy }: {
  plan: BillingPlan
  cycle: BillingCycle
  paymentReady: boolean
  buyingKey: string | null
  onBuy: (type: 'plan', id: number, key: string) => Promise<void>
}) {
  const price = plan.prices.find((item) => item.billing_cycle === cycle)
  const style = planStyles[plan.code] || planStyles.standard
  const key = `plan-${price?.id || plan.id}`
  const cycleLabel = cycle === 'monthly' ? '月' : cycle === 'quarterly' ? '季' : '年'
  return (
    <article className={`relative flex min-h-[510px] flex-col rounded-3xl border bg-white p-6 shadow-sm transition hover:-translate-y-1 hover:shadow-lg dark:bg-slate-900 ${style.border}`}>
      {plan.code === 'merchant' && <div className="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full bg-violet-600 px-4 py-1 text-xs font-semibold text-white">推荐套餐</div>}
      <div className={`mb-5 inline-flex w-fit rounded-full px-3 py-1 text-sm font-semibold ${style.badge}`}>{plan.name}</div>
      <div className="flex items-end gap-1 text-slate-900 dark:text-white"><span className="text-4xl font-bold">¥{plan.is_free ? '0' : price?.amount || '--'}</span><span className="pb-1 text-sm text-slate-500">/{plan.is_free ? '长期' : cycleLabel}</span></div>
      <div className="mt-5 grid grid-cols-2 gap-3 rounded-2xl bg-slate-50 p-4 text-sm dark:bg-slate-800/70">
        <div><div className="text-slate-500">闲鱼账号</div><div className="mt-1 font-bold">{plan.account_limit} 个</div></div>
        <div><div className="text-slate-500">每月 AI 额度</div><div className="mt-1 font-bold">{plan.monthly_ai_quota ? `${plan.monthly_ai_quota.toLocaleString('zh-CN')} 次` : '新用户赠100次'}</div></div>
      </div>
      <ul className="mt-5 flex-1 space-y-3 text-sm text-slate-600 dark:text-slate-300">
        {plan.feature_flags.map((feature) => <li key={feature} className="flex gap-2"><Check className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />{featureLabels[feature] || feature}</li>)}
      </ul>
      <button disabled={plan.is_free || !paymentReady || !price || buyingKey !== null} onClick={() => price && void onBuy('plan', price.id, key)} className={`mt-6 inline-flex h-11 items-center justify-center gap-2 rounded-xl font-semibold transition disabled:cursor-not-allowed disabled:opacity-60 ${style.button}`}>
        {buyingKey === key ? <Loader2 className="h-4 w-4 animate-spin" /> : plan.is_free ? <Gift className="h-4 w-4" /> : <CreditCard className="h-4 w-4" />}
        {plan.is_free ? '当前长期免费' : paymentReady ? '立即购买' : '支付暂未开通'}
      </button>
    </article>
  )
}

function QuotaPackageCard({ item, paymentReady, buyingKey, onBuy }: {
  item: AIQuotaPackage
  paymentReady: boolean
  buyingKey: string | null
  onBuy: (type: 'ai_quota_package', id: number, key: string) => Promise<void>
}) {
  const key = `quota-${item.id}`
  return (
    <article className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-700 dark:bg-slate-900">
      <div className="flex items-start justify-between gap-3"><div><div className="font-bold text-slate-900 dark:text-white">{item.name}</div><div className="mt-1 text-sm text-slate-500">有效期 {item.validity_days} 天</div></div><Bot className="h-6 w-6 text-violet-500" /></div>
      <div className="mt-5 text-3xl font-bold text-slate-900 dark:text-white">{item.total_quota.toLocaleString('zh-CN')} <span className="text-sm font-normal text-slate-500">次</span></div>
      <div className="mt-2 text-sm text-emerald-600">基础 {item.base_quota.toLocaleString('zh-CN')} + 赠送 {item.bonus_quota.toLocaleString('zh-CN')}</div>
      <div className="mt-5 flex items-center justify-between">
        <div className="text-2xl font-bold text-violet-600">¥{item.amount}</div>
        <button disabled={!paymentReady || buyingKey !== null} onClick={() => void onBuy('ai_quota_package', item.id, key)} className="inline-flex items-center gap-2 rounded-xl bg-violet-600 px-4 py-2 text-sm font-semibold text-white hover:bg-violet-700 disabled:cursor-not-allowed disabled:opacity-50">{buyingKey === key && <Loader2 className="h-4 w-4 animate-spin" />}{paymentReady ? '购买' : '暂未开通'}</button>
      </div>
    </article>
  )
}

function PaymentModal({ order, onClose }: { order: BillingOrder; onClose: () => void }) {
  const paid = order.status === 'paid' && order.entitlement_status === 'granted'
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 p-4 backdrop-blur-sm">
      <div className="relative w-full max-w-md rounded-3xl bg-white p-6 shadow-2xl dark:bg-slate-900">
        <button onClick={onClose} className="absolute right-4 top-4 rounded-full p-2 text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800"><X className="h-5 w-5" /></button>
        {paid ? (
          <div className="py-8 text-center">
            <BadgeCheck className="mx-auto h-16 w-16 text-emerald-500" />
            <h3 className="mt-4 text-2xl font-bold text-slate-900 dark:text-white">支付成功</h3>
            <p className="mt-2 text-slate-500">{order.product_name} 已自动到账</p>
            <button onClick={onClose} className="mt-7 w-full rounded-xl bg-emerald-600 py-3 font-semibold text-white hover:bg-emerald-700">完成</button>
          </div>
        ) : (
          <>
            <div className="pr-10"><h3 className="text-xl font-bold text-slate-900 dark:text-white">支付宝扫码支付</h3><p className="mt-1 text-sm text-slate-500">{order.product_name} · ¥{order.amount}</p></div>
            <div className="mx-auto mt-6 w-fit rounded-2xl border border-slate-200 bg-white p-4">
              {order.qr_code ? <QRCodeSVG value={order.qr_code} size={220} level="M" /> : <div className="flex h-[220px] w-[220px] items-center justify-center"><Loader2 className="h-8 w-8 animate-spin text-violet-600" /></div>}
            </div>
            <div className="mt-5 flex items-center justify-center gap-2 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" />等待支付结果，页面将自动刷新</div>
            {order.payment_expires_at && <div className="mt-2 text-center text-xs text-slate-400">二维码有效期至 {new Date(order.payment_expires_at).toLocaleString('zh-CN')}</div>}
            <div className="mt-5 rounded-xl bg-slate-50 p-3 text-xs text-slate-500 dark:bg-slate-800">订单号：{order.order_no}</div>
          </>
        )}
      </div>
    </div>
  )
}
