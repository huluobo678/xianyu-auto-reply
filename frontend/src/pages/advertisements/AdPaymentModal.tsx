/**
 * 广告付款弹窗组件
 *
 * 支付宝入口默认关闭（alipay.enabled=false）：广告付款不再通过支付宝发起，
 * 广告状态 unpaid -> approved 必须由管理员人工审核。本弹窗保留但停用支付宝
 * 付款路径，仅提示用户广告已提交、等待管理员审核，不调用支付宝 SDK、不创建
 * 真实付款订单、不轮询支付状态。
 */
import { useCallback } from 'react'
import { AlertCircle, CheckCircle, X } from 'lucide-react'
import type { Advertisement } from '@/api/advertisements'

interface AdPaymentModalProps {
  visible: boolean
  ad: Advertisement | null
  onClose: () => void
  onSuccess: () => void
}

export function AdPaymentModal({ visible, ad, onClose, onSuccess }: AdPaymentModalProps) {
  const handleClose = useCallback(() => {
    onClose()
  }, [onClose])

  const handleSubmitted = useCallback(() => {
    onSuccess()
    onClose()
  }, [onSuccess, onClose])

  if (!visible || !ad) return null

  // 广告已通过管理员审核：展示已通过状态
  const isApproved = ad.status === 'approved'

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white dark:bg-slate-800 rounded-xl p-6 w-full max-w-md mx-4 shadow-xl">
        {/* 标题栏 */}
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold text-slate-900 dark:text-slate-100">广告付款</h3>
          <button
            onClick={handleClose}
            className="p-1.5 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="space-y-4">
          <div className="p-4 bg-slate-50 dark:bg-slate-700/50 rounded-lg space-y-2">
            <div className="flex justify-between text-sm">
              <span className="text-slate-500">广告标题</span>
              <span className="font-medium text-slate-900 dark:text-slate-100 max-w-[200px] truncate">{ad.title}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-slate-500">广告类型</span>
              <span className="font-medium text-slate-900 dark:text-slate-100">{ad.ad_type === 'carousel' ? '轮播图' : '文字广告'}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-slate-500">购买月数</span>
              <span className="font-medium text-slate-900 dark:text-slate-100">{ad.months} 个月</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-slate-500">到期日期</span>
              <span className="font-medium text-slate-900 dark:text-slate-100">{ad.expire_date || '-'}</span>
            </div>
            <div className="flex justify-between text-sm pt-2 border-t border-slate-200 dark:border-slate-600">
              <span className="text-slate-500 font-medium">应付金额</span>
              <span className="text-xl font-bold text-amber-600 dark:text-amber-400">¥{ad.total_amount || '0'}</span>
            </div>
          </div>

          {isApproved ? (
            <div className="space-y-4 text-center py-4">
              <CheckCircle className="w-16 h-16 text-green-500 mx-auto" />
              <p className="text-lg font-semibold text-green-600 dark:text-green-400">广告已审核通过</p>
              <p className="text-sm text-slate-500">管理员已审核通过该广告，即将上线展示。</p>
              <button onClick={handleSubmitted} className="btn-ios-primary w-full">完成</button>
            </div>
          ) : (
            <div className="space-y-4 text-center py-4">
              <AlertCircle className="w-16 h-16 text-amber-500 mx-auto" />
              <p className="text-lg font-semibold text-amber-600 dark:text-amber-400">在线付款暂未开通</p>
              <p className="text-sm text-slate-500">第一版广告付款改为管理员人工审核。提交后请等待管理员审核通过，无需在线付款。</p>
              <button onClick={handleSubmitted} className="btn-ios-secondary w-full">我知道了</button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
