/**
 * 兑换码商城页（链动小铺 iframe）
 *
 * 功能：
 * 1. 从系统设置读取 redemption_store_url 嵌入 iframe（不在前端硬编码商城地址）；
 * 2. iframe 加载态/失败/白屏/支付跳转失败时提示用户用新窗口打开；
 * 3. 手机端受限或第三方 Cookie 被阻止时提示新窗口打开；
 * 4. 始终提供“在新窗口打开商城”降级按钮；
 * 5. 提供“我已有兑换码，立即兑换”按钮跳转兑换页；
 * 6. 提供返回套餐页入口；
 * 7. 显示安全提示：请妥善保存兑换码，不要发送给其他人。
 *
 * iframe 最小权限 sandbox：allow-forms allow-scripts allow-same-origin allow-popups
 * allow-popups-to-escape-sandbox allow-top-navigation-by-user-activation。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, ExternalLink, Loader2, RefreshCw, ShieldAlert, Ticket } from 'lucide-react'
import { getRedemptionStoreUrl } from '@/api/redemption'
import { useUIStore } from '@/store/uiStore'
import { getApiErrorMessage } from '@/utils/request'

export function RedeemStore() {
  const navigate = useNavigate()
  const addToast = useUIStore((state) => state.addToast)
  const [storeUrl, setStoreUrl] = useState<string>('')
  const [loading, setLoading] = useState(true)
  const [iframeLoading, setIframeLoading] = useState(true)
  const [iframeFailed, setIframeFailed] = useState(false)
  const [isMobile, setIsMobile] = useState(false)
  const blankTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  /** 加载商城地址 */
  const loadStoreUrl = useCallback(async () => {
    setLoading(true)
    try {
      const url = await getRedemptionStoreUrl()
      setStoreUrl(url)
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '商城地址加载失败') })
    } finally {
      setLoading(false)
    }
  }, [addToast])

  useEffect(() => {
    void loadStoreUrl()
    // 手机端：iframe 内支付/跳转常受限，提示新窗口打开
    const checkMobile = () => setIsMobile(window.innerWidth < 768)
    checkMobile()
    window.addEventListener('resize', checkMobile)
    return () => window.removeEventListener('resize', checkMobile)
  }, [loadStoreUrl])

  /** iframe 加载完成 */
  const handleIframeLoad = useCallback(() => {
    setIframeLoading(false)
    if (blankTimerRef.current) {
      clearTimeout(blankTimerRef.current)
      blankTimerRef.current = null
    }
    // 跨域无法读取 iframe 内容，白屏检测仅作兜底提示
  }, [])

  /** iframe 加载失败 */
  const handleIframeError = useCallback(() => {
    setIframeLoading(false)
    setIframeFailed(true)
    if (blankTimerRef.current) {
      clearTimeout(blankTimerRef.current)
      blankTimerRef.current = null
    }
  }, [])

  /** iframe 开始加载时启动白屏兜底计时 */
  useEffect(() => {
    if (!storeUrl || !iframeLoading) return undefined
    // 8 秒后仍未触发 onLoad 视为白屏/加载失败
    blankTimerRef.current = setTimeout(() => {
      setIframeLoading(false)
      setIframeFailed(true)
    }, 8000)
    return () => {
      if (blankTimerRef.current) {
        clearTimeout(blankTimerRef.current)
        blankTimerRef.current = null
      }
    }
  }, [storeUrl, iframeLoading])

  const openInNewWindow = useCallback(() => {
    if (storeUrl) window.open(storeUrl, '_blank', 'noopener,noreferrer')
  }, [storeUrl])

  if (loading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center text-slate-500">
        <Loader2 className="mr-3 h-6 w-6 animate-spin" />正在加载商城
      </div>
    )
  }

  return (
    <div className="space-y-6 pb-10">
      <div className="page-header flex-between flex-wrap gap-4">
        <div>
          <h1 className="page-title">购买套餐兑换码</h1>
          <p className="page-description">付款完成后链动小铺会向你发放兑换码</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button onClick={() => navigate('/billing')} className="btn-ios-secondary">
            <ArrowLeft className="w-4 h-4" />返回套餐页
          </button>
          <button onClick={openInNewWindow} className="btn-ios-secondary">
            <ExternalLink className="w-4 h-4" />在新窗口打开商城
          </button>
        </div>
      </div>

      {/* 安全提示 */}
      <div className="vben-card">
        <div className="vben-card-body flex items-start gap-3">
          <ShieldAlert className="mt-0.5 h-5 w-5 text-amber-500" />
          <div className="text-sm text-slate-600 dark:text-slate-300">
            请妥善保存兑换码，不要发送给其他人。兑换码核销后即失效，平台仅负责生成、验证与核销。
          </div>
        </div>
      </div>

      {/* 手机端 / 第三方 Cookie 受限提示 */}
      {(isMobile || iframeFailed) && (
        <div className="vben-card border-amber-200 dark:border-amber-900">
          <div className="vben-card-body flex items-start gap-3">
            <ShieldAlert className="mt-0.5 h-5 w-5 text-amber-500" />
            <div className="text-sm text-slate-600 dark:text-slate-300">
              {isMobile ? '手机端内嵌商城支付可能受限，' : '商城加载失败、白屏或支付跳转失败，'}
              建议使用“在新窗口打开商城”完成付款，付款后回到本平台兑换。
              <button onClick={openInNewWindow} className="ml-2 font-semibold text-blue-600 hover:underline">
                立即在新窗口打开
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="vben-card flex flex-col" style={{ minHeight: '520px' }}>
        <div className="vben-card-header flex-shrink-0">
          <h2 className="vben-card-title">链动小铺商城</h2>
          <button onClick={() => { setIframeLoading(true); setIframeFailed(false); void loadStoreUrl(); }} className="btn-ios-secondary">
            <RefreshCw className="w-4 h-4" />刷新
          </button>
        </div>
        <div className="relative flex-1">
          {storeUrl ? (
            <iframe
              src={storeUrl}
              title="兑换码商城"
              loading="lazy"
              referrerPolicy="strict-origin-when-cross-origin"
              sandbox="allow-forms allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox allow-top-navigation-by-user-activation"
              onLoad={handleIframeLoad}
              onError={handleIframeError}
              className="h-full min-h-[480px] w-full rounded-b-2xl border-0"
            />
          ) : (
            <div className="flex h-[480px] items-center justify-center text-slate-500">商城地址未配置</div>
          )}
          {iframeLoading && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-white/80 dark:bg-slate-900/80">
              <Loader2 className="h-8 w-8 animate-spin text-blue-500" />
              <div className="text-sm text-slate-500">商城加载中，若长时间无响应请在新窗口打开</div>
              <button onClick={openInNewWindow} className="btn-ios-secondary">
                <ExternalLink className="w-4 h-4" />在新窗口打开商城
              </button>
            </div>
          )}
        </div>
      </div>

      {/* 我已有兑换码，立即兑换 */}
      <div className="vben-card">
        <div className="vben-card-body flex flex-col items-center gap-4 py-6 text-center">
          <div className="flex items-center gap-2 text-slate-700 dark:text-slate-200">
            <Ticket className="h-5 w-5 text-violet-600" />
            <span className="font-medium">已收到兑换码？</span>
          </div>
          <p className="text-sm text-slate-500">回到平台输入兑换码，验证并核销后立即发放套餐或 AI 加量额度。</p>
          <button onClick={() => navigate('/billing/redeem')} className="btn-ios-primary">
            <Ticket className="w-4 h-4" />我已有兑换码，立即兑换
          </button>
        </div>
      </div>
    </div>
  )
}
