/**
 * 兑换码批次管理（管理员）
 *
 * 功能：
 * 1. 创建兑换码批次（产品类型 plan/ai_quota_package；月卡/季卡/常用包/商家包/无限包；不允许年卡）；
 * 2. 设置数量与过期时间；
 * 3. 一次性导出完整兑换码（生成时返回一次；批次未导出可下载文件，仅成功一次）；
 * 4. 显示批次状态、生成数量与兑换统计；
 * 5. 禁用批次 / 禁用单码；
 * 6. 显示兑换审计（不显示完整兑换码，仅尾4位/ID/状态）。
 */
import { useCallback, useEffect, useState } from 'react'
import { Download, Loader2, Plus, RefreshCw, Ticket, Ban } from 'lucide-react'
import {
  audit,
  createBatch,
  disableBatch,
  disableCode,
  exportBatch,
  listBatches,
  type CreateBatchResult,
  type RedemptionAuditRecord,
  type RedemptionBatch,
} from '@/api/redemption'
import { useUIStore } from '@/store/uiStore'
import { getApiErrorMessage } from '@/utils/request'
import { ConfirmModal } from '@/components/common/ConfirmModal'

type ProductType = 'plan' | 'ai_quota_package'

const PLAN_OPTIONS = [
  { code: 'standard', name: '标准版' },
  { code: 'merchant', name: '商家版' },
  { code: 'enterprise', name: '企业版' },
]
const CYCLE_OPTIONS = [
  { value: 'monthly', label: '月卡' },
  { value: 'quarterly', label: '季卡' },
]
const PACKAGE_OPTIONS = [
  { code: 'ai_regular', name: '常用包' },
  { code: 'ai_merchant', name: '商家包' },
  { code: 'ai_unlimited', name: '无限包' },
]

