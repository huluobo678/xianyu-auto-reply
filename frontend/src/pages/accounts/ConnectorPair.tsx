import { useEffect, useState } from 'react'
import { approveConnectorPairing, getConnectorPairing, type ConnectorPairingInfo } from '@/api/connectors'

export function ConnectorPair() {
  const state = new URLSearchParams(window.location.search).get('state') || ''
  const [pairing, setPairing] = useState<ConnectorPairingInfo | null>(null)
  const [message, setMessage] = useState('正在读取本机配对信息…')
  const [approving, setApproving] = useState(false)

  useEffect(() => {
    if (!state) {
      setMessage('配对链接无效')
      return
    }
    if (!localStorage.getItem('auth_token')) {
      const returnTo = `/connector/pair?state=${encodeURIComponent(state)}`
      window.location.replace(`/login?returnTo=${encodeURIComponent(returnTo)}`)
      return
    }
    getConnectorPairing(state)
      .then((result) => {
        setPairing(result)
        setMessage(result.status === 'pending' ? '' : `当前状态：${result.status}`)
      })
      .catch((error) => setMessage(error?.response?.data?.detail || '配对会话不存在、已过期或已被使用'))
  }, [state])

  const approve = async () => {
    setApproving(true)
    try {
      await approveConnectorPairing(state)
      setPairing((value) => value ? { ...value, status: 'approved' } : value)
      setMessage('本机已绑定，可返回连接器继续扫码登录闲鱼。')
    } catch (error: any) {
      setMessage(error?.response?.data?.detail || '绑定失败，请返回连接器重试')
    } finally {
      setApproving(false)
    }
  }

  return <div className="min-h-screen bg-slate-50 dark:bg-slate-900 flex items-center justify-center p-6">
    <div className="w-full max-w-xl rounded-2xl bg-white dark:bg-slate-800 shadow-xl p-8">
      <h1 className="text-2xl font-bold text-slate-900 dark:text-white">绑定此电脑</h1>
      <p className="mt-2 text-slate-600 dark:text-slate-300">仅授予本机连接器设备权限，不会向浏览器或云端上传闲鱼 Cookie、Token 和验证链接。</p>
      {pairing && <div className="mt-6 rounded-xl bg-slate-100 dark:bg-slate-700 p-4 space-y-2">
        <div><strong>电脑名称：</strong>{pairing.device_name}</div>
        <div><strong>平台：</strong>{pairing.platform === 'windows' ? 'Windows' : pairing.platform}</div>
        <div><strong>连接器版本：</strong>{pairing.app_version}</div>
        <div><strong>有效期至：</strong>{new Date(pairing.expires_at).toLocaleString()}</div>
      </div>}
      {message && <div className="mt-6 text-blue-600 dark:text-blue-300">{message}</div>}
      {pairing?.status === 'pending' && <button className="btn btn-primary mt-6 w-full" disabled={approving} onClick={() => void approve()}>
        {approving ? '正在绑定…' : '绑定此电脑'}
      </button>}
    </div>
  </div>
}
