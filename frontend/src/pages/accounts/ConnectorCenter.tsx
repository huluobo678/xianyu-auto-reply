import { useEffect, useState } from 'react'
import { getConnectorStatus, getLatestConnectorRelease, revokeConnectorDevice, type ConnectorDeviceStatus, type ConnectorReleaseInfo } from '@/api/connectors'

function formatFileSize(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '待发布方补充'
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

function formatPublishedAt(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

export function Accounts() {
  const [devices, setDevices] = useState<ConnectorDeviceStatus[]>([])
  const [release, setRelease] = useState<ConnectorReleaseInfo | null>(null)
  const [message, setMessage] = useState('正在读取本地连接器状态…')
  const [showInstallHelp, setShowInstallHelp] = useState(false)

  const refresh = async () => {
    try {
      const [status, latestRelease] = await Promise.all([getConnectorStatus(), getLatestConnectorRelease()])
      setDevices(status.devices || [])
      setRelease(latestRelease)
      setMessage(status.devices?.length ? '' : '尚未绑定设备，请先下载安装本地连接器。安装后会自动打开浏览器完成绑定。')
    } catch (error) {
      setMessage(`状态读取失败：${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  useEffect(() => {
    void refresh()
    const timer = window.setInterval(() => void refresh(), 30000)
    return () => window.clearInterval(timer)
  }, [])

  const revoke = async (deviceId: number) => {
    if (!window.confirm('吊销后该设备会立即停止自动回复，确认继续？')) return
    await revokeConnectorDevice(deviceId)
    await refresh()
  }

  const bindLocalConnector = () => {
    setMessage('正在打开本机连接器并自动绑定…')
    setShowInstallHelp(false)
    localStorage.setItem('connector_bind_requested_at', String(Date.now()))
    window.location.href = 'xianyuconnector://bind'
    window.setTimeout(() => setShowInstallHelp(true), 8000)
  }

  return <div className="page-container">
    <div className="page-header">
      <div><h1 className="page-title">本地连接器</h1><p className="page-description">Cookie、Token 和验证链接只保存在你的电脑，云端不提供闲鱼直连。</p></div>
      <button className="btn btn-primary" onClick={bindLocalConnector}>绑定本机</button>
    </div>
    <div className="card" style={{ padding: 20, marginBottom: 16 }}>
      <strong>一键绑定</strong>
      <p>点击“绑定本机”即可自动打开连接器并完成授权，无需填写服务器地址、绑定码或设备令牌。</p>
      {showInstallHelp && release?.download_url && <div style={{ marginTop: 12 }}>
        <p>如果连接器没有打开，说明本机尚未安装。</p>
        <a className="btn btn-secondary" href={release.download_url}>下载安装连接器</a>
      </div>}
      {release && <details style={{ marginTop: 16 }}>
        <summary style={{ cursor: 'pointer' }}>安装包与安全校验信息</summary>
        <div style={{ lineHeight: 1.8, marginTop: 8 }}>
        <div>最新版本：{release.version}</div>
        <div>发布日期：{formatPublishedAt(release.published_at)}</div>
        <div>文件大小：{formatFileSize(release.file_size_bytes)}</div>
        <div>数字签名：{release.signed ? '已签名' : '未签名（signed=false）'}</div>
        <div>SHA-256：<code style={{ userSelect: 'all', wordBreak: 'break-all' }}>{release.sha256}</code></div>
        {!release.signed && <p style={{ color: 'var(--warning-color, #b45309)' }}>当前安装包未购买 Windows 代码签名证书。请仅从本页 HTTPS 链接下载；若 SmartScreen 提示，请先核对 SHA-256，再选择“更多信息 → 仍要运行”。</p>}
        <details>
          <summary style={{ cursor: 'pointer' }}>如何核验 SHA-256</summary>
          <p>在 PowerShell 执行：<code style={{ userSelect: 'all' }}>Get-FileHash .\XianyuConnectorSetup-{release.version}.exe -Algorithm SHA256</code>，结果必须与本页完全一致。</p>
        </details>
        </div>
      </details>}
    </div>
    <div className="card" style={{ padding: 20, marginBottom: 16 }}>
      <strong>运行规则</strong>
      <p>电脑或本地连接器离线时，自动回复明确暂停；系统不会切换到云端连接。</p>
    </div>
    {message && <div className="card" style={{ padding: 20 }}>{message}</div>}
    {devices.map(device => <div className="card" style={{ padding: 20, marginBottom: 16 }} key={device.id}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16 }}>
        <div><h3>{device.device_name}</h3><p>{device.online ? '设备在线' : '设备离线'} · 连接器 {device.app_status} · v{device.app_version}</p></div>
        {device.status === 'active' && <button className="btn btn-danger" onClick={() => void revoke(device.id)}>解绑并吊销</button>}
      </div>
      {device.accounts.map(account => <div key={account.account_id} style={{ borderTop: '1px solid var(--border-color)', paddingTop: 12, marginTop: 12 }}>
        <strong>闲鱼账号 {account.account_id}</strong>
        <p>状态：{account.connection_status}{account.error_message ? ` · ${account.error_message}` : ''}</p>
      </div>)}
    </div>)}
  </div>
}