export function RedemptionBatches() {
  const addToast = useUIStore((state) => state.addToast)
  const [loading, setLoading] = useState(true)
  const [batches, setBatches] = useState<RedemptionBatch[]>([])
  const [auditRecords, setAuditRecords] = useState<RedemptionAuditRecord[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [created, setCreated] = useState<CreateBatchResult | null>(null)

  // 创建表单
  const [productType, setProductType] = useState<ProductType>('plan')
  const [planCode, setPlanCode] = useState('standard')
  const [cycle, setCycle] = useState('monthly')
  const [packageCode, setPackageCode] = useState('ai_regular')
  const [count, setCount] = useState(10)
  const [expiresAt, setExpiresAt] = useState('')

  // 禁用确认
  const [disableTarget, setDisableTarget] = useState<{ kind: 'batch' | 'code'; id: number } | null>(null)

  const loadAll = useCallback(async () => {
    setLoading(true)
    try {
      const [batchRes, auditRes] = await Promise.all([listBatches(100), audit({ limit: 100 })])
      setBatches(batchRes.data || [])
      setAuditRecords(auditRes.data || [])
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '加载兑换码批次失败') })
    } finally {
      setLoading(false)
    }
  }, [addToast])

  useEffect(() => { void loadAll() }, [loadAll])

  const handleCreate = useCallback(async () => {
    if (submitting) return
    setSubmitting(true)
    try {
      const payload: {
        product_type: ProductType
        product_code: string
        billing_cycle: string | null
        count: number
      } = productType === 'plan'
        ? { product_type: 'plan', product_code: planCode, billing_cycle: cycle, count }
        : { product_type: 'ai_quota_package', product_code: packageCode, billing_cycle: null, count }
      const expires = expiresAt ? new Date(expiresAt).toISOString() : null
      const res = await createBatch({ ...payload, count, expires_at: expires })
      if (res.success && res.data) {
        setCreated(res.data)
        addToast({ type: 'success', message: '兑换码批次已生成，请立即保存完整兑换码（仅显示一次）' })
        void loadAll()
      } else {
        addToast({ type: 'error', message: res.message || '创建批次失败' })
      }
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '创建批次失败') })
    } finally {
      setSubmitting(false)
    }
  }, [submitting, productType, planCode, cycle, packageCode, count, expiresAt, addToast, loadAll])

  const handleExport = useCallback(async (batchId: number) => {
    try {
      const blob = await exportBatch(batchId)
      // 导出失败（如已导出过）时后端返回 JSON 错误体，responseType=blob 也会被包成 Blob
      if (!(blob instanceof Blob) || blob.type.includes('json')) {
        addToast({ type: 'error', message: '导出失败或已导出过（仅可导出一次）' })
        void loadAll()
        return
      }
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `redemption_batch_${batchId}.txt`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      window.URL.revokeObjectURL(url)
      addToast({ type: 'success', message: '导出成功，请妥善保存完整兑换码' })
      void loadAll()
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '导出失败或已导出过（仅可导出一次）') })
      void loadAll()
    }
  }, [addToast, loadAll])

  const handleDisable = useCallback(async () => {
    if (!disableTarget) return
    setSubmitting(true)
    try {
      const res = disableTarget.kind === 'batch'
        ? await disableBatch(disableTarget.id)
        : await disableCode(disableTarget.id)
      if (res.success) {
        addToast({ type: 'success', message: '已禁用' })
        void loadAll()
      } else {
        addToast({ type: 'error', message: res.message || '禁用失败' })
      }
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '禁用失败') })
    } finally {
      setSubmitting(false)
      setDisableTarget(null)
    }
  }, [disableTarget, addToast, loadAll])

  if (loading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center text-slate-500">
        <Loader2 className="mr-3 h-6 w-6 animate-spin" />正在加载兑换码批次
      </div>
    )
  }

  return (
    <div className="space-y-6 pb-10">
      <div className="page-header flex-between flex-wrap gap-4">
        <div>
          <h1 className="page-title">兑换码批次管理</h1>
          <p className="page-description">生成、导出、禁用兑换码批次与单码，查看兑换审计</p>
        </div>
        <button onClick={() => void loadAll()} className="btn-ios-secondary">
          <RefreshCw className="w-4 h-4" />刷新
        </button>
      </div>

      {/* 创建批次 */}
      <div className="vben-card">
        <div className="vben-card-header"><h2 className="vben-card-title"><Plus className="w-4 h-4" />创建兑换码批次</h2></div>
        <div className="vben-card-body space-y-4">
          <div className="flex flex-wrap items-end gap-4">
            <div className="input-group">
              <label className="input-label">产品类型</label>
              <select value={productType} onChange={(e) => setProductType(e.target.value as ProductType)} className="input-ios">
                <option value="plan">套餐</option>
                <option value="ai_quota_package">AI 加量包</option>
              </select>
            </div>
            {productType === 'plan' ? (
              <>
                <div className="input-group">
                  <label className="input-label">套餐</label>
                  <select value={planCode} onChange={(e) => setPlanCode(e.target.value)} className="input-ios">
                    {PLAN_OPTIONS.map((p) => <option key={p.code} value={p.code}>{p.name}</option>)}
                  </select>
                </div>
                <div className="input-group">
                  <label className="input-label">周期（不允许年卡）</label>
                  <select value={cycle} onChange={(e) => setCycle(e.target.value)} className="input-ios">
                    {CYCLE_OPTIONS.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                  </select>
                </div>
              </>
            ) : (
              <div className="input-group">
                <label className="input-label">加量包</label>
                <select value={packageCode} onChange={(e) => setPackageCode(e.target.value)} className="input-ios">
                  {PACKAGE_OPTIONS.map((p) => <option key={p.code} value={p.code}>{p.name}</option>)}
                </select>
              </div>
            )}
            <div className="input-group">
              <label className="input-label">数量</label>
              <input type="number" min={1} max={10000} value={count} onChange={(e) => setCount(Math.max(1, Number(e.target.value)))} className="input-ios w-28" />
            </div>
            <div className="input-group">
              <label className="input-label">过期时间（可选）</label>
              <input type="datetime-local" value={expiresAt} onChange={(e) => setExpiresAt(e.target.value)} className="input-ios" />
            </div>
            <button onClick={handleCreate} disabled={submitting} className="btn-ios-primary">
              {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}生成批次
            </button>
          </div>
          <p className="text-xs text-slate-400">完整兑换码仅在生成时返回一次，请立即保存；导出文件也可下载一次。</p>
        </div>
      </div>

      {/* 生成结果（一次性明文） */}
      {created && (
        <div className="vben-card border-emerald-200 dark:border-emerald-900">
          <div className="vben-card-header flex-between">
            <h2 className="vben-card-title">批次 {created.batch_no} 的完整兑换码（仅显示一次）</h2>
            <button onClick={() => setCreated(null)} className="btn-ios-secondary">已保存，关闭</button>
          </div>
          <div className="vben-card-body">
            <textarea readOnly rows={Math.min(12, created.codes.length)} value={created.codes.join('\n')} className="input-ios w-full font-mono text-xs" />
            <button
              onClick={() => void navigator.clipboard?.writeText(created.codes.join('\n'))}
              className="btn-ios-secondary mt-2"
            >
              复制全部兑换码
            </button>
          </div>
        </div>
      )}

      {/* 批次列表 */}
      <div className="vben-card flex flex-col" style={{ minHeight: '320px' }}>
        <div className="vben-card-header flex-shrink-0">
          <h2 className="vben-card-title"><Ticket className="w-4 h-4" />批次列表</h2>
          <span className="badge-primary">{batches.length} 个批次</span>
        </div>
        <div className="flex-1 overflow-auto">
          <table className="table-ios min-w-[960px]">
            <thead className="sticky top-0 bg-white dark:bg-slate-800 z-10">
              <tr>
                <th>批次号</th><th>产品</th><th>周期/有效期</th><th>数量</th><th>已兑换</th><th>状态</th><th>已导出</th><th>操作</th>
              </tr>
            </thead>
            <tbody>
              {batches.length === 0 ? (
                <tr><td colSpan={8}><div className="empty-state py-8"><Ticket className="empty-state-icon" /><p className="text-gray-500">暂无批次</p></div></td></tr>
              ) : batches.map((b) => (
                <tr key={b.batch_id}>
                  <td className="font-mono text-xs">{b.batch_no}</td>
                  <td>{b.product_name}</td>
                  <td>{b.product_type === 'plan' ? (b.billing_cycle === 'monthly' ? '月卡' : '季卡') : `${b.validity_days ?? 0} 天`}</td>
                  <td>{b.generated_count}</td>
                  <td>{b.used_count}</td>
                  <td>
                    {b.disabled
                      ? <span className="badge-warning">已禁用</span>
                      : <span className="badge-success">可用</span>}
                  </td>
                  <td>{b.exported_at ? '已导出' : '未导出'}</td>
                  <td>
                    <div className="flex gap-2">
                      {!b.exported_at && !b.disabled && (
                        <button onClick={() => void handleExport(b.batch_id)} className="table-action-btn text-blue-600"><Download className="w-4 h-4" />导出</button>
                      )}
                      {!b.disabled && (
                        <button onClick={() => setDisableTarget({ kind: 'batch', id: b.batch_id })} className="table-action-btn text-red-600"><Ban className="w-4 h-4" />禁用批次</button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* 兑换审计（不含完整兑换码） */}
      <div className="vben-card flex flex-col" style={{ minHeight: '240px' }}>
        <div className="vben-card-header flex-shrink-0">
          <h2 className="vben-card-title">兑换审计</h2>
          <span className="badge-info">{auditRecords.length} 条</span>
        </div>
        <div className="flex-1 overflow-auto">
          <table className="table-ios min-w-[760px]">
            <thead className="sticky top-0 bg-white dark:bg-slate-800 z-10">
              <tr><th>记录ID</th><th>批次ID</th><th>兑换码ID</th><th>用户ID</th><th>产品</th><th>时间</th><th>操作</th></tr>
            </thead>
            <tbody>
              {auditRecords.length === 0 ? (
                <tr><td colSpan={7}><div className="empty-state py-8"><p className="text-gray-500">暂无兑换记录</p></div></td></tr>
              ) : auditRecords.map((r) => (
                <tr key={r.record_id}>
                  <td>{r.record_id}</td>
                  <td>{r.batch_id}</td>
                  <td>{r.code_id}</td>
                  <td>{r.user_id}</td>
                  <td>{r.product_type === 'plan' ? '套餐' : 'AI 加量包'} · {r.product_code}</td>
                  <td>{r.created_at || '-'}</td>
                  <td>
                    <button onClick={() => setDisableTarget({ kind: 'code', id: r.code_id })} className="table-action-btn text-red-600">
                      <Ban className="w-4 h-4" />禁用单码
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <ConfirmModal
        isOpen={disableTarget !== null}
        title={disableTarget?.kind === 'batch' ? '禁用批次' : '禁用单码'}
        message={disableTarget?.kind === 'batch' ? '确认禁用该批次？批次内未使用的兑换码将全部失效。' : '确认禁用该兑换码？'}
        type="danger"
        loading={submitting}
        onConfirm={() => void handleDisable()}
        onCancel={() => setDisableTarget(null)}
      />
    </div>
  )
}
