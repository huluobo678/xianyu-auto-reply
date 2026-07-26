import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Bot, Check, Gift, Loader2, PackagePlus, Sparkles, Ticket } from 'lucide-react'

import {
  getBillingCatalog,
  getMyAIUsage,
  getMyEntitlements,
  type AIQuotaPackage,
  type AIUsageSummary,
  type BillingCatalog,
  type BillingCycle,
  type BillingPlan,
  type UserEntitlements,
} from '@/api/billing'
import { useUIStore } from '@/store/uiStore'
import { getApiErrorMessage } from '@/utils/request'

// 第一版只支持月卡/季卡，不开放年付
const cycleOptions: Array<{ value: BillingCycle; label: string; hint: string }> = [
  { value: 'monthly', label: '月付', hint: '灵活订阅' },
  { value: 'quarterly', label: '季付', hint: '连续3个月' },
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

const planStyles: Record<string, { border: string; badge: string }> = {
  free: { border: 'border-slate-200 dark:border-slate-700', badge: 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200' },
  standard: { border: 'border-blue-200 dark:border-blue-800', badge: 'bg-blue-100 text-blue-700 dark:bg-blue-900/50 dark:text-blue-300' },
  merchant: { border: 'border-violet-400 ring-2 ring-violet-100 dark:ring-violet-900/40', badge: 'bg-violet-100 text-violet-700 dark:bg-violet-900/50 dark:text-violet-300' },
  enterprise: { border: 'border-amber-300 dark:border-amber-700', badge: 'bg-amber-100 text-amber-700 dark:bg-amber-900/50 dark:text-amber-300' },
}

const planNames: Record<string, string> = { free: '免费版', standard: '标准版', merchant: '商家版', enterprise: '企业版' }
const formatQuota = (value: number | null) => value === null ? '不限' : value.toLocaleString('zh-CN')

export function Billing() {
  const navigate = useNavigate()
  const addToast = useUIStore((state) => state.addToast)
  const [catalog, setCatalog] = useState<BillingCatalog | null>(null)
  const [usage, setUsage] = useState<AIUsageSummary | null>(null)
  const [entitlements, setEntitlements] = useState<UserEntitlements | null>(null)
  const [cycle, setCycle] = useState<BillingCycle>('monthly')
  const [loading, setLoading] = useState(true)

  const loadPage = useCallback(async () => {
    setLoading(true)
    try {
      const [catalogResponse, usageResponse, entitlementResponse] = await Promise.all([
        getBillingCatalog(), getMyAIUsage(), getMyEntitlements(),
      ])
      if (!catalogResponse.success || !catalogResponse.data) {
        throw new Error(catalogResponse.message || '套餐目录加载失败')
      }
      setCatalog(catalogResponse.data)
      setUsage(usageResponse.data || null)
      setEntitlements(entitlementResponse.data || null)
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '套餐与额度加载失败') })
    } finally {
      setLoading(false)
    }
  }, [addToast])

  useEffect(() => { void loadPage() }, [loadPage])

  const sortedPlans = useMemo(() => [...(catalog?.plans || [])].sort((left, right) => left.id - right.id), [catalog])

  // 当前套餐的无限权益状态：套餐本身无限，或权益来源为套餐且额度标记无限
  const unlimitedActive = useMemo(() => {
    const plan = catalog?.plans.find((p) => p.code === entitlements?.plan_code)
    return Boolean(plan?.ai_unlimited)
  }, [catalog, entitlements])

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
            <p className="mt-3 leading-7 text-slate-300">失败、过滤、重复消息、人工接管和发送失败均不扣额度。购买请前往兑换码商城，付款后凭兑换码回平台兑换。</p>
            <div className="mt-5 flex flex-wrap gap-3">
              <button onClick={() => navigate('/billing/store')} className="inline-flex items-center gap-2 rounded-xl bg-white px-5 py-2.5 text-sm font-semibold text-slate-900 hover:bg-slate-100">
                <Ticket className="h-4 w-4" />购买兑换码
              </button>
              <button onClick={() => navigate('/billing/redeem')} className="inline-flex items-center gap-2 rounded-xl border border-white/30 px-5 py-2.5 text-sm font-semibold text-white hover:bg-white/10">
                <Gift className="h-4 w-4" />前往兑换
              </button>
            </div>
          </div>
          <div className="grid min-w-[280px] grid-cols-2 gap-3 lg:min-w-[440px] lg:grid-cols-3">
            <UsageCard label="当前套餐" value={planNames[entitlements?.plan_code || ''] || entitlements?.plan_code || '免费版'} />
            <UsageCard label="账号上限" value={entitlements ? String(entitlements.account_limit) : '-'} />
            <UsageCard label="到期时间" value={entitlements?.expires_at ? new Date(entitlements.expires_at).toLocaleDateString('zh-CN') : '长期'} />
            <UsageCard label="本月有效回复" value={formatQuota(usage?.effective_replies ?? 0)} />
            <UsageCard label="剩余可用额度" value={unlimitedActive ? '不限' : formatQuota(usage ? usage.remaining_quota : 0)} />
            <UsageCard label="无限权益" value={unlimitedActive ? '已开启' : '未开启'} />
          </div>
        </div>
      </section>

      <section>
        <div className="mb-6 flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div><h2 className="text-2xl font-bold text-slate-900 dark:text-white">选择套餐</h2><p className="mt-1 text-slate-500 dark:text-slate-400">季付固定3个月，套餐内 AI 额度按自然月发放。仅支持月卡/季卡，不开放年付。</p></div>
          <div className="inline-flex rounded-2xl bg-slate-100 p-1 dark:bg-slate-800">
            {cycleOptions.map((option) => (
              <button key={option.value} onClick={() => setCycle(option.value)} className={`rounded-xl px-4 py-2 text-sm transition ${cycle === option.value ? 'bg-white font-semibold text-slate-900 shadow-sm dark:bg-slate-700 dark:text-white' : 'text-slate-500 dark:text-slate-400'}`}>
                <span className="block">{option.label}</span><span className="block text-[11px] font-normal opacity-70">{option.hint}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-4">
          {sortedPlans.map((plan) => <PlanCard key={plan.id} plan={plan} cycle={cycle} onBuy={() => navigate('/billing/store')} />)}
        </div>
      </section>

      <section>
        <div className="mb-5"><h2 className="flex items-center gap-2 text-2xl font-bold text-slate-900 dark:text-white"><PackagePlus className="h-6 w-6 text-violet-600" />AI 加量包</h2><p className="mt-1 text-slate-500 dark:text-slate-400">套餐额度不足时单独购买，赠送次数已包含在总额度中。</p></div>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {(catalog?.ai_quota_packages || []).map((item) => <QuotaPackageCard key={item.id} item={item} onBuy={() => navigate('/billing/store')} />)}
        </div>
      </section>

      <section className="flex flex-col gap-3 rounded-2xl border border-blue-200 bg-blue-50 p-4 dark:border-blue-900 dark:bg-blue-950/30 md:flex-row md:items-center md:justify-between">
        <div className="flex items-start gap-3">
          <Ticket className="mt-0.5 h-5 w-5 text-blue-600" />
          <div>
            <div className="font-semibold text-slate-900 dark:text-white">已购买兑换码？</div>
            <div className="mt-1 text-sm text-slate-600 dark:text-slate-300">前往兑换页输入兑换码，核销后立即发放套餐或 AI 加量额度。</div>
          </div>
        </div>
        <button onClick={() => navigate('/billing/redeem')} className="inline-flex items-center justify-center gap-2 rounded-xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700"><Ticket className="h-4 w-4" />前往兑换</button>
      </section>
    </div>
  )
}

function UsageCard({ label, value }: { label: string; value: string }) {
  return <div className="rounded-2xl border border-white/10 bg-white/10 p-4 backdrop-blur"><div className="text-sm text-slate-300">{label}</div><div className="mt-2 text-2xl font-bold">{value}</div></div>
}

function PlanCard({ plan, cycle, onBuy }: {
  plan: BillingPlan
  cycle: BillingCycle
  onBuy: () => void
}) {
  const price = plan.prices.find((item) => item.billing_cycle === cycle)
  const style = planStyles[plan.code] || planStyles.standard
  const cycleLabel = cycle === 'monthly' ? '月' : '季'
  return (
    <article className={`relative flex min-h-[510px] flex-col rounded-3xl border bg-white p-6 shadow-sm transition hover:-translate-y-1 hover:shadow-lg dark:bg-slate-900 ${style.border}`}>
      {plan.code === 'merchant' && <div className="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full bg-violet-600 px-4 py-1 text-xs font-semibold text-white">推荐套餐</div>}
      <div className={`mb-5 inline-flex w-fit rounded-full px-3 py-1 text-sm font-semibold ${style.badge}`}>{plan.name}{plan.ai_unlimited && <span className="ml-2 text-amber-600">无限次</span>}</div>
      <div className="flex items-end gap-1 text-slate-900 dark:text-white"><span className="text-4xl font-bold">¥{plan.is_free ? '0' : price?.amount || '--'}</span><span className="pb-1 text-sm text-slate-500">/{plan.is_free ? '长期' : cycleLabel}</span></div>
      <div className="mt-5 grid grid-cols-2 gap-3 rounded-2xl bg-slate-50 p-4 text-sm dark:bg-slate-800/70">
        <div><div className="text-slate-500">闲鱼账号</div><div className="mt-1 font-bold">{plan.account_limit} 个</div></div>
        <div><div className="text-slate-500">每月 AI 额度</div><div className="mt-1 font-bold">{plan.ai_unlimited ? '不限' : plan.monthly_ai_quota ? `${plan.monthly_ai_quota.toLocaleString('zh-CN')} 次` : '新用户赠100次'}</div></div>
      </div>
      <ul className="mt-5 flex-1 space-y-3 text-sm text-slate-600 dark:text-slate-300">
        {plan.feature_flags.map((feature) => <li key={feature} className="flex gap-2"><Check className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />{featureLabels[feature] || feature}</li>)}
      </ul>
      <button disabled={plan.is_free} onClick={onBuy} className="mt-6 inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-blue-600 font-semibold text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60">
        {plan.is_free ? <Gift className="h-4 w-4" /> : <Ticket className="h-4 w-4" />}
        {plan.is_free ? '当前长期免费' : '购买兑换码'}
      </button>
    </article>
  )
}

function QuotaPackageCard({ item, onBuy }: {
  item: AIQuotaPackage
  onBuy: () => void
}) {
  return (
    <article className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-700 dark:bg-slate-900">
      <div className="flex items-start justify-between gap-3"><div><div className="font-bold text-slate-900 dark:text-white">{item.name}{item.ai_unlimited && <span className="ml-2 text-amber-600">无限次</span>}</div><div className="mt-1 text-sm text-slate-500">有效期 {item.validity_days} 天</div></div><Bot className="h-6 w-6 text-violet-500" /></div>
      <div className="mt-5 text-3xl font-bold text-slate-900 dark:text-white">{item.ai_unlimited ? '不限' : <>{item.total_quota.toLocaleString('zh-CN')} <span className="text-sm font-normal text-slate-500">次</span></>}</div>
      {!item.ai_unlimited && <div className="mt-2 text-sm text-emerald-600">基础 {item.base_quota.toLocaleString('zh-CN')} + 赠送 {item.bonus_quota.toLocaleString('zh-CN')}</div>}
      <div className="mt-5 flex items-center justify-between">
        <div className="text-2xl font-bold text-violet-600">¥{item.amount}</div>
        <button onClick={onBuy} className="inline-flex items-center gap-2 rounded-xl bg-violet-600 px-4 py-2 text-sm font-semibold text-white hover:bg-violet-700"><Ticket className="h-4 w-4" />购买兑换码</button>
      </div>
    </article>
  )
}
