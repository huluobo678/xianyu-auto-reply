/**
 * 余额充值弹窗组件
 *
 * 支付宝入口默认关闭（alipay.enabled=false）：余额充值不再通过支付宝发起，
 * 第一版改为兑换码购买。本弹窗保留但停用支付宝付款路径，仅提示用户前往
 * 兑换码商城，不调用支付宝 SDK、不创建真实充值订单。
 */
import { useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertCircle, X } from 'lucide-react'

interface RechargeModalProps {
  visible: boolean
  onClose: () => void
  onSuccess?: () => void
}

export function RechargeModal({ visible, onClose }: RechargeModalProps) {
  const navigate = useNavigate()

  const handleClose = useCallback(() => {
    onClose()
  }, [onClose])

  const goToStore = useCallback(() => {
    onClose()
    navigate('/billing/store')
  }, [onClose, navigate])

  if (!visible) return null

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white dark:bg-slate-800 rounded-xl p-6 w-full max-w-md mx-4 shadow-xl">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold text-slate-900 dark:text-slate-100">余额充值</h3>
          <button
            onClick={handleClose}
            className="p-1.5 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="space-y-4 text-center py-4">
          <AlertCircle className="w-16 h-16 text-amber-500 mx-auto" />
          <p className="text-lg font-semibold text-amber-600 dark:text-amber-400">余额充值暂未开通</p>
          <p className="text-sm text-slate-500">第一版收费改为兑换码购买，请前往兑换码商城购买兑换码后回平台兑换。</p>
          <button onClick={goToStore} className="btn-ios-primary w-full">前往兑换码商城</button>
        </div>
      </div>
    </div>
  )
}
